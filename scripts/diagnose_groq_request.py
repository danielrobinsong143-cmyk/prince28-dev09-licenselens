"""Run one Groq-only diagnostic request for a selected document."""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from licenselens.config import get_settings
from licenselens.db.models import Document
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.llm.diagnostic import (
    DiagnosticOutcome,
    groq_provider_from_settings,
    measure_extraction_request_sizes,
    run_one_shot_diagnostic,
)
from licenselens.llm.extract import combine_document_text
from licenselens.llm.prompts import read_prompt


async def _load_document_text(document_id: str, settings) -> str | None:
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            document = await session.scalar(
                select(Document)
                .options(selectinload(Document.pages))
                .where(Document.external_id == document_id)
            )
            if document is None:
                return None
            return combine_document_text(document)
    finally:
        await engine.dispose()


async def _run(document_id: str, chunk_index: int = 1) -> DiagnosticOutcome:
    try:
        settings = get_settings()
        provider = groq_provider_from_settings(settings)
        prompt = read_prompt(Path("prompts"), "extract_v1")
    except Exception as error:
        return DiagnosticOutcome(
            classification="setup_error",
            error_classification=type(error).__name__,
        )

    async def load_document(doc_id: str) -> str | None:
        return await _load_document_text(doc_id, settings)

    return await run_one_shot_diagnostic(
        document_id,
        load_document=load_document,
        provider=provider,
        prompt=prompt,
        chunk_index=chunk_index,
    )


async def _measure_only(document_id: str) -> DiagnosticOutcome:
    """Measure request bodies for every chunk without constructing a provider."""
    outcome = DiagnosticOutcome(classification="measure_only_not_started")
    try:
        settings = get_settings()
        prompt = read_prompt(Path("prompts"), "extract_v1")
    except Exception as error:
        outcome.classification = "setup_error"
        outcome.error_classification = type(error).__name__
        return outcome

    try:
        document_text = await _load_document_text(document_id, settings)
    except Exception as error:
        outcome.classification = "document_lookup_error"
        outcome.error_classification = type(error).__name__
        return outcome

    outcome.document_found = document_text is not None
    if document_text is None:
        outcome.classification = "document_not_found"
        return outcome

    outcome.page_text_available = bool(document_text.strip())
    if not outcome.page_text_available:
        outcome.classification = "empty_document_text"
        return outcome

    try:
        outcome.chunk_measurements = measure_extraction_request_sizes(
            document_text,
            prompt,
            settings.llm_fallback_model,
        )
    except Exception as error:
        outcome.classification = "measurement_error"
        outcome.error_classification = type(error).__name__
        return outcome

    outcome.extraction_chunk_count = len(outcome.chunk_measurements)
    outcome.classification = "measure_only_success"
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make at most one Groq diagnostic request; never saves extracted metadata."
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument(
        "--chunk-index",
        type=int,
        default=1,
        help="one-based extraction chunk to inspect (default: first chunk)",
    )
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="read the document and report each serialized chunk request size without HTTP",
    )
    args = parser.parse_args()

    try:
        if args.measure_only:
            outcome = asyncio.run(_measure_only(args.document_id))
        else:
            outcome = asyncio.run(_run(args.document_id, args.chunk_index))
    except Exception as error:
        outcome = DiagnosticOutcome(
            classification="diagnostic_error",
            error_classification=type(error).__name__,
        )

    print(outcome.to_json())
    sys.exit(0 if outcome.classification in {"success", "measure_only_success"} else 1)


if __name__ == "__main__":
    main()
