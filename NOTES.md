
# Day 3 â€” PostgreSQL Migration Notes

## 1. Database Connection

- Database: Neon PostgreSQL
- Database version: PostgreSQL 18.7
- Driver: asyncpg
- ORM: SQLAlchemy 2.0
- Migrations: Alembic

## 2. Database Migrations

Two migrations were created and successfully applied.

- `0af5a112e9bb`: Create documents and pages tables.
- `685310b92290`: Add GIN index on JSONB metadata.

Verified migration operations:

- Upgrade to head
- Check current revision
- View migration history
- Downgrade one revision
- Upgrade to head again

Current migration: `685310b92290 (head)`

## 3. Idempotent Loader Evidence

The loader was executed three times.

### First execution

records=20 inserted=20 updated=0 unchanged=0

### Second execution

records=20 inserted=0 updated=0 unchanged=20

### Third execution

records=20 inserted=0 updated=1 unchanged=19

The third execution verified that the loader updates changed records without creating duplicates.

## 4. API Verification

Verified endpoints:

- GET /health
- GET /ready
- GET /v1/documents
- GET /v1/documents/{id}

Additional checks:

- Pagination
- Search
- JSONB metadata filtering
- Invalid filter validation
- Database unavailable readiness response

## 5. Automated Tests

- API unit tests: 16 passed
- PostgreSQL integration tests: 3 passed
- Total: 19 passed

Command:

`uv run pytest`

## 6. EXPLAIN ANALYZE Evidence

Query:

```sql
SELECT external_id, title
FROM documents
WHERE metadata @> '{"spdx_id": "MIT"}';
```

### Normal query plan

- PostgreSQL selected Sequential Scan.
- One matching record was returned.
- Nineteen records were removed by the filter.
- Execution time: 1.381 ms.

### Sequential scans disabled

- PostgreSQL selected Bitmap Heap Scan.
- Bitmap Index Scan used `ix_documents_metadata_gin`.
- One matching record was returned.
- Execution time: 1.993 ms.

## 7. My Understanding

PostgreSQL selected Sequential Scan initially because the documents table contains only 20 records. For a small table, scanning every row can be cheaper than using an index.

After sequential scans were disabled for the session, PostgreSQL used the GIN index through Bitmap Index Scan.

This confirms that the JSONB GIN index is correctly configured and usable for metadata containment queries.

The forced index plan was slower for this small dataset. Indexes become more valuable when tables grow and selective queries can avoid scanning large numbers of rows.

## Docker Image Build

- Initial build: approximately 1 minute 30 seconds, including base image and dependency downloads.
- Source-only rebuild: 15.4 seconds; the `uv sync --locked --no-install-project` dependency layer was cached.
- Image size: 300 MB.
- Runtime user check: `uid=999(app) gid=999(app) groups=999(app)`.

## Docker Compose Stack

- `docker compose up --build -d` built the API and migration images. PostgreSQL (`pgvector/pgvector:pg17`) and Redis became healthy; migrations exited with code 0; the API became healthy.
- The API returned 20 records from `GET /v1/documents`.
- `app_test` exists alongside `app`, created by `docker/initdb/01-create-test-db.sql`.
- Port 8000 was already occupied on this machine, so Compose was verified with `API_PORT=8001`; the default remains 8000.

### Health and database recovery

Initial health: `{"status":"ok","app":"licenselens","env":"local"}`

Initial readiness: `{"status":"ready","checks":{"database":true}}`

Health with Postgres stopped: `{"status":"ok","app":"licenselens","env":"local"}`

Readiness with Postgres stopped: `{"status":"not_ready","checks":{"database":false}}`

Readiness after restarting Postgres: `{"status":"ready","checks":{"database":true}}`

### Persistent volume and idempotent seed

After `docker compose down` and `docker compose up -d`, the API returned healthy and the migration service exited with code 0. The loader reported `records=20 inserted=0 updated=0 unchanged=20`.

## D04 · Docker, Compose and tests

### The image

```text
IMAGE             ID             DISK USAGE   CONTENT SIZE   EXTRA
licenselens:dev   98998015d864        300MB           70MB
uid=999(app) gid=999(app) groups=999(app)
```

- Size: 300 MB (target: under 400 MB).
- First build: approximately 90 s, including base image and dependency downloads. Source-only rebuild: 15.4 s.
- `uv sync --locked --no-install-project --no-dev` was `CACHED` because its `uv.lock` and `pyproject.toml` inputs had not changed when only source code changed.

### The stack

Clean-clone `docker compose ps -a` (API host port 8001 because port 8000 was occupied):

```text
NAME                    IMAGE                    COMMAND                  SERVICE   CREATED          STATUS                      PORTS
licenselens-api-1       licenselens-api          "sh -c 'exec uvicorn…'"   api       25 seconds ago   Up 15 seconds (healthy)     0.0.0.0:8001->8000/tcp
licenselens-db-1        pgvector/pgvector:pg17   "docker-entrypoint.s…"   db        25 seconds ago   Up 24 seconds (healthy)     0.0.0.0:5433->5432/tcp
licenselens-migrate-1   licenselens-migrate      "sh -c 'alembic upgr…'"   migrate   25 seconds ago   Exited (0) 15 seconds ago
licenselens-redis-1     redis:7-alpine           "docker-entrypoint.s…"   redis     25 seconds ago   Up 24 seconds (healthy)     0.0.0.0:6379->6379/tcp
```

Migration output, first up from the clean clone:

```text
records=20 inserted=20 updated=0 unchanged=0
```

Migration output, after `docker compose down` and `docker compose up -d` (volume retained):

```text
records=20 inserted=0 updated=0 unchanged=20
```

The committed clean clone had no `.venv` or `.env`; it built and reached a healthy stack. The temporary clone and its volume were removed afterward.

### Breaking the database on purpose

```text
Before stopping DB, /health: {"status":"ok","app":"licenselens","env":"local"}
Before stopping DB, /ready:  {"status":"ready","checks":{"database":true}}
DB stopped, /health:        {"status":"ok","app":"licenselens","env":"local"}
DB stopped, /ready:          {"status":"not_ready","checks":{"database":false}}
DB started, /ready:         {"status":"ready","checks":{"database":true}}
```

The load balancer should stop routing requests while readiness is false. Restarting the API cannot repair a database outage; the live process recovers its database checks when Postgres returns.

### Tests and coverage

```text
uv run poe test              ? 16 passed
uv run poe test-integration  ? 7 passed (ran twice; 7 passed both times)
coverage TOTAL, unit only    ? 83%
coverage TOTAL, with integration tests ? 92%
```

The Poe task list contains seven tasks: `dev`, `lint`, `test`, `cov`, `migrate`, `seed`, and `test-integration`. `uv run poe lint` finished with `31 files already formatted` and `All checks passed!`.

Coverage still misses `logging_setup.py:16-28` (the logging formatter and exception fields) and `api/deps.py:40-41` (the database-check exception path). Logging serialization merits a focused test; the readiness failure path was exercised manually by stopping Postgres, though it is not covered by the automated integration suite.
