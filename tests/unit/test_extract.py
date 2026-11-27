import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from licenselens.llm.doc_meta import DocMeta
from licenselens.llm.extract import (
    DEFAULT_EXTRACTION_CHUNK_CHARS,
    DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS,
    DEFAULT_EXTRACTION_RPM,
    EXTRACTION_MAX_OUTPUT_TOKENS,
    LLMOutputTruncatedError,
    build_extraction_chunks,
    extract_metadata,
    extract_metadata_from_chunks,
    merge_metadata,
    parse_json,
    split_document_text,
    validate_metadata,
)
from licenselens.llm.gateway import FakeLLM, LLMGateway, LLMResponse, RequestPacer

VALID_METADATA = {
    "doc_kind": "license",
    "year": None,
    "language": "en",
    "summary": "A permissive software licence.",
    "topics": ["Open Source"],
    "spdx_id": "MIT",
    "name": "MIT License",
    "osi_approved": True,
    "copyleft_type": "permissive",
    "permissions": ["commercial use"],
    "conditions": ["include copyright notice"],
    "limitations": ["no warranty"],
}


def test_extraction_default_configuration_constants():
    assert EXTRACTION_MAX_OUTPUT_TOKENS == 1000
    assert DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS == 1000
    assert DEFAULT_EXTRACTION_CHUNK_CHARS == 4000
    assert DEFAULT_EXTRACTION_RPM == 1.0


def make_gateway(responses):
    return LLMGateway(
        primary=FakeLLM(responses=responses),
        fallback=FakeLLM(),
    )


def test_parse_json():
    result = parse_json(json.dumps(VALID_METADATA))

    assert result["spdx_id"] == "MIT"


def test_parse_json_with_markdown_fence():
    raw = f"```json\n{json.dumps(VALID_METADATA)}\n```"

    result = parse_json(raw)

    assert result["name"] == "MIT License"


def test_validate_metadata():
    metadata = validate_metadata(VALID_METADATA)

    assert isinstance(metadata, DocMeta)
    assert metadata.name == "MIT License"
    assert metadata.topics == ["open source"]


def test_extraction_prompt_describes_every_docmeta_field():
    prompt = Path("prompts/extract_v1.md").read_text(encoding="utf-8")

    assert set(VALID_METADATA) == set(DocMeta.model_fields)
    assert all(f"`{field}`" in prompt for field in DocMeta.model_fields)


def test_validate_metadata_rejects_extra_fields():
    data = {**VALID_METADATA, "unexpected": "value"}

    with pytest.raises(ValidationError):
        validate_metadata(data)


def test_validate_metadata_rejects_coerced_scalar_types():
    data = {**VALID_METADATA, "year": "2024"}

    with pytest.raises(ValidationError):
        validate_metadata(data)


@pytest.mark.asyncio
async def test_extract_metadata_success():
    gateway = make_gateway([json.dumps(VALID_METADATA)])

    metadata, result = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.name == "MIT License"
    assert result.provider == "fake"
    assert gateway.primary.call_count == 1


@pytest.mark.asyncio
async def test_extract_metadata_repairs_invalid_json():
    gateway = make_gateway(
        [
            "This is not JSON",
            json.dumps(VALID_METADATA),
        ]
    )

    metadata, _ = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.spdx_id == "MIT"
    assert gateway.primary.call_count == 2


@pytest.mark.asyncio
async def test_invalid_json_repair_records_each_provider_attempt():
    gateway = make_gateway(["not-json", json.dumps(VALID_METADATA)])
    recorded_attempts = []

    async def record_attempt(attempt):
        recorded_attempts.append(attempt)

    metadata, result = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "Abstyles licence text",
        on_attempt=record_attempt,
    )

    assert metadata.spdx_id == "MIT"
    assert result.tokens_in == 10
    assert gateway.primary.call_count == 2
    assert [attempt.status for attempt in recorded_attempts] == ["ok", "ok"]
    assert all(attempt.tokens_in == 10 for attempt in recorded_attempts)


@pytest.mark.asyncio
async def test_extract_metadata_repairs_non_truncated_json_syntax_error():
    gateway = make_gateway(
        [
            '{"doc_kind": "license",}',
            json.dumps(VALID_METADATA),
        ]
    )

    metadata, _ = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.spdx_id == "MIT"
    assert gateway.primary.call_count == 2


@pytest.mark.asyncio
async def test_extract_metadata_repairs_invalid_schema():
    invalid = {**VALID_METADATA, "year": "unknown"}

    gateway = make_gateway(
        [
            json.dumps(invalid),
            json.dumps(VALID_METADATA),
        ]
    )

    metadata, _ = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.year is None
    assert gateway.primary.call_count == 2


