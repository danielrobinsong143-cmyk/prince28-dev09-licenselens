
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


### D07 · Parser comparison

Compared two readings of 10 matching licence texts from the corpus:

- **Source A:** SPDX licence JSON (`licenseText` field).
- **Source B:** ChooseALicense licence TXT (YAML front matter removed).
- Both texts were processed using the LicenseLens parser.
- Similarity was calculated after normalizing whitespace and converting text to lowercase.

| Licence | SPDX pages | ChooseALicense pages | SPDX headings | Choose headings | Similarity | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 0BSD | 1 | 1 | 0 | 0 | 3.38% | Equivalent legal terms; placeholder formatting differs |
| AFL-3.0 | 4 | 4 | 0 | 0 | 99.65% | Equivalent content; heading extraction needs review |
| AGPL-3.0 | 13 | 13 | 0 | 0 | 100.00% | Equivalent content; heading extraction needs review |
| Apache-2.0 | 4 | 5 | 0 | 0 | 100.00% | Equivalent content; heading extraction needs review |
| Artistic-2.0 | 3 | 3 | 0 | 0 | 99.99% | Equivalent content; heading extraction needs review |
| BlueOak-1.0.0 | 1 | 1 | 0 | 0 | 100.00% | Equivalent content; heading extraction needs review |
| BSD-2-Clause-Patent | 1 | 1 | 0 | 0 | 99.16% | Equivalent content; heading extraction needs review |
| BSD-2-Clause | 1 | 1 | 0 | 0 | 97.92% | Equivalent content; heading extraction needs review |
| BSD-3-Clause-Clear | 1 | 1 | 0 | 0 | 2.73% | Equivalent legal terms; placeholder formatting differs |
| BSD-3-Clause | 1 | 1 | 0 | 0 | 98.16% | Equivalent content; heading extraction needs review |

**Observations**

- Eight licence pairs showed at least 97.92% text similarity.
- 0BSD uses different copyright placeholders in the two sources.
- BSD-3-Clause-Clear also uses different copyright placeholders. Manual inspection confirmed that the main legal terms are substantially equivalent.
- Apache-2.0 produced different page counts (4 and 5), despite 100% normalized text similarity. This is a pagination difference worth reviewing.
- Both sources produced zero extracted headings across these 10 files. The current heading detector does not recognize the legal section-heading formats in these samples.
- Heading extraction therefore needs improvement and manual verification before treating heading paths as complete.
- Similarity measures text resemblance, not legal equivalence.

**Conclusion:** The comparison demonstrates that the two sources contain highly similar licence text in eight cases, with two placeholder-format differences. The current parser preserves the text but needs better heading recognition for these legal documents.


## D07 · Parsing my corpus

- Files in the manifest: **818** · parsed: **818** · failed: **0** · pages in Postgres: **7808**
- Parsing coverage: **100%** of downloaded files.
- Oversized pages (>3,000 characters): **0**.
- Final full reparse: `818` files parsed, `7,808` pages generated, `0` failures.
- Final Neon verification: `818` documents, `7,808` pages, `0` failures.
- Page-size verification: `0` pages exceed 3,000 characters.
- Second full run: `already_parsed=818`, `pages=0`, `seconds=1.2`. This verified that rerunning the parser does not create duplicate documents or pages.
- Failures by reason: **0** — no files failed during parsing.
- Issue fixed: Long paragraphs could exceed the 3,000-character page limit. Updated `split_pages()` to split long paragraphs into smaller pages. Verified that **0** pages exceed the limit.

### Failures by reason

The `parse_failures` query returned no rows.

| Reason | Files | What I did |
|---|---:|---|
| None | 0 | No parsing failures were recorded; no retry was needed. |

- The one I fixed: **None — 0 failures out of 818 files.**

### What I saw when I read my parsed pages

