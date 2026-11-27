"""Extract structured metadata from ingested LicenseLens documents."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from licenselens.config import get_settings
from licenselens.db.models import Document
from licenselens.db.repository import DocumentRepository
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.llm.extract import (
    DEFAULT_EXTRACTION_CHUNK_CHARS,
    DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS,
    DEFAULT_EXTRACTION_RPM,
    LLMOutputTruncatedError,
    combine_document_text,
    extract_metadata_from_chunks,
)
from licenselens.llm.extraction_state import (
    DEFAULT_FAILURES_PATH,
    clear_failure,
    load_failures,
    record_failure,
    safe_failure_reason,
)
from licenselens.llm.gateway import (
    ProviderBadRequestError,
    ProviderPayloadTooLargeError,
    calculate_cost_inr,
    create_gateway,
)
from licenselens.llm.prompts import read_prompt
from licenselens.llm.recorder import MODEL_PRICES, LLMCallRecorder

PROMPT_DIR = Path("prompts")
PROMPT_VERSION = "extract_v1"


async def get_pending_documents(
    session,
    limit: int,
    excluded_ids: set[str] | None = None,
    document_id: str | None = None,
    retry_ids: set[str] | None = None,
) -> list[str]:
    """Get external IDs of documents awaiting metadata extraction."""

    stmt = (
        select(Document.external_id)
        .where(Document.doc_type != "record")
        .where(~Document.metadata_.has_key("doc_kind"))
    )

    if excluded_ids:
        stmt = stmt.where(Document.external_id.notin_(excluded_ids))

    if document_id is not None:
        stmt = stmt.where(Document.external_id == document_id)

    if retry_ids:
        stmt = stmt.order_by(
            case((Document.external_id.in_(retry_ids), 0), else_=1),
        )

    stmt = stmt.order_by(Document.id).limit(limit)

    result = await session.scalars(stmt)
    return list(result)


async def process_document(
    session,
    document: Document,
    gateway,
    prompt: str,
    recorder: LLMCallRecorder,
    usage_totals: dict[str, int | float] | None = None,
    chunk_chars: int = DEFAULT_EXTRACTION_CHUNK_CHARS,
) -> bool:
    """Extract and save metadata for one document."""

    document_text = combine_document_text(document)

    if not document_text.strip():
        print(f"SKIPPED: {document.external_id} — no page text")
        return False

    before_usage = dict(usage_totals or {})
    record_attempt = recorder.callback(
        purpose="extract",
        prompt_version=PROMPT_VERSION,
    )

    async def record_and_measure(attempt) -> None:
        if usage_totals is not None and attempt.status == "ok":
            usage_totals["tokens_in"] = usage_totals.get("tokens_in", 0) + attempt.tokens_in
            usage_totals["tokens_out"] = usage_totals.get("tokens_out", 0) + attempt.tokens_out
            prices = MODEL_PRICES.get(attempt.provider)
            if prices is not None:
                usage_totals["cost_inr"] = usage_totals.get("cost_inr", 0.0) + calculate_cost_inr(
                    tokens_in=attempt.tokens_in,
                    tokens_out=attempt.tokens_out,
                    input_price_per_million=prices["input"],
                    output_price_per_million=prices["output"],
                    usd_inr=recorder.settings.usd_inr,
                )
        if record_attempt is not None:
            await record_attempt(attempt)

    try:
        metadata, response = await extract_metadata_from_chunks(
            gateway=gateway,
            prompt=prompt,
            version=PROMPT_VERSION,
            document_text=document_text,
            on_attempt=record_and_measure,
            max_chars=chunk_chars,
        )
    except (LLMOutputTruncatedError, ValidationError, ValueError) as error:
        print(f"Document ID: {document.external_id}")
        print(f"Title: {document.title}")
        if isinstance(error, LLMOutputTruncatedError):
            finish_reason = error.finish_reason or "unavailable"
            print(
                "Output truncation: "
                f"finish_reason={finish_reason} completion_tokens={error.tokens_out} "
                f"attempts={error.attempts}"
            )
        print(f"DocMeta validation: failed ({type(error).__name__})")
        raise
    except Exception:
        print(f"Document ID: {document.external_id}")
        print(f"Title: {document.title}")
        print("DocMeta validation: not completed (extraction failed before validation)")
        raise

    extracted_data = metadata.model_dump(mode="json")

    print(f"Document ID: {document.external_id}")
    print(f"Title: {document.title}")
    print("DocMeta validation: passed")
    if usage_totals is None:
        tokens_in, tokens_out, cost_inr = response.tokens_in, response.tokens_out, None
    else:
        tokens_in = usage_totals.get("tokens_in", 0) - before_usage.get("tokens_in", 0)
        tokens_out = usage_totals.get("tokens_out", 0) - before_usage.get("tokens_out", 0)
        cost_inr = usage_totals.get("cost_inr", 0.0) - before_usage.get("cost_inr", 0.0)
    cost_text = f" cost_inr={cost_inr:.6f}" if cost_inr is not None else ""
    print(f"Token usage: input={tokens_in} output={tokens_out}{cost_text}")

    repository = DocumentRepository(session)

    try:
        updated = await repository.update_metadata(
            document.external_id,
            extracted_data,
        )
        if updated is None:
            raise LookupError(f"document {document.external_id} disappeared before metadata update")

        await session.commit()
    except Exception:
        print(f"Database save: failed for {document.external_id}")
        raise

    print(
        f"EXTRACTED: {document.external_id}\n"
        f"DocMeta: {json.dumps(extracted_data, ensure_ascii=False, sort_keys=True)}\n"
        f"Database save: confirmed (metadata committed for {document.external_id})"
    )

    return True


async def run(
    limit: int,
    rpm: float | None = None,
    max_attempts: int | None = None,
    document_id: str | None = None,
    groq_only: bool = False,
    gemini_only: bool = False,
    failures_path: Path = DEFAULT_FAILURES_PATH,
    chunk_chars: int | None = None,
    max_output_tokens: int | None = None,
    settings: Any | None = None,
) -> None:
    """Run metadata extraction for pending documents."""

    if settings is None:
        settings = get_settings()

    if rpm is None:
        rpm = getattr(settings, "extraction_rpm", DEFAULT_EXTRACTION_RPM)
    if chunk_chars is None:
        chunk_chars = getattr(settings, "extraction_chunk_chars", DEFAULT_EXTRACTION_CHUNK_CHARS)
    if max_output_tokens is None:
        max_output_tokens = getattr(
            settings, "extraction_max_output_tokens", DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS
        )

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if rpm <= 0 or rpm > 2:
        raise ValueError("rpm must be greater than zero and at most 2")
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be at least 1")
    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be at least 1")
    if max_attempts is not None and max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if document_id is not None and limit != 1:
        raise ValueError("--document-id requires --limit 1")

    failure_state = load_failures(failures_path)

    # A one-success run is the safe single-document smoke-test mode.
    attempt_cap = 1 if document_id is not None or limit == 1 else max_attempts

    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)

    gateway = create_gateway(
        settings,
        requests_per_minute=rpm,
        max_tokens=max_output_tokens,
        groq_only=groq_only,
        gemini_only=gemini_only,
    )
    recorder = LLMCallRecorder(sessionmaker, settings)

    prompt = read_prompt(PROMPT_DIR, PROMPT_VERSION)

    processed = 0
    failed = 0
    skipped = 0
    attempted_ids: set[str] = set()
    usage_totals: dict[str, int | float] = {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_inr": 0.0,
    }

    if document_id is not None:
        print(f"Target document: {document_id}")

    try:
        while processed < limit and (attempt_cap is None or len(attempted_ids) < attempt_cap):
            batch_limit = limit - processed
            if attempt_cap is not None:
                batch_limit = min(batch_limit, attempt_cap - len(attempted_ids))
            async with sessionmaker() as session:
                external_ids = await get_pending_documents(
                    session,
                    batch_limit,
                    attempted_ids,
                    document_id,
                    set(failure_state),
                )

            if not external_ids:
                break

            stop = False
            for external_id in external_ids:
                if attempt_cap is not None and len(attempted_ids) >= attempt_cap:
                    break
                attempted_ids.add(external_id)
                attempt_ceiling = str(attempt_cap) if attempt_cap is not None else "unlimited"
                print(
                    f"Progress: attempt {len(attempted_ids)}/{attempt_ceiling}; "
                    f"success {processed}/{limit}; failed {failed}; skipped {skipped}"
                )
                document = None
                try:
                    async with sessionmaker() as session:
                        stmt = (
                            select(Document)
                            .options(selectinload(Document.pages))
                            .where(Document.external_id == external_id)
                        )

                        document = await session.scalar(stmt)

                        if document is None:
                            print(f"SKIPPED: {external_id} — document not found")
                            skipped += 1
                        else:
                            success = await process_document(
                                session=session,
                                document=document,
                                gateway=gateway,
                                prompt=prompt,
                                recorder=recorder,
                                usage_totals=usage_totals,
                                chunk_chars=chunk_chars,
                            )

                            if success:
                                processed += 1
                                try:
                                    failure_state = clear_failure(failures_path, external_id)
                                except (OSError, ValueError):
                                    print("Failure tracking cleanup: failed (safe_state_error)")
                            else:
                                skipped += 1
                                failure_state = record_failure(
                                    failures_path,
                                    external_id,
                                    "empty_document_text",
                                )

                except Exception as error:
                    failed += 1
                    title = document.title if document is not None else "unavailable"
                    reason = safe_failure_reason(error)
                    try:
                        failure_state = record_failure(failures_path, external_id, reason)
                    except (OSError, ValueError):
                        print("Failure tracking update: failed (safe_state_error)")
                    if isinstance(error, ProviderBadRequestError):
                        print(f"FAILED: {external_id} | Title: {title} | HTTP 400")
                        print(
                            "HTTP 400 diagnostic fields: "
                            f"{json.dumps(error.safe_details, sort_keys=True)}"
                        )
                    elif isinstance(error, ProviderPayloadTooLargeError):
                        print(f"FAILED: {external_id} | Title: {title} | HTTP 413")
                        print(
                            "HTTP 413 diagnostic fields: "
                            f"{json.dumps(error.safe_details, sort_keys=True)}"
                        )
                    else:
                        print(f"FAILED: {external_id} | Title: {title} | reason={reason}")
                    if document_id is not None:
                        print("Stopping after the selected document failed.")
                        stop = True
                        break
                    if gateway.all_providers_unavailable:
                        print("Stopping: all configured providers are unavailable.")
                        stop = True
                        break

                if processed >= limit:
                    break

            if stop or (attempt_cap is not None and len(attempted_ids) >= attempt_cap):
                break

        async with sessionmaker() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.doc_type != "record")
                .where(~Document.metadata_.has_key("doc_kind"))
            )

        print(
            f"\nExtraction completed."
            f"\nAttempted: {len(attempted_ids)}"
            f"\nSuccessful: {processed}"
            f"\nFailed: {failed}"
            f"\nSkipped: {skipped}"
            f"\nInput tokens: {int(usage_totals['tokens_in'])}"
            f"\nOutput tokens: {int(usage_totals['tokens_out'])}"
            f"\nEstimated cost (INR): {float(usage_totals['cost_inr']):.6f}"
            f"\nPending: {pending or 0}"
        )

    finally:
        await engine.dispose()


def parse_args(
    argv: list[str] | None = None,
    settings: Any | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract metadata from LicenseLens documents.")

    default_rpm = (
        getattr(settings, "extraction_rpm", DEFAULT_EXTRACTION_RPM)
        if settings is not None
        else DEFAULT_EXTRACTION_RPM
    )
    default_chunk_chars = (
        getattr(settings, "extraction_chunk_chars", DEFAULT_EXTRACTION_CHUNK_CHARS)
        if settings is not None
        else DEFAULT_EXTRACTION_CHUNK_CHARS
    )
    default_max_output_tokens = (
        getattr(settings, "extraction_max_output_tokens", DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS)
        if settings is not None
        else DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="successful documents to extract; each document is attempted once per run",
    )
    parser.add_argument(
        "--rpm",
        type=float,
        default=default_rpm,
        help="maximum request starts per minute per provider (maximum 2 for Groq's token cap)",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=default_chunk_chars,
        help="maximum character count per extraction chunk",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=default_max_output_tokens,
        help="maximum completion tokens for metadata extraction",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="maximum distinct documents to attempt; --limit 1 is always capped at one",
    )
    parser.add_argument(
        "--document-id",
        help="process only this pending document; requires --limit 1",
    )
    parser.add_argument(
        "--groq-only",
        action="store_true",
        help="disable Gemini fallback and use only Groq",
    )
    parser.add_argument(
        "--gemini-only",
        action="store_true",
        help="use only Gemini and disable Groq",
    )

    args = parser.parse_args(argv)
    if args.groq_only and args.gemini_only:
        parser.error("--groq-only and --gemini-only cannot both be enabled")

    if args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.rpm <= 0:
        parser.error("--rpm must be greater than zero")
    if args.rpm > 2:
        parser.error("--rpm must be at most 2 to stay within Groq's 8,000 TPM limit")
    if args.chunk_chars < 1:
        parser.error("--chunk-chars must be at least 1")
    if args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be at least 1")
    if args.max_attempts is not None and args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    if args.document_id is not None and args.limit != 1:
        parser.error("--document-id requires --limit 1")

    return args


def main() -> None:
    settings = get_settings()
    args = parse_args(settings=settings)
    asyncio.run(
        run(
            args.limit,
            args.rpm,
            args.max_attempts,
            document_id=args.document_id,
            groq_only=args.groq_only,
            gemini_only=args.gemini_only,
            chunk_chars=args.chunk_chars,
            max_output_tokens=args.max_output_tokens,
            settings=settings,
        )
    )


if __name__ == "__main__":
    main()
