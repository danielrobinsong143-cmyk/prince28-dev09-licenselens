import json
from collections import deque
from types import SimpleNamespace

import httpx
import pytest

from licenselens.db.repository import DocumentRepository
from licenselens.llm.extract import LLMOutputTruncatedError
from licenselens.llm.gateway import (
    CircuitBreaker,
    FakeLLM,
    LLMGateway,
    LLMResponse,
    ProviderBadRequestError,
    ProviderPayloadTooLargeError,
)
from scripts import extract_metadata as extract_runner
from scripts.extract_metadata import parse_args, process_document


def test_extraction_cli_defaults_to_fifty_successes_and_two_requests_per_minute():
    args = parse_args([])

    assert args.limit == 50
    assert args.rpm == 2
    assert args.max_attempts is None
    assert args.document_id is None
    assert args.groq_only is False


def test_extraction_cli_accepts_targeted_groq_only_single_document():
    args = parse_args(
        [
            "--document-id",
            "f-a1716d5b10ab4eaac614",
            "--limit",
            "1",
            "--max-attempts",
            "1",
            "--groq-only",
        ]
    )

    assert args.document_id == "f-a1716d5b10ab4eaac614"
    assert args.limit == 1
    assert args.max_attempts == 1
    assert args.groq_only is True


def test_extraction_cli_requires_limit_one_for_document_id():
    with pytest.raises(SystemExit):
        parse_args(["--document-id", "target", "--limit", "2"])


@pytest.mark.asyncio
async def test_get_pending_documents_filters_to_target_id():
    class Session:
        statement = None

        async def scalars(self, statement):
            self.statement = statement
            return []

    session = Session()

    await extract_runner.get_pending_documents(
        session,
        limit=1,
        document_id="target-document",
    )

    compiled = session.statement.compile()
    assert "documents.external_id =" in str(compiled)
    assert "target-document" in compiled.params.values()


def test_extraction_cli_rejects_rate_above_groq_token_budget():
    with pytest.raises(SystemExit):
        parse_args(["--rpm", "3"])


def test_extraction_cli_accepts_maximum_document_attempts():
    args = parse_args(["--limit", "50", "--max-attempts", "75"])

    assert args.max_attempts == 75


@pytest.mark.asyncio
async def test_document_metadata_update_preserves_existing_fields():
    document = SimpleNamespace(metadata_={"owner": "existing", "name": "old"})

    class Session:
        async def scalar(self, statement):
            return document

        async def flush(self):
            pass

    await DocumentRepository(Session()).update_metadata(
        "external-1",
        {"doc_kind": "license", "name": "MIT"},
    )

    assert document.metadata_ == {
        "owner": "existing",
        "name": "MIT",
        "doc_kind": "license",
    }


@pytest.mark.asyncio
async def test_process_document_commits_metadata_once_after_extraction(capsys):
    metadata = {
        "doc_kind": "license",
        "year": None,
        "language": "en",
        "summary": "A permissive licence.",
        "topics": [],
        "spdx_id": "MIT",
        "name": "MIT License",
        "osi_approved": True,
        "copyleft_type": "permissive",
        "permissions": [],
        "conditions": [],
        "limitations": [],
    }
    document = SimpleNamespace(
        external_id="external-1",
        title="MIT License",
        metadata_={"source": "SPDX"},
        pages=[SimpleNamespace(page_no=1, text="MIT License text")],
    )

    class Session:
        commits = 0

        async def scalar(self, statement):
            return document

        async def flush(self):
            pass

        async def commit(self):
            self.commits += 1

    class Recorder:
        def callback(self, **kwargs):
            return None

    session = Session()
    gateway = LLMGateway(
        primary=FakeLLM(responses=[json.dumps(metadata)]),
        fallback=FakeLLM(),
    )

    processed = await process_document(
        session=session,
        document=document,
        gateway=gateway,
        prompt="Extract metadata.",
        recorder=Recorder(),
    )

    assert processed is True
    assert session.commits == 1
    assert document.metadata_["source"] == "SPDX"
    assert document.metadata_["spdx_id"] == "MIT"
    output = capsys.readouterr().out
    assert "Title: MIT License" in output
    assert "DocMeta validation: passed" in output
    assert "EXTRACTED: external-1" in output
    assert '"spdx_id": "MIT"' in output
    assert "Token usage: input=10 output=8" in output
    assert "Database save: confirmed" in output


