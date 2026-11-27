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
    assert result.summary == "First chunk summary"
    assert "Second chunk summary" not in result.summary


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

    result = merge_metadata(
        [first, second],
        chunk_texts=["", "Licensed under MIT terms."],
    )

    assert result.doc_kind == "license"
    assert result.language == "en"
    assert result.year == 2024
    assert result.spdx_id == "MIT"


def test_merge_metadata_early_hallucination_later_corroborated():
    """1. Early hallucinated AFPL-2.0; later source contains licenseId AFL-2.0 -> AFL-2.0."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="Academic Free License v2.0 text.",
        spdx_id="AFPL-2.0",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Metadata section.",
        spdx_id="AFL-2.0",
    )
    chunk_texts = [
        "The Academic Free License v. 2.0 applies to any original work.",
        'Standard header: "licenseId": "AFL-2.0", isOsiApproved: true',
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id == "AFL-2.0"


def test_merge_metadata_early_corroborated_later_hallucination():
    """2. Early source explicitly contains MIT; later chunk hallucinates BSD-3-Clause -> MIT."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="MIT license terms.",
        spdx_id="MIT",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Disclaimer terms.",
        spdx_id="BSD-3-Clause",
    )
    chunk_texts = [
        "SPDX-License-Identifier: MIT\nPermission is hereby granted...",
        "THE SOFTWARE IS PROVIDED AS IS WITHOUT WARRANTY OF ANY KIND.",
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id == "MIT"


def test_merge_metadata_conflicting_corroborated_ids_returns_null():
    """3. Conflicting corroborated IDs -> null."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First part dual-licensed under MIT.",
        spdx_id="MIT",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Second part dual-licensed under Apache-2.0.",
        spdx_id="Apache-2.0",
    )
    chunk_texts = [
        "This component is distributed under SPDX-License-Identifier: MIT.",
        "This other component is distributed under SPDX-License-Identifier: Apache-2.0.",
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id is None


def test_merge_metadata_conflicting_uncorroborated_ids_returns_null():
    """4. Conflicting uncorroborated IDs -> null."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="Generic terms.",
        spdx_id="GPL-2.0-only",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Additional terms.",
        spdx_id="MIT",
    )
    chunk_texts = [
        "All original authors retain their respective copyrights.",
        "Redistribution without modification is strictly allowed.",
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id is None


def test_merge_metadata_unanimous_uncorroborated_ids_returns_null():
    """5. Unanimous but uncorroborated IDs -> null."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="Permissive terms.",
        spdx_id="MIT",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Permissive terms continuation.",
        spdx_id="MIT",
    )
    chunk_texts = [
        "Permission is hereby granted to copy and distribute this work.",
        "The software is provided without any warranty whatsoever.",
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id is None


def test_merge_metadata_no_identifiers_returns_null():
    """6. No identifiers -> null."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="Proprietary terms.",
        spdx_id=None,
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Additional restrictions.",
        spdx_id=None,
    )
    chunk_texts = [
        "All rights reserved. Proprietary software.",
        "Do not redistribute without written consent.",
    ]

    result = merge_metadata([first, second], chunk_texts=chunk_texts)

    assert result.spdx_id is None