@pytest.mark.asyncio
async def test_extract_metadata_stops_after_three_attempts():
    gateway = make_gateway(
        [
            "invalid",
            "invalid",
            "invalid",
        ]
    )

    with pytest.raises(ValueError, match="after 3 attempts"):
        await extract_metadata(
            gateway,
            "Extract document metadata.",
            "extract_v1",
            "MIT License text",
        )

    assert gateway.primary.call_count == 3


@pytest.mark.asyncio
async def test_extract_metadata_retries_truncation_and_recovers():
    gateway = LLMGateway(
        primary=FakeLLM(
            responses=['{"doc_kind":"license"', json.dumps(VALID_METADATA)],
            finish_reasons=["MAX_TOKENS", "stop"],
        ),
        fallback=FakeLLM(),
    )
    attempts = []

    async def record_attempt(attempt):
        attempts.append(attempt)

    metadata, response = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
        on_attempt=record_attempt,
    )

    assert metadata.spdx_id == "MIT"
    assert gateway.primary.call_count == 2
    assert "complete, concise JSON object" in gateway.primary.seen[1]["messages"][-1]["content"]
    assert response.tokens_in == 10
    assert response.tokens_out == 8
    assert len(attempts) == 2
    assert [attempt.tokens_out for attempt in attempts] == [8, 8]


@pytest.mark.asyncio
async def test_extract_metadata_reports_truncation_after_three_attempts():
    gateway = LLMGateway(
        primary=FakeLLM(
            responses=['{"doc_kind":"license"'] * 3,
            finish_reasons=["length", "length", "length"],
        ),
        fallback=FakeLLM(),
    )

    with pytest.raises(LLMOutputTruncatedError, match="completion_tokens=8") as error:
        await extract_metadata(
            gateway,
            "Extract document metadata.",
            "extract_v1",
            "MIT License text",
        )

    assert gateway.primary.call_count == 3
    assert error.value.finish_reason == "length"
    assert error.value.tokens_in == 10
    assert error.value.tokens_out == 8
    assert error.value.attempts == 3


@pytest.mark.asyncio
async def test_extract_metadata_stops_on_json_truncated_at_end_without_finish_reason():
    gateway = make_gateway(
        [
            '{"doc_kind": "license", "summary": "partial',
            json.dumps(VALID_METADATA),
        ]
    )

    metadata, _ = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.spdx_id == "MIT"
    assert gateway.primary.call_count == 2


@pytest.mark.asyncio
async def test_extract_metadata_uses_structured_json():
    gateway = make_gateway([json.dumps(VALID_METADATA)])

    await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    request = gateway.primary.seen[0]

    assert request["response_format"] == {"type": "json_object"}
    assert request["temperature"] == 0


def test_split_document_text_keeps_small_document_intact():
    text = "MIT License text"

    chunks = split_document_text(text)

    assert chunks == [text]


def test_split_document_text_splits_large_document():
    text = "A" * 20000

    chunks = split_document_text(text, max_chars=8000)

    assert len(chunks) == 3
    assert all(len(chunk) <= 8000 for chunk in chunks)
    assert "".join(chunks) == text


def test_split_document_text_preserves_all_content():
    text = "License section. " * 1000

    chunks = split_document_text(text, max_chars=5000)

    assert "".join(chunks) == text


def test_build_extraction_chunks_adds_the_runner_prefix_and_preserves_content():
    chunks = build_extraction_chunks("abcdefgh", max_chars=4)

    assert chunks == [
        "Document chunk 1 of 2.\n"
        "Extract only metadata supported by this chunk. "
        "Do not assume missing information is absent from the full document.\n\n"
        "abcd",
        "Document chunk 2 of 2.\n"
        "Extract only metadata supported by this chunk. "
        "Do not assume missing information is absent from the full document.\n\n"
        "efgh",
    ]
    assert all(len(chunk.rsplit("\n\n", 1)[-1]) <= 4 for chunk in chunks)


def test_extraction_chunk_sizes_remain_bounded_at_different_limits():
    text = "x" * 17

    small_chunks = build_extraction_chunks(text, max_chars=5)
    larger_chunks = build_extraction_chunks(text, max_chars=10)

    assert len(small_chunks) == 4
    assert len(larger_chunks) == 2
    assert [chunk.rsplit("\n\n", 1)[-1] for chunk in small_chunks] == [
        "x" * 5,
        "x" * 5,
        "x" * 5,
        "xx",
    ]


