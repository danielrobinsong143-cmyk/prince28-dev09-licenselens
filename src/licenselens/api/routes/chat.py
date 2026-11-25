"""Chat and LLM usage API endpoints."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from licenselens.api.deps import get_session
from licenselens.config import Settings
from licenselens.db.repository import LlmCallRepository
from licenselens.llm.gateway import (
    LLMProviderAttempt,
    LLMStreamResult,
    calculate_cost_inr,
)
from licenselens.llm.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

MODEL_PRICES = {
    "gemini": {"input": 0.75, "output": 3.75},
    "groq": {"input": 0.075, "output": 0.30},
}


def load_system_prompt(settings: Settings) -> str:
    prompt_path = settings.prompts_dir / "chat_system_v1.md"

    if not prompt_path.exists():
        raise HTTPException(
            status_code=500,
            detail="System prompt file not found.",
        )

    return prompt_path.read_text(encoding="utf-8")


def build_messages(
    system_prompt: str,
    message: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]


def estimate_cost(
    provider: str,
    tokens_in: int,
    tokens_out: int,
    settings: Settings,
) -> float:
    prices = MODEL_PRICES.get(
        provider,
        {"input": 0.0, "output": 0.0},
    )

    return calculate_cost_inr(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        input_price_per_million=prices["input"],
        output_price_per_million=prices["output"],
        usd_inr=settings.usd_inr,
    )


async def record_usage(
    session: AsyncSession,
    request_id: str,
    purpose: str,
    result: LLMStreamResult,
    settings: Settings,
) -> float:
    cost = estimate_cost(
        result.provider,
        result.tokens_in,
        result.tokens_out,
        settings,
    )

    repository = LlmCallRepository(session)

    try:
        await repository.record(
            request_id=request_id,
            provider=result.provider,
            model=result.model,
            purpose=purpose,
            prompt_version="chat_system_v1",
            status="ok",
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            cost_inr=cost,
            fallback_used=result.fallback_used,
        )
        await session.commit()

    except Exception:
        await session.rollback()
        logger.exception("Could not record LLM usage.")
        raise

    return cost


async def record_provider_attempt(
    session: AsyncSession,
    request_id: str,
    purpose: str,
    attempt: LLMProviderAttempt,
    settings: Settings,
) -> None:
    """Persist one provider attempt, including failures."""

    cost = estimate_cost(
        attempt.provider,
        attempt.tokens_in,
        attempt.tokens_out,
        settings,
    )

    repository = LlmCallRepository(session)

    try:
        await repository.record(
            request_id=request_id,
            provider=attempt.provider,
            model=attempt.model,
            purpose=purpose,
            prompt_version="chat_system_v1",
            status=attempt.status,
            error=attempt.error,
            tokens_in=attempt.tokens_in,
            tokens_out=attempt.tokens_out,
            latency_ms=attempt.latency_ms,
            cost_inr=cost,
            fallback_used=attempt.fallback_used,
        )
        await session.commit()

    except Exception:
        await session.rollback()
        logger.exception("Could not record provider attempt.")
        raise


async def stream_response(
    payload: ChatRequest,
    request: Request,
    session: AsyncSession,
) -> StreamingResponse:
    """Create an SSE response after confirming the first LLM chunk."""

    settings: Settings = request.app.state.settings
    gateway = request.app.state.llm_gateway

    messages = build_messages(
        load_system_prompt(settings),
        payload.message,
    )

    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    async def on_attempt(attempt: LLMProviderAttempt) -> None:
        await record_provider_attempt(
            session=session,
            request_id=request_id,
            purpose=payload.purpose,
            attempt=attempt,
            settings=settings,
        )

    stream_iterator = gateway.stream(
        messages,
        temperature=payload.temperature,
        on_attempt=on_attempt,
    ).__aiter__()

    # Obtain the first item before sending HTTP response headers.
    try:
        first_item = await stream_iterator.__anext__()
    except StopAsyncIteration as exc:
        raise HTTPException(
            status_code=503,
            detail="LLM providers returned an empty stream.",
        ) from exc
    except Exception as exc:
        logger.exception("All LLM streaming providers failed.")

        raise HTTPException(
            status_code=503,
            detail="All configured LLM providers failed.",
        ) from exc

    async def generate() -> AsyncIterator[str]:
        stream_result: LLMStreamResult | None = None

        yield (f"event: start\ndata: {json.dumps({'request_id': request_id})}\n\n")

        try:
            items = _stream_items(first_item, stream_iterator)

            async for item in items:
                if isinstance(item, LLMStreamResult):
                    stream_result = item
                else:
                    yield f"data: {json.dumps({'content': item})}\n\n"

            if stream_result is None:
                raise RuntimeError("LLM stream completed without usage metadata.")

            cost = estimate_cost(
                stream_result.provider,
                stream_result.tokens_in,
                stream_result.tokens_out,
                settings,
            )

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            yield (
                "event: done\n"
                "data: "
                + json.dumps(
                    {
                        "request_id": request_id,
                        "provider": stream_result.provider,
                        "model": stream_result.model,
                        "tokens_in": stream_result.tokens_in,
                        "tokens_out": stream_result.tokens_out,
                        "latency_ms": latency_ms,
                        "cost_inr": cost,
                        "fallback_used": stream_result.fallback_used,
                    }
                )
                + "\n\n"
            )

        except Exception:
            await session.rollback()
            logger.exception("LLM streaming request failed.")

            yield (f"event: error\ndata: {json.dumps({'detail': 'LLM streaming failed.'})}\n\n")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_items(first_item, iterator):
    """Yield the prefetched item followed by remaining stream items."""
    yield first_item

    async for item in iterator:
        yield item


@router.post("/chat", response_model=None)
async def chat(
    payload: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    if payload.stream:
        return await stream_response(payload, request, session)

    settings: Settings = request.app.state.settings
    gateway = request.app.state.llm_gateway

    request_id = str(uuid.uuid4())
    messages = build_messages(
        load_system_prompt(settings),
        payload.message,
    )

    try:
        result = await gateway.complete(
            messages,
            temperature=payload.temperature,
        )
    except Exception as exc:
        logger.exception("LLM request failed.")
        raise HTTPException(
            status_code=502,
            detail="All configured LLM providers failed.",
        ) from exc

    cost = estimate_cost(
        result.provider,
        result.tokens_in,
        result.tokens_out,
        settings,
    )

    repository = LlmCallRepository(session)

    try:
        await repository.record(
            request_id=request_id,
            provider=result.provider,
            model=result.model,
            purpose=payload.purpose,
            prompt_version="chat_system_v1",
            status="ok",
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            cost_inr=cost,
            fallback_used=result.fallback_used,
        )
        await session.commit()

    except Exception:
        await session.rollback()
        logger.exception("Could not record LLM usage.")

    return ChatResponse(
        request_id=request_id,
        answer=result.content,
        provider=result.provider,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        latency_ms=result.latency_ms,
        cost_inr=cost,
        fallback_used=result.fallback_used,
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    return await stream_response(payload, request, session)


@router.get("/usage")
async def get_usage(
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict:
    repository = LlmCallRepository(session)
    return await repository.get_usage(days=days)
