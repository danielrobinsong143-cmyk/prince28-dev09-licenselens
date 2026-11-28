"""Unit tests for IngestRepository dead-letter resolution and deduplication."""

from datetime import UTC, datetime

import pytest

from licenselens.db.ingest_jobs import IngestRepository, _is_valid_sha256
from licenselens.db.models import Document, IngestItem, ParseFailure
from licenselens.ingest.store import document_id

VALID_SHA_1 = "a" * 64
VALID_SHA_2 = "b" * 64
VALID_SHA_3 = "c" * 64


class MockSession:
    """Mock session supporting IngestRepository queries without a real database."""

    def __init__(self, documents=None, parse_failures=None, ingest_items=None):
        self.documents = documents or []
        self.parse_failures = parse_failures or []
        self.ingest_items = ingest_items or []
        self.committed = False
        self.last_stmt = None

    async def commit(self):
        self.committed = True

    async def scalars(self, stmt):
        self.last_stmt = stmt
        desc = getattr(stmt, "column_descriptions", None)
        col_name = desc[0]["name"] if desc else ""
        if col_name == "external_id":
            return [d.external_id for d in self.documents if d.doc_type != "record"]
        if col_name == "sha256":
            return [pf.sha256 for pf in self.parse_failures]
        if col_name == "record":
            sorted_items = sorted(self.ingest_items, key=lambda x: x.updated_at, reverse=True)
            return [item.record for item in sorted_items if item.status == "failed"]
        if col_name == "id":
            return [
                item.id
                for item in sorted(self.ingest_items, key=lambda x: x.id)
                if item.status in ("queued", "running")
            ]
        if col_name == "IngestItem" or "ingest_items" in str(stmt):
            params = stmt.compile().params
            if "running" in params.values():
                return [
                    item
                    for item in sorted(self.ingest_items, key=lambda x: x.id)
                    if item.status == "running"
                ]
            sorted_items = sorted(self.ingest_items, key=lambda x: x.updated_at, reverse=True)
            items = [item for item in sorted_items if item.status == "failed"]
            for k, v in params.items():
                if "job_id" in k:
                    items = [item for item in items if item.job_id == v]
            return items
        return []


def test_is_valid_sha256():
    assert _is_valid_sha256(VALID_SHA_1) is True
    assert _is_valid_sha256("A" * 64) is True
    assert _is_valid_sha256("0123456789abcdef" * 4) is True

    # Invalid lengths
    assert _is_valid_sha256("a" * 63) is False
    assert _is_valid_sha256("a" * 65) is False
    assert _is_valid_sha256("") is False

    # Non-hex characters
    assert _is_valid_sha256("g" * 64) is False
    assert _is_valid_sha256("a" * 63 + "!") is False

    # Non-string types
    assert _is_valid_sha256(None) is False
    assert _is_valid_sha256(12345) is False
    assert _is_valid_sha256({"sha256": VALID_SHA_1}) is False


@pytest.mark.asyncio
async def test_failed_document_without_successful_retry_appears_globally():
    item = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="parse error",
        record={"url": "https://x.test/unresolved.pdf", "sha256": VALID_SHA_1},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    repo = IngestRepository(MockSession(ingest_items=[item]))

    dead = await repo.dead_letter()
    assert len(dead) == 1
    assert dead[0]["item"] == 1
    assert dead[0]["url"] == "https://x.test/unresolved.pdf"

    failed = await repo.failed_records()
    assert len(failed) == 1
    assert failed[0]["url"] == "https://x.test/unresolved.pdf"


@pytest.mark.asyncio
async def test_successfully_retried_document_disappears_from_global_dead_letter():
    # Document is now in the database
    doc = Document(
        id=10,
        external_id=document_id(VALID_SHA_1),
        doc_type="pdf",
        sha256=VALID_SHA_1,
    )
    # Old failed item
    item = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="TimeoutError",
        record={"url": "https://x.test/retry.pdf", "sha256": VALID_SHA_1},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    repo = IngestRepository(MockSession(documents=[doc], ingest_items=[item]))

    # Must NOT appear in global dead-letter
    dead = await repo.dead_letter()
    assert dead == []

    # Must NOT appear in failed_records()
    failed = await repo.failed_records()
    assert failed == []


