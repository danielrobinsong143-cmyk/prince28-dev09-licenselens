"""One-request, no-persistence provider diagnostic helpers."""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from licenselens.llm.doc_meta import DocMeta
from licenselens.llm.extract import (
    EXTRACTION_MAX_OUTPUT_TOKENS,
    build_extraction_chunks,
    build_messages,
    split_document_text,
)
from licenselens.llm.gateway import (
    OpenAICompatProvider,
    _safe_provider_error_details,
    build_completion_request_body,
    serialized_request_body_size,
)

DocumentLoader = Callable[[str], Awaitable[str | None]]


@dataclass
class DiagnosticOutcome:
    document_found: bool | None = None
    page_text_available: bool | None = None
    http_request_started: bool = False
    http_request_count: int = 0
    extraction_chunk_count: int | None = None
    extraction_chunk_index: int | None = None
    request_body_bytes: int | None = None
    chunk_measurements: list[dict[str, int]] | None = None
    response_status: int | None = None
    classification: str = "not_started"
    error_classification: str | None = None
    http_400: dict[str, str | None] | None = None
    configured_output_token_limit: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reached_output_token_limit: bool | None = None
    content_valid_json: bool | None = None
    docmeta_valid: bool | None = None
    json_error_classification: str | None = None
    docmeta_error_classification: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


