---
name: interpol-full-stack-dev
description: 'Use this skill when implementing the Python-only Interpol red notice pipeline: scheduled scraper/producer, RabbitMQ publisher/consumer, PostgreSQL persistence, MinIO object storage, and web UI update/alarm logic for Container A and Container B.'
argument-hint: 'Implement Python code for the Interpol pipeline and web server.'
---

# Interpol Full-Stack Developer (Python)

## When to Use
- Building Container A and Container B application code in Python.
- Implementing Interpol data scraping, RabbitMQ producer/consumer, PostgreSQL writes, and MinIO storage.
- Creating the web server HTML view and alarm behavior for updated records.

## Inputs (environment-config)
- INTERPOL_SOURCE_URL
- SCRAPE_INTERVAL_SECONDS
- RABBITMQ_URL
- RABBITMQ_QUEUE
- RABBITMQ_EXCHANGE (optional)
- POSTGRES_DSN
- MINIO_ENDPOINT
- MINIO_ACCESS_KEY
- MINIO_SECRET_KEY
- MINIO_BUCKET
- WEB_HOST
- WEB_PORT
- QA_REPORT_URL or QA_REPORT_LISTEN
- LOG_LEVEL

## Step 0: Self-check (BLOCKING)

Before declaring implementation complete, run:

```bash
bash .claude/skills/interpol-full-stack-dev/scripts/run_all.sh
```

This checks:
- **verify_patterns** — PSC-1..6 (psycopg2 containment, heartbeat both sides, streaming publish, pagination shape, healthchecks, rate math)
- **check_curl_cffi_usage** — no bare `import requests`, `impersonate=` set, curl_cffi in requirements
- **check_psycopg2_containment** — every write method has try/commit/except/rollback, INERROR guard present
- **check_pagination_shape** — response keys, count_notices, offset param, UI controls, page-1 reset
- **check_streaming_publish** — on_record callback, threading.Lock, no batch publish
- **check_circuit_breaker** — consecutive 403 counter, global pause, lock protection

**Any FAIL = fix before handoff. Do not pass code to QA with known violations.**

## Procedure
1. Confirm data source URLs: list endpoint and detail endpoint. Both are configurable via env vars. Log both on startup.
2. Define an OOP data model for notice records. Include ALL fields from the detail endpoint: forename, name, date_of_birth, place_of_birth, sex_id, height, weight, nationalities, languages, eyes_colors_id, hairs_id, distinguishing_marks, arrest_warrants (JSONB), image_url.
3. Container A (scraper/producer):
   - **HTTP client:** Use `curl_cffi.requests.Session(impersonate="chrome120")` for ALL outbound HTTP. The standard `requests` library is blocked by Akamai TLS fingerprinting on this CDN (applies to API and images alike). Do NOT set a User-Agent header — the impersonation handles it.
   - **Exception handling:** `curl_cffi.requests` does NOT expose `HTTPError` / `RequestException`. Catch `Exception` and inspect `getattr(exc, "response", None)` to differentiate HTTP errors from network errors.
   - Phase 1: Sweep notice IDs by ISO-2 nationality (the unfiltered API caps at 160 records per query — see CLAUDE.md → Anti-Bot section). For countries with `total > 160`, sub-slice by `sexId × ageMin/ageMax`. Dedupe by entity_id.
   - Phase 2: For each ID, call detail endpoint → build full NoticeRecord. Apply jitter between requests.
   - **Concurrency:** Run Phase 2 detail fetches on a `ThreadPoolExecutor` (size from `SCRAPE_CONCURRENCY` env, default 4). Keep `concurrency / avg_jitter` under ~10 req/s to stay below Akamai's blocking threshold.
   - **Circuit breaker:** Track consecutive HTTP 403s across workers. After N (default 5), open the circuit and pause all requests globally for 5–10 minutes. Otherwise retries during a penalty window deepen the block.
   - **Streaming publish:** `scrape()` MUST accept an `on_record` callback that the producer wires to `publish_notice`. Records flow to RabbitMQ as they are built — no end-of-cycle batch publish. Wrap the publish call in a `threading.Lock` since pika's `BlockingConnection` is not thread-safe.
   - Skip individual notices that fail after max retries — do not abort the whole cycle.