- Inspected AGPL-3.0 pages in Neon: extracted licence text was readable, page numbers were present, and headings were empty for the structured JSON content.
- Mozilla Public License 2.0, page 2: heading path correctly detected as ["Mozilla Public License Version 2.0", "1. Definitions"].
- ODC Open Database License, page 2: heading path correctly detected as ["ODC Open Database License (ODbL)", "Preamble", "1.0 Definitions of Capitalised Words"].
- Mozilla Public License, page 1: YAML front matter was preserved, with an empty heading path.
- ODC Open Database License: 0 tables detected; the inspected content contained prose and YAML metadata rather than Markdown tables.
- No extraction failure was identified in the inspected examples.
- PDF/HTML comparison was not applicable because the corpus contains no PDF or HTML files.

### My parser choice

- The corpus contains no PDF or HTML files, so PyMuPDF/pypdf and Trafilatura/tag-stripping comparisons are not applicable.
- The earlier ten-source comparison above applies the same parser to SPDX JSON and ChooseALicense TXT. The evaluation below separately compares two JSON parsing approaches.
- Manual inspection of MPL-2.0 and ODbL confirmed readable licence text and correctly detected heading paths on later pages.

### One thing that went wrong today, and how I fixed it

The automatic parser-comparison selection originally picked JSON files, so it could not exercise PDF or HTML parsers. I kept the evaluation within the actual corpus and compared generic JSON parsing with extraction of each SPDX `licenseText` field instead.

### D07 · JSON parser approach comparison

**Approach A — current plain parser:** pass the complete SPDX JSON file to `parse_file()`. This is the existing ingestion behavior for JSON.

**Approach B — licence-text aware:** decode the JSON, select `licenseText`, and send that value through the existing text cleaning, heading, table and page-splitting stages. This is an evaluation adapter; it does not change ingestion or database behavior.

The ten documents were selected from the local SPDX corpus to cover long texts, varied licensing models and structured legal sections. The script `scripts/compare_json_parsers.py` reproduces the measurements and reports metrics only.

| Licence | A: chars / pages / headings / tables / failures | B: chars / pages / headings / tables / failures | Normalized output similarity |
|---|---:|---:|---:|
| 3D-Slicer-1.0 | 39,081 / 14 / 0 / 0 / 0 | 9,992 / 4 / 8 / 0 / 0 | 31.42% |
| AGPL-3.0 | 120,503 / 43 / 0 / 0 / 0 | 33,786 / 13 / 18 / 0 / 0 | 41.71% |
| Apache-2.0 | 40,738 / 15 / 0 / 0 / 0 | 10,250 / 4 / 9 / 0 / 0 | 39.22% |
| Artistic-2.0 | 35,316 / 13 / 0 / 0 / 0 | 8,655 / 3 / 0 / 0 / 0 | 38.38% |
| EPL-2.0 | 60,836 / 21 / 0 / 0 / 0 | 13,940 / 6 / 9 / 0 / 0 | 37.03% |
| GPL-3.0 | 122,943 / 44 / 0 / 0 / 0 | 34,376 / 13 / 18 / 0 / 0 | 41.73% |
| LGPL-3.0 | 151,072 / 55 / 0 / 0 / 0 | 41,718 / 16 / 25 / 0 / 0 | 41.19% |
| MPL-1.1 | 101,601 / 37 / 0 / 0 / 0 | 23,329 / 9 / 25 / 0 / 0 | 30.13% |
| MPL-2.0 | 71,604 / 27 / 0 / 0 / 0 | 15,641 / 6 / 26 / 0 / 0 | 28.97% |
| ODbL-1.0 | 106,182 / 39 / 0 / 0 / 0 | 24,884 / 9 / 38 / 0 / 0 | 31.17% |

**Results:** Both approaches completed all ten files with zero failures. Approach A treated the whole JSON serialization as document text: it produced 13–55 pages and detected no headings. Approach B produced only the licence text, reduced output size by 72–78%, and detected 8–38 headings in nine of ten documents. Artistic-2.0 had no headings recognized by the current heading rules. Neither approach identified Markdown tables in these ten licence texts.