@pytest.mark.asyncio
async def test_per_job_dead_letter_still_shows_historical_failures():
    # Document was resolved later
    doc = Document(
        id=10,
        external_id=document_id(VALID_SHA_1),
        doc_type="pdf",
        sha256=VALID_SHA_1,
    )
    item = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="initial timeout",
        record={"url": "https://x.test/retry.pdf", "sha256": VALID_SHA_1},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    repo = IngestRepository(MockSession(documents=[doc], ingest_items=[item]))

    # When querying specific job_id, historical failure MUST remain visible
    job_dead = await repo.dead_letter(job_id="job1")
    assert len(job_dead) == 1
    assert job_dead[0]["item"] == 1
    assert job_dead[0]["job_id"] == "job1"
    assert job_dead[0]["error"] == "initial timeout"

    # Querying a different job_id returns empty
    assert await repo.dead_letter(job_id="other_job") == []


@pytest.mark.asyncio
async def test_identical_content_at_different_urls_treated_as_resolved():
    # Content VALID_SHA_1 is ingested
    doc = Document(
        id=10,
        external_id=document_id(VALID_SHA_1),
        doc_type="pdf",
        sha256=VALID_SHA_1,
    )
    # Two failed items with identical content hash but different URLs
    item1 = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="err1",
        record={"url": "https://x.test/mirror-a.pdf", "sha256": VALID_SHA_1},
        updated_at=datetime(2026, 11, 28, 9, 0, tzinfo=UTC),
    )
    item2 = IngestItem(
        id=2,
        job_id="job2",
        status="failed",
        attempts=3,
        error="err2",
        record={"url": "https://x.test/mirror-b.pdf", "sha256": VALID_SHA_1},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    repo = IngestRepository(MockSession(documents=[doc], ingest_items=[item1, item2]))

    # Both are resolved because content exists in documents
    assert await repo.dead_letter() == []
    assert await repo.failed_records() == []


@pytest.mark.asyncio
async def test_missing_and_malformed_hashes_remain_visible():
    item_missing = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="no hash",
        record={"url": "https://x.test/missing.pdf"},
        updated_at=datetime(2026, 11, 28, 8, 0, tzinfo=UTC),
    )
    item_short = IngestItem(
        id=2,
        job_id="job1",
        status="failed",
        attempts=3,
        error="short hash",
        record={"url": "https://x.test/short.pdf", "sha256": "abcdef"},
        updated_at=datetime(2026, 11, 28, 9, 0, tzinfo=UTC),
    )
    item_non_hex = IngestItem(
        id=3,
        job_id="job1",
        status="failed",
        attempts=3,
        error="non-hex hash",
        record={"url": "https://x.test/nonhex.pdf", "sha256": "z" * 64},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    # Even if known_ids has documents
    doc = Document(id=1, external_id="f-" + "a" * 20, doc_type="pdf", sha256=VALID_SHA_1)
    repo = IngestRepository(
        MockSession(documents=[doc], ingest_items=[item_missing, item_short, item_non_hex])
    )

    dead = await repo.dead_letter()
    assert len(dead) == 3
    urls_dead = {d["url"] for d in dead}
    assert urls_dead == {
        "https://x.test/missing.pdf",
        "https://x.test/short.pdf",
        "https://x.test/nonhex.pdf",
    }

    failed = await repo.failed_records()
    assert len(failed) == 3
    urls_failed = {f["url"] for f in failed}
    assert urls_failed == urls_dead


@pytest.mark.asyncio
async def test_duplicate_urls_retain_the_newest_failed_record():
    # Two failed records for the same URL with different timestamps
    older_record = {
        "url": "https://x.test/dup.pdf",
        "sha256": VALID_SHA_1,
        "title": "Older Record",
    }
    newer_record = {
        "url": "https://x.test/dup.pdf",
        "sha256": VALID_SHA_2,
        "title": "Newer Record",
    }

    item_older = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="old error",
        record=older_record,
        updated_at=datetime(2026, 11, 28, 8, 0, tzinfo=UTC),
    )
    item_newer = IngestItem(
        id=2,
        job_id="job2",
        status="failed",
        attempts=3,
        error="new error",
        record=newer_record,
        updated_at=datetime(2026, 11, 28, 11, 0, tzinfo=UTC),
    )

    repo = IngestRepository(MockSession(ingest_items=[item_older, item_newer]))

    # failed_records() must deduplicate by URL and retain the newest record
    failed = await repo.failed_records()
    assert len(failed) == 1
    assert failed[0]["title"] == "Newer Record"
    assert failed[0]["sha256"] == VALID_SHA_2


