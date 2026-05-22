# Interpol Red Notice Scraper

A two-phase scraper that retrieves full wanted-person data published by Interpol,
pushes it through a RabbitMQ message queue, persists it in PostgreSQL, stores raw
payloads in MinIO, and serves a rich-filterable web UI with live updates via SSE.

## Architecture

```
Container A (Scraper + Producer)
  ├── Phase 1: Nationality-sweep across ISO-2 codes → collect all entity IDs
  ├── Phase 2: Detail fetch per ID → build enriched record
  └── Per-record publish to RabbitMQ (streaming, via on_record callback)

RabbitMQ (Message Broker)
  └── Main queue: interpol_notices
  └── Dead-letter queue: interpol_notices.dlq

Container B (Web Server + Consumer)
  ├── Background consumer: reads from RabbitMQ → upserts to PostgreSQL
  ├── Stores raw payloads in MinIO
  ├── SSE dispatcher for live UI updates
  └── Flask web server with:
      ├── GET /  — HTML UI
      ├── GET /api/notices  — paginated, filtered JSON
      ├── GET /api/notices/<id>  — single record
      ├── GET /api/filters  — filter dropdown options
      ├── GET /api/thumbnail/<id>  — proxied image (via curl_cffi)
      ├── GET /api/stream  — SSE live updates
      └── GET /health  — liveness check

PostgreSQL (Database)
  └── Full notice records with arrest_warrants as JSONB

MinIO (Object Storage)
  └── Raw JSON payloads stored per notice per scrape cycle
```

## Prerequisites

- Docker (version 24+) and Docker Compose (v2.23+)
- Git
- At least 4 GB of available RAM (for all 5 containers)

## Quick Start

1. **Clone the repository and enter the directory:**

   ```bash
   git clone <repo-url>
   cd interpol-using-agents-deepseek
   ```

2. **Create the environment file:**

   ```bash
   cp .env.example .env
   ```

3. **Edit credentials (recommended for any network-exposed deployment):**

   Modify the following in `.env`:
   - `RABBITMQ_DEFAULT_PASS` — change from `guest`
   - `POSTGRES_PASSWORD` — change from `postgres`
   - `MINIO_ROOT_PASSWORD` — change from `minioadmin`

4. **Build and start all services:**

   ```bash
   docker compose up --build
   ```

   The first build will download base images and install Python dependencies.
   Subsequent starts use cached layers.

