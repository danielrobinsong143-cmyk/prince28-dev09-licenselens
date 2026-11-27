import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from licenselens.llm.gateway import (
    CircuitBreaker,
    CircuitOpenError,
    FakeLLM,
    LLMGateway,
    LLMResponse,
    OpenAICompatProvider,
    ProviderBadRequestError,
    ProviderPayloadTooLargeError,
    ProviderQuotaExhaustedError,
    ProviderRateLimitedError,
    RequestPacer,
    build_completion_request_body,
    calculate_cost_inr,
    create_gateway,
    quota_exhausted,
    retry_after_seconds,
    serialized_request_body_size,
)


def test_serialized_request_size_matches_httpx_json_encoding():
    body = build_completion_request_body(
        model="openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": "Extract fields."},
            {"role": "user", "content": "Résumé text."},
        ],
        temperature=0,
        max_tokens=1000,
        response_format={"type": "json_object"},
    )

    assert serialized_request_body_size(body) == len(
        httpx.Request("POST", "https://test.invalid", json=body).content
    )


def test_serialized_request_size_increases_with_document_chunk_length():
    common = {
        "model": "openai/gpt-oss-20b",
        "temperature": 0,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }
    sizes = []
    for chunk_size in (500, 1000, 2000, 4000, 8000):
        body = build_completion_request_body(
            **common,
            messages=[
                {"role": "system", "content": "Prompt"},
                {"role": "user", "content": "x" * chunk_size},
            ],
        )
        sizes.append(serialized_request_body_size(body))

    assert sizes == sorted(sizes)
    assert sizes[-1] - sizes[0] == 7500


def test_create_gateway_groq_only_does_not_construct_gemini():
    settings = SimpleNamespace(
        llm_fallback_base_url="https://groq.example/v1",
        groq_api_key="groq-test-key",  # pragma: allowlist secret
        llm_fallback_model="openai/gpt-oss-20b",
        llm_timeout_seconds=30,
        llm_primary_base_url="https://gemini.example/v1",
        gemini_api_key="gemini-test-key",  # pragma: allowlist secret
        llm_primary_model="gemini-test-model",
    )

    gateway = create_gateway(settings, groq_only=True, requests_per_minute=2, max_tokens=1000)

    assert gateway.primary.name == "groq"
    assert gateway.primary.model == "openai/gpt-oss-20b"
    assert gateway.primary.max_tokens == 1000
    assert gateway.circuit_breaker.failure_threshold == 1
    assert gateway.fallback is None


def test_create_gateway_gemini_only_does_not_construct_groq():
    settings = SimpleNamespace(
        llm_fallback_base_url="https://groq.example/v1",
        groq_api_key="groq-test-key",  # pragma: allowlist secret
        llm_fallback_model="openai/gpt-oss-20b",
        llm_timeout_seconds=30,
        llm_primary_base_url="https://gemini.example/v1",
        gemini_api_key="gemini-test-key",  # pragma: allowlist secret
        llm_primary_model="gemini-3.1-flash-lite",
    )

    gateway = create_gateway(
        settings,
        gemini_only=True,
        requests_per_minute=1,
        max_tokens=1000,
    )

    assert gateway.primary.name == "gemini"
    assert gateway.primary.model == "gemini-3.1-flash-lite"
    assert gateway.primary.max_tokens == 1000
    assert gateway.primary.request_pacer is not None
    assert gateway.fallback is None


def test_create_gateway_shares_extraction_pacing_across_providers():
    settings = SimpleNamespace(
        llm_fallback_base_url="https://groq.example/v1",
        groq_api_key="groq-test-key",  # pragma: allowlist secret
        llm_fallback_model="openai/gpt-oss-20b",
        llm_timeout_seconds=30,
        llm_primary_base_url="https://gemini.example/v1",
        gemini_api_key="gemini-test-key",  # pragma: allowlist secret
        llm_primary_model="gemini-test-model",
    )

    gateway = create_gateway(settings, requests_per_minute=2, max_tokens=1000)

    assert gateway.primary.request_pacer is gateway.fallback.request_pacer
    assert gateway.primary.request_interval is None
    assert gateway.fallback.request_interval is None


