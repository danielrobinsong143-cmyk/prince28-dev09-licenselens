"""LLM gateway: providers, retries, streaming, fallback and circuit breaker."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 2


def calculate_cost_inr(
    tokens_in: int,
    tokens_out: int,
    input_price_per_million: float,
    output_price_per_million: float,
    usd_inr: float,
) -> float:
    """Calculate the total LLM cost in INR."""
    input_cost = tokens_in * input_price_per_million / 1_000_000
    output_cost = tokens_out * output_price_per_million / 1_000_000

    return round((input_cost + output_cost) * usd_inr, 6)


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    fallback_used: bool = False


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]: ...


class FakeLLM:
    """A predictable provider for testing without API calls."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        return LLMResponse(
            content="This is a fake LLM response.",
            provider="fake",
            model="fake-model",
            tokens_in=10,
            tokens_out=8,
            latency_ms=1,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        for word in ["This ", "is ", "a ", "fake ", "stream."]:
            yield word


class OpenAICompatProvider:
    """Calls providers supporting the OpenAI chat-completions API."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def _complete_with_retry(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> dict:
        """Retry temporary provider failures using exponential backoff."""

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                    },
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code

                if status not in RETRYABLE_STATUS_CODES:
                    raise

                if attempt == MAX_RETRIES:
                    raise

                delay = 2**attempt

                logger.warning(
                    "%s returned HTTP %s. Retrying in %s seconds.",
                    self.name,
                    status,
                    delay,
                )

                await asyncio.sleep(delay)

            except httpx.TimeoutException:
                if attempt == MAX_RETRIES:
                    raise

                delay = 2**attempt

                logger.warning(
                    "%s timed out. Retrying in %s seconds.",
                    self.name,
                    delay,
                )

                await asyncio.sleep(delay)

        raise RuntimeError("LLM request failed after retries.")

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Send a completion request with retry support."""

        start = time.perf_counter()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            data = await self._complete_with_retry(
                client,
                messages,
                temperature,
            )

        usage = data.get("usage", {})

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Stream response chunks using Server-Sent Events."""

        async with (
            httpx.AsyncClient(timeout=self.timeout) as client,
            client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as response,
        ):
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data = line[6:].strip()

                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content")

                if content:
                    yield content


class CircuitOpenError(Exception):
    """Raised when the circuit breaker blocks a provider request."""


class CircuitBreaker:
    """Prevents repeated requests to an unhealthy provider."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.opened_at: float | None = None
        self.half_open_probe = False
        self.lock = asyncio.Lock()

    async def allow_request(self) -> bool:
        async with self.lock:
            # CLOSED: provider is allowed to receive requests.
            if self.opened_at is None:
                return True

            # OPEN: recovery period has not elapsed.
            if time.monotonic() - self.opened_at < self.recovery_timeout:
                return False

            # HALF-OPEN: allow only one recovery probe.
            if self.half_open_probe:
                return False

            self.half_open_probe = True
            return True

    async def record_success(self) -> None:
        async with self.lock:
            self.failure_count = 0
            self.opened_at = None
            self.half_open_probe = False

            logger.info("Circuit breaker closed after successful request.")

    async def record_failure(self) -> None:
        async with self.lock:
            self.failure_count += 1
            self.half_open_probe = False

            if self.failure_count >= self.failure_threshold:
                self.opened_at = time.monotonic()

                logger.warning(
                    "Circuit breaker opened after %s failures.",
                    self.failure_count,
                )


class LLMGateway:
    """Uses the primary provider and falls back when it fails."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Try primary first, then use fallback when necessary."""

        if not await self.circuit_breaker.allow_request():
            logger.warning("Primary circuit is open. Using fallback.")
            response = await self.fallback.complete(messages, temperature)
            return replace(response, fallback_used=True)

        try:
            response = await self.primary.complete(messages, temperature)
        except httpx.HTTPError:
            logger.warning(
                "Primary provider unavailable. Switching to fallback.",
                exc_info=True,
            )
            await self.circuit_breaker.record_failure()
            response = await self.fallback.complete(messages, temperature)
            return replace(response, fallback_used=True)

        await self.circuit_breaker.record_success()
        return response

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Stream from primary, falling back only before the first chunk."""

        # If primary circuit is open, use fallback immediately.
        if not await self.circuit_breaker.allow_request():
            logger.warning("Primary circuit is open. Streaming from fallback.")

            async for chunk in self.fallback.stream(messages, temperature):
                yield chunk
            return

        primary_stream = self.primary.stream(
            messages,
            temperature,
        ).__aiter__()

        # Request the first chunk before committing to the primary stream.
        try:
            first_chunk = await primary_stream.__anext__()

        except StopAsyncIteration:
            await self.circuit_breaker.record_success()
            return

        except httpx.HTTPError:
            logger.warning(
                "Primary streaming failed before first chunk. Switching to fallback.",
                exc_info=True,
            )
            await self.circuit_breaker.record_failure()

            async for chunk in self.fallback.stream(messages, temperature):
                yield chunk
            return

        # First chunk received: primary stream is now committed.
        await self.circuit_breaker.record_success()
        yield first_chunk

        # Never switch providers after output has started.
        async for chunk in primary_stream:
            yield chunk


def create_gateway(settings) -> LLMGateway:
    """Create the configured Gemini and Groq gateway."""

    primary = OpenAICompatProvider(
        name="gemini",
        base_url=settings.llm_primary_base_url,
        api_key=settings.gemini_api_key,
        model=settings.llm_primary_model,
        timeout=settings.llm_timeout_seconds,
    )

    fallback = OpenAICompatProvider(
        name="groq",
        base_url=settings.llm_fallback_base_url,
        api_key=settings.groq_api_key,
        model=settings.llm_fallback_model,
        timeout=settings.llm_timeout_seconds,
    )

    return LLMGateway(
        primary=primary,
        fallback=fallback,
    )


def test_calculate_cost_inr():
    cost = calculate_cost_inr(
        tokens_in=1000,
        tokens_out=500,
        input_price_per_million=0.10,
        output_price_per_million=0.40,
        usd_inr=95.0,
    )

    assert cost == 0.0285


def test_calculate_cost_inr_with_zero_tokens():
    cost = calculate_cost_inr(
        tokens_in=0,
        tokens_out=0,
        input_price_per_million=0.10,
        output_price_per_million=0.40,
        usd_inr=95.0,
    )

    assert cost == 0.0