5. **Open the web UI:**

   Navigate to [http://localhost:8080](http://localhost:8080).

   Container A (scraper) will begin its first scrape cycle immediately.
   Scraped notices flow through RabbitMQ → Consumer → DB → Web UI in real time.

## Port Reference

| Host Port | Service     | Purpose                              |
|-----------|-------------|--------------------------------------|
| `8080`    | container-b | Flask web UI (configurable via WEB_PORT) |
| `5432`    | postgres    | PostgreSQL direct access (debugging) |
| `9000`    | minio       | MinIO S3 API                         |
| `9001`    | minio       | MinIO Web Console                    |
| `15672`   | rabbitmq    | RabbitMQ Management UI               |

## Environment Variables

### RabbitMQ

| Variable | Default | Description |
|----------|---------|-------------|
| `RABBITMQ_DEFAULT_USER` | `guest` | RabbitMQ management user |
| `RABBITMQ_DEFAULT_PASS` | `guest` | RabbitMQ management password |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/%2F` | Full AMQP URL for producers/consumers |
| `RABBITMQ_QUEUE` | `interpol_notices` | Main queue name |
| `RABBITMQ_MGMT_PORT` | `15672` | Host port for RabbitMQ Management UI |

### PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `postgres` | PostgreSQL superuser |
| `POSTGRES_PASSWORD` | `postgres` | PostgreSQL password |
| `POSTGRES_DB` | `interpol` | Database name |
| `POSTGRES_PORT` | `5432` | Host port for PostgreSQL |
| `POSTGRES_DSN` | `postgresql://postgres:postgres@postgres:5432/interpol` | Full DSN for application |

### MinIO

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ROOT_USER` | `minioadmin` | MinIO admin access key |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | MinIO admin secret key |
| `MINIO_ENDPOINT` | `minio:9000` | Internal endpoint (Docker service name) |
| `MINIO_BUCKET` | `interpol-notices` | Bucket for raw notice payloads |
| `MINIO_API_PORT` | `9000` | Host port for MinIO S3 API |
| `MINIO_CONSOLE_PORT` | `9001` | Host port for MinIO Web Console |

### Scraper (Container A)

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERPOL_SOURCE_URL` | `https://ws-public.interpol.int` | Base URL for Interpol API |
| `SCRAPE_INTERVAL_SECONDS` | `3600` | Delay between full scrape cycles |
| `JITTER_MIN_SECONDS` | `0.3` | Minimum jitter delay between API calls (seconds) |
| `JITTER_MAX_SECONDS` | `0.8` | Maximum jitter delay between API calls (seconds) |
| `SCRAPE_CONCURRENCY` | `4` | Number of parallel detail-fetch threads |
| `SCRAPE_MAX_RETRIES` | `3` | Max retry attempts per failed API request |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive 403s before pausing all requests |
| `CIRCUIT_BREAKER_PAUSE_SECONDS` | `600` | Global pause duration when circuit breaker trips |

### Web Server (Container B)

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_PORT` | `8080` | Host/container port for the Flask web UI |

### General

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR |

## Troubleshooting

### "Connection refused" errors on startup

All 5 services start in parallel, but Container B and Container A wait for their
dependencies to be healthy. Allow 30–60 seconds for PostgreSQL, RabbitMQ, and
MinIO to complete their first-time initialisation. Check logs with:

```bash
docker compose logs -f postgres   # Database
docker compose logs -f rabbitmq   # Message broker
docker compose logs -f minio      # Object storage
docker compose logs -f container-a  # Scraper / producer
docker compose logs -f container-b  # Web server / consumer
```

### Credential changes don't take effect

Stateful services (PostgreSQL, RabbitMQ, MinIO) persist data in named Docker
volumes. **Changing credentials in `.env` does NOT update existing volumes.**
To apply new credentials, wipe the volumes and recreate:

```bash
docker compose down -v
docker compose up --build
```

⚠️ This destroys all existing data (notices, messages, payloads).

### RabbitMQ connection drops

If the consumer logs show frequent reconnection, the heartbeat timeout may be
too short. Default heartbeat is 600s on both sides (server via
`RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS` and client via `params.heartbeat = 600`).
If you override `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS`, ensure the value is
consistent with the pika client heartbeat in `producer.py` and `consumer.py`.

### Web UI shows "Database not available"

The Flask app starts before the database schema is fully initialised. Container B
retries connections automatically. Wait 10–15 seconds and refresh the page.
If the issue persists, check the container-b logs.

### Scraper shows "Circuit breaker TRIPPED"

The Interpol API is fronted by Akamai which applies rate limiting and TLS
fingerprinting. Multiple consecutive HTTP 403 responses trigger a 600-second
global pause. This is normal behaviour — the scraper resumes automatically
after the pause expires. To reduce the likelihood of triggering the breaker,
decrease `SCRAPE_CONCURRENCY` or increase `JITTER_MIN_SECONDS` / `JITTER_MAX_SECONDS`.

## Health Checks

All stateful services and application containers have Docker healthchecks:

| Service | Command | Notes |
|---------|---------|-------|
| PostgreSQL | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` | Native PostgreSQL tool |
| RabbitMQ | `rabbitmq-diagnostics ping` | Native RabbitMQ tool |
| MinIO | `mc ready local` | Uses MinIO Client bundled in image (needs `MC_HOST_local` env var) |
| container-a | `python -c "import socket; socket.create_connection(('rabbitmq', 5672), 2)"` | Verifies RabbitMQ reachability |
| container-b | `python -c "import socket; socket.create_connection(('localhost', PORT), 2)"` | Verifies Flask server listening |

## Running Tests

Unit tests (no Docker required):

```bash
cd tests
pip install -r requirements.txt
python -m pytest
```

Playwright browser tests (require a running Docker stack with `BASE_URL` set):

```bash
BASE_URL=http://localhost:8080 python -m pytest tests/test_ui.py
```

## Testing Notes

- Playwright tests must use `BASE_URL` environment variable — never hardcode URLs.
- SSE endpoints are infinite streams. Tests must use `page.goto(BASE_URL)` first,
  then `page.evaluate()` with `fetch("/api/stream")` + `AbortController` to avoid
  30-second timeouts.
- All database tests use a shared `Database` instance; PSC-1 patterns must be
  observed (connection rollback after reads, try/commit/except/rollback for writes).
- Rate-sensitive scraper tests should mock the HTTP layer to avoid hitting the
  live Interpol API during unit tests.