4. Persist raw payloads to MinIO. Use deterministic keys: `notices/<notice_id>/<fetched_at>.json`.
5. Container B (consumer/web server):
   - Start a background consumer that reads from RabbitMQ and acknowledges after successful persistence.
   - **psycopg2 transaction containment:** Every write method MUST wrap its body in `try: … self._conn.commit(); except Exception: self._conn.rollback(); raise`. Every read method MUST end with `self._conn.rollback()` to release the implicit SELECT transaction. The `_cursor()` helper SHOULD pre-emptively rollback if `conn.get_transaction_status() == TRANSACTION_STATUS_INERROR` — defensive against poisoning from elsewhere. Without this, one bad row poisons the connection and every subsequent statement fails with `InFailedSqlTransaction`.
   - **Thread safety:** Consumer thread and Flask request handlers each get their OWN `Database` instance (separate psycopg2 connections). A single shared connection across threads is undefined behaviour.
   - **RabbitMQ heartbeat:** Set `params.heartbeat = 600` in the consumer's pika `URLParameters`. The DevOps side must set the matching `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS` so the negotiated value actually takes effect.
   - Upsert into PostgreSQL. Set `is_alarm = True` only when meaningful fields changed.
   - Store `received_at` for display.
6. Database layer must support filtered queries: by nationality, sex_id, issuing_country (from arrest_warrants JSONB), charges keyword, is_alarm. Also expose `get_filter_options()` for dropdown population AND `count_notices(filters)` for pagination totals. `get_all_notices()` MUST accept an `offset` parameter alongside `limit`.
7. Web UI:
   - Filter panel populated from `GET /api/filters` on load.
   - Filtering sends requests to `GET /api/notices` with query params (server-side). Response is `{notices, total, page, page_size, pages}` — NOT a bare list. The UI renders compact pagination (Prev / 1 … 5 [6] 7 … last / Next) and resets to page 1 on every filter change.
   - Cards show: photo (via `/api/thumbnail/<path:notice_id>` proxy — NEVER raw CDN URL), name, age from date_of_birth, nationality flags, sex, charges, issuing countries.
   - SSE for live updates. The handler MUST only insert new cards when the user is on page 1 with no active filters; otherwise pagination would silently drift. SSE handler MAY skip in-place updates of existing cards entirely (user preference is to keep cards stable while browsing).
8. Provide `GET /api/filters` endpoint returning distinct nationalities, issuing countries, AND `total_notices` (unfiltered DB count) so the UI can show the absolute DB total live (not the page count).
9. Provide `GET /api/thumbnail/<path:notice_id>` route that fetches the CDN image via `curl_cffi` and streams the bytes back to the browser. Do NOT 302-redirect — Akamai blocks browser fetches too.
10. Provide `POST /api/qa-report` endpoint for QA error reporting.

## Done Criteria
- Container A publishes valid JSON messages and respects the scrape interval.
- Container A uses `curl_cffi` (NOT `requests`) for every outbound HTTP call against the Interpol host.
- Phase 1 implements the nationality-sweep workaround (sub-sliced by sex × age for high-volume countries).
- Phase 2 is concurrent (`ThreadPoolExecutor`) and a circuit breaker is in place for sustained 403s.
- Producer publishes per-record (streaming) via an `on_record` callback, NOT in an end-of-cycle batch.
- Container B consumes messages, persists to PostgreSQL, and updates alarms on changes.
- Every `upsert_notice` / write method has explicit `try/commit/except/rollback/raise`. Every read method rolls back the implicit SELECT txn. `_cursor()` clears `INERROR` state defensively.
- Pika `params.heartbeat = 600` in both producer and consumer (paired with the matching compose-side `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS`).
- Raw payloads are stored in MinIO (or explicitly documented if skipped).
- Flask `GET /api/notices` returns `{notices, total, page, page_size, pages}` and accepts `page`/`page_size`. Backed by a paired `count_notices()` query.
- `GET /api/thumbnail/<path:notice_id>` proxies image bytes via `curl_cffi` — NOT a 302 to the CDN.
- UI renders pagination controls and a live DB-total counter that updates from `/api/filters.total_notices` and SSE increments.
- All configuration values are read from environment variables.
- `tests/test_ui.py` uses `BASE_URL = os.environ.get("BASE_URL", "http://localhost:PORT")` — never a hardcoded URL. The orchestrator sets `BASE_URL` when running Playwright against the live Docker stack.
- `tests/requirements.txt` includes `playwright` and `pytest-playwright`.

## Outputs
- Python source code only (no Docker or docs).
- Clear module boundaries for scraper, queue, persistence, and web UI.