The normalized output similarity compares the full JSON serialization from A with only the `licenseText` value from B. Its low score primarily reflects that the outputs contain different material (JSON fields/escaping versus licence body), and is not a legal-text equivalence score. The earlier SPDX-vs-ChooseALicense comparison above is the source-to-source text comparison.

**Conclusion:** For SPDX licence JSON used as document content, extracting `licenseText` before the existing text parsing stages is the more useful parsing approach. This evaluation does not change the production parser or ingestion behavior; adopting it there would require a separate implementation decision.

### Day 8 – Provider connection test

- Gemini: HTTP 503 – temporary high demand.
- Groq: SUCCESS.
- Model: openai/gpt-oss-20b
- Response: pong
- Latency: 965 ms
- Input tokens: 77
- Output tokens: 45
- Observation: Primary provider failed; backup provider worked.

### English vs Tamil Token Comparison

| Test | Input tokens | Output tokens |
|---|---:|---:|
| Baseline: "Hi" | 200 | 56 |
| English refund question | 208 | 110 |
| Tamil refund question | 222 | 218 |

- English message tokens: 208 - 200 = 8
- Tamil message tokens: 222 - 200 = 22
- Tamil / English ratio: 22 / 8 = 2.75x

Observation: The Tamil message used approximately 2.75 times as many input tokens as the English message.

### Answer Quality Tests

1. Unanswerable question:
   - Question: What is the licence of the private library used by my company?
   - Result: Correctly admitted missing information.
   - Observation: No licence details were fabricated.

2. Off-topic question:
   - Question: Write a funny poem about cats.
   - Result: Generated a poem.
   - Observation: The assistant answered outside its licence-compliance scope.
   - Improvement: Add an instruction to politely decline unrelated requests.


### D08 · LLM gateway

- Primary: gemini-3.8-flash (Gemini)
- Fallback: openai/gpt-oss-20b (Groq)

### Tokens: English against Tamil

| Message | Tokens in |
|---|---:|
| Hi | 200 |
| English refund question | 208 |
| Tamil refund question | 222 |

- English message: 8 tokens
- Tamil message: 22 tokens
- Tamil needs approximately 2.75x the input tokens.

### What the model does without my documents

- Private library licence question: admitted missing information.
- Off-topic cat poem question: answered instead of declining.

### Fallback and circuit breaker

- Primary: Gemini (`gemini-3.8-flash`).
- Fallback: Groq (`openai/gpt-oss-20b`).
- Primary-provider failures trigger fallback to Groq.
- Circuit breaker threshold: 3 consecutive primary-provider failures.
- Recovery timeout: 30 seconds.
- Verified circuit opening through a gateway integration test using fake providers.
- The fourth request skipped the primary provider while the circuit was open.

### Production usage verification

Verified endpoint:

`GET /v1/usage?days=7`

Production response snapshot: 25 November 2026.

| Metric | Result |
|---|---:|
| Provider attempts | 17 |
| Errors | 2 |
| Fallback attempts | 15 |
| Input tokens | 3,459 |
| Output tokens | 5,715 |
| Estimated cost | ₹0.285585 |

Daily breakdown:

| Date | Attempts | Errors | Fallbacks |
|---|---:|---:|---:|
| 2026-11-25 | 17 | 2 | 15 |

- The endpoint preserves lifetime totals.
- It also reports totals for the requested date range.
- Daily usage is grouped by calendar date.
- The `days` query parameter accepts values from 1 to 90, defaulting to 7.
- Costs are estimates based on configured model pricing, not a verified provider invoice.

### Circuit breaker verification

- Failure threshold: 3 consecutive primary-provider failures.
- Recovery timeout: 30 seconds.
- First three requests: primary failed and fallback was used.
- Fourth request: primary was skipped because the circuit was open.
- Verified through a gateway integration test using fake providers.
- Test result: 14 gateway tests passed.