def test_merge_metadata_combines_unique_list_fields():
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First chunk summary",
        topics=["software", "opensource"],
        permissions=["use"],
    )

    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Second chunk summary",
        topics=["opensource", "copyright"],
        permissions=["use", "modify"],
    )

    result = merge_metadata([first, second])

    assert result.topics == ["software", "opensource", "copyright"]
    assert result.permissions == ["use", "modify"]
    assert "First chunk summary" in result.summary
    assert "Second chunk summary" in result.summary


def test_merge_metadata_keeps_merged_lists_within_schema_maximum():
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First chunk summary",
        topics=[f"topic-{number}" for number in range(1, 8)],
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Second chunk summary",
        topics=["topic-7", "topic-8", "topic-9"],
    )

    result = merge_metadata([first, second])

    assert result.topics == [f"topic-{number}" for number in range(1, 9)]
    assert len(result.topics) == 8


def test_merge_metadata_keeps_first_non_null_scalar():
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First summary",
        year=None,
        spdx_id=None,
    )

    second = DocMeta(
        doc_kind="document",
        language="fr",
        summary="Second summary",
        year=2024,
        spdx_id="MIT",
    )

    result = merge_metadata([first, second])

    assert result.doc_kind == "license"
    assert result.language == "en"
    assert result.year == 2024
    assert result.spdx_id == "MIT"


def test_merge_metadata_rejects_empty_results():
    with pytest.raises(ValueError):
        merge_metadata([])


@pytest.mark.asyncio
async def test_extract_metadata_from_multiple_chunks():
    first = {**VALID_METADATA, "topics": ["software"]}
    second = {**VALID_METADATA, "topics": ["opensource"]}

    gateway = make_gateway(
        [
            json.dumps(first),
            json.dumps(second),
        ]
    )

    metadata, response = await extract_metadata_from_chunks(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "A" * 7000,
    )

    assert gateway.primary.call_count == 2
    assert metadata.topics == ["software", "opensource"]
    assert response.provider == "fake"
    assert response.tokens_in == 20
    assert response.tokens_out == 16


@pytest.mark.asyncio
async def test_truncated_chunk_retries_then_merges_with_following_chunk():
    first_chunk = {**VALID_METADATA, "topics": [f"topic-{number}" for number in range(1, 8)]}
    second_chunk = {**VALID_METADATA, "topics": ["topic-7", "topic-8", "topic-9"]}
    gateway = LLMGateway(
        primary=FakeLLM(
            responses=[
                '{"doc_kind":"license"',
                json.dumps(first_chunk),
                json.dumps(second_chunk),
            ],
            finish_reasons=["length", "stop", "stop"],
        ),
        fallback=FakeLLM(),
    )

    metadata, response = await extract_metadata_from_chunks(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "A" * 7000,
    )

    assert gateway.primary.call_count == 3
    assert metadata.topics == [f"topic-{number}" for number in range(1, 9)]
    assert response.tokens_in == 20
    assert response.tokens_out == 16


@pytest.mark.asyncio
async def test_extract_metadata_from_chunks_rejects_empty_text():
    gateway = make_gateway([])

    with pytest.raises(ValueError, match="Document text is empty"):
        await extract_metadata_from_chunks(
            gateway,
            "Extract document metadata.",
            "extract_v1",
            "",
        )

    assert gateway.primary.call_count == 0


@pytest.mark.asyncio
async def test_extract_metadata_repair_attempt_respects_request_pacer():
    delays = []

    async def fake_sleep(duration: float):
        delays.append(duration)

    clock_time = [100.0]

    def fake_clock():
        return clock_time[0]

    pacer = RequestPacer(requests_per_minute=1.0, clock=fake_clock, sleep=fake_sleep)

    class PacedProvider:
        def __init__(self, responses):
            self.responses = list(responses)
            self.call_count = 0
            self.request_pacer = pacer

        async def complete(self, messages, temperature=0.2, response_format=None):
            await self.request_pacer.wait_for_slot()
            self.call_count += 1
            content = self.responses.pop(0)
            return LLMResponse(
                content=content,
                provider="paced",
                model="paced-model",
                tokens_in=10,
                tokens_out=8,
                latency_ms=1,
                finish_reason="stop",
            )

    gateway = LLMGateway(
        primary=PacedProvider(
            [
                '{"doc_kind": "license", "invalid_json',
                json.dumps(VALID_METADATA),
            ]
        ),
        fallback=None,
    )

    metadata, _ = await extract_metadata(
        gateway,
        "Extract document metadata.",
        "extract_v1",
        "MIT License text",
    )

    assert metadata.spdx_id == "MIT"
    assert gateway.primary.call_count == 2
    assert len(delays) == 1
    assert delays[0] == pytest.approx(60.0)