@pytest.mark.asyncio
async def test_process_document_reports_provider_tokens_and_estimated_cost(capsys):
    metadata = {
        "doc_kind": "license",
        "year": None,
        "language": "en",
        "summary": "A permissive licence.",
    }
    document = SimpleNamespace(
        external_id="external-cost",
        title="Costed License",
        metadata_={"parser": "pymupdf", "pages": 1},
        pages=[SimpleNamespace(page_no=1, text="MIT License text")],
    )

    class Groq:
        name = "groq"
        model = "openai/gpt-oss-20b"

        async def complete(self, messages, temperature=0.2, response_format=None):
            return LLMResponse(
                content=json.dumps(metadata),
                provider=self.name,
                model=self.model,
                tokens_in=12,
                tokens_out=9,
                latency_ms=1,
            )

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    class Session:
        async def scalar(self, _statement):
            return document

        async def flush(self):
            return None

        async def commit(self):
            return None

    class Recorder:
        settings = SimpleNamespace(usd_inr=95.0)

        def callback(self, **_kwargs):
            async def record(_attempt):
                return None

            return record

    usage = {"tokens_in": 0, "tokens_out": 0, "cost_inr": 0.0}
    await process_document(
        session=Session(),
        document=document,
        gateway=LLMGateway(primary=Groq()),
        prompt="Extract metadata.",
        recorder=Recorder(),
        usage_totals=usage,
    )

    assert usage["tokens_in"] == 12
    assert usage["tokens_out"] == 9
    assert usage["cost_inr"] == pytest.approx(0.000342)
    output = capsys.readouterr().out
    assert "Token usage: input=12 output=9 cost_inr=0.000342" in output


@pytest.mark.asyncio
async def test_process_document_does_not_count_missing_update_as_success():
    metadata = {
        "doc_kind": "license",
        "year": None,
        "language": "en",
        "summary": "A permissive licence.",
        "topics": [],
        "spdx_id": "MIT",
        "name": "MIT License",
        "osi_approved": True,
        "copyleft_type": "permissive",
        "permissions": [],
        "conditions": [],
        "limitations": [],
    }
    document = SimpleNamespace(
        external_id="external-1",
        title="MIT License",
        pages=[SimpleNamespace(page_no=1, text="MIT License text")],
    )

    class Session:
        commits = 0

        async def scalar(self, statement):
            return None

        async def commit(self):
            self.commits += 1

    class Recorder:
        def callback(self, **kwargs):
            return None

    session = Session()
    gateway = LLMGateway(primary=FakeLLM(responses=[json.dumps(metadata)]), fallback=FakeLLM())

    with pytest.raises(LookupError, match="disappeared before metadata update"):
        await process_document(
            session=session,
            document=document,
            gateway=gateway,
            prompt="Extract metadata.",
            recorder=Recorder(),
        )

    assert session.commits == 0


