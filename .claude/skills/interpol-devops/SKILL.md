---
name: interpol-devops
description: 'Use this skill when creating Dockerfiles, docker-compose configuration, environment variable documentation, and deployment documentation for a multi-container Python microservice system.'
argument-hint: 'Produce Docker, env config, and docs for a multi-container Python project.'
tools: execute, read, edit
---

# DevOps — Multi-Container Python Services

## When to Use
- Writing Dockerfiles for Python application containers
- Creating docker-compose configuration for a system with message broker, database, and object storage dependencies
- Defining environment variable schemas and documentation
- Writing setup and operational documentation (README)

## Inputs
- Paths to Python services and their `requirements.txt` files
- Required ports for web UI and service dependencies
- Full list of environment variables consumed by each service

## Step 0: Run verification scripts (BLOCKING)

Before declaring any DevOps deliverable done, run the full verification suite:

```bash
bash .claude/skills/interpol-devops/scripts/run_all.sh
```

This checks:
- **check_env_bidirectional** — compose ↔ .env.example ↔ Python 3-way name match
- **check_healthcheck_binaries** — healthcheck commands use image-bundled tools only
- **check_image_pinning** — no `:latest` tags, Python 3.11 everywhere
- **check_no_hardcoded** — no credentials/IPs/ports hardcoded in compose
- **check_protocol_negotiation** — RabbitMQ heartbeat configured on BOTH sides
- **check_readme** — README has all required sections + env var coverage

**Any FAIL = fix before handoff to QA. Do not skip.**

## Procedure

### 1. Dockerfiles
For each Python service:
- Use a **multi-stage build**: a `builder` stage for installing dependencies, then a slim final image that only copies installed packages and app code
- Base image: `python:3.x-slim` (match the Python version used in development)
- Install dependencies from `requirements.txt` in the builder stage
- Set an explicit `WORKDIR`
- Add a non-root user and switch to it before `CMD`
- Add a `HEALTHCHECK` instruction appropriate to the service (e.g., `curl -f http://localhost:<port>/health` for web servers, or a simple process check for workers)
- Never use the `latest` tag for the base image in production — pin to a specific version

### 2. docker-compose.yml
- Define all services with explicit image versions or `build` context
- Use named volumes for stateful services (database, object store, message broker)
- Define a shared network so services can reach each other by service name
- Add `healthcheck` blocks to stateful services; use `depends_on: condition: service_healthy` to enforce startup order
- Source all configurable values from environment variables — no hardcoded credentials, ports, or paths
- Map only the ports that need to be accessible from the host; internal service-to-service ports stay internal
- **Healthcheck binary rule:** For each `healthcheck.test` command, verify the binary actually exists in the target image. Minimal/distroless images (especially ARM64 variants) frequently lack `curl`, `wget`, and even `sh`. Prefer image-native tooling:
  - PostgreSQL → `pg_isready`
  - RabbitMQ → `rabbitmq-diagnostics ping`
  - MinIO → `mc ready local` (set `MC_HOST_local` env var; `mc` is bundled in all RELEASE images)
  - Generic Python service → `python -c "import socket; socket.create_connection(('localhost', PORT), 2)"`
  Do NOT default to `curl -f http://...` unless the image is documented to bundle curl.
- **Protocol-negotiation rule:** When a protocol negotiates a value between client and server (RabbitMQ heartbeat, gRPC max message size, HTTP/2 window), the negotiated value is typically `min(server, client)`. Setting only the client side has no effect if the server default is lower. Always configure BOTH. For RabbitMQ heartbeat specifically: `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS: "-rabbit heartbeat 600"` on the broker AND `params.heartbeat = 600` in every consumer/producer.

### 3. Environment configuration
- Create a `.env.example` file listing every environment variable consumed by any service
- Group variables by service (RabbitMQ, PostgreSQL, MinIO, scraper, web server, etc.)
- Include a comment on each variable explaining its purpose and format
- Document which variables are safe to leave at their defaults and which must be changed before production use
- Never commit a populated `.env` to version control; add it to `.gitignore`
- **Bidirectional name-consistency check (MANDATORY):**
  1. For every `${VAR}` reference in `docker-compose.yml`, confirm `VAR` is documented in `.env.example`. (one direction)
  2. For every `VAR=` line in `.env.example`, confirm `VAR` is either referenced in `docker-compose.yml` as `${VAR}` OR read in Python source via `os.environ`. (other direction)
  3. Name mismatches like `JITTER_MIN` (env) vs `JITTER_MIN_SECONDS` (compose/code) are a recurring failure mode — the env file says one thing, the code expects another, the value is silently ignored. Use `grep` to confirm BOTH directions before finalising.

### 4. Documentation (README.md)
- Prerequisites (Docker version, Docker Compose version)
- Quick-start: `cp .env.example .env` → edit credentials → `docker-compose up --build`
- Architecture overview: what each container does, how they communicate
- Port reference table (host port → service)
- Environment variable reference (with defaults and notes)
- Common troubleshooting: volume wipes for credential changes, log commands, health check endpoints
- Note: credential or volume changes require `docker-compose down -v` to take effect on stateful services

## Done Criteria
- Dockerfiles build successfully for all Python services
- `docker-compose up --build` starts all containers with correct networking and service dependencies
- All configuration is driven by environment variables — no hardcoded values in any file
- `.env.example` documents every variable with comments
- README covers setup, run, architecture, ports, env vars, and troubleshooting

## Outputs
- Dockerfiles, `docker-compose.yml`, `.env.example`, and `README.md` only
