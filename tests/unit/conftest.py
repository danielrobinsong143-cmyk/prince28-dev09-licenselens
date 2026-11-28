import pytest
from fastapi.testclient import TestClient

from licenselens.api.deps import (
    check_db,
    get_ingest_repo,
    get_manifest,
    get_queue,
    get_repo,
)
from licenselens.api.main import create_app
from licenselens.config import Settings


class FakeRepository:
    """In-memory repository for API unit tests."""

    def __init__(self):
        self.documents = [
            {
                "id": "001",
                "spdx_id": "MIT",
                "name": "MIT License",
                "title": "MIT",
                "doc_type": "record",
                "source_url": "https://spdx.org/licenses/MIT.html",
                "licence": None,
                "published_on": None,
            },
            {
                "id": "002",
                "spdx_id": "Apache-2.0",
                "name": "Apache License 2.0",
                "title": "Apache-2.0",
                "doc_type": "record",
                "source_url": "https://spdx.org/licenses/Apache-2.0.html",
                "licence": None,
                "published_on": None,
            },
        ]

    async def list(self, *, q, filters, limit, offset):
        results = self.documents

        if q:
            results = [
                doc
                for doc in results
                if q.lower() in doc["title"].lower() or q.lower() in doc["name"].lower()
            ]

        for key, value in filters.items():
            results = [doc for doc in results if str(doc.get(key, "")).lower() == value.lower()]

        total = len(results)
        return total, results[offset : offset + limit]

    async def get(self, external_id):
        return next(
            (doc for doc in self.documents if doc["id"] == external_id),
            None,
        )


class FakeQueue:
    """Remembers which items were queued. No Redis anywhere near a unit test."""

    def __init__(self) -> None:
        self.items: list[int] = []

    async def put(self, item_ids: list[int]) -> int:
        self.items += item_ids
        return len(item_ids)

    async def close(self) -> None:
        pass


OPEN_STATUSES = ("queued", "running")


class FakeIngestRepo:
    """Behaves like IngestRepository, with Python lists instead of tables."""

    def __init__(self) -> None:
        self.known: set[str] = {"f-" + "a" * 20}  # one document is already in the database
        self.jobs: dict[str, list[dict]] = {}
        self.job_items: dict[str, list[int]] = {}
        self.failed: list[dict] = [
            {
                "item": 7,
                "job_id": "old",
                "url": "https://x.test/bad.pdf",
                "path": "data/raw/x/bad.pdf",
                "attempts": 3,
                "error": "parse: corrupt",
            }
        ]
        self.items: dict[int, dict] = {
            7: {
                "id": 7,
                "job_id": "old",
                "status": "failed",
                "record": {"url": "https://x.test/bad.pdf", "path": "data/raw/x/bad.pdf"},
            }
        }
        self._item_counter: int = 1

    async def known_ids(self) -> set[str]:
        return set(self.known)

    async def create(self, records: list[dict]) -> tuple[str, list[int]]:
        job_id = f"job{len(self.jobs) + 1}"
        self.jobs[job_id] = records
        item_ids = list(range(self._item_counter, self._item_counter + len(records)))
        self._item_counter += len(records)
        self.job_items[job_id] = item_ids
        for item_id, record in zip(item_ids, records, strict=True):
            self.items[item_id] = {
                "id": item_id,
                "job_id": job_id,
                "record": record,
                "status": "queued",
            }
        return job_id, item_ids

    async def progress(self, job_id: str) -> dict | None:
        if job_id not in self.jobs:
            return None
        requested = len(self.jobs[job_id])
        counts = {"queued": 0, "running": 0, "done": 0, "skipped": 0, "failed": 0}
        for item_id in self.job_items.get(job_id, []):
            status = self.items.get(item_id, {}).get("status", "queued")
            counts[status] = counts.get(status, 0) + 1
        return {
            "job_id": job_id,
            "status": "finished" if (counts["queued"] + counts["running"] == 0) else "working",
            "requested": requested,
            "counts": counts,
            "pages": 0,
            "seconds": 0.0,
            "created_at": "2026-11-27T00:00:00+00:00",
            "dead_letter": await self.dead_letter(job_id=job_id, limit=20),
        }

    async def dead_letter(self, *, job_id: str | None = None, limit: int = 50) -> list[dict]:
        items = self.failed
        if job_id:
            items = [item for item in items if item.get("job_id") == job_id]
        return items[:limit]

    async def failed_records(self) -> list[dict]:
        deduped = {
            item["url"]: {"url": item["url"], "path": item.get("path", "")} for item in self.failed
        }
        return list(deduped.values())

    async def open_item_ids(self) -> list[int]:
        return sorted(
            item_id for item_id, item in self.items.items() if item.get("status") in OPEN_STATUSES
        )

    async def recover_crashed_items(
        self,
        max_tries: int,
        *,
        redis: object = None,
    ) -> tuple[list[int], list[int]]:
        failed_ids = []
        requeued_ids = []
        for item_id, item in list(self.items.items()):
            if item.get("status") == "running":
                attempts = item.get("attempts", 0)
                if attempts >= max_tries:
                    item["status"] = "failed"
                    prefix = f"gave up: started {attempts} times and never finished"
                    err = item.get("error", "")
                    if err and err.strip():
                        if not err.startswith(prefix):
                            item["error"] = f"{prefix}; last error: {err.strip()}"[:500]
                    else:
                        item["error"] = prefix[:500]
                    failed_ids.append(item_id)
                else:
                    item["status"] = "queued"
                    requeued_ids.append(item_id)
        return failed_ids, requeued_ids


def manifest_line(n: int, **changes) -> dict:
    record = {
        "url": f"https://x.test/{n}.pdf",
        "source": "x",
        "licence": "CC BY 4.0",
        "path": f"data/raw/x/{n}.pdf",
        "sha256": str(n) * 64,
        "status": "downloaded",
    }
    return record | changes


MANIFEST = [
    manifest_line(1),
    manifest_line(2),
    manifest_line(3, sha256="a" * 64),  # already in the database
    manifest_line(4, status="failed"),  # never downloaded
]


@pytest.fixture
def fake_repo():
    return FakeRepository()


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
def ingest_repo():
    return FakeIngestRepo()


@pytest.fixture
def manifest():
    return list(MANIFEST)


@pytest.fixture
def settings():
    return Settings(
        app_env="ci",
        database_url="postgresql://localhost:5432/testdb",
        admin_token="test-token",  # pragma: allowlist secret
        _env_file=None,
    )


@pytest.fixture
def client(settings, fake_repo, queue, ingest_repo, manifest):
    app = create_app(settings)

    app.dependency_overrides[get_repo] = lambda: fake_repo
    app.dependency_overrides[check_db] = lambda: True
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_ingest_repo] = lambda: ingest_repo
    app.dependency_overrides[get_manifest] = lambda: manifest

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
