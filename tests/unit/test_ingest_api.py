"""POST /v1/ingest and its progress endpoints, with a fake queue and a fake job table."""

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from licenselens.api.deps import get_ingest_repo, get_manifest
from licenselens.api.main import create_app
from licenselens.config import Settings

TOKEN = {"X-Admin-Token": "test-token"}  # pragma: allowlist secret


def test_start_answers_202_and_queues_one_job_per_new_document(client, queue, ingest_repo):
    r = client.post("/v1/ingest", json={"limit": 10}, headers=TOKEN)
    assert r.status_code == 202
    body = r.json()
    assert body == {"job_id": "job1", "queued": 2, "status_url": "/v1/ingest/job1"}
    assert queue.items == [1, 2]
    urls = [record["url"] for record in ingest_repo.jobs["job1"]]
    assert urls == ["https://x.test/1.pdf", "https://x.test/2.pdf"]


def test_start_needs_the_admin_token(client, queue):
    assert client.post("/v1/ingest", json={}).status_code == 401
    wrong = client.post("/v1/ingest", json={}, headers={"X-Admin-Token": "guess"})
    assert wrong.status_code == 401 and wrong.headers["content-type"] == "application/problem+json"
    assert queue.items == []


def test_without_a_configured_token_ingestion_is_closed_outside_your_laptop():
    settings = Settings(
        app_env="production",
        database_url="postgresql://localhost:5432/unused",
        admin_token="",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as public:
        assert public.post("/v1/ingest", json={}).status_code == 403


def test_nothing_new_is_a_plain_200(client, queue, ingest_repo):
    ingest_repo.known |= {"f-" + "1" * 20, "f-" + "2" * 20}
    r = client.post("/v1/ingest", json={}, headers=TOKEN)
    assert r.status_code == 200 and r.json()["queued"] == 0 and queue.items == []


def test_limit_and_source_are_respected_and_validated(client, queue):
    r = client.post("/v1/ingest", json={"limit": 1}, headers=TOKEN)
    assert r.json()["queued"] == 1
    r = client.post("/v1/ingest", json={"source": "somewhere-else"}, headers=TOKEN)
    assert r.status_code == 200 and r.json()["queued"] == 0
    assert client.post("/v1/ingest", json={"limit": 0}, headers=TOKEN).status_code == 422


def test_retry_failed_queues_the_dead_letter_list_again(client, queue, ingest_repo):
    r = client.post("/v1/ingest", json={"retry_failed": True}, headers=TOKEN)
    assert r.status_code == 202 and r.json()["queued"] == 1
    assert ingest_repo.jobs["job1"][0]["url"] == "https://x.test/bad.pdf"


def test_progress_is_public_and_an_unknown_job_is_a_404_problem(client):
    job_id = client.post("/v1/ingest", json={}, headers=TOKEN).json()["job_id"]
    r = client.get(f"/v1/ingest/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id
    assert body["status"] == "working"
    assert body["requested"] == 2
    assert body["counts"]["queued"] == 2
    assert "pages" in body
    assert "seconds" in body
    assert "created_at" in body
    assert "dead_letter" in body
    missing = client.get("/v1/ingest/no-such-job")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"


def test_dead_letter_lists_what_failed_for_good(client):
    r = client.get("/v1/ingest/dead-letter")
    assert r.status_code == 200
    assert r.json() == {
        "count": 1,
        "items": [
            {
                "item": 7,
                "job_id": "old",
                "url": "https://x.test/bad.pdf",
                "path": "data/raw/x/bad.pdf",
                "attempts": 3,
                "error": "parse: corrupt",
            }
        ],
    }


def test_when_redis_is_away_starting_is_a_503_and_the_rest_still_works(settings, ingest_repo):
    settings.redis_url = "redis://127.0.0.1:1"
    app = create_app(settings)
    app.dependency_overrides[get_ingest_repo] = lambda: ingest_repo
    app.dependency_overrides[get_manifest] = lambda: [
        {"url": "https://x.test/9.pdf", "sha256": "9" * 64, "status": "downloaded", "source": "x"}
    ]
    with TestClient(app) as lonely:
        r = lonely.post("/v1/ingest", json={}, headers=TOKEN)
        assert r.status_code == 503 and "Redis" in r.json()["detail"]
        assert lonely.get("/health").status_code == 200


async def test_fake_ingest_repo_open_item_ids_matches_open_filter(ingest_repo):
    # Initially, seed failed item (7) is excluded
    assert await ingest_repo.open_item_ids() == []

    # Create a batch of items
    _, item_ids = await ingest_repo.create(
        [{"url": "https://x.test/a.pdf"}, {"url": "https://x.test/b.pdf"}]
    )
    assert await ingest_repo.open_item_ids() == item_ids

    # Transition one item to running (still open)
    ingest_repo.items[item_ids[0]]["status"] = "running"
    assert await ingest_repo.open_item_ids() == item_ids

    # Transition to terminal states (excluded)
    ingest_repo.items[item_ids[0]]["status"] = "done"
    ingest_repo.items[item_ids[1]]["status"] = "failed"
    assert await ingest_repo.open_item_ids() == []


async def test_worker_in_api_false_never_starts_worker(settings):
    settings.worker_in_api = False
    with patch("licenselens.workers.main.run_embedded") as mock_run:
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            pass
        mock_run.assert_not_called()


async def test_worker_in_api_true_starts_and_cancels_worker(settings):
    settings.worker_in_api = True
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def mock_run_embedded(_):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch("licenselens.workers.main.run_embedded", side_effect=mock_run_embedded):
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert started.is_set()
        assert cancelled.is_set()


async def test_lifespan_closes_queue_and_disposes_engine(settings):
    mock_engine = AsyncMock()
    with patch("licenselens.api.main.make_engine", return_value=mock_engine):
        app = create_app(settings)
        mock_queue = AsyncMock()
        app.state.queue = mock_queue

        async with app.router.lifespan_context(app):
            pass

        mock_queue.close.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()