@pytest.mark.asyncio
async def test_newest_record_resolved_excludes_url():
    # If the newest record for a URL was resolved, the URL should not be retried
    older_unresolved_record = {
        "url": "https://x.test/dup.pdf",
        "sha256": VALID_SHA_1,
        "title": "Older",
    }
    newer_resolved_record = {
        "url": "https://x.test/dup.pdf",
        "sha256": VALID_SHA_2,
        "title": "Newer",
    }

    item_older = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="old error",
        record=older_unresolved_record,
        updated_at=datetime(2026, 11, 28, 8, 0, tzinfo=UTC),
    )
    item_newer = IngestItem(
        id=2,
        job_id="job2",
        status="failed",
        attempts=3,
        error="new error",
        record=newer_resolved_record,
        updated_at=datetime(2026, 11, 28, 11, 0, tzinfo=UTC),
    )
    doc = Document(
        id=10,
        external_id=document_id(VALID_SHA_2),
        doc_type="pdf",
        sha256=VALID_SHA_2,
    )

    repo = IngestRepository(MockSession(documents=[doc], ingest_items=[item_older, item_newer]))
    failed = await repo.failed_records()
    assert failed == []


@pytest.mark.asyncio
async def test_parse_failure_sha_treated_as_resolved():
    # A ParseFailure represents a file permanently given up on
    pf = ParseFailure(
        id=1,
        path="data/raw/x/bad.pdf",
        sha256=VALID_SHA_3,
        reason="corrupt pdf",
    )
    item = IngestItem(
        id=1,
        job_id="job1",
        status="failed",
        attempts=3,
        error="parse error",
        record={"url": "https://x.test/corrupt.pdf", "sha256": VALID_SHA_3},
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    repo = IngestRepository(MockSession(parse_failures=[pf], ingest_items=[item]))

    assert await repo.dead_letter() == []
    assert await repo.failed_records() == []


@pytest.mark.asyncio
async def test_open_item_ids_returns_queued_and_running_items():
    items = [
        IngestItem(id=1, status="queued"),
        IngestItem(id=2, status="running"),
        IngestItem(id=3, status="done"),
        IngestItem(id=4, status="failed"),
        IngestItem(id=5, status="skipped"),
    ]
    repo = IngestRepository(MockSession(ingest_items=items))
    assert await repo.open_item_ids() == [1, 2]


@pytest.mark.asyncio
async def test_recovery_marks_item_at_max_tries_as_failed_and_preserves_error():
    item = IngestItem(
        id=46,
        status="running",
        attempts=3,
        error="try 2 failed: CircuitOpenError: gemini circuit is open",
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    session = MockSession(ingest_items=[item])
    repo = IngestRepository(session)

    failed_ids, requeued_ids = await repo.recover_crashed_items(max_tries=3)

    assert failed_ids == [46]
    assert requeued_ids == []
    assert item.status == "failed"
    assert item.error == (
        "gave up: started 3 times and never finished; "
        "last error: try 2 failed: CircuitOpenError: gemini circuit is open"
    )
    assert session.committed is True


@pytest.mark.asyncio
async def test_recovery_without_previous_error_sets_default_failed_message():
    item = IngestItem(
        id=10,
        status="running",
        attempts=3,
        error=None,
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    session = MockSession(ingest_items=[item])
    repo = IngestRepository(session)

    failed_ids, requeued_ids = await repo.recover_crashed_items(max_tries=3)

    assert failed_ids == [10]
    assert requeued_ids == []
    assert item.status == "failed"
    assert item.error == "gave up: started 3 times and never finished"


@pytest.mark.asyncio
async def test_recovery_makes_item_below_max_tries_eligible_for_retry():
    item = IngestItem(
        id=20,
        status="running",
        attempts=1,
        error=None,
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    session = MockSession(ingest_items=[item])
    repo = IngestRepository(session)

    failed_ids, requeued_ids = await repo.recover_crashed_items(max_tries=3)

    assert failed_ids == []
    assert requeued_ids == [20]
    assert item.status == "queued"
    assert item.error == "try 1 interrupted (worker restarted)"
    assert session.committed is True


@pytest.mark.asyncio
async def test_recovery_is_idempotent_when_executed_repeatedly():
    item_failed = IngestItem(
        id=30,
        status="running",
        attempts=3,
        error="gemini circuit is open",
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    item_requeued = IngestItem(
        id=31,
        status="running",
        attempts=1,
        error=None,
        updated_at=datetime(2026, 11, 28, 10, 0, tzinfo=UTC),
    )
    session = MockSession(ingest_items=[item_failed, item_requeued])
    repo = IngestRepository(session)

    # First run
    f1, r1 = await repo.recover_crashed_items(max_tries=3)
    assert f1 == [30]
    assert r1 == [31]
    assert item_failed.status == "failed"
    assert item_requeued.status == "queued"
    assert (
        item_failed.error
        == "gave up: started 3 times and never finished; last error: gemini circuit is open"
    )
    assert item_requeued.error == "try 1 interrupted (worker restarted)"

    # Second run (items are now "failed" and "queued", not "running")
    f2, r2 = await repo.recover_crashed_items(max_tries=3)
    assert f2 == []
    assert r2 == []
    assert item_failed.status == "failed"
    assert item_requeued.status == "queued"
    assert (
        item_failed.error
        == "gave up: started 3 times and never finished; last error: gemini circuit is open"
    )
    assert item_requeued.error == "try 1 interrupted (worker restarted)"


@pytest.mark.asyncio
async def test_recovery_does_not_modify_done_skipped_or_failed_items():
    done = IngestItem(id=1, status="done", attempts=1, error=None)
    skipped = IngestItem(id=2, status="skipped", attempts=0, error=None)
    failed = IngestItem(id=3, status="failed", attempts=3, error="original error")

    session = MockSession(ingest_items=[done, skipped, failed])
    repo = IngestRepository(session)

    failed_ids, requeued_ids = await repo.recover_crashed_items(max_tries=3)
    assert failed_ids == []
    assert requeued_ids == []
    assert done.status == "done"
    assert skipped.status == "skipped"
    assert failed.status == "failed"
    assert failed.error == "original error"


@pytest.mark.asyncio
async def test_recovery_skips_item_actively_in_progress_in_redis():
    item = IngestItem(
        id=50,
        status="running",
        attempts=3,
        error="previous attempt error",
    )

    class MockRedisActive:
        async def exists(self, key):
            if key == "arq:in-progress:ingest:50":
                return 1
            return 0

        async def zscore(self, queue, job_id):
            return None

    session = MockSession(ingest_items=[item])
    repo = IngestRepository(session)

    failed_ids, requeued_ids = await repo.recover_crashed_items(
        max_tries=3,
        redis=MockRedisActive(),
    )
    assert failed_ids == []
    assert requeued_ids == []
    assert item.status == "running"
    assert item.error == "previous attempt error"


@pytest.mark.asyncio
async def test_recovery_query_uses_for_update_skip_locked():
    session = MockSession(ingest_items=[])
    repo = IngestRepository(session)
    await repo.recover_crashed_items(max_tries=3)

    assert session.last_stmt is not None
    assert getattr(session.last_stmt, "_for_update_arg", None) is not None
    assert session.last_stmt._for_update_arg.skip_locked is True
