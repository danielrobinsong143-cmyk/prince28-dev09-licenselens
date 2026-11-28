"""One job = one document: get the file, parse it, extract its record, store everything.

A job can run twice (the worker died, or a retry). That is safe, because every step first
checks what is already in the database: a document is identified by its content hash.
"""

import asyncio
import hashlib
import logging
import math
import re
import tempfile
import time
from pathlib import Path

import httpx
from arq import Retry
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from licenselens.db.models import Document, IngestItem
from licenselens.db.repository import DocumentRepository
from licenselens.ingest.fetch import RETRYABLE, HostGate, Robots, Source, fetch_one
from licenselens.ingest.parse import ParseError, parse_file
from licenselens.ingest.store import document_id, save_document, save_failure
from licenselens.llm.doc_meta import DocMeta
from licenselens.llm.extract import (
    DEFAULT_EXTRACTION_CHUNK_CHARS,
    LLMOutputTruncatedError,
    combine_document_text,
    extract_metadata_from_chunks,
    parse_json,
)
from licenselens.llm.gateway import CircuitOpenError, ProviderRateLimitedError

logger = logging.getLogger("licenselens.worker")
GITHUB_BLOB = re.compile(r"^https://github\.com/([^/]+/[^/]+)/blob/[^/]+/(.+)$")


class Permanent(Exception):
    """This document will never work, however often we try: no retry, straight to the list."""


def plan(
    records: list[dict], done_ids: set[str], *, source: str = "", limit: int = 0
) -> list[dict]:
    """Which manifest records need a job: downloaded, wanted, and not in the database yet."""
    todo, seen = [], set(done_ids)
    for record in records:
        if record.get("status") != "downloaded" or (source and record.get("source") != source):
            continue
        doc_id = document_id(record["sha256"])
        if doc_id in seen:
            continue
        seen.add(doc_id)
        todo.append(record)
    return todo[:limit] if limit else todo


