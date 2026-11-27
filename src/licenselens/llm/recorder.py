"""Record LLM provider attempts in the database."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from licenselens.config import Settings
from licenselens.db.repository import LlmCallRepository
from licenselens.llm.gateway import (
    LLMProviderAttempt,
    calculate_cost_inr,
)

MODEL_PRICES = {
    "gemini": {"input": 0.75, "output": 3.75},
    "groq": {"input": 0.075, "output": 0.30},
}


class LLMCallRecorder:
    """Persist provider attempts using independent database sessions."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        settings: Settings,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings

    def callback(
        self,
        *,
        purpose: str,
        prompt_version: str,
    ):
        request_id = str(uuid.uuid4())

        async def record(attempt: LLMProviderAttempt) -> None:
            prices = MODEL_PRICES.get(
                attempt.provider,
                {"input": 0.0, "output": 0.0},
            )

            cost = calculate_cost_inr(
                tokens_in=attempt.tokens_in,
                tokens_out=attempt.tokens_out,
                input_price_per_million=prices["input"],
                output_price_per_million=prices["output"],
                usd_inr=self.settings.usd_inr,
            )

            async with self.sessionmaker() as session:
                repository = LlmCallRepository(session)

                await repository.record(
                    request_id=request_id,
                    provider=attempt.provider,
                    model=attempt.model,
                    purpose=purpose,
                    prompt_version=prompt_version,
                    status=attempt.status,
                    error=attempt.error,
                    tokens_in=attempt.tokens_in,
                    tokens_out=attempt.tokens_out,
                    latency_ms=attempt.latency_ms,
                    cost_inr=cost,
                    fallback_used=attempt.fallback_used,
                )

                await session.commit()

        return record
