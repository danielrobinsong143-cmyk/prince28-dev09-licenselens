import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from licenselens.llm.diagnostic import DiagnosticOutcome


def test_cli_import_and_entry_point(monkeypatch, capsys):
    cli_path = Path(__file__).resolve().parents[2] / "scripts" / "diagnose_groq_request.py"
    namespace = runpy.run_path(str(cli_path), run_name="diagnostic_cli_test")
    requested_ids = []

    async def mocked_run(document_id, chunk_index=1):
        requested_ids.append((document_id, chunk_index))
        return DiagnosticOutcome(
            document_found=True,
            page_text_available=True,
            classification="success",
            http_request_started=True,
            http_request_count=1,
            response_status=200,
        )

    monkeypatch.setitem(namespace["main"].__globals__, "_run", mocked_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(cli_path), "--document-id", "f-a1716d5b10ab4eaac614"],
    )

    with pytest.raises(SystemExit) as exit_info:
        namespace["main"]()

    assert exit_info.value.code == 0
    assert requested_ids == [("f-a1716d5b10ab4eaac614", 1)]
    output = json.loads(capsys.readouterr().out)
    assert output["classification"] == "success"
    assert output["http_request_count"] == 1


def test_measure_only_cli_reports_all_chunks_without_http(monkeypatch, capsys):
    cli_path = Path(__file__).resolve().parents[2] / "scripts" / "diagnose_groq_request.py"
    namespace = runpy.run_path(str(cli_path), run_name="measure_only_cli_test")
    module_globals = namespace["main"].__globals__
    requested_ids = []
    document_text = "PRIVATE_DOCUMENT_TEXT" * 500
    prompt = "PRIVATE_PROMPT_TEXT"

    async def load_text(document_id, _settings):
        requested_ids.append(document_id)
        return document_text

    def unexpected_http_client(*_args, **_kwargs):
        raise AssertionError("measure-only must not construct an HTTP client")

    def unexpected_provider(*_args, **_kwargs):
        raise AssertionError("measure-only must not construct a provider")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        unexpected_http_client,
    )
    monkeypatch.setitem(
        module_globals,
        "get_settings",
        lambda: SimpleNamespace(llm_fallback_model="openai/gpt-oss-20b"),
    )
    monkeypatch.setitem(module_globals, "read_prompt", lambda *_args: prompt)
    monkeypatch.setitem(module_globals, "_load_document_text", load_text)
    monkeypatch.setitem(
        module_globals,
        "groq_provider_from_settings",
        unexpected_provider,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(cli_path), "--document-id", "doc-measure", "--measure-only"],
    )

    with pytest.raises(SystemExit) as exit_info:
        namespace["main"]()

    assert exit_info.value.code == 0
    assert requested_ids == ["doc-measure"]
    output = json.loads(capsys.readouterr().out)
    assert output["classification"] == "measure_only_success"
    assert output["http_request_started"] is False
    assert output["http_request_count"] == 0
    assert output["extraction_chunk_count"] == 2
    measurements = output["chunk_measurements"]
    assert [item["chunk_index"] for item in measurements] == [1, 2]
    assert [item["chunk_characters"] for item in measurements] == [
        8000,
        len(document_text) - 8000,
    ]
    assert all(item["request_body_bytes"] > 0 for item in measurements)
    assert measurements[0]["request_body_bytes"] > measurements[1]["request_body_bytes"]
    serialized = json.dumps(output)
    assert "PRIVATE_DOCUMENT_TEXT" not in serialized
    assert "PRIVATE_PROMPT_TEXT" not in serialized