def patch_runner_for_mocked_run(monkeypatch, document_ids, outcomes, gateway_override=None):
    ids = list(document_ids)
    documents = {
        doc_id: SimpleNamespace(external_id=doc_id, title=doc_id, pages=[]) for doc_id in ids
    }
    result_queue = deque(outcomes)
    processed_ids = []
    gateway_config = {}

    class Engine:
        async def dispose(self):
            pass

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def scalar(self, statement):
            if statement.column_descriptions[0]["entity"] is extract_runner.Document:
                compiled = statement.compile()
                selected_id = next(
                    value for key, value in compiled.params.items() if "external_id" in key
                )
                return documents.get(selected_id)
            return len(ids)

    def sessionmaker():
        return Session()

    gateway = gateway_override or SimpleNamespace(all_providers_unavailable=False)

    async def get_pending(session, limit, excluded_ids=None, document_id=None, retry_ids=None):
        excluded_ids = excluded_ids or set()
        candidates = [doc_id for doc_id in ids if doc_id not in excluded_ids]
        if document_id is not None:
            candidates = [doc_id for doc_id in candidates if doc_id == document_id]
        if retry_ids:
            candidates.sort(key=lambda doc_id: doc_id not in retry_ids)
        return candidates[:limit]

    def create_gateway(*args, **kwargs):
        gateway_config.update(kwargs)
        return gateway

    async def process_document(**kwargs):
        document_id = kwargs["document"].external_id
        processed_ids.append(document_id)
        outcome = result_queue.popleft()
        if outcome == "invoke_gateway":
            await kwargs["gateway"].complete([{"role": "user", "content": "test"}])
            return False
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(extract_runner, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(extract_runner, "make_engine", lambda settings: Engine())
    monkeypatch.setattr(extract_runner, "make_sessionmaker", lambda engine: sessionmaker)
    monkeypatch.setattr(extract_runner, "create_gateway", create_gateway)
    monkeypatch.setattr(extract_runner, "read_prompt", lambda *args: "fake prompt")
    monkeypatch.setattr(extract_runner, "get_pending_documents", get_pending)
    monkeypatch.setattr(extract_runner, "process_document", process_document)
    return processed_ids, gateway_config


@pytest.mark.asyncio
async def test_limit_one_never_attempts_more_than_one_document(monkeypatch, capsys, tmp_path):
    processed_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["first", "second", "third"],
        [False, True, True],
    )

    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=10,
        failures_path=tmp_path / "failures.json",
    )

    assert processed_ids == ["first"]
    assert "Attempted: 1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_batch_limit_counts_successes_and_continues_after_a_failure(
    monkeypatch, capsys, tmp_path
):
    processed_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["first", "second", "third"],
        [False, True, True],
    )

    await extract_runner.run(limit=2, rpm=2, failures_path=tmp_path / "failures.json")

    assert processed_ids == ["first", "second", "third"]
    output = capsys.readouterr().out
    assert "Successful: 2" in output
    assert "Attempted: 3" in output


@pytest.mark.asyncio
async def test_runner_reaches_five_success_target_when_all_documents_succeed(
    monkeypatch, capsys, tmp_path
):
    document_ids = [f"doc-{number}" for number in range(5)]
    processed_ids, _ = patch_runner_for_mocked_run(monkeypatch, document_ids, [True] * 5)

    await extract_runner.run(
        limit=5,
        rpm=2,
        max_attempts=5,
        failures_path=tmp_path / "failures.json",
    )

    assert processed_ids == document_ids
    output = capsys.readouterr().out
    assert "Successful: 5" in output
    assert "Attempted: 5" in output
    assert "Progress: attempt 5/5" in output


@pytest.mark.asyncio
async def test_failed_document_is_prioritized_and_cleared_after_later_success(
    monkeypatch, capsys, tmp_path
):
    failure_path = tmp_path / "failures.json"
    first_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["doc-a", "doc-b"],
        [RuntimeError("private document text must not be printed")],
    )
    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        failures_path=failure_path,
    )

    failure_record = json.loads(failure_path.read_text(encoding="utf-8"))
    assert first_ids == ["doc-a"]
    assert failure_record["doc-a"]["reason"] == "unexpected_error"
    assert "private document text" not in failure_path.read_text(encoding="utf-8")

    second_ids, _ = patch_runner_for_mocked_run(monkeypatch, ["doc-a", "doc-b"], [True])
    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        failures_path=failure_path,
    )

    assert second_ids == ["doc-a"]
    assert json.loads(failure_path.read_text(encoding="utf-8")) == {}
    assert "private document text" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_truncated_document_remains_pending_and_is_recorded_for_resume(
    monkeypatch, capsys, tmp_path
):
    failure_path = tmp_path / "failures.json"
    truncated = LLMOutputTruncatedError(
        "output reached limit",
        finish_reason="length",
        tokens_in=1200,
        tokens_out=2000,
        attempts=3,
    )
    processed_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["truncated-doc", "next-doc"],
        [truncated],
    )

    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        failures_path=failure_path,
    )

    output = capsys.readouterr().out
    failures = json.loads(failure_path.read_text(encoding="utf-8"))
    assert processed_ids == ["truncated-doc"]
    assert failures["truncated-doc"]["reason"] == "output_truncated"
    assert "Successful: 0" in output