def raw_url(record: dict) -> str:
    """Where a worker without your laptop's files can download this document."""
    match = GITHUB_BLOB.match(record["url"])
    if record.get("via") == "git" and match and record.get("commit"):
        return f"https://raw.githubusercontent.com/{match[1]}/{record['commit']}/{match[2]}"
    return record["url"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Pace:
    """At most `per_minute` passes a minute, shared by all jobs of this worker (backpressure)."""

    def __init__(self, per_minute: float, clock=time.monotonic, sleep=asyncio.sleep) -> None:
        self._gap, self._clock, self._sleep = 60 / per_minute, clock, sleep
        self._next = 0.0

    async def wait(self) -> None:
        now = self._clock()
        delay = max(0.0, self._next - now)
        self._next = max(now, self._next) + self._gap
        if delay:
            await self._sleep(delay)


async def get_file(
    record: dict, folder: Path, client: httpx.AsyncClient, sleep=asyncio.sleep
) -> Path:
    """The file from disk if it is there; otherwise download it into `folder`."""
    path = Path(record["path"])
    if path.is_file():
        return path
    if record.get("via") == "manual":
        raise Permanent("downloaded by hand: this file exists only on the laptop that has it")
    source = Source(raw_url(record), record["source"], record.get("licence") or "unknown")
    result = await fetch_one(
        client,
        source,
        root=folder,
        raw_dir=folder / "raw",
        robots=Robots(client),
        gate=HostGate(0.5, sleep),
        slots=asyncio.Semaphore(1),
        max_attempts=2,
        sleep=sleep,
    )
    if result["status"] == "downloaded":
        return folder / result["path"]
    problem = f"download {result['status']}: {result.get('error', '')}"
    if result["status"] == "blocked" or result.get("http_status") not in {None, *RETRYABLE}:
        raise Permanent(problem)
    raise RuntimeError(problem)


async def process(ctx: dict, session: AsyncSession, item: IngestItem) -> tuple[str, int]:
    """Do the work for one item. Returns (status, pages). Raises to ask for a retry."""
    record = dict(item.record)
    with tempfile.TemporaryDirectory() as folder:
        path = await get_file(record, Path(folder), ctx["http"])
        record["sha256"] = await asyncio.to_thread(sha256_of, path)
        doc_id = document_id(record["sha256"])
        document = await session.scalar(
            select(Document)
            .options(selectinload(Document.pages))
            .where(Document.external_id == doc_id)
        )
        status = "skipped"
        if document is None:
            try:
                parsed = await asyncio.to_thread(parse_file, path)
            except ParseError as error:
                await save_failure(session, record, error)
                raise Permanent(f"parse: {error.reason}") from error
            await save_document(session, record, parsed)
            document = await session.scalar(
                select(Document)
                .options(selectinload(Document.pages))
                .where(Document.external_id == doc_id)
            )
            status = "done"
    pages = int(document.metadata_.get("pages", 0))
    task = ctx.get("extract")
    note = document.metadata_.get("extract") or {}
    if task and (note.get("prompt") != task["version"] or "error" in note):
        await ctx["pace"].wait()
        text = combine_document_text(document)
        if not text.strip():
            repo = DocumentRepository(session)
            await repo.update_metadata(
                document.external_id,
                {"extract": {"prompt": task["version"], "skipped": "no_text"}},
            )
            return status, pages

        record_attempt = None
        recorder = ctx.get("recorder")
        if recorder is not None:
            record_attempt = recorder.callback(
                purpose="extract",
                prompt_version=task["version"],
            )

        if task.get("schema") and task["schema"] is not DocMeta:
            schema = task["schema"]
            messages = [
                {"role": "system", "content": task["prompt"]},
                {"role": "user", "content": f"<document>\n{text.strip()}\n</document>"},
            ]
            response = await ctx["gateway"].complete(messages)
            try:
                data = parse_json(response.text)
                record_obj = schema.model_validate(data)
            except Exception as error:
                repo = DocumentRepository(session)
                await repo.update_metadata(
                    document.external_id,
                    {"extract": {"prompt": task["version"], "error": str(error)[:200]}},
                )
                await session.commit()
                raise Permanent(f"extract: {str(error)[:200]}") from error
            extracted_data = record_obj.model_dump(mode="json")
        else:
            try:
                metadata, _response = await extract_metadata_from_chunks(
                    gateway=ctx["gateway"],
                    prompt=task["prompt"],
                    version=task["version"],
                    document_text=text,
                    on_attempt=record_attempt,
                    max_chars=task.get("chars", DEFAULT_EXTRACTION_CHUNK_CHARS),
                )
            except (LLMOutputTruncatedError, ValidationError, ValueError) as error:
                repo = DocumentRepository(session)
                await repo.update_metadata(
                    document.external_id,
                    {"extract": {"prompt": task["version"], "error": str(error)[:200]}},
                )
                await session.commit()
                raise Permanent(f"extract: {str(error)[:200]}") from error
            extracted_data = metadata.model_dump(mode="json")

        extracted_data["extract"] = {"prompt": task["version"]}
        repo = DocumentRepository(session)
        await repo.update_metadata(document.external_id, extracted_data)
        status = "done"
    return status, pages


async def _cleanup_cancelled_item(ctx: dict, item_id: int, attempt: int, max_tries: int) -> None:
    """Safely reset or fail a running item if worker task was cancelled/timed out."""

    async def _do_cleanup() -> None:
        try:
            sessionmaker = ctx.get("sessionmaker")
            if sessionmaker is None:
                return
            async with sessionmaker() as session:
                item = await session.get(IngestItem, item_id)
                if item is not None and item.status == "running":
                    if attempt >= max_tries:
                        item.status = "failed"
                        item.error = f"gave up after {attempt} tries: job cancelled or timed out"
                    else:
                        item.status = "queued"
                        item.error = f"try {attempt} interrupted (job cancelled or timed out)"
                    await session.commit()
                    logger.warning("item %s reset to %s after cancellation", item_id, item.status)
        except Exception:
            logger.exception("Failed to update status for cancelled item %s", item_id)

    cleanup_task = asyncio.create_task(_do_cleanup())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task


async def ingest_document(ctx: dict, item_id: int) -> str:
    """The function the worker runs for every queued item. It never loses an item silently."""
    max_tries = ctx["settings"].ingest_max_tries
    attempt: int | None = None
    try:
        async with ctx["sessionmaker"]() as session:
            item = await session.get(IngestItem, item_id)
            if item is None or item.status not in ("queued", "running"):
                return "ignored"
            if item.attempts >= max_tries:
                item.status = "failed"
                prefix = f"gave up: started {item.attempts} times and never finished"
                if item.error and item.error.strip():
                    item.error = f"{prefix}; last error: {item.error.strip()}"[:500]
                else:
                    item.error = prefix[:500]
                await session.commit()
                return "failed"
            item.status, item.attempts = "running", item.attempts + 1
            attempt = item.attempts
            await session.commit()
            try:
                async with asyncio.timeout(45.0):
                    item.status, item.pages = await process(ctx, session, item)
                item.error = ""
            except Permanent as error:
                item = await session.get(IngestItem, item_id)
                if item is None:
                    return "failed"
                item.status, item.error = "failed", str(error)[:500]
            except Exception as error:
                await session.rollback()
                item = await session.get(IngestItem, item_id)
                if item is None:
                    return "failed"
                reason = f"{type(error).__name__}: {error}"[:500]
                if attempt >= max_tries:
                    item.status, item.error = "failed", f"gave up after {attempt} tries: {reason}"
                else:
                    item.status, item.error = "queued", f"try {attempt} failed: {reason}"
                    await session.commit()
                    logger.warning("item %s will be retried: %s", item_id, reason)
                    if isinstance(error, ProviderRateLimitedError):
                        delay = getattr(error, "retry_after", None) or 60.0
                        defer_seconds = max(35, math.ceil(delay))
                    elif isinstance(error, CircuitOpenError):
                        # Circuit recovery timeout is 30.0s; defer beyond it to allow recovery
                        defer_seconds = max(35, attempt * 30)
                    else:
                        defer_seconds = attempt * 30
                    raise Retry(defer=defer_seconds) from error
            await session.commit()
            logger.info("item %s %s", item_id, item.status)
            return item.status
    except asyncio.CancelledError:
        if attempt is not None:
            await _cleanup_cancelled_item(ctx, item_id, attempt, max_tries)
        raise
