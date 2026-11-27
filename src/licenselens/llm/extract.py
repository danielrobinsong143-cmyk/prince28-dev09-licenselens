"""Document metadata extraction and validation."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from licenselens.llm.doc_meta import DocMeta
from licenselens.llm.gateway import LLMGateway, LLMProviderAttempt, LLMResponse

DEFAULT_EXTRACTION_CHUNK_CHARS = 4000
DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS = 1000
DEFAULT_EXTRACTION_RPM = 1.0

EXTRACTION_MAX_OUTPUT_TOKENS = DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS
EXTRACTION_CHUNK_CHARS = DEFAULT_EXTRACTION_CHUNK_CHARS
EXTRACTION_RPM = DEFAULT_EXTRACTION_RPM
EXTRACTION_MAX_ATTEMPTS = 3


class LLMOutputTruncatedError(RuntimeError):
    """The provider stopped because its output-token limit was reached."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.attempts = attempts


def combine_document_text(document: Any) -> str:
    """Combine a document's pages into one ordered text input."""
    pages = sorted(document.pages, key=lambda page: page.page_no)

    return "\n\n".join(f"[Page {page.page_no}]\n{page.text}" for page in pages if page.text.strip())


def _was_truncated(finish_reason: str | None) -> bool:
    if not finish_reason:
        return False
    reason = finish_reason.strip().lower().replace("-", "_").replace(" ", "_")
    return reason in {
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "token_limit",
    }


def _json_error_is_at_end(error: json.JSONDecodeError) -> bool:
    document = error.doc.rstrip()
    return "unterminated" in error.msg.lower() or error.pos >= len(document)


def parse_json(response: str) -> dict[str, Any]:
    """Parse an LLM response into a JSON object."""
    cleaned = response.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")

    return data


def validate_metadata(data: dict[str, Any]) -> DocMeta:
    """Validate extracted metadata against the DocMeta schema."""
    return DocMeta.model_validate(data)


async def extract_metadata(
    gateway: LLMGateway,
    prompt: str,
    version: str,
    document_text: str,
    on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
) -> tuple[DocMeta, LLMResponse]:
    """Extract metadata and retry when JSON or schema validation fails."""

    messages = build_messages(prompt, document_text)
    last_error: Exception | None = None

    last_truncation: LLMOutputTruncatedError | None = None

    for attempt in range(1, EXTRACTION_MAX_ATTEMPTS + 1):
        result = await gateway.generate(
            messages,
            purpose="extract",
            prompt_version=version,
            temperature=0,
            response_format={"type": "json_object"},
            on_attempt=on_attempt,
        )

        if _was_truncated(result.finish_reason):
            last_truncation = LLMOutputTruncatedError(
                "Metadata response was truncated at the provider output-token limit "
                f"({result.finish_reason}; completion_tokens={result.tokens_out}).",
                finish_reason=result.finish_reason,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                attempts=attempt,
            )

        else:
            try:
                data = parse_json(result.content)
                metadata = validate_metadata(data)
                return metadata, result

            except (ValueError, ValidationError) as error:
                if isinstance(error, json.JSONDecodeError) and _json_error_is_at_end(error):
                    last_truncation = LLMOutputTruncatedError(
                        "Metadata response ended inside JSON "
                        f"(finish_reason={result.finish_reason or 'unavailable'}; "
                        f"completion_tokens={result.tokens_out}).",
                        finish_reason=result.finish_reason,
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        attempts=attempt,
                    )
                    last_error = last_truncation
                else:
                    last_error = error
                    last_truncation = None

        if attempt == EXTRACTION_MAX_ATTEMPTS:
            break

        messages.extend(
            [
                {"role": "assistant", "content": result.content},
                {
                    "role": "user",
                    "content": (
                        "Your previous response was incomplete or invalid. "
                        "Return one complete, concise JSON object matching the requested "
                        "metadata fields. Keep the summary to at most two short sentences, "
                        "keep list items concise, and include no more than 8 items per list. "
                        "Do not omit required fields."
                    )
                    if last_truncation is not None
                    else (
                        "Your previous response was invalid. "
                        f"Error: {last_error}\n"
                        "Correct the errors and return the complete metadata as valid JSON only."
                    ),
                },
            ]
        )

    if last_truncation is not None:
        last_truncation.attempts = EXTRACTION_MAX_ATTEMPTS
        raise last_truncation from last_error
    raise ValueError("Metadata extraction failed after 3 attempts.") from last_error


async def extract_metadata_from_chunks(
    gateway: LLMGateway,
    prompt: str,
    version: str,
    document_text: str,
    on_attempt: Callable[[LLMProviderAttempt], Awaitable[None]] | None = None,
    *,
    max_chars: int = DEFAULT_EXTRACTION_CHUNK_CHARS,
) -> tuple[DocMeta, LLMResponse]:

    chunks = build_extraction_chunks(document_text, max_chars=max_chars)

    if not chunks:
        raise ValueError("Document text is empty.")

    results = []
    last_response = None
    total_tokens_in = 0
    total_tokens_out = 0
    total_latency_ms = 0
    fallback_used = False

    for chunk_text in chunks:
        metadata, response = await extract_metadata(
            gateway,
            prompt,
            version,
            chunk_text,
            on_attempt,
        )

        results.append(metadata)
        last_response = response
        total_tokens_in += response.tokens_in
        total_tokens_out += response.tokens_out
        total_latency_ms += response.latency_ms
        fallback_used = fallback_used or response.fallback_used

    aggregated_response = replace(
        last_response,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        latency_ms=total_latency_ms,
        fallback_used=fallback_used,
    )
    return merge_metadata(results), aggregated_response


def build_extraction_chunks(
    text: str,
    max_chars: int = DEFAULT_EXTRACTION_CHUNK_CHARS,
) -> list[str]:
    """Build the exact per-chunk text sent by extraction and diagnostics."""
    source_chunks = split_document_text(text, max_chars=max_chars)
    total = len(source_chunks)
    return [
        (
            f"Document chunk {index} of {total}.\n"
            "Extract only metadata supported by this chunk. "
            "Do not assume missing information is absent from the full document.\n\n"
            f"{chunk}"
        )
        for index, chunk in enumerate(source_chunks, start=1)
    ]


def split_document_text(
    text: str,
    max_chars: int = DEFAULT_EXTRACTION_CHUNK_CHARS,
) -> list[str]:
    """Split document text into chunks without losing content."""

    if max_chars < 1:
        raise ValueError("max_chars must be greater than zero")

    if not text:
        return []

    return [text[start : start + max_chars] for start in range(0, len(text), max_chars)]


def merge_metadata(results: list[DocMeta]) -> DocMeta:
    """Merge metadata extracted from multiple document chunks."""

    if not results:
        raise ValueError("At least one metadata result is required")

    merged = results[0].model_dump()

    list_fields = {
        "topics",
        "permissions",
        "conditions",
        "limitations",
    }

    for result in results[1:]:
        data = result.model_dump()

        for key, value in data.items():
            if key in list_fields:
                merged[key] = list(dict.fromkeys(merged[key] + value))[:8]

            elif merged[key] is None and value is not None:
                merged[key] = value

            elif key == "summary" and value and value not in merged[key]:
                merged[key] = f"{merged[key]} {value}"

    return DocMeta.model_validate(merged)


def build_messages(prompt: str, document_text: str) -> list[dict[str, str]]:
    """Combine extraction instructions with the source document."""
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (f"<document>\n{document_text.strip()}\n</document>"),
        },
    ]
