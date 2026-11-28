# Ingestion report

<!-- generated:start -->
_Generated 28 Nov 2026 19:14 from the database._

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
| Items in the dead-letter list | 21 |

Model calls for extraction: 249 · tokens in 397,453 · tokens out 133,215 · cost at list price Rs 20.87

Last job `390d5b35446a4789b00c39560ac2535f`: 20 requested, 10 ok, 10 failed, 137.0 minutes.

Dead-letter list by stage: gave up after 3 tries 18, gave up 3
<!-- generated:end -->

## What I learnt

1. The actual parsed corpus yielded 7,808 pages and 5,172,206 text tokens, within 6.5% of Monday's capacity model estimate (7,376 pages and 5.53M tokens).
2. Asynchronous queueing with ARQ decouples HTTP ingestion acceptance from long-running LLM extraction, eliminating request timeouts and connection drops.
3. Crash recovery requires PostgreSQL row-level locking (`FOR UPDATE SKIP LOCKED`) combined with Redis key lifecycle awareness to guarantee idempotency and prevent duplicate job processing.

## Live Crash Recovery Verification (28 Nov 2026)

On 28 Nov 2026 at 19:12 UTC, startup crash recovery was verified against the database for Job `390d5b35446a4789b00c39560ac2535f`:

1. **Worker Command**: Executed in batch mode using `uv run arq licenselens.workers.main.WorkerSettings --burst`.
2. **Detection & Reconciliation**: Startup recovery identified Item 46 left in `status = 'running'` with `attempts = 3`. Because attempts reached `max_tries = 3`, Item 46 was transitioned to `status = 'failed'` while preserving its previous error detail (`try 2 failed: CircuitOpenError: gemini circuit is open`).
3. **Final Job Counts**:
   - Done: 10
   - Failed: 10
   - Running: 0
   - Queued: 0
   - Total: 20 items (100% of requested items accounted for).
4. **Idempotency & Integrity**: Zero new ingestion jobs were created, zero completed items were reprocessed, and zero items were enqueued into Redis.
5. **Outcome Clarification**: The crash recovery mechanism itself was verified live, but 10 of the 20 items failed due to processing and upstream LLM provider errors (rate limits, 503 service unavailable, timeouts, and circuit-breaker openings). Crash recovery safely resolved the uncompleted state; it does not convert provider failures into successes.