@pytest.mark.asyncio
async def test_gateway_without_fallback_propagates_groq_failure():
    class FailingGroq:
        name = "groq"
        model = "openai/gpt-oss-20b"

        async def complete(self, messages, temperature=0.2, response_format=None):
            raise httpx.ConnectError("Groq unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    gateway = LLMGateway(primary=FailingGroq())

    with pytest.raises(httpx.ConnectError, match="Groq unavailable"):
        await gateway.complete([{"role": "user", "content": "Hello"}])

    assert gateway.fallback is None


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
async def test_gateway_records_primary_failure_and_fallback_success():
    class FailingLLM:
        name = "primary"
        model = "test-model"

        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("Primary unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    attempts = []

    async def record_attempt(attempt):
        attempts.append(attempt)

    gateway = LLMGateway(primary=FailingLLM(), fallback=FakeLLM())

    await gateway.complete(
        [{"role": "user", "content": "Hello"}],
        on_attempt=record_attempt,
    )

    assert [attempt.status for attempt in attempts] == ["error", "ok"]
    assert [attempt.provider for attempt in attempts] == ["primary", "fake"]
    assert attempts[1].fallback_used is True


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


@pytest.mark.asyncio
async def test_gateway_skips_primary_when_circuit_is_open():
    class FailingPrimary:
        name = "primary"
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, temperature=0.2):
            self.calls += 1
            raise httpx.ConnectError("Primary unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    primary = FailingPrimary()
    fallback = FakeLLM()
    gateway = LLMGateway(primary=primary, fallback=fallback)

    messages = [{"role": "user", "content": "Hello"}]

    # First three requests fail over to the fallback.
    for _ in range(3):
        response = await gateway.complete(messages)
        assert response.fallback_used is True

    assert primary.calls == 3

    # Fourth request must skip the primary.
    response = await gateway.complete(messages)

    assert response.fallback_used is True
    assert primary.calls == 3


@pytest.mark.asyncio
async def test_gateway_forwards_response_format():
    primary = FakeLLM()
    fallback = FakeLLM()

    gateway = LLMGateway(
        primary=primary,
        fallback=fallback,
    )

    response_format = {"type": "json_object"}
    messages = [{"role": "user", "content": "Extract metadata"}]

    await gateway.complete(
        messages,
        response_format=response_format,
    )

    assert primary.seen[0]["response_format"] == response_format
    assert fallback.seen == []


@pytest.mark.asyncio
async def test_generate_forwards_response_format():
    primary = FakeLLM()
    gateway = LLMGateway(primary=primary, fallback=FakeLLM())

    response_format = {"type": "json_object"}

    await gateway.generate(
        [{"role": "user", "content": "Extract metadata"}],
        purpose="extract",
        prompt_version="extract_v1",
        response_format=response_format,
    )

    assert primary.seen[0]["response_format"] == response_format


@pytest.mark.asyncio
async def test_gateway_forwards_response_format_to_fallback():
    class FailingLLM:
        async def complete(
            self,
            messages,
            temperature=0.2,
            response_format=None,
        ):
            raise httpx.ConnectError("Primary unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    fallback = FakeLLM()
    gateway = LLMGateway(
        primary=FailingLLM(),
        fallback=fallback,
    )

    response_format = {"type": "json_object"}

    await gateway.complete(
        [{"role": "user", "content": "Extract metadata"}],
        response_format=response_format,
    )

    assert fallback.seen[0]["response_format"] == response_format


@pytest.mark.asyncio
async def test_provider_sends_response_format_in_http_request():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"name": "MIT"}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            },
        )

    provider = OpenAICompatProvider(
        name="test",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
        max_tokens=1000,
    )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        response_format = {"type": "json_object"}

        await provider._complete_with_retry(
            client,
            [{"role": "user", "content": "Extract metadata"}],
            0.0,
            response_format,
        )

    assert captured["response_format"] == response_format
    assert captured["max_tokens"] == 1000