### Day 8 testing

- Unit tests: 73 passed.
- PostgreSQL integration tests: 11 passed.
- Ruff lint: passed.
- Ruff formatting: passed.
- CI build image: passed.
- CI lint and test: passed.


### SPDX JSON vs ChooseALicense text — 10-file comparison

| Licence | SPDX pages | ChooseALicense pages | SPDX headings | Choose headings | Similarity | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 0BSD | 1 | 1 | 0 | 0 | 3.38% | Placeholder formatting differs; low similarity requires manual review |
| AFL-3.0 | 4 | 4 | 0 | 0 | 99.65% | Near-identical text |
| AGPL-3.0 | 13 | 13 | 18 | 18 | 100.00% | Text and headings match |
| Apache-2.0 | 4 | 5 | 9 | 9 | 100.00% | Text and headings match; page counts differ |
| Artistic-2.0 | 3 | 3 | 0 | 0 | 99.99% | Near-identical text |
| BlueOak-1.0.0 | 1 | 1 | 9 | 9 | 100.00% | Text and headings match |
| BSD-2-Clause-Patent | 1 | 1 | 0 | 0 | 99.16% | Near-identical text |
| BSD-2-Clause | 1 | 1 | 0 | 0 | 97.92% | Near-identical text |
| BSD-3-Clause-Clear | 1 | 1 | 0 | 0 | 2.73% | Placeholder formatting differs; low similarity requires manual review |
| BSD-3-Clause | 1 | 1 | 0 | 0 | 98.16% | Near-identical text |

**Method:** Extracted `licenseText` from SPDX JSON and the licence body from ChooseALicense TXT files after removing YAML front matter. Both texts were passed through the same `parse_file()` pipeline. Compared normalized text similarity, page counts, and extracted headings.

**Observation:** Apache-2.0, AGPL-3.0, and BlueOak-1.0.0 had matching heading counts and titles. BSD numbered conditions were correctly excluded from heading detection. Apache-2.0 had different page counts despite identical normalized text.

**Limitations:** Similarity scores for 0BSD and BSD-3-Clause-Clear are affected by placeholder or template formatting differences. Similarity scores alone do not establish legal equivalence.

## D09 - Prompt comparison

| Prompt version | Correct | Accuracy | Invalid replies | Average input tokens |
|---|---:|---:|---:|---:|
| classify_v1 | 11/20 | 55% | 1 | 113 |
| classify_v2 | 19/20 | 95% | 0 | 258 |
| classify_v3 | 18/20 | 90% | 1 | 366 |
| classify_v4 | 20/20 | 100% | 0 | 322 |

### Prompt comparison analysis

- Best prompt: `classify_v4`, with 20/20 correct classifications (100%) on the 20-question test set.
- `classify_v2` achieved 19/20 (95%). It incorrectly classified question 20 as `docs`.
- `classify_v3` achieved 18/20 (90%). It incorrectly classified question 4 as `personal` and question 20 as `invalid`.
- The v4 improvement added a clearer distinction between general analysis of named open-source licences and questions requiring access to private repositories or actual dependency information.
- This corrected both question 4 (`docs`) and question 20 (`personal`) in this evaluation.
- One remaining error in the earlier versions: question 20 was treated as a general licence question, even though answering it requires private package and current product information.
- **Hardest earlier wrong question:** Question 20 ("Does the licence of the private package in our production repository allow us to distribute our current product to customers?"). It requires private package and current product information, not general licence knowledge.
- **Wrong answers in current evaluation:** `classify_v4` achieved 20/20, so there were no wrong answers in the current 20-question evaluation.
- **Changed test-set labels:** None (confirmed by evaluation git history; ground-truth labels in `eval/prompt_testset.jsonl` remained unchanged).
- **Token trade-off:** Average input tokens increased from 113 (v1) to 322 (v4), approximately 2.85x, while accuracy improved from 55% to 100%.
- No test-set leakage warnings appeared during the comparison.