@pytest.mark.asyncio
async def test_max_attempts_caps_a_batch_before_success_target(monkeypatch, capsys, tmp_path):
    processed_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["first", "second", "third"],
        [False, False, True],
    )

    await extract_runner.run(
        limit=2,
        rpm=2,
        max_attempts=2,
        failures_path=tmp_path / "failures.json",
    )

    assert processed_ids == ["first", "second"]
    output = capsys.readouterr().out
    assert "Attempted: 2" in output
    assert "Successful: 0" in output


@pytest.mark.asyncio
async def test_targeted_groq_only_run_processes_only_selected_document_once(
    monkeypatch, capsys, tmp_path
):
    processed_ids, gateway_config = patch_runner_for_mocked_run(
        monkeypatch,
        ["other-document", "f-a1716d5b10ab4eaac614", "third-document"],
        [True],
    )

    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        document_id="f-a1716d5b10ab4eaac614",
        groq_only=True,
        failures_path=tmp_path / "failures.json",
    )

    assert processed_ids == ["f-a1716d5b10ab4eaac614"]
    assert gateway_config["groq_only"] is True
    assert gateway_config["max_tokens"] == 2000
    output = capsys.readouterr().out
    assert "Target document: f-a1716d5b10ab4eaac614" in output
    assert "Attempted: 1" in output
    assert "Successful: 1" in output


@pytest.mark.asyncio
async def test_targeted_run_stops_after_failure_without_attempting_another(
    monkeypatch, capsys, tmp_path
):
    processed_ids, _ = patch_runner_for_mocked_run(
        monkeypatch,
        ["target-document", "must-not-run"],
        [RuntimeError("mock extraction failure"), True],
    )

    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        document_id="target-document",
        groq_only=True,
        failures_path=tmp_path / "failures.json",
    )

    assert processed_ids == ["target-document"]
    output = capsys.readouterr().out
    assert "Stopping after the selected document failed." in output
    assert "Attempted: 1" in output
    assert "Failed: 1" in output


@pytest.mark.asyncio
async def test_groq_temporary_failure_opens_circuit_and_stops_batch(monkeypatch, capsys, tmp_path):
    class UnavailableGroq:
        name = "groq"
        model = "openai/gpt-oss-20b"

        async def complete(self, messages, temperature=0.2, response_format=None):
            raise httpx.ConnectError("Groq temporarily unavailable")

        async def stream(self, messages, temperature=0.2):
            yield "unused"

    gateway = LLMGateway(
        primary=UnavailableGroq(),
        circuit_breaker=CircuitBreaker(failure_threshold=1),
    )
    processed_ids, gateway_config = patch_runner_for_mocked_run(
        monkeypatch,
        ["first", "must-not-be-attempted", "also-must-not-be-attempted"],
        ["invoke_gateway", True, True],
        gateway_override=gateway,
    )

    await extract_runner.run(
        limit=4,
        rpm=2,
        max_attempts=4,
        groq_only=True,
        failures_path=tmp_path / "failures.json",
    )

    assert gateway_config["groq_only"] is True
    assert processed_ids == ["first"]
    output = capsys.readouterr().out
    assert "Stopping: all configured providers are unavailable." in output
    assert "Attempted: 1" in output
    assert "Failed: 1" in output