async def run_one_shot_diagnostic(
    document_id: str,
    *,
    load_document: DocumentLoader,
    provider: OpenAICompatProvider,
    prompt: str,
    chunk_index: int = 1,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DiagnosticOutcome:
    """Load one document and make at most one request, reporting safe outcomes."""
    outcome = DiagnosticOutcome()
    outcome.configured_output_token_limit = provider.max_tokens

    try:
        document_text = await load_document(document_id)
    except Exception as error:
        outcome.classification = "document_lookup_error"
        outcome.error_classification = type(error).__name__
        return outcome

    outcome.document_found = document_text is not None
    if document_text is None:
        outcome.classification = "document_not_found"
        return outcome

    outcome.page_text_available = bool(document_text.strip())
    if not outcome.page_text_available:
        outcome.classification = "empty_document_text"
        return outcome

    try:
        chunks = build_extraction_chunks(document_text)
        outcome.extraction_chunk_count = len(chunks)
        if chunk_index < 1 or chunk_index > len(chunks):
            outcome.classification = "chunk_index_out_of_range"
            return outcome
        outcome.extraction_chunk_index = chunk_index
        messages = build_messages(prompt, chunks[chunk_index - 1])
        request_body = build_completion_request_body(
            model=provider.model,
            messages=messages,
            temperature=0,
            max_tokens=provider.max_tokens,
            response_format={"type": "json_object"},
        )
        outcome.request_body_bytes = serialized_request_body_size(request_body)
    except Exception as error:
        outcome.classification = "request_preparation_error"
        outcome.error_classification = type(error).__name__
        return outcome

    async def mark_request_started(request: httpx.Request) -> None:
        del request
        outcome.http_request_started = True
        outcome.http_request_count += 1

    async def capture_response(response: httpx.Response) -> None:
        outcome.response_status = response.status_code

    diagnostic_logger = logging.getLogger("licenselens.llm.gateway")
    previous_disabled = diagnostic_logger.disabled
    diagnostic_logger.disabled = True
    try:
        async with httpx.AsyncClient(
            timeout=provider.timeout,
            transport=transport,
            event_hooks={
                "request": [mark_request_started],
                "response": [capture_response],
            },
        ) as client:
            try:
                response_data = await provider._complete_with_retry(
                    client,
                    messages,
                    temperature=0,
                    response_format={"type": "json_object"},
                    max_retries=0,
                )
            except httpx.HTTPStatusError as error:
                outcome.response_status = error.response.status_code
                outcome.error_classification = type(error).__name__
                if outcome.response_status == 400:
                    safe_fields = _safe_provider_error_details(
                        error.response,
                        api_key=provider.api_key,
                        messages=messages,
                    )
                    outcome.classification = "http_400"
                    outcome.http_400 = {
                        field: safe_fields.get(field)
                        for field in ("message", "type", "code", "param")
                    }
                else:
                    outcome.classification = "http_error"
            except httpx.TransportError as error:
                outcome.classification = "transport_error"
                outcome.error_classification = type(error).__name__
            except Exception as error:
                outcome.classification = "unexpected_error"
                outcome.error_classification = type(error).__name__
            else:
                _inspect_completion(outcome, response_data, provider.max_tokens)
    except Exception as error:
        # Covers client construction and event-hook failures without exposing text.
        outcome.classification = "diagnostic_error"
        outcome.error_classification = type(error).__name__
    finally:
        diagnostic_logger.disabled = previous_disabled

    return outcome


def _inspect_completion(
    outcome: DiagnosticOutcome,
    response: Any,
    output_token_limit: int | None,
) -> None:
    """Keep only safe completion metadata and validation outcomes."""
    try:
        choices = response["choices"]
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError):
        outcome.classification = "invalid_provider_response"
        outcome.error_classification = "MalformedProviderResponse"
        return

    if not isinstance(content, str):
        outcome.classification = "invalid_provider_response"
        outcome.error_classification = "MissingCompletionContent"
        return

    known_finish_reasons = {
        "stop",
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "token_limit",
        "tool_calls",
        "function_call",
        "content_filter",
    }
    raw_finish_reason = choice.get("finish_reason")
    if isinstance(raw_finish_reason, str):
        normalized_reason = raw_finish_reason.strip().lower().replace("-", "_")
        outcome.finish_reason = (
            normalized_reason if normalized_reason in known_finish_reasons else "unknown"
        )

    usage = response.get("usage", {})
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool):
            outcome.prompt_tokens = prompt_tokens
        if isinstance(completion_tokens, int) and not isinstance(completion_tokens, bool):
            outcome.completion_tokens = completion_tokens

    finish_reasons_at_limit = {
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "token_limit",
    }
    reason_reached_limit = outcome.finish_reason in finish_reasons_at_limit
    count_reached_limit = (
        output_token_limit is not None
        and outcome.completion_tokens is not None
        and outcome.completion_tokens >= output_token_limit
    )
    known_non_limit_reason = outcome.finish_reason in {
        "stop",
        "tool_calls",
        "function_call",
        "content_filter",
    }
    if reason_reached_limit or count_reached_limit:
        outcome.reached_output_token_limit = True
    elif known_non_limit_reason or (
        output_token_limit is not None
        and outcome.completion_tokens is not None
        and outcome.completion_tokens < output_token_limit
    ):
        outcome.reached_output_token_limit = False

    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError as error:
        outcome.content_valid_json = False
        outcome.docmeta_valid = False
        outcome.json_error_classification = type(error).__name__
        outcome.classification = (
            "output_token_limit_reached" if outcome.reached_output_token_limit else "invalid_json"
        )
        return

    outcome.content_valid_json = True
    try:
        DocMeta.model_validate(parsed_content)
    except ValidationError as error:
        outcome.docmeta_valid = False
        outcome.docmeta_error_classification = type(error).__name__
        outcome.classification = (
            "output_token_limit_reached"
            if outcome.reached_output_token_limit
            else "docmeta_validation_failed"
        )
        return

    outcome.docmeta_valid = True
    outcome.classification = (
        "output_token_limit_reached" if outcome.reached_output_token_limit else "success"
    )


def groq_provider_from_settings(settings: Any) -> OpenAICompatProvider:
    """Build only the Groq provider with the extraction request limits."""
    return OpenAICompatProvider(
        name="groq",
        base_url=settings.llm_fallback_base_url,
        api_key=settings.groq_api_key,
        model=settings.llm_fallback_model,
        timeout=settings.llm_timeout_seconds,
        requests_per_minute=2,
        max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
    )


def measure_extraction_request_sizes(
    document_text: str,
    prompt: str,
    model: str,
    *,
    max_tokens: int = EXTRACTION_MAX_OUTPUT_TOKENS,
) -> list[dict[str, int]]:
    """Measure each exact extraction request body without creating an HTTP client."""
    wrapped_chunks = build_extraction_chunks(document_text)
    source_chunks = split_document_text(document_text)
    measurements = []

    for index, (source_chunk, wrapped_chunk) in enumerate(
        zip(source_chunks, wrapped_chunks, strict=True),
        start=1,
    ):
        body = build_completion_request_body(
            model=model,
            messages=build_messages(prompt, wrapped_chunk),
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        measurements.append(
            {
                "chunk_index": index,
                "chunk_characters": len(source_chunk),
                "request_body_bytes": serialized_request_body_size(body),
            }
        )

    return measurements