@pytest.mark.asyncio
async def test_groq_gpt_oss_omits_json_mode_for_local_repair(monkeypatch):
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not-json"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="openai/gpt-oss-20b",
        max_tokens=1000,
    )
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mocked_async_client(*, timeout):
        return real_async_client(transport=transport, timeout=timeout)

    monkeypatch.setattr("licenselens.llm.gateway.httpx.AsyncClient", mocked_async_client)

    result = await provider.complete(
        [{"role": "user", "content": "Return JSON"}],
        0.0,
        response_format={"type": "json_object"},
    )

    assert "response_format" not in captured
    assert result.content == "not-json"


@pytest.mark.asyncio
async def test_fake_llm_returns_scripted_responses_in_order():
    llm = FakeLLM(
        responses=[
            '{"name": "MIT"}',
            "Invalid JSON",
            '{"name": "Apache-2.0"}',
        ]
    )

    first = await llm.complete([])
    second = await llm.complete([])
    third = await llm.complete([])

    assert first.content == '{"name": "MIT"}'
    assert second.content == "Invalid JSON"
    assert third.content == '{"name": "Apache-2.0"}'

    assert llm.call_count == 3


@pytest.mark.asyncio
async def test_provider_logs_rate_limit_response(caplog, monkeypatch):
    from licenselens.llm import gateway as gateway_module

    error_body = '{"error": {"message": "tokens per minute limit exceeded"}}'

    def handler(request):
        return httpx.Response(429, text=error_body)

    monkeypatch.setattr(gateway_module, "MAX_RETRIES", 0)
    caplog.set_level("ERROR")

    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await provider._complete_with_retry(
                client,
                [{"role": "user", "content": "Test"}],
                0.0,
            )

    assert "HTTP 429 quota_exhausted=False" in caplog.text
    assert "tokens per minute limit exceeded" not in caplog.text


@pytest.mark.asyncio
async def test_provider_logs_sanitized_http_400_details(caplog):
    api_key = "test-secret-key"  # pragma: allowlist secret
    document_text = "private license text"

    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": (f"Unsupported parameter for {document_text}; token={api_key}"),
                    "type": "invalid_request_error",
                    "code": "unsupported_parameter",
                    "param": "max_tokens",
                }
            },
        )

    caplog.set_level("ERROR")
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key=api_key,
        model="openai/gpt-oss-20b",
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await provider._complete_with_retry(
                client,
                [{"role": "user", "content": document_text}],
                0.0,
            )

    assert "HTTP 400 request error details=" in caplog.text
    assert "unsupported_parameter" in caplog.text
    assert "max_tokens" in caplog.text
    assert api_key not in caplog.text
    assert document_text not in caplog.text
    assert "Unsupported parameter for [REDACTED]" in caplog.text
    assert '"type": "invalid_request_error"' in caplog.text
    assert '"code": "unsupported_parameter"' in caplog.text
    assert '"param": "max_tokens"' in caplog.text


