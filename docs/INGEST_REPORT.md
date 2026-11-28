# Ingestion report

<!-- generated:start -->
_Generated 28 Nov 2026 18:50 from the database._

| What | Count |
|---|---|
| Documents in the database | 818 |
| Pages | 7808 |
| Tokens of text (characters ÷ 4) | 5,172,206 |
| Files the parser gave up on | 0 |
| Documents with an extracted record | 27 |
| Documents whose extraction failed | 0 |

| Queue | Count |
|---|---|
| Ingest jobs started | 6 |
| Items finished (done or skipped) | 42 |
| Items that needed more than one try | 27 |
| Items in the dead-letter list | 20 |

Model calls for extraction: 249 · tokens in 397,453 · tokens out 133,215 · cost at list price Rs 20.87

Last job `390d5b35446a4789b00c39560ac2535f`: 20 requested, 10 ok, 9 failed, 1 running, 6.4 minutes.

Dead-letter list by stage: gave up after 3 tries 18, gave up 2
<!-- generated:end -->

## What I learnt

1. The actual parsed corpus yielded 7,808 pages and 5,172,206 text tokens, within 6.5% of Monday's capacity model estimate (7,376 pages and 5.53M tokens).
2. Asynchronous queueing with ARQ decouples HTTP ingestion acceptance from long-running LLM extraction, eliminating request timeouts and connection drops.
3. Crash recovery requires PostgreSQL row-level locking (`FOR UPDATE SKIP LOCKED`) combined with Redis key lifecycle awareness to guarantee idempotency and prevent duplicate job processing.
