from datetime import UTC, datetime, timedelta

import pytest

from licenselens.config import Settings
from licenselens.db.models import LlmCall
from licenselens.db.repository import LlmCallRepository
from licenselens.db.session import make_engine, make_sessionmaker


async def test_usage_returns_daily_breakdown(database_url):
    settings = Settings(
        app_env="ci",
        database_url=database_url,
        _env_file=None,
    )
    engine = make_engine(settings)

    try:
        now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)

        records = [
            LlmCall(
                request_id="request-1",
                provider="groq",
                model="openai/gpt-oss-20b",
                purpose="chat",
                prompt_version="v1",
                status="ok",
                tokens_in=100,
                tokens_out=50,
                latency_ms=200,
                cost_inr=0.01,
                fallback_used=False,
                created_at=now,
            ),
            LlmCall(
                request_id="request-2",
                provider="gemini",
                model="gemini-3.8-flash",
                purpose="chat",
                prompt_version="v1",
                status="error",
                error="Provider unavailable",
                tokens_in=20,
                tokens_out=0,
                latency_ms=100,
                cost_inr=0,
                fallback_used=False,
                created_at=now,
            ),
            LlmCall(
                request_id="request-3",
                provider="groq",
                model="openai/gpt-oss-20b",
                purpose="chat",
                prompt_version="v1",
                status="ok",
                tokens_in=80,
                tokens_out=40,
                latency_ms=150,
                cost_inr=0.02,
                fallback_used=True,
                created_at=now - timedelta(days=1),
            ),
            LlmCall(
                request_id="request-old",
                provider="groq",
                model="openai/gpt-oss-20b",
                purpose="chat",
                prompt_version="v1",
                status="ok",
                tokens_in=999,
                tokens_out=999,
                latency_ms=100,
                cost_inr=1,
                fallback_used=False,
                created_at=now - timedelta(days=10),
            ),
        ]

        async with make_sessionmaker(engine)() as session:
            await session.execute(LlmCall.__table__.delete())
            session.add_all(records)
            await session.commit()

            usage = await LlmCallRepository(session).get_usage(days=7)

        # Lifetime totals include the older record.
        assert usage["total_calls"] == 4
        assert usage["total_tokens_in"] == 1199
        assert usage["total_tokens_out"] == 1089
        assert usage["total_cost_inr"] == pytest.approx(1.03)

        # Date-range totals exclude the older record.
        assert usage["days"] == 7
        assert usage["totals"]["calls"] == 3
        assert usage["totals"]["errors"] == 1
        assert usage["totals"]["fallbacks"] == 1
        assert usage["totals"]["tokens_in"] == 200
        assert usage["totals"]["tokens_out"] == 90
        assert usage["totals"]["cost_inr"] == pytest.approx(0.03)

        # Verify daily breakdown.
        assert len(usage["daily"]) == 2

        today = next(row for row in usage["daily"] if row["date"] == now.date().isoformat())
        yesterday = next(
            row
            for row in usage["daily"]
            if row["date"] == (now - timedelta(days=1)).date().isoformat()
        )

        assert today["calls"] == 2
        assert today["errors"] == 1
        assert today["tokens_in"] == 120
        assert today["tokens_out"] == 50

        assert yesterday["calls"] == 1
        assert yesterday["fallbacks"] == 1
        assert yesterday["tokens_in"] == 80
        assert yesterday["tokens_out"] == 40

    finally:
        await engine.dispose()
