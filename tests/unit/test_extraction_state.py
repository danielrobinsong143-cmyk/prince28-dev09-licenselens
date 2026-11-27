from datetime import UTC, datetime

import httpx
import pytest

from licenselens.llm.extraction_state import (
    clear_failure,
    load_failures,
    record_failure,
    safe_failure_reason,
)


def test_failure_state_records_safe_reason_and_increments_attempt_count(tmp_path):
    path = tmp_path / "state" / "failures.json"
    now = datetime(2026, 11, 26, tzinfo=UTC)

    first = record_failure(path, "doc-1", "http_status_413", attempted_at=now)
    second = record_failure(path, "doc-1", "provider_timeout", attempted_at=now)

    assert first["doc-1"]["attempts"] == 1
    assert second["doc-1"]["attempts"] == 2
    assert second["doc-1"]["reason"] == "provider_timeout"
    assert second["doc-1"]["last_attempt_at"] == now.isoformat()
    contents = path.read_text(encoding="utf-8")
    assert "document text" not in contents
    assert "api_key" not in contents


def test_successful_document_clears_failure_state(tmp_path):
    path = tmp_path / "failures.json"
    record_failure(path, "doc-1", "http_status_413")
    record_failure(path, "doc-2", "provider_timeout")

    failures = clear_failure(path, "doc-1")

    assert "doc-1" not in failures
    assert "doc-2" in failures
    assert load_failures(path) == failures


def test_failure_state_rejects_unsafe_reasons(tmp_path):
    with pytest.raises(ValueError, match="safe classification"):
        record_failure(tmp_path / "failures.json", "doc-1", "private text from document")


def test_failure_state_fails_closed_on_invalid_json(tmp_path):
    path = tmp_path / "failures.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable or invalid JSON"):
        load_failures(path)


def test_http_status_reason_records_only_the_numeric_status():
    request = httpx.Request("POST", "https://provider.invalid")
    response = httpx.Response(413, request=request, text="private response body")
    error = httpx.HTTPStatusError("private error detail", request=request, response=response)

    assert safe_failure_reason(error) == "http_status_413"
