"""Chat and LLM usage API endpoints."""

import json
import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from licenselens.api.deps import get_session
from licenselens.config import Settings
from licenselens.db.repository import LlmCallRepository
from licenselens.llm.gateway import calculate_cost_inr
from licenselens.llm.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Temporary prices per million tokens.
# Replace with verified provider pricing before production use.
MODEL_PRICES = {
    "gemini": {"input": 0.10, "output": 0.40},
    "groq": {"input": 0.10, "output": 0.50},
}


def load_system_prompt(settings: Settings) -> str:
    """Load the versioned system prompt."""
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
    """Build the messages sent to the LLM."""
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
    """Estimate provider cost in INR."""
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatResponse:
    """Generate an answer using the configured LLM gateway."""

    settings: Settings = request.app.state.settings
    gateway = request.app.state.llm_gateway
    request_id = str(uuid.uuid4())

    system_prompt = load_system_prompt(settings)
    messages = build_messages(system_prompt, payload.message)

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
            status="success",
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
) -> StreamingResponse:
    """Stream an LLM response using Server-Sent Events."""

    settings: Settings = request.app.state.settings
    gateway = request.app.state.llm_gateway

    system_prompt = load_system_prompt(settings)
    messages = build_messages(system_prompt, payload.message)

    async def generate():
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()

        yield f"event: start\ndata: {json.dumps({'request_id': request_id})}\n\n"

        try:
            async for chunk in gateway.stream(
                messages,
                temperature=payload.temperature,
            ):
                yield f"data: {json.dumps({'content': chunk})}\n\n"

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            yield (
                "event: done\n"
                f"data: {json.dumps({'request_id': request_id, 'latency_ms': latency_ms})}\n\n"
            )

        except Exception:
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


@router.get("/usage")
async def get_usage(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return aggregated LLM usage and cost."""

    repository = LlmCallRepository(session)

    return await repository.get_usage()