@pytest.mark.asyncio
async def test_provider_redacts_echoed_content_but_preserves_ordinary_400_details(caplog):
    api_key = "gsk_test_diagnostic_key"  # pragma: allowlist secret
    prompt_text = "You are extracting structured DocMeta fields from the supplied source."
    document_text = "SOURCE SENTINEL 4728 confidential copied phrase from the document payload."
    echoed_excerpt = "SOURCE SENTINEL 4728 confidential copied phrase"

    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        "Failed to generate response: unsupported parameter max_tokens; "
                        f"excerpt={echoed_excerpt}; prompt={prompt_text}; "
                        f"Authorization: Bearer {api_key}"
                    ),
                    "type": "invalid_request_error",
                    "code": "unsupported_parameter",
                    "param": "max_tokens",
                }
            },
        )

    caplog.set_level("ERROR")
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key=api_key,
        model="openai/gpt-oss-20b",
    )
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": document_text},
    ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderBadRequestError) as error_info:
            await provider._complete_with_retry(client, messages, 0.0)

    details = error_info.value.safe_details
    assert details["message"] is not None
    assert "Failed to generate response" in details["message"]
    assert "unsupported parameter max_tokens" in details["message"]
    assert "[REDACTED]" in details["message"]
    assert echoed_excerpt not in details["message"]
    assert prompt_text not in details["message"]
    assert api_key not in details["message"]
    assert "Bearer gsk_test_diagnostic_key" not in details["message"]
    assert details["type"] == "invalid_request_error"
    assert details["code"] == "unsupported_parameter"
    assert details["param"] == "max_tokens"

    assert "Failed to generate response" in caplog.text
    assert '"code": "unsupported_parameter"' in caplog.text


@pytest.mark.asyncio
async def test_provider_captures_sanitized_http_413_details(caplog):
    api_key = "gsk_test_413_key"  # pragma: allowlist secret
    prompt = "Private extraction instructions."
    document_text = "PRIVATE DOCUMENT BODY 413"

    def handler(request):
        return httpx.Response(
            413,
            request=request,
            json={
                "error": {
                    "message": f"Request too large; echoed {document_text}; key={api_key}",
                    "type": "request_too_large",
                    "code": "payload_too_large",
                    "param": "messages",
                }
            },
        )

    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key=api_key,
        model="openai/gpt-oss-20b",
    )
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": document_text},
    ]
    caplog.set_level("ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderPayloadTooLargeError) as error_info:
            await provider._complete_with_retry(client, messages, 0.0)

    details = error_info.value.safe_details
    assert details["type"] == "request_too_large"
    assert details["code"] == "payload_too_large"
    assert details["param"] == "messages"
    assert "Request too large" in details["message"]
    assert "[REDACTED]" in details["message"]
    assert document_text not in json.dumps(details)
    assert prompt not in json.dumps(details)
    assert api_key not in json.dumps(details)
    assert "payload_too_large" in caplog.text
    assert document_text not in caplog.text
    assert api_key not in caplog.text


def test_retry_after_reads_seconds_and_provider_reset_headers():
    assert retry_after_seconds(httpx.Headers({"Retry-After": "7"})) == 7
    assert (
        retry_after_seconds(
            httpx.Headers(
                {
                    "x-ratelimit-reset-requests": "2s",
                    "x-ratelimit-reset-tokens": "750ms",
                }
            )
        )
        == 2
    )


def test_quota_exhaustion_does_not_match_temporary_per_minute_limit():
    daily = httpx.Response(429, text='{"message": "tokens per day quota exceeded"}')
    temporary = httpx.Response(429, text='{"message": "tokens per minute limit exceeded"}')

    assert quota_exhausted(daily) is True
    assert quota_exhausted(temporary) is False


@pytest.mark.asyncio
async def test_provider_honors_retry_after_for_temporary_429(monkeypatch, caplog):
    from licenselens.llm import gateway as gateway_module

    calls = 0
    delays = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "4"}, text="rate limited")
        return httpx.Response(200, json={"ok": True})

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(gateway_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(gateway_module.asyncio, "sleep", capture_sleep)
    caplog.set_level("INFO")

    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await provider._complete_with_retry(
            client,
            [{"role": "user", "content": "Test"}],
            0.0,
        )

    assert result == {"ok": True}
    assert calls == 2
    assert delays == [4]
    assert "provider_http_request provider=groq request=1/2" in caplog.text
    assert "provider_http_request provider=groq request=2/2" in caplog.text


@pytest.mark.asyncio
async def test_provider_http_retry_respects_request_pacer(monkeypatch):
    from licenselens.llm import gateway as gateway_module

    calls = 0
    pacer_slots_waited = 0

    class TrackingPacer:
        async def wait_for_slot(self):
            nonlocal pacer_slots_waited
            pacer_slots_waited += 1

    pacer = TrackingPacer()

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="temporary server error")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(gateway_module, "MAX_RETRIES", 1)

    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
        request_pacer=pacer,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await provider._complete_with_retry(
            client,
            [{"role": "user", "content": "Test"}],
            0.0,
        )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert calls == 2
    assert pacer_slots_waited == 2