**Limitation:** The 100% result applies only to this 20-question test set. Unseen questions still need evaluation.

### Evaluation evidence and cache limits

- Local verification found 20 unique labelled questions and 20 cached responses for each prompt version. The cached scores and average input-token counts match the table above.
- A local scan found no exact test-question leakage in `classify_v1` through `classify_v4`.
- The historical cache does not record provider or model names. The scores are therefore verified cached outputs, but they cannot be attributed to a model or reproduced from the cache alone.
- New comparison cache keys include the model, prompt and question. `--no-cache` now bypasses both cache reads and writes. A fresh comparison still makes live provider calls and must be run separately when approved.
- `eval/cache/` is ignored by Git.

### D09 · Prompts and structured output

#### Prompt evaluation

The labelled evaluation set contains 20 questions. Results recorded from the prompt comparison are:

| Prompt version | Correct | Accuracy |
|---|---:|---:|
| classify_v1 | 11/20 | 55% |
| classify_v2 | 19/20 | 95% |
| classify_v3 | 18/20 | 90% |
| classify_v4 | 20/20 | 100% |

`classify_v4` adds an explicit boundary: general analysis of named licences or hypothetical dependency sets is `docs`, while questions needing a private repository, actual dependency list, private package, or current product configuration are `personal`. Compared with v3, v4 removes the earlier examples and adds this distinction. The measured improvement is on this 20-question set only; it does not establish performance on unseen questions.

#### DocMeta document-level metadata

`DocMeta` is strict, rejects unlisted fields, and retains these 12 fields:

| Field | Why it belongs at document level |
|---|---|
| `doc_kind` | Identifies the document category for filtering and routing. |
| `year` | Captures an explicitly stated year for document identification and context. |
| `language` | Supports language-aware retrieval and interpretation. |
| `summary` | Gives a concise overview without rereading the full document. |
| `topics` | Supports subject discovery and filtering. |
| `spdx_id` | Provides a standardized licence identifier for matching and compatibility lookup. |
| `name` | Preserves the human-readable licence or document name. |
| `osi_approved` | Records the licence's OSI approval status when supported by the source. |
| `copyleft_type` | Makes the licence's copyleft category available for comparison. |
| `permissions` | Captures the key uses granted by the licence. |
| `conditions` | Captures obligations attached to those permissions. |
| `limitations` | Captures restrictions relevant to licence use and compatibility. |

The scalar fields are strict strings, integers, booleans, or nullable values as defined by the model. `topics`, `permissions`, `conditions`, and `limitations` are lists limited to eight items; topics are lowercased and deduplicated. Missing optional values remain null or empty lists rather than being inferred.


#### Extraction progress and verification
- **Document-level parsed / repaired / failed counts:** Not separately recorded in the available evidence. The 109 successful and 13 failed LLM calls are API invocation counts, not document counts.
- **Remaining records:** 788 records currently lack `doc_kind`; their eligibility for extraction has not yet been established.

- **Total documents in database:** 838
- **Documents with DocMeta:** 50 (100% of the milestone target verified; verified document milestone is exactly 50 records).
- **Documents without DocMeta:** 788 (records currently lacking the `doc_kind` field; their extraction eligibility is not yet established).
- **Model invocations vs document extractions:** The 109 successful LLM calls are model invocations across multi-chunk and retried documents, NOT 109 successful document extractions.
- **Detailed parse/repair breakdown:** Exact parsed, repaired, and failed document counts are not available from the current evidence and are explicitly marked as not separately recorded.
- **Extraction prompt version:** `extract_v1`
- **Document-level parsed / repaired / failed counts:** Not separately recorded in the available evidence. The 109 successful and 13 failed LLM calls are API invocation counts, not document counts.
- **Remaining records:** 788 records currently lack `doc_kind`; their eligibility for extraction has not yet been established.
- **Recorded failure:** One document has three recorded attempts ending in `provider_quota_exhausted`.
- **Milestone status:** Achieved. Exactly 50 documents have been independently verified in Neon PostgreSQL using read-only SQL transactions (`SET TRANSACTION READ ONLY`). Each verified document satisfies:
  - Database record exists.
  - `metadata.doc_kind` is present and non-empty.
  - All 12 required `DocMeta` fields are present.
  - All 6 original ingestion fields (`path`, `chars`, `pages`, `parser`, `source`, `tables`) are fully preserved.
  - Excluded from the pending extraction query (`doc_type != 'record'` and missing `metadata.doc_kind`).

