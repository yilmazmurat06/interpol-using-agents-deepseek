# Interpol Red Notice Scraper

Retrieve full wanted-person data published by Interpol using a two-phase scraping approach, push it into a RabbitMQ queue, consume it into PostgreSQL, store raw payloads in MinIO, and display via a Flask web server with rich filtering and alarm markers.

## Architecture

```
Container A (Scraper + Producer)
  └── Phase 1: GET /notices/v1/red (list) → all entity IDs (nationality sweep)
  └── Phase 2: GET /notices/v1/red/{id} (detail) → full data per notice
  └── Anti-bot jitter between ALL calls → RabbitMQ producer

Container B (Web Server + Consumer)
  └── RabbitMQ consumer → PostgreSQL (full model) + MinIO (raw payloads)
  └── Flask web server → HTML (rich filtering, SSE auto-update, alarm markers)

Container C (RabbitMQ)
  └── Message broker with dead-letter queue

Supporting
  └── PostgreSQL 16 ← full notice records (incl. arrest_warrants JSONB)
  └── MinIO       ← raw notice payloads (objects keyed by notice_id/timestamp)
```

### Communication Flow

1. **Container A** sweeps Interpol's public API by nationality (ISO-2 codes), sub-slicing high-volume countries by sex + age buckets to work around the API's 160-record cap. Each notice detail is published to RabbitMQ as soon as it is fetched (streaming per-record publish).
2. **RabbitMQ** holds the messages in a durable queue with a dead-letter queue for failed messages.
3. **Container B** consumes messages from RabbitMQ, upserts records into PostgreSQL, stores the raw JSON payload in MinIO, and pushes SSE events to connected browsers.
4. **Browser** displays notices in a filterable, paginated grid with live updates via SSE.

## Prerequisites

- Docker Engine 24+ (with Compose V2 plugin)
- At least 4 GB of RAM allocated to Docker
- Internet access to `ws-public.interpol.int` (the Interpol API)

## Quick Start

```bash
# 1. Clone the repository (if you haven't already)
git clone <repo-url> interpol-red-notices
cd interpol-red-notices

# 2. Create environment configuration from the example
cp .env.example .env

# 3. (Optional) Edit .env to change credentials or tuning parameters.
#    Defaults are safe for local development but should be changed
#    before any public deployment.

# 4. Build and start all services
docker compose up --build

# 5. Open the web UI in your browser
open http://localhost:8080

# 6. View RabbitMQ management console (optional, for debugging)
open http://localhost:15672
#    Default credentials: guest / guest
```

### Stopping the Stack

```bash
# Stop all services (preserves volumes)
docker compose down

# Stop and delete all volumes (WARNING: deletes all data)
docker compose down -v
```

## Services

| Service | Container | Purpose | Host Port |
|---------|-----------|---------|-----------|
| `container-a` | `interpol-container-a` | Interpol API scraper + RabbitMQ producer | (none, internal only) |
| `container-b` | `interpol-container-b` | Flask web server + RabbitMQ consumer | `${FLASK_PORT}` (default 8080) |
| `rabbitmq` | `interpol-rabbitmq` | Message broker + management UI | `${RABBITMQ_PORT}` (default 5672), `${RABBITMQ_MGMT_PORT}` (default 15672) |
| `postgres` | `interpol-postgres` | Notice database | `${POSTGRES_PORT}` (default 5432) |
| `minio` | `interpol-minio` | Object storage for raw payloads | `${MINIO_API_PORT}` (default 9000), `${MINIO_CONSOLE_PORT}` (default 9001) |

## Healthchecks

The following services have healthchecks configured (PSC-5 compliant, using only binaries bundled in each image):

| Service | Command | Bundled Binary |
|---------|---------|----------------|
| `postgres` | `pg_isready -U interpol -d interpol` | `pg_isready` (PostgreSQL image) |
| `rabbitmq` | `rabbitmq-diagnostics ping` | `rabbitmq-diagnostics` (RabbitMQ image) |
| `minio` | `mc ready local` | `mc` (MinIO RELEASE images) |

All `depends_on` blocks use `condition: service_healthy` to enforce startup ordering.

## Web UI

- **Filter panel**: Text search, nationality select, gender, issuing country, charges keyword, alarms-only toggle
- **Sort options**: Newest first, name A-Z, nationality
- **Pagination**: Server-side pagination with compact controls and ellipses
- **Live updates**: SSE endpoint pushes new/alarm notices to the browser in real-time
- **Alarm markers**: Notices with meaningful field changes (name, charges, nationalities) are highlighted with an alarm badge
- **Notice cards**: Photo, full name, age, nationality flags (Unicode Regional Indicator Symbols), sex icon, charges excerpt, alarm badge

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | HTML UI (Jinja2 template) |
| `GET` | `/health` | Liveness check |
| `GET` | `/api/notices` | Paginated, filtered notice list (supports `page`, `page_size`, `nationality`, `sex`, `issuing_country`, `charges`, `is_alarm`, `sort`, `search`) |
| `GET` | `/api/notices/<notice_id>` | Single notice detail |
| `GET` | `/api/filters` | Available filter options (nationalities, issuing countries, total_notices) |
| `GET` | `/api/thumbnail/<notice_id>` | Image proxy (fetches from Interpol CDN via curl_cffi, streams bytes — no 302 redirect) |
| `GET` | `/api/stream` | SSE endpoint for live updates |

## Environment Variables

### RabbitMQ

