"""All SQL for documents and LLM usage lives here."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Text, case, cast, func, or_, select
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

    async def update_metadata(
        self,
        external_id: str,
        metadata: dict[str, Any],
    ) -> Document | None:
        """Merge extracted metadata into an existing document."""
        doc = await self.session.scalar(select(Document).where(Document.external_id == external_id))

        if doc is None:
            return None

        existing = dict(doc.metadata_ or {})
        existing.update(metadata)

        doc.metadata_ = existing

        await self.session.flush()

        return doc


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

    async def get_usage(self, days: int = 7) -> dict[str, Any]:
        """Return lifetime totals and daily usage for the requested period."""
        cutoff = datetime.now(UTC) - timedelta(days=days)

        # 1. Lifetime totals (backward compatible).
        lifetime_stmt = select(
            func.count(LlmCall.id),
            func.coalesce(func.sum(LlmCall.tokens_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_out), 0),
            func.coalesce(func.sum(LlmCall.cost_inr), 0),
        )

        lifetime_result = await self.session.execute(lifetime_stmt)
        calls, tokens_in, tokens_out, cost_inr = lifetime_result.one()

        # 2. Usage totals within the requested date range.
        totals_stmt = select(
            func.count(LlmCall.id),
            func.coalesce(func.sum(case((LlmCall.status == "error", 1), else_=0)), 0),
            func.coalesce(func.sum(case((LlmCall.fallback_used.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(LlmCall.tokens_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_out), 0),
            func.coalesce(func.sum(LlmCall.cost_inr), 0),
        ).where(LlmCall.created_at >= cutoff)

        totals_result = await self.session.execute(totals_stmt)
        (
            period_calls,
            errors,
            fallbacks,
            period_tokens_in,
            period_tokens_out,
            period_cost,
        ) = totals_result.one()

        # 3. Group usage by calendar day.
        day_column = func.date(LlmCall.created_at)

        daily_stmt = (
            select(
                day_column.label("date"),
                func.count(LlmCall.id).label("calls"),
                func.coalesce(func.sum(case((LlmCall.status == "error", 1), else_=0)), 0).label(
                    "errors"
                ),
                func.coalesce(
                    func.sum(case((LlmCall.fallback_used.is_(True), 1), else_=0)),
                    0,
                ).label("fallbacks"),
                func.coalesce(func.sum(LlmCall.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(LlmCall.tokens_out), 0).label("tokens_out"),
                func.coalesce(func.sum(LlmCall.cost_inr), 0).label("cost_inr"),
            )
            .where(LlmCall.created_at >= cutoff)
            .group_by(day_column)
            .order_by(day_column)
        )

        daily_result = await self.session.execute(daily_stmt)

        daily = [
            {
                "date": row.date.isoformat(),
                "calls": row.calls,
                "errors": row.errors,
                "fallbacks": row.fallbacks,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "cost_inr": float(row.cost_inr),
            }
            for row in daily_result
        ]

        return {
            "days": days,
            "total_calls": calls,
            "total_tokens_in": tokens_in,
            "total_tokens_out": tokens_out,
            "total_cost_inr": float(cost_inr),
            "totals": {
                "calls": period_calls,
                "errors": errors,
                "fallbacks": fallbacks,
                "tokens_in": period_tokens_in,
                "tokens_out": period_tokens_out,
                "cost_inr": float(period_cost),
            },
            "daily": daily,
        }