#### Earlier five-record manual verification history (ADSL, Abstyles, Adobe-Utopia)

Earlier in Day 9, five extracted records were manually compared with their original source text. That manual comparison identified valid-but-wrong metadata: `spdx_id` was null for ADSL, Abstyles, and Adobe-Utopia even though the source `licenseId` values were present in the source files. Those three Neon records were manually corrected and verified in the database. That was a direct data correction that preserved the DocMeta contract.

#### Recorded extraction cost and LLM calls

Extraction model usage across the corpus extraction runs is tracked through the `llm_calls` repository. Across all runs, model calls and documents remain strictly distinguished (individual multi-chunk or retried documents generate multiple model calls):

- **Total LLM calls:** 122
- **Successful LLM calls:** 109 (this count represents model API invocations, not unique documents)
- **Failed LLM calls:** 13
- **Input tokens from successful calls:** 215,027
- **Output tokens from successful calls:** 92,077
- **Estimated recorded extraction cost:** ₹8.345240 (estimated based on configured model token rates, not an invoice)
- **Approximate average allocated cost per verified DocMeta record:** ₹0.166905 (₹8.345240 / 50 records)

**Provider Breakdown:**

| Provider & Model | Successful calls | Failed calls | Estimated cost |
|---|---:|---:|---:|
| Gemini 3.1 Flash Lite | 31 | 0 | ₹4.606454 |
| Gemini 3.8 Flash | 0 | 4 | ₹0.000000 |
| Groq GPT-OSS-20B | 78 | 9 | ₹3.738786 |
| **Total** | **109** | **13** | **₹8.345240** |

- **Recorded Failed Document:** One document, `f-106ab0d93705b837d4ca`, recorded 3 failed attempts due to `provider_quota_exhausted`. It remains safely tracked in `data/extraction_failures.json` and currently remains without DocMeta.

#### Valid-but-wrong extraction case and merge fix (AFL-2.0 vs AFPL-2.0)

During live extraction inspection, a valid-but-wrong metadata extraction was identified on the production record for Academic Free License v2.0 (`f-0e482ccfd4cdbfe36b04`):
- **Source licenseId:** `AFL-2.0`
- **Extracted spdx_id:** `AFPL-2.0` (incorrect hallucinated identifier)
- **Source isOsiApproved:** `true`
- **Extracted osi_approved:** `true`
- **Extracted summary:** Repeated itself multiple times across chunks.

**Root Cause:**
1. `AFL-2.0.json` (34,638 characters) was split across 13 pages during plain text ingestion, producing 9 extraction chunks.
2. In Chunk 1, the model saw only the preamble text ("The Academic Free License v. 2.0") without the structured `"licenseId": "AFL-2.0"` (which appeared on Page 7 in Chunk 6). The model guessed `AFPL-2.0`.
3. `merge_metadata()` previously locked in any scalar where `merged[key] is not None`. When Chunk 6 correctly extracted `AFL-2.0`, `merge_metadata()` ignored it because `spdx_id` had already been populated. Conversely, `osi_approved` was null in Chunk 1 and true in Chunk 6, which is why it was accepted.
4. `merge_metadata()` previously performed naive space-concatenation on summaries (`merged[key] = f"{merged[key]} {value}"`), accumulating multi-chunk summaries into a repetitive block.

