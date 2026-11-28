"""The pure parts of the worker: planning, pacing, and getting a file. No Redis, no database."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from arq import Retry

from licenselens.db.models import Document, IngestItem
from licenselens.llm.gateway import CircuitOpenError, ProviderRateLimitedError
from licenselens.workers.ingest import (
    Pace,
    Permanent,
    get_file,
    ingest_document,
    plan,
    process,
    raw_url,
)


def record(n: int, **changes) -> dict:
    base = {
        "url": f"https://x.test/{n}.pdf",
        "source": "x",
        "licence": "CC BY 4.0",
        "via": "http",
        "path": f"data/raw/x/{n}.pdf",
        "sha256": str(n) * 64,
        "status": "downloaded",
    }
    return base | changes


async def no_sleep(seconds: float) -> None:
    pass


def test_plan_takes_what_is_downloaded_new_and_wanted():
    records = [
        record(1),
        record(2, sha256="1" * 64),
        record(3),
        record(4, status="failed"),
        record(5, source="other"),
        record(6),
    ]
    known = {"f-" + "3" * 20}
    assert [r["url"][-5:] for r in plan(records, known)] == ["1.pdf", "5.pdf", "6.pdf"]
    assert [r["url"][-5:] for r in plan(records, known, source="x")] == ["1.pdf", "6.pdf"]
    assert len(plan(records, known, limit=2)) == 2
    assert known == {"f-" + "3" * 20}


def test_raw_url_points_a_git_record_at_the_exact_commit():
    git = record(
        1,
        via="git",
        commit="05e6df7",
        url="https://github.com/fastapi/fastapi/blob/HEAD/docs/en/docs/index.md",
    )
    wanted = "https://raw.githubusercontent.com/fastapi/fastapi/05e6df7/docs/en/docs/index.md"
    assert raw_url(git) == wanted
    assert raw_url(record(2)) == "https://x.test/2.pdf"


async def test_pace_spreads_calls_evenly():
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    pace = Pace(per_minute=60, clock=lambda: 100.0, sleep=sleep)
    for _ in range(3):
        await pace.wait()
    assert slept == [1.0, 2.0]


async def test_a_file_on_disk_is_used_and_a_hand_downloaded_one_is_never_fetched(tmp_path):
    here = tmp_path / "rules.md"
    here.write_text("# Rules", encoding="utf-8")
    assert await get_file(record(1, path=str(here)), tmp_path, client=None) == here
    by_hand = record(2, via="manual", path=str(tmp_path / "not-here.pdf"))
    with pytest.raises(Permanent, match="by hand"):
        await get_file(by_hand, tmp_path, client=None)


async def test_a_missing_file_is_downloaded_and_errors_are_sorted_into_two_kinds(tmp_path):
    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        status = {"/1.pdf": 200, "/2.pdf": 404, "/3.pdf": 503}.get(request.url.path, 200)
        return httpx.Response(status, content=b"%PDF-1.4 hello")

    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
        got = await get_file(record(1), tmp_path, client, no_sleep)
        assert got.read_bytes() == b"%PDF-1.4 hello" and tmp_path in got.parents
        with pytest.raises(Permanent, match="404"):
            await get_file(record(2), tmp_path, client, no_sleep)
        with pytest.raises(RuntimeError, match="503"):
            await get_file(record(3), tmp_path, client, no_sleep)
        blocked = record(4, url="https://x.test/private/4.pdf")
        with pytest.raises(Permanent, match="robots"):
            await get_file(blocked, tmp_path, client, no_sleep)


async def test_permanent_extraction_failure_preserves_document_metadata_and_fails_item():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})
    document = Document(
        external_id="f-12345",
        title="Test License",
        metadata_={"pages": 1},
    )

    rollbacks = 0
    commits = 0

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            nonlocal commits
            commits += 1

        async def rollback(self):
            nonlocal rollbacks
            rollbacks += 1

    sessionmaker = MockSession
    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": sessionmaker,
    }

    async def fake_process(_ctx, session, _item):
        document.metadata_["extract"] = {"prompt": "extract_v1", "error": "validation failed"}
        raise Permanent("extract: validation failed")

    with patch("licenselens.workers.ingest.process", side_effect=fake_process):
        result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert item.status == "failed"
    assert "extract: validation failed" in item.error
    assert rollbacks == 0
    assert document.metadata_["extract"]["error"] == "validation failed"


async def test_transient_failure_triggers_retry_and_rolls_back():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})
    rollbacks = 0
    commits = 0
    get_calls = 0

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            nonlocal get_calls
            if model is IngestItem and ident == 42:
                get_calls += 1
                return item
            return None

        async def commit(self):
            nonlocal commits
            commits += 1

        async def rollback(self):
            nonlocal rollbacks
            rollbacks += 1

    sessionmaker = MockSession
    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": sessionmaker,
    }

    async def fake_process(_ctx, session, _item):
        raise RuntimeError("temporary LLM 503")

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 30000
    assert item.status == "queued"
    assert "try 1 failed: RuntimeError: temporary LLM 503" in item.error
    assert rollbacks == 1
    assert get_calls == 2


async def test_transient_failure_handles_deleted_item_after_rollback():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})
    get_calls = 0

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            nonlocal get_calls
            if model is IngestItem and ident == 42:
                get_calls += 1
                if get_calls == 1:
                    return item
                return None
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    sessionmaker = MockSession
    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": sessionmaker,
    }

    async def fake_process(_ctx, session, _item):
        raise RuntimeError("transient network drop")

    with patch("licenselens.workers.ingest.process", side_effect=fake_process):
        result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert get_calls == 2


async def test_process_extraction_failure_updates_metadata_and_commits(tmp_path):
    doc_path = tmp_path / "test.txt"
    doc_path.write_text("dummy license text", encoding="utf-8")

    document = Document(
        external_id="f-" + "a" * 20,
        title="Test License",
        metadata_={"pages": 1, "extract": {}},
    )

    commits = 0

    class MockSession:
        async def scalar(self, stmt):
            return document

        async def flush(self):
            pass

        async def commit(self):
            nonlocal commits
            commits += 1

    item = IngestItem(
        id=1,
        status="running",
        record={"path": str(doc_path), "url": "https://x.test/1.txt", "source": "x"},
    )
    ctx = {
        "http": None,
        "pace": SimpleNamespace(wait=AsyncMock()),
        "gateway": None,
        "extract": {"version": "extract_v1", "prompt": "extract prompt"},
    }

    with (
        patch("licenselens.workers.ingest.combine_document_text", return_value="some text"),
        patch(
            "licenselens.workers.ingest.extract_metadata_from_chunks",
            side_effect=ValueError("invalid spdx"),
        ),
        pytest.raises(Permanent, match="extract: invalid spdx"),
    ):
        await process(ctx, MockSession(), item)

    assert commits == 1
    assert document.metadata_["extract"]["error"] == "invalid spdx"
    assert document.metadata_["extract"]["prompt"] == "extract_v1"


def test_has_provider_checks():
    from licenselens.config import Settings
    from licenselens.workers.main import _has_provider

    # No keys
    s_empty = Settings(
        app_env="ci",
        database_url="postgresql+asyncpg://localhost:5432/testdb",
        groq_api_key="",
        gemini_api_key="",
        _env_file=None,
    )
    mock_gateway = SimpleNamespace(primary=SimpleNamespace(api_key=""))
    assert not _has_provider(mock_gateway, s_empty)

    # Groq key
    s_groq = Settings(
        app_env="ci",
        database_url="postgresql+asyncpg://localhost:5432/testdb",
        groq_api_key="gsk-123",  # pragma: allowlist secret
        gemini_api_key="",
        _env_file=None,
    )
    assert _has_provider(mock_gateway, s_groq)

    # Gemini key
    s_gemini = Settings(
        app_env="ci",
        database_url="postgresql+asyncpg://localhost:5432/testdb",
        groq_api_key="",
        gemini_api_key="aiza-123",  # pragma: allowlist secret
        _env_file=None,
    )
    assert _has_provider(mock_gateway, s_gemini)

    # Mock gateway with test provider (no api_key attribute)
    mock_test_gateway = SimpleNamespace(primary=SimpleNamespace())
    assert _has_provider(mock_test_gateway, s_empty)


async def test_startup_initializes_cleanly_with_real_gateway():
    from licenselens.config import Settings
    from licenselens.workers.main import startup

    settings = Settings(
        app_env="ci",
        groq_api_key="",
        gemini_api_key="",
        database_url="postgresql+asyncpg://localhost:5432/testdb",
        _env_file=None,
    )
    ctx = {"settings": settings, "embedded": True, "redis": AsyncMock()}

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with (
        patch("licenselens.workers.main.make_engine", return_value=AsyncMock()),
        patch("licenselens.workers.main.make_sessionmaker", return_value=lambda: DummySession()),
        patch("licenselens.workers.main.Queue.put", new_callable=AsyncMock, return_value=0),
        patch(
            "licenselens.workers.main.IngestRepository.recover_crashed_items",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch(
            "licenselens.workers.main.IngestRepository.open_item_ids",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await startup(ctx)

    assert "gateway" in ctx
    assert ctx["gateway"].primary.timeout == 12.0
    assert ctx["gateway"].primary.max_retries == 0
    assert ctx["gateway"].fallback.timeout == 12.0
    assert ctx["gateway"].fallback.max_retries == 0
    assert ctx["extract"] is None  # No API keys configured


async def test_cancellation_resets_running_item_to_queued_and_reraises():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_process(_ctx, session, _item):
        raise asyncio.CancelledError()

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_process),
        pytest.raises(asyncio.CancelledError),
    ):
        await ingest_document(ctx, 42)

    assert item.status == "queued"
    assert item.attempts == 1
    assert "try 1 interrupted (job cancelled or timed out)" in item.error


async def test_cancellation_at_max_attempts_marks_item_failed():
    item = IngestItem(id=42, status="queued", attempts=2, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_process(_ctx, session, _item):
        raise asyncio.CancelledError()

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_process),
        pytest.raises(asyncio.CancelledError),
    ):
        await ingest_document(ctx, 42)

    assert item.status == "failed"
    assert item.attempts == 3
    assert "gave up after 3 tries: job cancelled or timed out" in item.error


async def test_cancellation_cleanup_survives_db_error_and_still_reraises():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})
    call_count = 0

    class FirstSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class FailingSession:
        async def __aenter__(self):
            raise RuntimeError("Database connection dropped during cancellation")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def session_factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FirstSession()
        return FailingSession()

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": session_factory,
    }

    async def fake_process(_ctx, session, _item):
        raise asyncio.CancelledError()

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_process),
        pytest.raises(asyncio.CancelledError),
    ):
        await ingest_document(ctx, 42)


async def test_provider_rate_limited_defers_arq_retry_by_60_seconds():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    dummy_request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")
    dummy_response = httpx.Response(429, request=dummy_request)
    rate_err = ProviderRateLimitedError(
        "rate limited", request=dummy_request, response=dummy_response
    )

    async def fake_process(_ctx, session, _item):
        raise rate_err

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 60000
    assert item.status == "queued"
    assert "ProviderRateLimitedError" in item.error


async def test_max_attempts_preserves_existing_error():
    item = IngestItem(
        id=42,
        status="queued",
        attempts=3,
        error="try 2 failed: ProviderRateLimitedError: 429 Too Many Requests",
        record={"url": "https://x.test/1.pdf"},
    )

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert item.status == "failed"
    assert "gave up: started 3 times and never finished" in item.error
    assert "ProviderRateLimitedError: 429 Too Many Requests" in item.error


async def test_max_attempts_without_previous_error():
    item = IngestItem(
        id=42,
        status="queued",
        attempts=3,
        error="",
        record={"url": "https://x.test/1.pdf"},
    )

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert item.status == "failed"
    assert item.error == "gave up: started 3 times and never finished"


async def test_processing_deadline_timeout_commits_queued_before_arq_retry():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})
    commits = 0

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            nonlocal commits
            commits += 1

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_slow_process(_ctx, session, _item):
        raise TimeoutError("processing took longer than 45s")

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_slow_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 30000
    assert item.status == "queued"
    assert "TimeoutError" in item.error
    assert commits >= 2  # initial "running" commit + post-rollback "queued" commit


async def test_processing_deadline_timeout_at_max_tries_marks_failed():
    item = IngestItem(id=42, status="queued", attempts=2, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_slow_process(_ctx, session, _item):
        raise TimeoutError("processing took longer than 45s")

    with patch("licenselens.workers.ingest.process", side_effect=fake_slow_process):
        result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert item.status == "failed"
    assert "gave up after 3 tries" in item.error
    assert "TimeoutError" in item.error


async def test_circuit_open_error_defers_past_recovery_window():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_circuit_process(_ctx, session, _item):
        raise CircuitOpenError("gemini circuit is open")

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_circuit_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 35000  # 35 seconds in ms
    assert item.status == "queued"
    assert item.attempts == 1
    assert "CircuitOpenError: gemini circuit is open" in item.error


async def test_circuit_open_error_second_attempt_defers_60s():
    item = IngestItem(id=42, status="queued", attempts=1, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_circuit_process(_ctx, session, _item):
        raise CircuitOpenError("gemini circuit is open")

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_circuit_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 60000  # 60 seconds in ms
    assert item.status == "queued"
    assert item.attempts == 2


async def test_circuit_open_error_at_max_tries_marks_failed():
    item = IngestItem(id=42, status="queued", attempts=2, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_circuit_process(_ctx, session, _item):
        raise CircuitOpenError("gemini circuit is open")

    with patch("licenselens.workers.ingest.process", side_effect=fake_circuit_process):
        result = await ingest_document(ctx, 42)

    assert result == "failed"
    assert item.status == "failed"
    assert item.attempts == 3
    assert "gave up after 3 tries" in item.error
    assert "CircuitOpenError: gemini circuit is open" in item.error


async def test_rate_limited_error_respects_retry_after():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_rate_limited_process(_ctx, session, _item):
        req = httpx.Request("POST", "https://api.groq.com")
        resp = httpx.Response(429, request=req)
        raise ProviderRateLimitedError("rate limit", request=req, response=resp, retry_after=45.2)

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_rate_limited_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 46000  # math.ceil(45.2) * 1000 == 46000 ms
    assert item.status == "queued"
    assert item.attempts == 1


async def test_standard_error_defers_attempt_times_30():
    item = IngestItem(id=42, status="queued", attempts=0, record={"url": "https://x.test/1.pdf"})

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, model, ident):
            if model is IngestItem and ident == 42:
                return item
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

    ctx = {
        "settings": SimpleNamespace(ingest_max_tries=3),
        "sessionmaker": MockSession,
    }

    async def fake_error_process(_ctx, session, _item):
        raise RuntimeError("network glitch")

    with (
        patch("licenselens.workers.ingest.process", side_effect=fake_error_process),
        pytest.raises(Retry) as exc_info,
    ):
        await ingest_document(ctx, 42)

    assert exc_info.value.defer_score == 30000  # 1 * 30 * 1000 == 30000 ms
    assert item.status == "queued"
    assert item.attempts == 1


@pytest.mark.asyncio
async def test_queue_put_clears_stale_orphan_keys_before_enqueueing():
    from licenselens.workers.queue import Queue

    deleted_keys = []
    enqueued_jobs = []

    class MockPool:
        default_queue_name = "arq:queue"

        async def zscore(self, queue, job_id):
            return None

        async def exists(self, key):
            return False

        async def delete(self, *keys):
            deleted_keys.extend(keys)

        async def enqueue_job(self, function, *args, **kwargs):
            enqueued_jobs.append((function, args, kwargs))
            return SimpleNamespace(job_id=kwargs.get("_job_id"))

    queue = Queue(MockPool())
    count = await queue.put([10])

    assert count == 1
    assert deleted_keys == [
        "arq:job:ingest:10",
        "arq:result:ingest:10",
        "arq:retry:ingest:10",
    ]
    assert len(enqueued_jobs) == 1
    assert enqueued_jobs[0][0] == "ingest_document"
    assert enqueued_jobs[0][1] == (10,)
    assert enqueued_jobs[0][2] == {"_job_id": "ingest:10"}


@pytest.mark.asyncio
async def test_queue_put_does_not_duplicate_item_already_in_queue():
    from licenselens.workers.queue import Queue

    enqueued_jobs = []

    class MockPool:
        default_queue_name = "arq:queue"

        async def zscore(self, queue, job_id):
            if job_id == "ingest:10":
                return 12345.0
            return None

        async def exists(self, key):
            return False

        async def delete(self, *keys):
            pass

        async def enqueue_job(self, function, *args, **kwargs):
            enqueued_jobs.append((function, args, kwargs))
            return SimpleNamespace(job_id=kwargs.get("_job_id"))

    queue = Queue(MockPool())
    count = await queue.put([10])

    assert count == 0
    assert enqueued_jobs == []


@pytest.mark.asyncio
async def test_queue_put_does_not_interrupt_active_in_progress_job():
    from licenselens.workers.queue import Queue

    enqueued_jobs = []

    class MockPool:
        default_queue_name = "arq:queue"

        async def zscore(self, queue, job_id):
            return None

        async def exists(self, key):
            return key == "arq:in-progress:ingest:10"

        async def delete(self, *keys):
            pass

        async def enqueue_job(self, function, *args, **kwargs):
            enqueued_jobs.append((function, args, kwargs))
            return SimpleNamespace(job_id=kwargs.get("_job_id"))

    queue = Queue(MockPool())
    count = await queue.put([10])

    assert count == 0
    assert enqueued_jobs == []


@pytest.mark.asyncio
async def test_worker_startup_recovers_crashed_items_and_requeues_open(tmp_path):
    from licenselens.workers.main import startup

    recovered_calls = []
    open_calls = []

    class MockRepo:
        def __init__(self, session):
            self.session = session

        async def recover_crashed_items(self, max_tries, redis=None):
            recovered_calls.append((max_tries, redis))
            return ([46], [20])

        async def open_item_ids(self):
            open_calls.append(True)
            return [20]

    class MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_redis = SimpleNamespace(
        default_queue_name="arq:queue",
        zscore=AsyncMock(return_value=None),
        exists=AsyncMock(return_value=False),
        delete=AsyncMock(),
        enqueue_job=AsyncMock(return_value=SimpleNamespace(job_id="ingest:20")),
    )

    ctx = {
        "settings": SimpleNamespace(
            log_level="INFO",
            ingest_max_tries=3,
            ingest_rpm=60,
            prompts_dir=tmp_path,
            groq_api_key=None,
            gemini_api_key=None,
            database_url="sqlite+aiosqlite:///:memory:",
            db_pool_size=1,
            db_max_overflow=0,
        ),
        "embedded": True,
        "redis": mock_redis,
    }

    with (
        patch("licenselens.workers.main.make_engine"),
        patch("licenselens.workers.main.make_sessionmaker", return_value=lambda: MockSession()),
        patch("licenselens.workers.main.create_gateway"),
        patch("licenselens.workers.main.make_client"),
        patch("licenselens.workers.main.IngestRepository", MockRepo),
    ):
        await startup(ctx)

    assert len(recovered_calls) == 1
    assert recovered_calls[0] == (3, mock_redis)
    assert len(open_calls) == 1
    mock_redis.enqueue_job.assert_awaited_once_with("ingest_document", 20, _job_id="ingest:20")
