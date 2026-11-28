"""ingest_jobs and ingest_items: the state of every queued document lives in Postgres.

Redis only carries "please handle item 42". If Redis loses its memory, the truth is still
here, and the worker can queue the unfinished items again.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from licenselens.db.models import Document, IngestItem, IngestJob, ParseFailure
from licenselens.ingest.store import document_id

OPEN = ("queued", "running")  # not finished yet
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _is_valid_sha256(value: object) -> bool:
    """Validate that value is a string containing exactly 64 hexadecimal characters."""
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX_CHARS for c in value)


class IngestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def known_ids(self) -> set[str]:
        """Documents already in the database, plus files the parser has already given up on."""
        stmt = select(Document.external_id).where(Document.doc_type != "record")
        known = set(await self.session.scalars(stmt))
        failed = await self.session.scalars(select(ParseFailure.sha256))
        return known | {document_id(sha) for sha in failed}

    async def create(self, records: list[dict]) -> tuple[str, list[int]]:
        """One job, one item per manifest record. Returns the job id and the item ids."""
        job = IngestJob(id=uuid.uuid4().hex, requested=len(records))
        job.items = [IngestItem(record=record) for record in records]
        self.session.add(job)
        await self.session.commit()
        return job.id, [item.id for item in job.items]

    async def progress(self, job_id: str) -> dict | None:
        job = await self.session.get(IngestJob, job_id)
        if job is None:
            return None
        rows = await self.session.execute(
            select(IngestItem.status, func.count(), func.sum(IngestItem.pages))
            .where(IngestItem.job_id == job_id)
            .group_by(IngestItem.status)
        )
        counts = {"queued": 0, "running": 0, "done": 0, "skipped": 0, "failed": 0}
        pages = 0
        for status, count, page_sum in rows:
            counts[status] = count
            pages += int(page_sum or 0)
        last = await self.session.scalar(
            select(func.max(IngestItem.updated_at)).where(IngestItem.job_id == job_id)
        )
        finished = counts["queued"] + counts["running"] == 0
        end = last if finished and last else datetime.now(UTC)
        return {
            "job_id": job.id,
            "status": "finished" if finished else "working",
            "requested": job.requested,
            "counts": counts,
            "pages": pages,
            "seconds": round((end - job.created_at).total_seconds(), 1),
            "created_at": job.created_at.isoformat(),
            "dead_letter": await self.dead_letter(job_id=job_id, limit=20),
        }

    async def dead_letter(self, *, job_id: str | None = None, limit: int = 50) -> list[dict]:
        """Items that failed for good, newest first: the list a person has to look at."""
        stmt = select(IngestItem).where(IngestItem.status == "failed")
        if job_id:
            stmt = stmt.where(IngestItem.job_id == job_id)
            rows = await self.session.scalars(
                stmt.order_by(IngestItem.updated_at.desc()).limit(limit)
            )
            return [
                {
                    "item": row.id,
                    "job_id": row.job_id,
                    "url": row.record.get("url", "") if isinstance(row.record, dict) else "",
                    "path": row.record.get("path", "") if isinstance(row.record, dict) else "",
                    "attempts": row.attempts,
                    "error": row.error,
                }
                for row in rows
            ]

        known = await self.known_ids()
        rows = await self.session.scalars(stmt.order_by(IngestItem.updated_at.desc()))
        results: list[dict] = []
        for row in rows:
            record = row.record if isinstance(row.record, dict) else {}
            sha = record.get("sha256")
            if _is_valid_sha256(sha) and document_id(sha) in known:
                continue
            results.append(
                {
                    "item": row.id,
                    "job_id": row.job_id,
                    "url": record.get("url", ""),
                    "path": record.get("path", ""),
                    "attempts": row.attempts,
                    "error": row.error,
                }
            )
            if len(results) >= limit:
                break
        return results

    async def failed_records(self) -> list[dict]:
        """Manifest records of unresolved dead-letter items, newest record per URL."""
        known = await self.known_ids()
        stmt = select(IngestItem.record).where(IngestItem.status == "failed")
        rows = await self.session.scalars(stmt.order_by(IngestItem.updated_at.desc()))
        newest_by_url: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url", "")
            if url not in newest_by_url:
                newest_by_url[url] = row

        unresolved: list[dict] = []
        for record in newest_by_url.values():
            sha = record.get("sha256")
            if _is_valid_sha256(sha) and document_id(sha) in known:
                continue
            unresolved.append(record)
        return unresolved

    async def open_item_ids(self) -> list[int]:
        """Items that should be in the queue. Used to queue them again after Redis lost them."""
        stmt = select(IngestItem.id).where(IngestItem.status.in_(OPEN)).order_by(IngestItem.id)
        return list(await self.session.scalars(stmt))

    async def recover_crashed_items(
        self,
        max_tries: int,
        *,
        redis: object = None,
    ) -> tuple[list[int], list[int]]:
        """Recover items left in 'running' by a crashed or killed worker.

        - If attempts >= max_tries: mark failed, preserve existing error.
        - If attempts < max_tries: ensure item is eligible for retry.
        - Clean up any stale Redis keys so deduplication doesn't block recovery.
        """
        stmt = (
            select(IngestItem)
            .where(IngestItem.status == "running")
            .order_by(IngestItem.id)
            .with_for_update(skip_locked=True)
        )
        running_items = list(await self.session.scalars(stmt))
        failed_ids: list[int] = []
        requeued_ids: list[int] = []

        queue_name = (
            getattr(redis, "default_queue_name", "arq:queue") if redis is not None else "arq:queue"
        )

        for item in running_items:
            job_id = f"ingest:{item.id}"
            in_progress = False
            in_queue = False
            if redis is not None:
                try:
                    if hasattr(redis, "exists"):
                        in_progress = bool(await redis.exists(f"arq:in-progress:{job_id}"))
                    if hasattr(redis, "zscore"):
                        score = await redis.zscore(queue_name, job_id)
                        in_queue = score is not None
                except Exception:
                    pass

            # If actively running on another live worker, do not interfere
            if in_progress:
                continue

            if item.attempts >= max_tries:
                item.status = "failed"
                prefix = f"gave up: started {item.attempts} times and never finished"
                if item.error and item.error.strip():
                    if not item.error.startswith(prefix):
                        item.error = f"{prefix}; last error: {item.error.strip()}"[:500]
                else:
                    item.error = prefix[:500]
                failed_ids.append(item.id)
                if redis is not None:
                    try:
                        if hasattr(redis, "zrem"):
                            await redis.zrem(queue_name, job_id)
                        if hasattr(redis, "delete"):
                            await redis.delete(
                                f"arq:job:{job_id}",
                                f"arq:retry:{job_id}",
                                f"arq:in-progress:{job_id}",
                                f"arq:result:{job_id}",
                            )
                    except Exception:
                        pass
            else:
                if not in_queue:
                    item.status = "queued"
                    if not item.error or "interrupted" not in item.error:
                        item.error = f"try {item.attempts} interrupted (worker restarted)"[:500]
                    requeued_ids.append(item.id)
                else:
                    requeued_ids.append(item.id)

        if running_items:
            await self.session.commit()

        return failed_ids, requeued_ids