**Resolution & Merge Fix (Commit `1e44ce5`):**
- **Strict SPDX Evidence Precedence:**
  1. `exact_spdx_id` supplied by a trusted caller is authoritative. (`exact_spdx_id` is an optional merge-function interface parameter on `merge_metadata()` / `extract_metadata_from_chunks()` and is not currently supplied or wired into the production extraction runner in `scripts/extract_metadata.py`).
  2. Otherwise, accept an SPDX identifier only when explicitly corroborated by the corresponding source chunk text using identifier-aware boundary matching (`(?<![a-zA-Z0-9.+-])ID(?![a-zA-Z0-9.+-])`).
  3. If multiple different identifiers are corroborated, return `null` rather than choosing arbitrarily.
  4. If candidates are not corroborated by source text, return `null`. Unanimous model agreement without source-text evidence is strictly rejected.
  5. If no identifier exists, return `null`.
- **Chunk Text Parity:** When `chunk_texts` is supplied, `len(chunk_texts) == len(results)` is strictly enforced, raising `ValueError` on length mismatch.
- **Trusted Override Validation:** `exact_spdx_id` normalizes whitespace and rejects blank or whitespace-only inputs with `ValueError`.
- **Summary Fix:** Naive concatenation was replaced with deterministic selection of the primary concise summary (the first non-empty summary from the ordered chunks).
- **Prompt Guidance:** Updated `prompts/extract_v1.md` to strictly prohibit guessing or constructing SPDX identifiers from licence names or document titles, mandating `null` when not explicitly stated.

#### Live production API verification (27 Nov 2026)

Read-only verification of the live deployed production API on Render yielded the following outcomes:
- `GET /v1/documents?doc_kind=licence&limit=3`: HTTP/API success, total 30.
- `GET /v1/documents?doc_kind=license&limit=3`: HTTP/API success, total 17.
- `GET /v1/usage`: 142 calls, 15 errors, 20 fallbacks, ₹8.660985 total application usage over seven days.
- **Usage scope:** The ₹8.660985 figure represents application-wide usage across all services and operations over seven days, not an extraction-only cost (the extraction-specific estimated cost is ₹8.345240 across 122 extraction calls).
- **Academic Free License v2.0 live status:** The live record for Academic Free License v2.0 (`f-0e482ccfd4cdbfe36b04`) in production still returns `spdx_id: AFPL-2.0` and a repeated summary. The fix in commit `1e44ce5` is committed in the PR branch `feat/day-09-prompts` but has not yet been deployed to Render.
- Input tokens: 219,409
- Output tokens: 97,916

### Milestone 4 — extraction implementation

- `DocMeta` forbids extra fields and validates strict Pydantic types. The extraction prompt describes all 12 model fields.
- Automated tests exercise JSON parsing, repair, validation, truncation recovery, configurable chunking, metadata merging, and persistence confirmation.
- Output truncation is detected via provider finish reason (`length`, `max_tokens`) or incomplete JSON at EOF, triggering targeted compact-retry messages before raising `LLMOutputTruncatedError`.
- Configurable settings precedence is enforced: CLI flags override environment-backed Settings, which override code defaults (`DEFAULT_EXTRACTION_CHUNK_CHARS = 4000`, `DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS = 1000`, `DEFAULT_EXTRACTION_RPM = 1.0`).
- The runner counts success only after the repository update is confirmed and committed. Existing parser metadata is merged, not replaced.

### Groq 400 and 413 investigation

