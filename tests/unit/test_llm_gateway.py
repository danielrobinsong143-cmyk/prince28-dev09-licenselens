import asyncio

import httpx
import pytest

from licenselens.llm.gateway import (
    CircuitBreaker,
    FakeLLM,
    LLMGateway,
    LLMResponse,
    OpenAICompatProvider,
    calculate_cost_inr,
)


@pytest.mark.asyncio
async def test_fake_llm_complete():
    llm = FakeLLM()

    response = await llm.complete([{"role": "user", "content": "Hello"}])

    assert response.content == "This is a fake LLM response."
    assert response.provider == "fake"
    assert response.tokens_in == 10
    assert response.tokens_out == 8


@pytest.mark.asyncio
async def test_fake_llm_stream():
    llm = FakeLLM()

    chunks = [chunk async for chunk in llm.stream([{"role": "user", "content": "Hello"}])]

    assert "".join(chunks) == "This is a fake stream."


@pytest.mark.asyncio
async def test_gateway_uses_primary():
    gateway = LLMGateway(
        primary=FakeLLM(),
        fallback=FakeLLM(),
    )

    response = await gateway.complete([{"role": "user", "content": "Hello"}])

    assert response.provider == "fake"
    assert response.fallback_used is False


@pytest.mark.asyncio
async def test_gateway_uses_fallback_when_primary_fails():
    class FailingLLM:
        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("Primary unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    gateway = LLMGateway(
        primary=FailingLLM(),
        fallback=FakeLLM(),
    )

    response = await gateway.complete([{"role": "user", "content": "Hello"}])

    assert response.provider == "fake"
    assert response.fallback_used is True


@pytest.mark.asyncio
async def test_gateway_fallback_failure_is_propagated():
    class FailingLLM:
        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("Provider unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    gateway = LLMGateway(
        primary=FailingLLM(),
        fallback=FailingLLM(),
    )

    with pytest.raises(httpx.ConnectError):
        await gateway.complete([{"role": "user", "content": "Hello"}])


def test_llm_response_default_fallback_flag():
    response = LLMResponse(
        content="Hello",
        provider="test",
        model="test-model",
        tokens_in=5,
        tokens_out=5,
        latency_ms=10,
    )

    assert response.fallback_used is False


def test_provider_configuration():
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1/",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )

    assert provider.name == "groq"
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.model == "test-model"


@pytest.mark.asyncio
async def test_circuit_opens_after_three_failures():
    breaker = CircuitBreaker(failure_threshold=3)

    assert await breaker.allow_request() is True

    await breaker.record_failure()
    await breaker.record_failure()
    await breaker.record_failure()

    assert await breaker.allow_request() is False


@pytest.mark.asyncio
async def test_circuit_closes_after_success():
    breaker = CircuitBreaker(failure_threshold=1)

    await breaker.record_failure()
    assert await breaker.allow_request() is False

    await breaker.record_success()

    assert await breaker.allow_request() is True


@pytest.mark.asyncio
async def test_circuit_allows_one_probe_after_recovery():
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.01,
    )

    await breaker.record_failure()
    await asyncio.sleep(0.02)

    assert await breaker.allow_request() is True
    assert await breaker.allow_request() is False


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


@pytest.mark.asyncio
async def test_gateway_records_failed_primary_and_fallback_stream_attempts():
    class FailingStreamLLM:
        def __init__(self, name):
            self.name = name
            self.model = "test-model"

        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError(f"{self.name} unavailable")

        async def stream(self, messages, temperature=0.2):
            raise httpx.ConnectError(f"{self.name} unavailable")
            yield  # Makes this an async generator.

    attempts = []

    async def record_attempt(attempt):
        attempts.append(attempt)

    gateway = LLMGateway(
        primary=FailingStreamLLM("primary"),
        fallback=FailingStreamLLM("fallback"),
    )

    with pytest.raises(httpx.ConnectError):
        async for _ in gateway.stream(
            [{"role": "user", "content": "Hello"}],
            on_attempt=record_attempt,
        ):
            pass

    assert len(attempts) == 2
    assert [attempt.status for attempt in attempts] == ["error", "error"]
    assert [attempt.provider for attempt in attempts] == ["primary", "fallback"]
