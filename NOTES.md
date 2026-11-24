
# Day 3 — PostgreSQL Migration Notes

## D03 · PostgreSQL

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
NAME                    IMAGE                    COMMAND                  SERVICE   CREATED          STATUS                       PORTS
licenselens-api-1       licenselens-api          "sh -c 'exec uvicorn…'"   api       25 seconds ago   Up 15 seconds (healthy)      0.0.0.0:8001->8000/tcp
licenselens-db-1        pgvector/pgvector:pg17   "docker-entrypoint.s…"   db        25 seconds ago   Up 24 seconds (healthy)      0.0.0.0:5433->5432/tcp
licenselens-migrate-1   licenselens-migrate      "sh -c 'alembic upgr…'"   migrate   25 seconds ago   Exited (0) 15 seconds ago
licenselens-redis-1     redis:7-alpine           "docker-entrypoint.s…"   redis     25 seconds ago   Up 24 seconds (healthy)      0.0.0.0:6379->6379/tcp
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
DB stopped, /ready:         {"status":"not_ready","checks":{"database":false}}
DB started, /ready:         {"status":"ready","checks":{"database":true}}
```

The load balancer should stop routing requests while readiness is false. Restarting the API cannot repair a database outage; the live process recovers its database checks when Postgres returns.

### Tests and coverage

```text
uv run poe test               ? 16 passed
uv run poe test-integration   ? 7 passed (ran twice; 7 passed both times)
coverage TOTAL, unit only     ? 83%
coverage TOTAL, with integration tests ? 92%
```

The Poe task list contains seven tasks: `dev`, `lint`, `test`, `cov`, `migrate`, `seed`, and `test-integration`. `uv run poe lint` finished with `31 files already formatted` and `All checks passed!`.

Coverage still misses `logging_setup.py:16-28` (the logging formatter and exception fields) and `api/deps.py:40-41` (the database-check exception path). Logging serialization merits a focused test; the readiness failure path was exercised manually by stopping Postgres, though it is not covered by the automated integration suite.

## D05 · CI/CD and going live

- Live URL: https://licenselens-api.onrender.com
- GitHub repository: https://github.com/danielrobinsong143-cmyk/prince28-dev09-licenselens
- CI workflow: https://github.com/danielrobinsong143-cmyk/prince28-dev09-licenselens/actions
- UptimeRobot: Checks `/health` every 5 minutes; email alerts enabled.

### Deployment

- First automatic deployment: CD workflow run on main — record the run link and completion time.
- Smoke test: `/health`, `/ready`, and `/v1/documents?limit=1` returned 200.
- Production URL: https://licenselens-api.onrender.com

### Rollback drill

- Rolled back to SHA: `0cf5b2a77a4988d0b7358740622e2d4334c9f0f1`
- Rolled-back deployment: successful; smoke test passed.
- Roll-forward attempt to SHA: `c4b21eec839145a24f120fdbb6092706bacf5a79` — deployment hook returned HTTP 400. Verify the final deployment status before marking the roll-forward drill complete.

### Where my secrets live

- Neon connection string: GitHub Secret `DATABASE_URL` and Render environment. Not in Git or the Docker image.
- Render deploy hook: GitHub Secret `RENDER_DEPLOY_HOOK` only.
- UptimeRobot monitor-specific API key: used only in the README badge. Never commit account-wide API keys.

### Monitoring

- UptimeRobot checks `/health` every 5 minutes.
- Email alerts are enabled.
- One thing that would make my live app faster on its first request: Render may need to wake up after an idle period.

### Kill and resume

- Run 1: Pressed Ctrl+C after 5 files had downloaded.
- Run 2: downloaded=7, skipped=5.
- Run 3: downloaded=0, skipped=12.
- Result: The downloader resumed successfully without downloading completed files again.

**What I learned:** The manifest tracks completed downloads. When the downloader runs again, it skips files that are already recorded and available locally.

## D06 · Collecting the corpus

- Start target: SPDX list + ChooseALicense metadata
- Collected today: **818 files, 7,376 pages, 22.3 MB**
- Doors used: **Git repository** (SPDX and ChooseALicense)

### Licence check

| Source | Licence or terms, in my words | May I show full text in answers? |
|---|---|---|
| SPDX | CC0; public-domain dedication | Yes, subject to checking the source record |
| ChooseALicense | Content CC BY 3.0; repository code MIT | Yes, with appropriate attribution for CC BY content |
| ScanCode | CC BY 4.0, as stated on the project card | With attribution and applicable licence conditions |

### robots.txt

- Site checked: github.com
- Checked: https://github.com/robots.txt
- Relevant rules: GitHub disallows crawling raw paths, `.git` paths, archive paths and zipball paths for general user agents.
- Collection method: Git repository clone, not individual URL crawling.
- No blocked records were reported during my collection.
- I will continue respecting robots.txt when using the URL and sitemap collection methods.

### Kill and resume

- Run 1: Ctrl+C after 5 files
- Run 2: downloaded=7, skipped=5
- Run 3: downloaded=0, skipped=12
- Result: Completed files were skipped on subsequent runs.

### Capacity

- Tokens per page: approximately 750 (estimated)
- Full corpus: 13.53 million tokens
- Estimated chunks: 38,889
- One-time embedding estimate: Rs 222
- Estimated vector storage: 119 MB
- Estimated text storage: 65 MB

### One thing that went wrong today, and how I fixed it

The first documentation review showed that my Day 06 notes were missing. I checked the dataset and capacity reports and added a dedicated Day 06 section to record the corpus, licence checks, resume test and capacity estimates.

## D07 · Parser comparison

<!-- parsers:start -->

| File | Parsers (A / B) | Pages | A found | B found | Time (A / B) | Better, why |
|---|---|---|---|---|---|---|
| APL-1.0.json | plain (check headings by eye) | 1 | 177,572 chars, 0 headings, 0 tables | - | 0.03 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| LGPL-3.0+.json | plain (check headings by eye) | 1 | 151,076 chars, 0 headings, 0 tables | - | 0.04 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| LGPL-3.0-or-later.json | plain (check headings by eye) | 1 | 151,367 chars, 0 headings, 0 tables | - | 0.05 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| LGPL-3.0-only.json | plain (check headings by eye) | 1 | 151,305 chars, 0 headings, 0 tables | - | 0.04 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| LGPL-3.0.json | plain (check headings by eye) | 1 | 151,072 chars, 0 headings, 0 tables | - | 0.05 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| RPL-1.1.json | plain (check headings by eye) | 1 | 127,563 chars, 0 headings, 0 tables | - | 0.04 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| GPL-3.0-or-later.json | plain (check headings by eye) | 1 | 125,290 chars, 0 headings, 0 tables | - | 0.05 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| GPL-3.0-only.json | plain (check headings by eye) | 1 | 123,868 chars, 0 headings, 0 tables | - | 0.06 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| GPL-3.0+.json | plain (check headings by eye) | 1 | 123,618 chars, 0 headings, 0 tables | - | 0.04 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |
| CPAL-1.0.json | plain (check headings by eye) | 1 | 120,364 chars, 0 headings, 0 tables | - | 0.05 s | One plain parser was used; this JSON file was not part of a two-parser comparison. |

<!-- parsers:end -->

## D07 · Parsing my corpus

- Files in the manifest: **818** · parsed: **818** · failed: **0** · pages in Postgres: **1,022**
- Parsing coverage: **100%** of downloaded files.
- First run: `parsed=20`, `pages=20`.
- Full run: `already_parsed=20`, `parsed=798`, `pages=1,022`, `seconds=361.3`.
- Second full run: `already_parsed=818`, `pages=0`, `seconds=1.2`. This verified that rerunning the parser does not create duplicate documents or pages.

### Failures by reason

The `parse_failures` query returned no rows.

| Reason | Files | What I did |
|---|---:|---|
| None | 0 | No parsing failures were recorded; no retry was needed. |

- The one I fixed: **None — 0 failures out of 818 files.**

### What I saw when I read my parsed pages

- I queried the latest five pages in Neon and confirmed that the extracted text was readable and linked to the expected licence document titles.
- The inspected examples included JSON-style licence records such as `AGPL-1.0`.
- The inspected records had empty `headings` arrays. These examples are structured licence data rather than documents with section-heading paths.
- The inspected JSON records showed 0 extracted tables; their content appeared as structured JSON text rather than Markdown table grids.
- No extraction failure was identified in the five-page sample. I should inspect PDF/HTML pages separately before making claims about their heading paths or tables.
- PDF/HTML comparison: Not applicable to the current corpus. The manifest contains 0 downloaded PDF/HTML files, so only the plain parser could be evaluated on the selected JSON files.

### My parser choice

- For the ten files selected by the comparison script, the files were all `.json`. The script used the plain parser and reported one page per file, 0 headings and 0 tables.
- This run did **not** compare PyMuPDF against pypdf or Trafilatura against tag stripping, because none of the selected files were PDFs or HTML.
- I cannot yet choose a winning PDF or HTML parser from this comparison. A separate comparison using actual PDF/HTML files is still needed if those file types are present.
- The JSON records were extracted as readable text, but heading and table counts were not applicable to the sampled records.

### One thing that went wrong today, and how I fixed it

The automatic parser-comparison selection picked the ten largest `.json` files, so it did not exercise the PDF or HTML parser comparisons. I recorded that limitation instead of claiming that one PDF or HTML parser was better. I need to choose actual PDF/HTML files for a meaningful A/B comparison.