| Variable | Default | Description |
|----------|---------|-------------|
| `RABBITMQ_DEFAULT_USER` | `guest` | RabbitMQ admin user |
| `RABBITMQ_DEFAULT_PASS` | `guest` | RabbitMQ admin password |
| `RABBITMQ_PORT` | `5672` | RabbitMQ AMQP port (host mapping) |
| `RABBITMQ_MGMT_PORT` | `15672` | RabbitMQ management UI port (host mapping) |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/%2F` | AMQP connection URL (used by both containers) |
| `RABBITMQ_QUEUE` | `red_notices` | Queue name for red notice messages |
| `RABBITMQ_HEARTBEAT` | `600` | Heartbeat seconds (must match server-side in docker-compose.yml) |

### PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DSN` | `host=postgres port=5432 dbname=interpol user=interpol password=interpol` | Full PostgreSQL DSN |
| `POSTGRES_PORT` | `5432` | PostgreSQL port (both host + internal) |
| `POSTGRES_DB` | `interpol` | Database name |
| `POSTGRES_USER` | `interpol` | Database user |
| `POSTGRES_PASSWORD` | `interpol` | Database password |

### MinIO

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ROOT_USER` | `minioadmin` | MinIO root user |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | MinIO root password |
| `MINIO_API_PORT` | `9000` | MinIO S3 API host port |
| `MINIO_CONSOLE_PORT` | `9001` | MinIO console UI host port |
| `MINIO_ENDPOINT` | `minio:9000` | MinIO S3 API endpoint (internal Docker hostname) |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key (used by container-b) |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key (used by container-b) |
| `MINIO_BUCKET` | `interpol-notices` | MinIO bucket name for raw payloads |
| `MINIO_SECURE` | `false` | Use HTTPS for MinIO (true/false) |

### Scraper (Container A)

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERPOL_BASE_URL` | `https://ws-public.interpol.int` | Interpol API base URL |
| `INTERPOL_LIST_PATH` | `/notices/v1/red` | List endpoint path |
| `INTERPOL_DETAIL_PATH` | `/notices/v1/red/{entity_id}` | Detail endpoint path (use `{entity_id}` placeholder) |
| `SCRAPE_NATIONALITY_CODES` | *(all ISO-2 codes)* | Comma-separated ISO-2 nationality codes to sweep |
| `SCRAPE_CONCURRENCY` | `4` | Number of concurrent detail fetches |
| `SCRAPE_INTERVAL_SECONDS` | `3600` | Seconds between full scrape cycles (1 hour) |
| `SCRAPE_MAX_RETRIES` | `3` | Max retries per failed HTTP request |
| `JITTER_MIN_SECONDS` | `1.0` | Minimum jitter delay between requests (seconds) |
| `JITTER_MAX_SECONDS` | `3.5` | Maximum jitter delay between requests (seconds) |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive 403s before circuit opens |
| `CIRCUIT_BREAKER_PAUSE_SECONDS` | `600` | Seconds to pause when circuit breaker opens |

### Web Server (Container B)

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_PORT` | `8080` | Port for Flask web server (host mapping) |
| `FLASK_HOST` | `0.0.0.0` | Flask bind address |
| `FLASK_DEBUG` | `false` | Flask debug mode (true/false) |
| `API_THUMBNAIL_TIMEOUT` | `15` | Timeout for thumbnail proxy fetches (seconds) |
| `DEFAULT_PAGE_SIZE` | `20` | Default notices per API page |
| `MAX_PAGE_SIZE` | `200` | Maximum allowed page size |

### Application-wide

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |

## Testing

```bash
# Run unit tests (requires Python 3.11+ and pip install -r tests/requirements.txt)
cd tests
pip install -r requirements.txt
pytest

# Run Playwright UI tests (requires the Docker stack to be running)
BASE_URL=http://localhost:8080 pytest tests/test_ui.py
```

Tests directory structure:
- `test_scraper.py` — Unit tests for the scraper (Phase 1, Phase 2, circuit breaker, jitter)
- `test_consumer.py` — Unit tests for the consumer and database layer
- `test_ui.py` — Playwright end-to-end UI tests

## Troubleshooting

### Credential or Volume Changes

If you change credentials after the first `docker compose up`, you must delete the volumes and recreate them:

```bash
docker compose down -v
docker compose up --build
```

This is because PostgreSQL, RabbitMQ, and MinIO store credentials in their data directories on first start.

### Logs

View logs for a specific service:

```bash
docker compose logs -f container-a
docker compose logs -f container-b
docker compose logs -f rabbitmq
docker compose logs -f postgres
docker compose logs -f minio
```

### Health Check Endpoints

- Flask liveness: `GET http://localhost:${FLASK_PORT}/health`
- RabbitMQ management UI: `http://localhost:${RABBITMQ_MGMT_PORT}`
- MinIO console: `http://localhost:${MINIO_CONSOLE_PORT}`

### Container A Scraper Not Producing Notices

1. Check logs: `docker compose logs -f container-a`
2. Verify RabbitMQ is healthy: `docker compose ps rabbitmq`
3. The first scrape cycle may take several minutes (jitter between all API calls).

### Container B Not Connecting

1. Verify all 3 dependencies are healthy: `docker compose ps`
2. Check consumer logs: `docker compose logs -f container-b`
3. Verify RabbitMQ URL matches the credentials in `.env`.

### Akamai Rate-Limiting

If the scraper receives sustained HTTP 403s, the circuit breaker will pause all requests for 600 seconds (default). To adjust:

- Lower `SCRAPE_CONCURRENCY` (default 4)
- Raise `JITTER_MIN_SECONDS` and `JITTER_MAX_SECONDS` (default 1.0–3.5)
- Raise `CIRCUIT_BREAKER_THRESHOLD` (default 5)
- Raise `CIRCUIT_BREAKER_PAUSE_SECONDS` (default 600)

The relationship: `concurrency / avg_jitter` should stay under ~10 req/s.
Default: `4 / ((1.0 + 3.5) / 2)` = ~4 / 2.25 ≈ 1.8 req/s (safe).