@pytest.mark.asyncio
async def test_provider_complete_preserves_finish_reason(monkeypatch):
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )

    async def fake_complete_with_retry(*args, **kwargs):
        return {
            "choices": [
                {
                    "message": {"content": '{"summary": "partial"'},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1000},
        }

    monkeypatch.setattr(provider, "_complete_with_retry", fake_complete_with_retry)

    result = await provider.complete([])

    assert result.finish_reason == "length"
    assert result.tokens_out == 1000


@pytest.mark.asyncio
async def test_provider_does_not_retry_exhausted_daily_quota(monkeypatch):
    from licenselens.llm import gateway as gateway_module

    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, text='{"message": "requests per day quota exceeded"}')

    monkeypatch.setattr(gateway_module, "MAX_RETRIES", 2)
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderQuotaExhaustedError):
            await provider._complete_with_retry(
                client,
                [{"role": "user", "content": "Test"}],
                0.0,
            )

    assert calls == 1


@pytest.mark.asyncio
async def test_provider_falls_back_when_retry_after_exceeds_wait_limit(monkeypatch):
    from licenselens.llm import gateway as gateway_module

    calls = 0
    delays = []

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "120"}, text="rate limited")

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(gateway_module, "MAX_RETRIES", 2)
    monkeypatch.setattr(gateway_module.asyncio, "sleep", capture_sleep)
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRateLimitedError):
            await provider._complete_with_retry(
                client,
                [{"role": "user", "content": "Test"}],
                0.0,
            )

    assert calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_provider_paces_each_request_at_configured_rate(monkeypatch):
    from licenselens.llm import gateway as gateway_module

    delays = []

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", capture_sleep)
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://example.com/v1",
        api_key="test-key",  # pragma: allowlist secret
        model="test-model",
        requests_per_minute=2,
        max_tokens=1000,
    )
    provider._last_request_at = gateway_module.time.monotonic()

    await provider._wait_for_request_slot()

    assert delays[0] == pytest.approx(30, abs=0.1)
    assert provider.max_tokens == 1000


@pytest.mark.asyncio
async def test_gateway_shares_request_pacing_across_both_providers():
    current_time = [0.0]
    delays = []

    async def advance_clock(delay):
        delays.append(delay)
        current_time[0] += delay

    pacer = RequestPacer(
        2,
        clock=lambda: current_time[0],
        sleep=advance_clock,
    )
    groq = OpenAICompatProvider(
        name="groq",
        base_url="https://groq.example/v1",
        api_key="groq-key",  # pragma: allowlist secret
        model="groq-model",
        request_pacer=pacer,
    )
    gemini = OpenAICompatProvider(
        name="gemini",
        base_url="https://gemini.example/v1",
        api_key="gemini-key",  # pragma: allowlist secret
        model="gemini-model",
        request_pacer=pacer,
    )

    await groq._wait_for_request_slot()
    await gemini._wait_for_request_slot()

    assert delays == [30]
    assert groq.request_pacer is gemini.request_pacer


