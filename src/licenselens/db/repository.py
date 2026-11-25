"""All SQL for documents and LLM usage lives here."""

import re
from typing import Any

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from licenselens.db.models import Document, LlmCall

FILTER_KEY = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def to_dict(doc: Document) -> dict[str, Any]:
    """Keep the same flat response shape as the previous API."""
    return {
        "id": doc.external_id,
        **doc.metadata_,
        "title": doc.title,
        "doc_type": doc.doc_type,
        "source_url": doc.source_url,
        "licence": doc.licence,
        "published_on": (doc.published_on.isoformat() if doc.published_on else None),
    }


def _value_candidates(value: str) -> list[Any]:
    """Interpret URL values as text, numbers, booleans or list items."""
    out: list[Any] = [value, [value]]
    lowered = value.lower()

    if lowered in ("true", "false"):
        out.append(lowered == "true")
    elif re.fullmatch(r"-?\d+", value):
        out.append(int(value))
    elif re.fullmatch(r"-?\d+\.\d+", value):
        out.append(float(value))

    return out


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        *,
        q: str | None,
        filters: dict[str, str],
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        stmt = select(Document)

        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(like),
                    cast(Document.metadata_, Text).ilike(like),
                )
            )

        for key, value in filters.items():
            stmt = stmt.where(
                or_(
                    *(
                        Document.metadata_.contains({key: candidate})
                        for candidate in _value_candidates(value)
                    )
                )
            )

        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))

        rows = await self.session.scalars(
            stmt.order_by(Document.external_id).limit(limit).offset(offset)
        )

        return total or 0, [to_dict(doc) for doc in rows]

    async def get(self, external_id: str) -> dict[str, Any] | None:
        doc = await self.session.scalar(select(Document).where(Document.external_id == external_id))
        return to_dict(doc) if doc else None


class LlmCallRepository:
    """Stores and retrieves LLM provider usage records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        request_id: str,
        provider: str,
        model: str,
        purpose: str,
        prompt_version: str,
        status: str,
        error: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        cost_inr: float = 0.0,
        fallback_used: bool = False,
    ) -> LlmCall:
        """Add one provider attempt to the current database transaction."""
        call = LlmCall(
            request_id=request_id,
            provider=provider,
            model=model,
            purpose=purpose,
            prompt_version=prompt_version,
            status=status,
            error=error,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_inr=cost_inr,
            fallback_used=fallback_used,
        )

        self.session.add(call)
        await self.session.flush()

        return call

    async def get_usage(self) -> dict[str, int | float]:
        """Return total LLM usage and cost."""
        stmt = select(
            func.count(LlmCall.id),
            func.coalesce(func.sum(LlmCall.tokens_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_out), 0),
            func.coalesce(func.sum(LlmCall.cost_inr), 0),
        )

        result = await self.session.execute(stmt)
        calls, tokens_in, tokens_out, cost_inr = result.one()

        return {
            "total_calls": calls,
            "total_tokens_in": tokens_in,
            "total_tokens_out": tokens_out,
            "total_cost_inr": float(cost_inr),
        }
