"""Read-only aggregate verification for saved extraction metadata."""

import argparse
import asyncio
from collections.abc import Iterable

from sqlalchemy import func, select, text

from licenselens.config import get_settings
from licenselens.db.models import Document
from licenselens.db.session import make_engine, make_sessionmaker
from licenselens.llm.doc_meta import DocMeta

PARSER_FIELDS = {"source", "path", "parser", "pages", "tables", "chars"}


def summarize_extraction_rows(rows: Iterable[tuple[str, dict]]) -> dict[str, int]:
    """Check metadata in memory and return counts without exposing document values."""
    checked = complete = valid = parser_preserved = 0
    expected_fields = set(DocMeta.model_fields)

    for _external_id, metadata in rows:
        if "doc_kind" not in metadata:
            continue
        checked += 1
        extracted = {key: metadata[key] for key in expected_fields if key in metadata}
        complete += expected_fields <= metadata.keys()
        parser_preserved += metadata.keys() >= PARSER_FIELDS
        try:
            DocMeta.model_validate(extracted)
        except Exception:
            continue
        valid += 1

    return {
        "checked": checked,
        "complete_fields": complete,
        "valid_docmeta": valid,
        "parser_metadata_present": parser_preserved,
    }


async def verify(limit: int, document_ids: list[str] | None = None) -> dict[str, int]:
    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            parsed = await session.scalar(
                select(func.count()).select_from(Document).where(Document.doc_type != "record")
            )
            pending = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.doc_type != "record")
                .where(~Document.metadata_.has_key("doc_kind"))
            )
            stmt = select(Document.external_id, Document.metadata_).where(
                Document.doc_type != "record"
            )
            if document_ids:
                stmt = stmt.where(Document.external_id.in_(document_ids))
            else:
                stmt = stmt.where(Document.metadata_.has_key("doc_kind"))
            stmt = stmt.order_by(Document.updated_at.desc(), Document.id).limit(limit)
            rows = (await session.execute(stmt)).all()
            summary = summarize_extraction_rows(rows)
            return {
                "parsed_documents": parsed or 0,
                "pending_documents": pending or 0,
                "selected_documents_found": len(rows),
                "selected_extracted_documents": summary["checked"],
                "complete_docmeta_fields": summary["complete_fields"],
                "valid_docmeta": summary["valid_docmeta"],
                "parser_metadata_present": summary["parser_metadata_present"],
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check saved extraction metadata using read-only database queries."
    )
    parser.add_argument("--limit", type=int, default=50, help="maximum extracted rows to check")
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="verify one specified document (repeat the option for more documents)",
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        result = asyncio.run(verify(args.limit, args.document_ids))
    except Exception as error:
        print(f"Verification failed: {type(error).__name__}")
        raise SystemExit(1) from None

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
