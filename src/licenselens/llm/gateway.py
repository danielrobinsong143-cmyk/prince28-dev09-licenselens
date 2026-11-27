"""LLM gateway: providers, retries, streaming, fallback and circuit breaker."""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


def build_completion_request_body(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None,
    response_format: dict | None,
) -> dict:
    """Build the JSON body shared by sending and local size diagnostics."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if response_format is not None:
        body["response_format"] = response_format
    return body


def serialized_request_body_size(body: dict) -> int:
    """Return the byte size httpx will send for a JSON request body."""
    return len(httpx.Request("POST", "https://payload-size.invalid", json=body).content)


class RequestPacer:
    """Share one request-start limit across every provider in an extraction gateway."""

    def __init__(
        self,
        requests_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than zero")
        self.interval = 60 / requests_per_minute
        self.clock = clock
        self.sleep = sleep
        self.last_request_at: float | None = None
        self.lock = asyncio.Lock()

    async def wait_for_slot(self) -> None:
        async with self.lock:
            now = self.clock()
            if self.last_request_at is not None:
                delay = self.interval - (now - self.last_request_at)
                if delay > 0:
                    await self.sleep(delay)
            self.last_request_at = self.clock()


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 2
MAX_RETRY_WAIT_SECONDS = 60.0


def _safe_provider_error_details(
    response: httpx.Response,
    *,
    api_key: str,
    messages: list[dict[str, str]],
) -> dict[str, str | None]:
    """Return useful provider error fields with secrets and prompt text removed."""
    try:
        payload = response.json()
    except ValueError:
        return {
            "message": "<non-JSON response omitted>",
            "type": None,
            "code": None,
            "param": None,
        }

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}

    request_content = [
        message.get("content", "")
        for message in messages
        if isinstance(message.get("content"), str) and message.get("content")
    ]

    def clean(value: object, *, redact_echoed_passages: bool) -> str:
        cleaned = str(value)

        for content in request_content:
            cleaned = cleaned.replace(content, "[REDACTED]")

            # Remove echoed passages, while preserving ordinary diagnostic words.
            # Short shared words are not enough to count as echoed request content.
            while redact_echoed_passages and cleaned:
                match = SequenceMatcher(
                    None,
                    cleaned.lower(),
                    content.lower(),
                    autojunk=False,
                ).find_longest_match()
                if match.size < 12:
                    break
                cleaned = cleaned[: match.a] + "[REDACTED]" + cleaned[match.a + match.size :]

        if api_key:
            cleaned = cleaned.replace(api_key, "[REDACTED]")
        cleaned = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", cleaned)
        return cleaned[:300]

    return {
        field: (
            clean(error[field], redact_echoed_passages=field == "message")
            if error.get(field) is not None
            else None
        )
        for field in ("message", "type", "code", "param")
    }


def retry_after_seconds(headers: httpx.Headers, now: float | None = None) -> float | None:
    """Read standard and common provider reset headers as a delay in seconds."""
    values = [
        headers.get("retry-after"),
        headers.get("retry-after-ms"),
        headers.get("x-ratelimit-reset-requests"),
        headers.get("x-ratelimit-reset-tokens"),
    ]
    delays = []

    for index, value in enumerate(values):
        if not value:
            continue

        raw = value.strip().lower()
        try:
            if index == 1:
                delay = float(raw) / 1000
            elif raw.endswith("ms"):
                delay = float(raw[:-2]) / 1000
            elif raw.endswith("s"):
                delay = float(raw[:-1])
            elif raw.endswith("m"):
                delay = float(raw[:-1]) * 60
            elif raw.endswith("h"):
                delay = float(raw[:-1]) * 3600
            else:
                delay = float(raw)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                delay = date.timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, OverflowError):
                continue

        delays.append(max(0.0, delay))

    return max(delays) if delays else None


def quota_exhausted(response: httpx.Response) -> bool:
    """Distinguish a hard quota/billing failure from a short rate-limit window."""
    try:
        body = response.text.lower()
    except Exception:
        body = ""

    permanent_markers = (
        "per day",
        "daily",
        "daily quota",
        "per month",
        "monthly",
        "insufficient_quota",
        "quota exceeded",
        "quota has been exceeded",
        "billing limit",
        "credit limit",
        "billing hard limit",
    )
    return any(marker in body for marker in permanent_markers)


class ProviderQuotaExhaustedError(httpx.HTTPStatusError):
    """A provider reported a hard daily, monthly, or billing quota limit."""


class ProviderRateLimitedError(httpx.HTTPStatusError):
    """A provider remains rate-limited after retries or has a long reset window."""


class ProviderBadRequestError(httpx.HTTPStatusError):
    """A provider rejected a request and supplied sanitized diagnostic fields."""

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        safe_details: dict[str, str | None],
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.safe_details = safe_details


class ProviderPayloadTooLargeError(httpx.HTTPStatusError):
    """A provider rejected a request body that exceeded its accepted size."""

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        safe_details: dict[str, str | None],
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.safe_details = safe_details


def is_temporary_provider_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in {408, 429} or status >= 500
    return isinstance(error, httpx.TransportError)


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
    finish_reason: str | None = None


@dataclass
class LLMStreamResult:
    content: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    fallback_used: bool = False


@dataclass
class LLMProviderAttempt:
    """One logical provider attempt; HTTP retries are logged by the provider separately."""

    provider: str
    model: str
    status: str
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    fallback_used: bool = False


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str | LLMStreamResult]: ...


class FakeLLM:
    """A predictable provider for testing without API calls."""

    def __init__(
        self,
        responses: list[str] | None = None,
        finish_reasons: list[str | None] | None = None,
    ) -> None:
        self.seen: list[dict] = []
        self.responses = list(responses or [])
        self.finish_reasons = list(finish_reasons or [])
        self.call_count = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict | None = None,
    ) -> LLMResponse:
        # Remember the request for testing.
        self.seen.append(
            {
                "messages": messages,
                "temperature": temperature,
                "response_format": response_format,
            }
        )

        content = self.responses.pop(0) if self.responses else "This is a fake LLM response."
        finish_reason = self.finish_reasons.pop(0) if self.finish_reasons else None
        self.call_count += 1
        return LLMResponse(
            content=content,
            provider="fake",
            model="fake-model",
            tokens_in=10,
            tokens_out=8,
            latency_ms=1,
            finish_reason=finish_reason,
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
        requests_per_minute: float | None = None,
        max_tokens: int | None = None,
        request_pacer: RequestPacer | None = None,
    ) -> None:
        if requests_per_minute is not None and requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than zero")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be greater than zero")

        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.request_pacer = request_pacer
        self.request_interval = (
            60 / requests_per_minute if requests_per_minute is not None else None
        )
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    async def _wait_for_request_slot(self) -> None:
        """Pace every HTTP request, including retries, for this provider or gateway."""
        if self.request_pacer is not None:
            await self.request_pacer.wait_for_slot()
            return

        if self.request_interval is None:
            return

        async with self._request_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                delay = self.request_interval - (now - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_request_at = time.monotonic()

    async def _complete_with_retry(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: dict | None = None,
        *,
        max_retries: int | None = None,
    ) -> dict:
        """Retry temporary provider failures using exponential backoff."""

        retry_limit = MAX_RETRIES if max_retries is None else max_retries
        if retry_limit < 0:
            raise ValueError("max_retries must be non-negative")

        for attempt in range(retry_limit + 1):
            try:
                await self._wait_for_request_slot()
                logger.info(
                    "provider_http_request provider=%s request=%d/%d",
                    self.name,
                    attempt + 1,
                    retry_limit + 1,
                )
                body = build_completion_request_body(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                    response_format=response_format,
                )
                body_size = serialized_request_body_size(body)
                logger.info(
                    "provider_request_body provider=%s body_bytes=%d",
                    self.name,
                    body_size,
                )

                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
                logger.info(
                    "provider_http_response provider=%s request=%d/%d status=%d",
                    self.name,
                    attempt + 1,
                    retry_limit + 1,
                    response.status_code,
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code

                if status == 400:
                    safe_details = _safe_provider_error_details(
                        exc.response,
                        api_key=self.api_key,
                        messages=messages,
                    )
                    logger.error(
                        "%s returned HTTP 400 request error details=%s",
                        self.name,
                        json.dumps(safe_details, sort_keys=True),
                    )
                    raise ProviderBadRequestError(
                        str(exc),
                        request=exc.request,
                        response=exc.response,
                        safe_details=safe_details,
                    ) from exc

                if status == 413:
                    safe_details = _safe_provider_error_details(
                        exc.response,
                        api_key=self.api_key,
                        messages=messages,
                    )
                    logger.error(
                        "%s returned HTTP 413 request size details=%s",
                        self.name,
                        json.dumps(safe_details, sort_keys=True),
                    )
                    raise ProviderPayloadTooLargeError(
                        str(exc),
                        request=exc.request,
                        response=exc.response,
                        safe_details=safe_details,
                    ) from exc

                if status == 429:
                    is_quota_exhausted = quota_exhausted(exc.response)
                    logger.error(
                        "%s returned HTTP 429 quota_exhausted=%s",
                        self.name,
                        is_quota_exhausted,
                    )

                    if is_quota_exhausted:
                        logger.warning(
                            "%s quota is exhausted; skipping same-provider retries",
                            self.name,
                        )
                        raise ProviderQuotaExhaustedError(
                            str(exc), request=exc.request, response=exc.response
                        ) from exc

                if status not in RETRYABLE_STATUS_CODES:
                    raise

                if attempt == retry_limit:
                    if status == 429:
                        raise ProviderRateLimitedError(
                            str(exc), request=exc.request, response=exc.response
                        ) from exc
                    raise

                if status == 429:
                    delay = retry_after_seconds(exc.response.headers)
                    if delay is None:
                        # A minute is the documented window for TPM/RPM limits.
                        delay = 60.0
                    if delay > MAX_RETRY_WAIT_SECONDS:
                        logger.warning(
                            "%s rate-limit reset is %.1f seconds away; falling back",
                            self.name,
                            delay,
                        )
                        raise ProviderRateLimitedError(
                            str(exc), request=exc.request, response=exc.response
                        ) from exc
                else:
                    delay = 2**attempt

                logger.warning(
                    "%s returned HTTP %s. Retrying in %s seconds.",
                    self.name,
                    status,
                    delay,
                )

                await asyncio.sleep(delay)

            except httpx.TimeoutException:
                logger.warning(
                    "provider_http_error provider=%s request=%d/%d error=timeout",
                    self.name,
                    attempt + 1,
                    retry_limit + 1,
                )
                if attempt == retry_limit:
                    raise

                delay = 2**attempt

                logger.warning(
                    "%s HTTP request %d/%d timed out. Retrying in %s seconds.",
                    self.name,
                    attempt + 1,
                    retry_limit + 1,
                    delay,
                )

                await asyncio.sleep(delay)

            except httpx.TransportError as exc:
                logger.warning(
                    "provider_http_error provider=%s request=%d/%d error=%s",
                    self.name,
                    attempt + 1,
                    retry_limit + 1,
                    type(exc).__name__,
                )
                raise

        raise RuntimeError("LLM request failed after retries.")

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Send a completion request with retry support."""

        start = time.perf_counter()

        # Groq's legacy JSON-object mode can reject a generation server-side with
        # json_validate_failed (HTTP 400), before extraction's local parser and
        # repair loop can inspect the content. GPT-OSS 20B supports this mode, but
        # omit it for this extraction provider/model so malformed JSON can be
        # repaired locally. The extraction prompt still requires JSON, and other
        # provider/model combinations retain their configured response format.
        effective_response_format = response_format
        if self.name == "groq" and self.model == "openai/gpt-oss-20b":
            effective_response_format = None
            if response_format is not None:
                logger.info(
                    "provider_response_format_disabled provider=groq model=%s "
                    "reason=local_json_repair",
                    self.model,
                )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            data = await self._complete_with_retry(
                client,
                messages,
                temperature,
                effective_response_format,
            )

        usage = data.get("usage", {})

        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - start) * 1000),
            finish_reason=choice.get("finish_reason"),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str | LLMStreamResult]:
        """Stream text and return usage metadata after completion."""

        start = time.perf_counter()
        content_parts: list[str] = []
        tokens_in = 0
        tokens_out = 0

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await self._wait_for_request_slot()
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            ) as response:
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

                    usage = chunk.get("usage")
                    if usage:
                        tokens_in = usage.get("prompt_tokens", 0)
                        tokens_out = usage.get("completion_tokens", 0)

                    choices = chunk.get("choices", [])
                    if choices:
                        text = choices[0].get("delta", {}).get("content")

                        if text:
                            content_parts.append(text)
                            yield text

        yield LLMStreamResult(
            content="".join(content_parts),
            provider=self.name,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


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

    @property
    def is_open(self) -> bool:
        return (
            self.opened_at is not None and time.monotonic() - self.opened_at < self.recovery_timeout
        )

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
        fallback: LLMProvider | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        fallback_circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.fallback_circuit_breaker = fallback_circuit_breaker or CircuitBreaker()
        self._unavailable_providers: set[str] = set()

    @property
    def all_providers_unavailable(self) -> bool:
        """Whether both configured providers are known to be unavailable for this run."""
        primary_unavailable = (
            getattr(self.primary, "name", "") in self._unavailable_providers
            or self.circuit_breaker.is_open
        )
        fallback_unavailable = self.fallback is None or (
            getattr(self.fallback, "name", "") in self._unavailable_providers
            or self.fallback_circuit_breaker.is_open
        )
        return primary_unavailable and fallback_unavailable

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict | None = None,
        on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Try primary first, then use fallback when necessary."""

        async def report(
            provider: LLMProvider,
            status: str,
            *,
            error: str | None = None,
            result: LLMResponse | None = None,
            fallback_used: bool = False,
        ) -> None:
            provider_name = result.provider if result else getattr(provider, "name", "unknown")
            logger.info(
                "llm_provider_attempt provider=%s status=%s fallback_used=%s",
                provider_name,
                status,
                fallback_used,
            )
            if on_attempt is None:
                return

            try:
                await on_attempt(
                    LLMProviderAttempt(
                        provider=(
                            result.provider if result else getattr(provider, "name", "unknown")
                        ),
                        model=(result.model if result else getattr(provider, "model", "unknown")),
                        status=status,
                        error=error,
                        tokens_in=result.tokens_in if result else 0,
                        tokens_out=result.tokens_out if result else 0,
                        latency_ms=result.latency_ms if result else 0,
                        fallback_used=fallback_used,
                    )
                )
            except Exception:
                logger.exception("Failed to record LLM provider attempt.")

        async def call_provider(provider: LLMProvider) -> LLMResponse:
            provider_name = getattr(provider, "name", "")
            if provider_name in self._unavailable_providers:
                raise CircuitOpenError(f"{provider_name} is unavailable for this run")

            if response_format is None:
                return await provider.complete(messages, temperature)

            return await provider.complete(
                messages,
                temperature,
                response_format=response_format,
            )

        async def use_fallback() -> LLMResponse:
            if self.fallback is None:
                raise CircuitOpenError("No fallback provider is configured")
            fallback_name = getattr(self.fallback, "name", "")
            if fallback_name in self._unavailable_providers:
                raise CircuitOpenError(f"{fallback_name} is unavailable for this run")
            if not await self.fallback_circuit_breaker.allow_request():
                raise CircuitOpenError(f"{fallback_name} circuit is open")

            try:
                response = await call_provider(self.fallback)
            except Exception as exc:
                if isinstance(exc, (ProviderQuotaExhaustedError, ProviderRateLimitedError)):
                    self._unavailable_providers.add(fallback_name)
                elif is_temporary_provider_error(exc):
                    await self.fallback_circuit_breaker.record_failure()
                await report(self.fallback, "error", error=str(exc), fallback_used=True)
                raise

            await self.fallback_circuit_breaker.record_success()
            response = replace(response, fallback_used=True)
            await report(self.fallback, "ok", result=response, fallback_used=True)
            return response

        primary_name = getattr(self.primary, "name", "")
        if (
            primary_name in self._unavailable_providers
            or not await self.circuit_breaker.allow_request()
        ):
            logger.warning("Primary provider is unavailable. Using fallback.")
            if self.fallback is None:
                raise CircuitOpenError("Primary provider unavailable and no fallback is configured")
            return await use_fallback()

        try:
            response = await call_provider(self.primary)

        except httpx.HTTPError as exc:
            logger.warning(
                "Primary provider unavailable. Switching to fallback.",
                exc_info=True,
            )
            await self.circuit_breaker.record_failure()
            if isinstance(exc, (ProviderQuotaExhaustedError, ProviderRateLimitedError)):
                self._unavailable_providers.add(primary_name)
            await report(self.primary, "error", error=str(exc))
            if self.fallback is None:
                raise
            return await use_fallback()

        await self.circuit_breaker.record_success()
        await report(self.primary, "ok", result=response)
        return response

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str = "chat",
        prompt_version: str = "",
        temperature: float = 0.2,
        response_format: dict | None = None,
        on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Generate a response while recording the purpose and prompt version."""

        logger.info(
            "LLM request: purpose=%s prompt_version=%s",
            purpose,
            prompt_version,
        )

        return await self.complete(
            messages,
            temperature=temperature,
            response_format=response_format,
            on_attempt=on_attempt,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
    ) -> AsyncIterator[str | LLMStreamResult]:
        """Stream from primary, falling back only before the first chunk."""

        async def report(
            provider,
            status: str,
            *,
            error: str | None = None,
            result: LLMStreamResult | None = None,
            fallback_used: bool = False,
        ) -> None:
            provider_name = result.provider if result else getattr(provider, "name", "unknown")
            logger.info(
                "llm_provider_attempt provider=%s status=%s fallback_used=%s",
                provider_name,
                status,
                fallback_used,
            )
            if on_attempt is None:
                return

            try:
                await on_attempt(
                    LLMProviderAttempt(
                        provider=(
                            result.provider if result else getattr(provider, "name", "unknown")
                        ),
                        model=(result.model if result else getattr(provider, "model", "unknown")),
                        status=status,
                        error=error,
                        tokens_in=result.tokens_in if result else 0,
                        tokens_out=result.tokens_out if result else 0,
                        latency_ms=result.latency_ms if result else 0,
                        fallback_used=fallback_used,
                    )
                )
            except Exception:
                logger.exception("Failed to record LLM provider attempt.")

        async def stream_provider(provider, fallback_used: bool):
            is_fallback = provider is self.fallback and fallback_used
            fallback_name = getattr(provider, "name", "")
            if is_fallback:
                if fallback_name in self._unavailable_providers:
                    raise CircuitOpenError(f"{fallback_name} is unavailable for this run")
                if not await self.fallback_circuit_breaker.allow_request():
                    raise CircuitOpenError(f"{fallback_name} circuit is open")

            try:
                async for item in provider.stream(messages, temperature):
                    if isinstance(item, LLMStreamResult):
                        result = replace(
                            item,
                            fallback_used=fallback_used,
                        )
                        await report(
                            provider,
                            "ok",
                            result=result,
                            fallback_used=fallback_used,
                        )
                        yield result
                    else:
                        yield item

            except Exception as exc:
                if is_fallback:
                    if isinstance(exc, (ProviderQuotaExhaustedError, ProviderRateLimitedError)):
                        self._unavailable_providers.add(fallback_name)
                    elif is_temporary_provider_error(exc):
                        await self.fallback_circuit_breaker.record_failure()
                await report(
                    provider,
                    "error",
                    error=str(exc),
                    fallback_used=fallback_used,
                )
                raise
            else:
                if is_fallback:
                    await self.fallback_circuit_breaker.record_success()

        if not await self.circuit_breaker.allow_request():
            logger.warning("Primary circuit is open. Using fallback.")

            if self.fallback is None:
                raise CircuitOpenError("Primary circuit is open and no fallback is configured")

            async for item in stream_provider(self.fallback, True):
                yield item
            return

        primary_stream = self.primary.stream(
            messages,
            temperature,
        ).__aiter__()

        try:
            first_item = await primary_stream.__anext__()

        except StopAsyncIteration:
            error = "Primary provider returned an empty stream."
            await self.circuit_breaker.record_failure()
            await report(self.primary, "error", error=error)

            if self.fallback is None:
                raise CircuitOpenError(
                    "Primary stream was empty and no fallback is configured"
                ) from None

            async for item in stream_provider(self.fallback, True):
                yield item
            return

        except Exception as exc:
            logger.warning(
                "Primary streaming failed before first chunk.",
                exc_info=True,
            )
            await self.circuit_breaker.record_failure()
            await report(
                self.primary,
                "error",
                error=str(exc),
            )

            if self.fallback is None:
                raise

            async for item in stream_provider(self.fallback, True):
                yield item
            return

        await self.circuit_breaker.record_success()

        yield first_item

        try:
            async for item in primary_stream:
                if isinstance(item, LLMStreamResult):
                    await report(
                        self.primary,
                        "ok",
                        result=item,
                    )
                yield item

        except Exception as exc:
            await report(
                self.primary,
                "error",
                error=str(exc),
            )
            raise


def create_gateway(
    settings,
    *,
    requests_per_minute: float | None = None,
    max_tokens: int | None = None,
    groq_only: bool = False,
    gemini_only: bool = False,
) -> LLMGateway:
    """Create the configured Groq/Gemini gateway.

    Extraction jobs pass a low request rate and output cap. Interactive requests leave
    both unset so the provider's normal gateway behavior is preserved.
    """
    if groq_only and gemini_only:
        raise ValueError("groq_only and gemini_only cannot both be enabled")

    shared_pacer = RequestPacer(requests_per_minute) if requests_per_minute is not None else None
    provider_rpm = None if shared_pacer is not None else requests_per_minute
    if gemini_only:
        primary = OpenAICompatProvider(
            name="gemini",
            base_url=settings.llm_primary_base_url,
            api_key=settings.gemini_api_key,
            model=settings.llm_primary_model,
            timeout=settings.llm_timeout_seconds,
            requests_per_minute=provider_rpm,
            max_tokens=max_tokens,
            request_pacer=shared_pacer,
        )
    else:
        primary = OpenAICompatProvider(
            name="groq",
            base_url=settings.llm_fallback_base_url,
            api_key=settings.groq_api_key,
            model=settings.llm_fallback_model,
            timeout=settings.llm_timeout_seconds,
            requests_per_minute=provider_rpm,
            max_tokens=max_tokens,
            request_pacer=shared_pacer,
        )

    fallback = None
    if not groq_only and not gemini_only:
        fallback = OpenAICompatProvider(
            name="gemini",
            base_url=settings.llm_primary_base_url,
            api_key=settings.gemini_api_key,
            model=settings.llm_primary_model,
            timeout=settings.llm_timeout_seconds,
            requests_per_minute=provider_rpm,
            max_tokens=max_tokens,
            request_pacer=shared_pacer,
        )

    return LLMGateway(
        primary=primary,
        fallback=fallback,
        circuit_breaker=CircuitBreaker(failure_threshold=1) if groq_only else None,
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