- The original HTTP 400 response body and its structured fields are not present in project artifacts. The earlier error report preserved status but not provider details, so its cause remains unconfirmed.
- The original HTTP 413 response body and request identifier are also unavailable. A later read-only measurement of `f-b506cdbfb790bde64dd8` found five request bodies between 10,617 and 11,466 bytes for 8,000-character chunks (the final chunk had 7,225 characters). This does not prove that document was the original failed request.
- HTTP 400 and 413 responses now use the same sanitizer for `message`, `type`, `code`, and `param`. Request text, prompt text and keys are redacted. The request logger reports only serialized body size.
- No model parameter or default chunk size was changed based on the incomplete incident evidence.

### Milestone 5 — resumable extraction readiness

- The default runner target is 50 successfully persisted documents. `--max-attempts` caps distinct document attempts, including failed attempts. Completed documents are filtered by `doc_kind`; failures remain eligible and are prioritized on the next run.
- Safe failure IDs, reason codes, attempt counts and timestamps are stored locally in ignored `data/extraction_failures.json`. The state contains no document text or provider error body and requires reruns from the same workspace to preserve retry priority. No database schema change is required.
- Per-attempt progress, successful input/output token totals, estimated INR cost and final counts are reported. LLM provider attempts continue to be recorded through the existing `llm_calls` repository.
- The configured request-start rate is shared across Groq and Gemini, and applies to HTTP retries as well as first attempts.
- `scripts/verify_extraction_metadata.py` performs read-only verification and prints aggregate validation outcomes. It can check selected document IDs with repeated `--document-id` options.
- The following commands are retained as the reproducible operator procedure. They are examples, not a record of the exact commands used across the six batches that achieved the 50-record milestone.

```powershell
# Inspect a small pending sample (read-only).
uv run python scripts/check_pending_extraction.py

# After explicit approval, make at most five document attempts with Groq only.
uv run python scripts/extract_metadata.py --limit 5 --max-attempts 5 --rpm 2 --groq-only

# Verify the five IDs printed by the runner; repeat --document-id for each ID.
uv run python scripts/verify_extraction_metadata.py --document-id ID1 --document-id ID2 --document-id ID3 --document-id ID4 --document-id ID5
```

The five-attempt command may yield fewer than five successes if any attempt fails. Failed IDs remain pending and are retried first on a later run. Verify that each selected document has all DocMeta fields and that parser keys (`source`, `path`, `parser`, `pages`, `tables`, `chars`) remain present. The verifier reports only counts and validation outcomes, not metadata values.

For a manual SQL cross-check, replace the placeholders with the IDs printed by the runner:

```sql
SELECT
  external_id,
  metadata ?& ARRAY[
    'doc_kind', 'year', 'language', 'summary', 'topics', 'spdx_id', 'name',
    'osi_approved', 'copyleft_type', 'permissions', 'conditions', 'limitations'
  ] AS docmeta_fields_present,
  metadata ?& ARRAY['source', 'path', 'parser', 'pages', 'tables', 'chars']
    AS parser_metadata_present
FROM documents
WHERE external_id IN ('<ID-1>', '<ID-2>', '<ID-3>', '<ID-4>', '<ID-5>');
```

The full-corpus command, after provider availability and explicit run approval, is:

```powershell
uv run python scripts/extract_metadata.py --limit 50 --rpm 2 --groq-only
```

This targets 50 successful saves and stops earlier when no pending documents remain or the Groq-only circuit opens. `--max-attempts N` can cap the number of distinct documents attempted in a run.

### Day 9 runtime acceptance status

- **Runtime Target Complete:** Exactly 50 documents have been extracted, saved to Neon PostgreSQL, and independently verified using read-only transactions across Batches 1 through 6.
- All 50 records possess 100% of the 12 required DocMeta fields and all 6 original ingestion metadata fields.
- 788 documents remain without DocMeta in the database.

### Local Day 9 validation

- `uv run pytest tests/unit`: **214 passed** (including all SPDX boundary, length parity, override validation, and non-concatenation regression tests).
- `uv run ruff check .`: passed with 0 errors.
- `uv run ruff format --check .`: passed with 87 files clean.
- `git diff --check`: passed with 0 whitespace or merge conflicts.