@pytest.mark.asyncio
async def test_gateway_skips_provider_after_daily_quota_exhaustion():
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(429, request=request)

    class QuotaProvider:
        name = "groq"
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, temperature=0.2):
            self.calls += 1
            raise ProviderQuotaExhaustedError(
                "daily quota exceeded",
                request=request,
                response=response,
            )

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    primary = QuotaProvider()
    fallback = FakeLLM()
    gateway = LLMGateway(primary=primary, fallback=fallback)
    messages = [{"role": "user", "content": "Test"}]

    await gateway.complete(messages)
    await gateway.complete(messages)

    assert primary.calls == 1
    assert len(fallback.seen) == 2


@pytest.mark.asyncio
async def test_gateway_stops_retrying_providers_after_both_quotas_are_exhausted():
    class QuotaProvider:
        def __init__(self, name):
            self.name = name
            self.model = "test-model"
            self.calls = 0

        async def complete(self, messages, temperature=0.2):
            self.calls += 1
            request = httpx.Request("POST", "https://example.com")
            response = httpx.Response(429, request=request)
            raise ProviderQuotaExhaustedError(
                "daily quota exceeded",
                request=request,
                response=response,
            )

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    primary = QuotaProvider("groq")
    fallback = QuotaProvider("gemini")
    gateway = LLMGateway(primary=primary, fallback=fallback)

    with pytest.raises(ProviderQuotaExhaustedError):
        await gateway.complete([])

    assert gateway.all_providers_unavailable is True

    with pytest.raises(CircuitOpenError, match="unavailable for this run"):
        await gateway.complete([])

    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_circuit_cools_down_after_503_and_recovers():
    class FailingPrimary:
        name = "primary"
        model = "test-model"

        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("primary unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    class FlakyFallback:
        name = "fallback"
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, temperature=0.2):
            self.calls += 1
            if self.calls == 1:
                request = httpx.Request("POST", "https://example.com")
                response = httpx.Response(503, request=request)
                raise httpx.HTTPStatusError(
                    "service unavailable", request=request, response=response
                )
            return LLMResponse(
                content="Recovered",
                provider=self.name,
                model=self.model,
                tokens_in=1,
                tokens_out=1,
                latency_ms=1,
            )

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    fallback = FlakyFallback()
    gateway = LLMGateway(
        primary=FailingPrimary(),
        fallback=fallback,
        fallback_circuit_breaker=CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0.02,
        ),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.complete([])

    with pytest.raises(CircuitOpenError, match="fallback circuit is open"):
        await gateway.complete([])
    assert fallback.calls == 1

    await asyncio.sleep(0.03)
    response = await gateway.complete([])

    assert response.content == "Recovered"
    assert fallback.calls == 2
    assert gateway.fallback_circuit_breaker.is_open is False


@pytest.mark.asyncio
async def test_stream_fallback_circuit_cools_down_and_recovers():
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(503, request=request)

    class FailingPrimary:
        name = "primary"
        model = "test-model"

        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("primary unavailable")

        async def stream(self, messages, temperature=0.2):
            raise httpx.HTTPStatusError("primary unavailable", request=request, response=response)
            yield "unreachable"

    class FlakyFallback:
        name = "fallback"
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, temperature=0.2):
            raise httpx.ConnectError("unused")

        async def stream(self, messages, temperature=0.2):
            self.calls += 1
            if self.calls == 1:
                raise httpx.HTTPStatusError(
                    "fallback unavailable", request=request, response=response
                )
            yield "recovered"

    fallback = FlakyFallback()
    gateway = LLMGateway(
        primary=FailingPrimary(),
        fallback=fallback,
        fallback_circuit_breaker=CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0.02,
        ),
    )

    with pytest.raises(httpx.HTTPStatusError):
        async for _ in gateway.stream([]):
            pass

    with pytest.raises(CircuitOpenError, match="fallback circuit is open"):
        async for _ in gateway.stream([]):
            pass
    assert fallback.calls == 1

    await asyncio.sleep(0.03)
    chunks = [item async for item in gateway.stream([])]

    assert chunks == ["recovered"]
    assert fallback.calls == 2
