import json

import httpx
import pytest

from licenselens.llm.diagnostic import run_one_shot_diagnostic
from licenselens.llm.extract import build_extraction_chunks
from licenselens.llm.gateway import OpenAICompatProvider, serialized_request_body_size

DOCUMENT_TEXT = "A secret sample license document."
API_KEY = "mock-secret-key"  # pragma: allowlist secret
VALID_DOCMETA = {
    "doc_kind": "software_license",
    "language": "English",
    "summary": "Permissive software license.",
}


def make_provider():
    return OpenAICompatProvider(
        name="groq",
        base_url="https://groq.example/v1",
        api_key=API_KEY,
        model="openai/gpt-oss-20b",
        max_tokens=1000,
    )


@pytest.mark.asyncio
async def test_diagnostic_reports_document_not_found_without_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async def load_document(document_id):
        assert document_id == "missing-doc"
        return None

    result = await run_one_shot_diagnostic(
        "missing-doc",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.document_found is False
    assert result.page_text_available is None
    assert result.http_request_started is False
    assert result.http_request_count == 0
    assert result.response_status is None
    assert result.classification == "document_not_found"
    assert requests == []


@pytest.mark.asyncio
async def test_diagnostic_reports_empty_text_without_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async def load_document(_document_id):
        return "  \n  "

    result = await run_one_shot_diagnostic(
        "empty-doc",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.document_found is True
    assert result.page_text_available is False
    assert result.http_request_started is False
    assert result.http_request_count == 0
    assert result.response_status is None
    assert result.classification == "empty_document_text"
    assert requests == []


@pytest.mark.asyncio
async def test_diagnostic_reports_http_200_success_without_response_content():
    request_bodies = []

    def handler(request):
        request_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(VALID_DOCMETA)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 321, "completion_tokens": 42},
            },
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-200",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.document_found is True
    assert result.page_text_available is True
    assert result.http_request_started is True
    assert result.http_request_count == 1
    assert result.response_status == 200
    assert result.classification == "success"
    assert result.error_classification is None
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 321
    assert result.completion_tokens == 42
    assert result.configured_output_token_limit == 1000
    assert result.reached_output_token_limit is False
    assert result.content_valid_json is True
    assert result.docmeta_valid is True
    assert len(request_bodies) == 1
    assert DOCUMENT_TEXT in request_bodies[0]["messages"][1]["content"]
    assert DOCUMENT_TEXT not in result.to_json()
    assert API_KEY not in result.to_json()
    assert json.dumps(VALID_DOCMETA) not in result.to_json()


@pytest.mark.asyncio
async def test_diagnostic_uses_runner_chunk_prefix_and_reports_exact_body_size():
    document_text = "A" * 8001
    prompt = "Extract safely."
    request_bodies = []

    def handler(request):
        body = json.loads(request.content)
        request_bodies.append((body, len(request.content)))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(VALID_DOCMETA)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 10},
            },
        )

    async def load_document(_document_id):
        return document_text

    provider = make_provider()
    result = await run_one_shot_diagnostic(
        "doc-chunked",
        load_document=load_document,
        provider=provider,
        prompt=prompt,
        chunk_index=2,
        transport=httpx.MockTransport(handler),
    )

    expected_chunk = build_extraction_chunks(document_text)[1]
    body, actual_size = request_bodies[0]
    assert result.extraction_chunk_count == 2
    assert result.extraction_chunk_index == 2
    assert body["messages"][1]["content"] == f"<document>\n{expected_chunk}\n</document>"
    assert result.request_body_bytes == actual_size
    assert result.request_body_bytes == serialized_request_body_size(body)
    assert "A" * 8001 not in result.to_json()


@pytest.mark.asyncio
async def test_diagnostic_reports_invalid_generated_json():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "not valid JSON"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 4},
            },
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-invalid-json",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.response_status == 200
    assert result.classification == "invalid_json"
    assert result.content_valid_json is False
    assert result.docmeta_valid is False
    assert result.json_error_classification == "JSONDecodeError"
    assert result.docmeta_error_classification is None
    assert "not valid JSON" not in result.to_json()


@pytest.mark.asyncio
async def test_diagnostic_reports_docmeta_schema_validation_failure():
    invalid_metadata = {"doc_kind": "software_license", "summary": "Missing language."}

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(invalid_metadata)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8},
            },
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-invalid-schema",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.response_status == 200
    assert result.classification == "docmeta_validation_failed"
    assert result.content_valid_json is True
    assert result.docmeta_valid is False
    assert result.json_error_classification is None
    assert result.docmeta_error_classification == "ValidationError"
    assert "Missing language" not in result.to_json()


@pytest.mark.asyncio
async def test_diagnostic_reports_token_limit_truncation():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"summary": "incomplete'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 700, "completion_tokens": 1000},
            },
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-truncated",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.response_status == 200
    assert result.classification == "output_token_limit_reached"
    assert result.finish_reason == "length"
    assert result.prompt_tokens == 700
    assert result.completion_tokens == 1000
    assert result.configured_output_token_limit == 1000
    assert result.reached_output_token_limit is True
    assert result.content_valid_json is False
    assert result.docmeta_valid is False
    assert '{"summary": "incomplete' not in result.to_json()


@pytest.mark.asyncio
async def test_diagnostic_reports_only_sanitized_http_400_fields():
    def handler(request):
        return httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "message": f"Request echoed {DOCUMENT_TEXT}; key={API_KEY}",
                    "type": "invalid_request_error",
                    "code": "unsupported_parameter",
                    "param": "max_tokens",
                }
            },
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-400",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.http_request_started is True
    assert result.http_request_count == 1
    assert result.response_status == 400
    assert result.classification == "http_400"
    assert result.error_classification == "ProviderBadRequestError"
    assert result.http_400 is not None
    assert set(result.http_400) == {"message", "type", "code", "param"}
    assert result.http_400["type"] == "invalid_request_error"
    assert result.http_400["code"] == "unsupported_parameter"
    assert result.http_400["param"] == "max_tokens"
    serialized = result.to_json()
    assert DOCUMENT_TEXT not in serialized
    assert API_KEY not in serialized


@pytest.mark.asyncio
async def test_diagnostic_reports_other_http_error_without_body():
    def handler(request):
        return httpx.Response(503, request=request, text=f"private {DOCUMENT_TEXT} {API_KEY}")

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-503",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.http_request_started is True
    assert result.http_request_count == 1
    assert result.response_status == 503
    assert result.classification == "http_error"
    assert result.error_classification == "HTTPStatusError"
    serialized = result.to_json()
    assert "private" not in serialized
    assert DOCUMENT_TEXT not in serialized
    assert API_KEY not in serialized
    assert result.http_400 is None


@pytest.mark.asyncio
async def test_diagnostic_reports_transport_error_without_exception_text():
    def handler(request):
        raise httpx.ConnectError(
            f"private transport detail {DOCUMENT_TEXT} {API_KEY}",
            request=request,
        )

    async def load_document(_document_id):
        return DOCUMENT_TEXT

    result = await run_one_shot_diagnostic(
        "doc-network",
        load_document=load_document,
        provider=make_provider(),
        prompt="Extract the metadata.",
        transport=httpx.MockTransport(handler),
    )

    assert result.http_request_started is True
    assert result.http_request_count == 1
    assert result.response_status is None
    assert result.classification == "transport_error"
    assert result.error_classification == "ConnectError"
    serialized = result.to_json()
    assert "private transport detail" not in serialized
    assert DOCUMENT_TEXT not in serialized
    assert API_KEY not in serialized