def test_merge_metadata_exact_spdx_id_overrides_conflicting_outputs():
    """7. exact_spdx_id overrides conflicting model outputs."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First chunk.",
        spdx_id="INFERRED-1.0",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Second chunk.",
        spdx_id="INFERRED-2.0",
    )
    chunk_texts = [
        "Text mentions INFERRED-1.0 here.",
        "Text mentions INFERRED-2.0 here.",
    ]

    result = merge_metadata(
        [first, second],
        exact_spdx_id="  TRUSTED-OVERRIDE-1.0 \n",
        chunk_texts=chunk_texts,
    )

    assert result.spdx_id == "TRUSTED-OVERRIDE-1.0"


def test_merge_metadata_blank_exact_spdx_id_raises_value_error():
    """Reject empty or whitespace-only exact_spdx_id."""
    doc = DocMeta(
        doc_kind="license",
        language="en",
        summary="Summary text.",
        spdx_id="MIT",
    )

    with pytest.raises(ValueError, match="exact_spdx_id must not be empty or whitespace-only"):
        merge_metadata([doc], exact_spdx_id="")

    with pytest.raises(ValueError, match="exact_spdx_id must not be empty or whitespace-only"):
        merge_metadata([doc], exact_spdx_id="   \t\n")


def test_merge_metadata_chunk_texts_length_mismatch_raises_value_error():
    """Require len(chunk_texts) == len(results) when chunk_texts is supplied."""
    first = DocMeta(doc_kind="license", language="en", summary="Chunk 1", spdx_id="MIT")
    second = DocMeta(doc_kind="license", language="en", summary="Chunk 2", spdx_id="MIT")

    # Direction 1: chunk_texts shorter than results (1 < 2)
    with pytest.raises(
        ValueError, match=r"chunk_texts length \(1\) must match results length \(2\)"
    ):
        merge_metadata([first, second], chunk_texts=["Text 1"])

    # Direction 2: chunk_texts longer than results (2 > 1)
    with pytest.raises(
        ValueError, match=r"chunk_texts length \(2\) must match results length \(1\)"
    ):
        merge_metadata([first], chunk_texts=["Text 1", "Text 2"])


def test_merge_metadata_boundary_matching_prevents_false_positives():
    """8. Identifier boundary matching prevents false positives."""
    from licenselens.llm.extract import is_spdx_corroborated

    # "MIT" in substring words like commit/admit/transmit must NOT match
    assert not is_spdx_corroborated("MIT", "We commit to admitting this patch.")
    assert not is_spdx_corroborated("MIT", "Transmitted via secure channel.")
    assert is_spdx_corroborated("MIT", "Licensed under MIT terms.")
    assert is_spdx_corroborated("MIT", "SPDX-License-Identifier: MIT")

    # "+" boundary check: "MIT" inside "MIT+" must NOT match
    assert not is_spdx_corroborated("MIT", "Licensed under MIT+ terms.")
    assert not is_spdx_corroborated("GPL-2.0", "Notice: GPL-2.0+ permitted.")
    assert is_spdx_corroborated("MIT+", "Licensed under MIT+ terms.")

    # "AFL-2.0" in extension like AFL-2.0.1 or AFL-2.0-custom must NOT match
    assert not is_spdx_corroborated("AFL-2.0", "Notice: AFL-2.0.1 variant.")
    assert not is_spdx_corroborated("AFL-2.0", "Derived from AFL-2.0-beta.")
    assert is_spdx_corroborated("AFL-2.0", '"licenseId": "AFL-2.0"')

    # DocMeta integration check
    candidate_doc = DocMeta(
        doc_kind="license",
        language="en",
        summary="False positive test.",
        spdx_id="MIT",
    )
    result = merge_metadata(
        [candidate_doc],
        chunk_texts=["Commit message mentions nothing."],
    )
    assert result.spdx_id is None


def test_merge_metadata_summary_is_not_concatenated():
    """9. Summary is not concatenated."""
    first = DocMeta(
        doc_kind="license",
        language="en",
        summary="First chunk representative summary.",
    )
    second = DocMeta(
        doc_kind="license",
        language="en",
        summary="Second chunk independent summary.",
    )
    third = DocMeta(
        doc_kind="license",
        language="en",
        summary="Third chunk independent summary.",
    )

    result = merge_metadata([first, second, third])

    assert result.summary == "First chunk representative summary."
    assert "Second chunk independent summary." not in result.summary
    assert "Third chunk independent summary." not in result.summary


def test_build_messages_with_and_without_structured_context():
    from licenselens.llm.extract import build_messages

    base_messages = build_messages("Prompt text", "Document body text")
    assert base_messages == [
        {"role": "system", "content": "Prompt text"},
        {"role": "user", "content": "<document>\nDocument body text\n</document>"},
    ]

    context_messages = build_messages(
        "Prompt text",
        "Document body text",
        structured_context={"licenseId": "AFL-2.0"},
    )
    assert context_messages[0] == {"role": "system", "content": "Prompt text"}
    assert "Authoritative context:\n- licenseId: AFL-2.0" in context_messages[1]["content"]
    assert "<document>\nDocument body text\n</document>" in context_messages[1]["content"]


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