@pytest.mark.asyncio
async def test_runner_displays_only_sanitized_http_400_fields(monkeypatch, capsys, tmp_path):
    request = httpx.Request("POST", "https://example.com/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {}})
    safe_details = {
        "message": "Unsupported parameter max_tokens; echoed text=[REDACTED]",
        "type": "invalid_request_error",
        "code": "unsupported_parameter",
        "param": "max_tokens",
    }
    bad_request = ProviderBadRequestError(
        "HTTP 400 request rejected",
        request=request,
        response=response,
        safe_details=safe_details,
    )
    patch_runner_for_mocked_run(
        monkeypatch,
        ["target-document"],
        [bad_request],
    )

    await extract_runner.run(
        limit=1,
        rpm=2,
        max_attempts=1,
        document_id="target-document",
        groq_only=True,
        failures_path=tmp_path / "failures.json",
    )

    output = capsys.readouterr().out
    assert "HTTP 400 diagnostic fields:" in output
    assert "unsupported_parameter" in output
    assert "max_tokens" in output
    assert "[REDACTED]" in output
    assert "request rejected" not in output


@pytest.mark.asyncio
async def test_runner_displays_sanitized_http_413_fields(monkeypatch, capsys, tmp_path):
    request = httpx.Request("POST", "https://example.com/chat/completions")
    response = httpx.Response(413, request=request, json={"error": {}})
    error = ProviderPayloadTooLargeError(
        "HTTP 413 request rejected",
        request=request,
        response=response,
        safe_details={
            "message": "Request body exceeded [REDACTED]",
            "type": "request_too_large",
            "code": "payload_too_large",
            "param": None,
        },
    )
    patch_runner_for_mocked_run(monkeypatch, ["target-document"], [error])

    await extract_runner.run(
        limit=1,
        rpm=2,
        document_id="target-document",
        groq_only=True,
        failures_path=tmp_path / "failures.json",
    )

    output = capsys.readouterr().out
    assert "HTTP 413" in output
    assert "payload_too_large" in output
    assert "[REDACTED]" in output
    assert "request rejected" not in output


@pytest.mark.asyncio
async def test_process_document_reports_docmeta_validation_failure(capsys):
    document = SimpleNamespace(
        external_id="invalid-doc",
        title="Invalid Metadata Document",
        pages=[SimpleNamespace(page_no=1, text="Some text")],
    )

    class Recorder:
        def callback(self, **kwargs):
            return None

    with pytest.raises(ValueError, match="Metadata extraction failed"):
        await process_document(
            session=SimpleNamespace(),
            document=document,
            gateway=LLMGateway(
                primary=FakeLLM(responses=["not json", "still not json", "nope"]),
            ),
            prompt="Extract metadata.",
            recorder=Recorder(),
        )

    output = capsys.readouterr().out
    assert "Title: Invalid Metadata Document" in output
    assert "DocMeta validation: failed (ValueError)" in output


@pytest.mark.asyncio
async def test_process_document_reports_truncation_details_without_persisting(capsys):
    document = SimpleNamespace(
        external_id="truncated-doc",
        title="Truncated Document",
        pages=[SimpleNamespace(page_no=1, text="A" * 100)],
    )

    class Recorder:
        def callback(self, **kwargs):
            return None

    gateway = LLMGateway(
        primary=FakeLLM(
            responses=['{"doc_kind":"license"'] * 3,
            finish_reasons=["length"] * 3,
        ),
    )
    session = SimpleNamespace(commits=0)

    with pytest.raises(LLMOutputTruncatedError) as error:
        await process_document(
            session=session,
            document=document,
            gateway=gateway,
            prompt="Extract metadata.",
            recorder=Recorder(),
        )

    assert error.value.attempts == 3
    assert session.commits == 0
    output = capsys.readouterr().out
    assert "Output truncation: finish_reason=length completion_tokens=8 attempts=3" in output
    assert "DocMeta validation: failed (LLMOutputTruncatedError)" in output
