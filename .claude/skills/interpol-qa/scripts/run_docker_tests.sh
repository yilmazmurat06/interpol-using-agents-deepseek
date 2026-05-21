#!/usr/bin/env bash
# run_docker_tests.sh — Build the full Docker stack, run pytest, tear it down.
#
# Called by the QA agent during the Docker integration test phase.
# If Docker is not available in this environment, emits WARN and exits 0.
#
# Environment variables:
#   BASE_URL   URL pytest will target (default: http://localhost:8080)
#   REPO_ROOT  auto-detected from common.sh (walk up to CLAUDE.md)

set -u
source "$(dirname "$0")/../../_lib/common.sh"

BASE_URL="${BASE_URL:-http://localhost:8080}"

# ── Docker availability ──────────────────────────────────────────────────────
section "Docker availability"
if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found in this environment — skipping Docker integration tests"
    exit 0
fi
pass "docker: $(docker --version)"

# ── Environment setup ────────────────────────────────────────────────────────
section "Environment setup"
if [[ ! -f "$REPO_ROOT/.env.example" ]]; then
    fail ".env.example not found — cannot configure stack"
    summary
fi
cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
pass "copied .env.example → .env"

# ── Wipe any stale volumes from previous runs ────────────────────────────────
section "Wiping stale volumes"
cd "$REPO_ROOT"
docker compose down -v 2>/dev/null || true
pass "stale volumes removed"

# ── Build and start ──────────────────────────────────────────────────────────
section "docker compose up --build -d"
if ! docker compose up --build -d; then
    fail "docker compose up --build -d failed"
    summary
fi
pass "stack started (detached)"

# ── Wait for all services to be healthy (up to 3 minutes) ───────────────────
section "Waiting for services healthy (max 180s)"
_timeout=180
_elapsed=0
while (( _elapsed < _timeout )); do
    _unhealthy=$(docker compose ps 2>/dev/null | grep -cE "(unhealthy|starting)" || true)
    if (( _unhealthy == 0 )); then
        pass "all services healthy after ${_elapsed}s"
        break
    fi
    sleep 5
    _elapsed=$((_elapsed + 5))
done
if (( _elapsed >= _timeout )); then
    warn "health wait timed out after ${_timeout}s — continuing anyway"
fi

# ── Show current stack state ─────────────────────────────────────────────────
section "Stack state"
docker compose ps
docker compose logs --tail=20

# ── Install test dependencies ────────────────────────────────────────────────
section "Installing test dependencies"
if [[ -f "$REPO_ROOT/tests/requirements.txt" ]]; then
    pip install -q -r "$REPO_ROOT/tests/requirements.txt"
    pass "test requirements installed"
else
    warn "tests/requirements.txt not found — skipping pip install"
fi

if command -v playwright >/dev/null 2>&1; then
    playwright install chromium --with-deps 2>/dev/null \
        && pass "playwright chromium installed" \
        || warn "playwright install failed — UI tests may be skipped"
else
    warn "playwright binary not found — UI tests may be skipped"
fi

# ── Run pytest ───────────────────────────────────────────────────────────────
section "pytest tests/ (BASE_URL=$BASE_URL)"
_pytest_rc=0
BASE_URL="$BASE_URL" python -m pytest "$REPO_ROOT/tests/" -v --timeout=60 2>&1 \
    || _pytest_rc=$?

if (( _pytest_rc == 0 )); then
    pass "pytest: all tests passed"
else
    fail "pytest: tests failed (exit code $_pytest_rc)"
fi

# ── Tear down ────────────────────────────────────────────────────────────────
section "docker compose down -v"
docker compose down -v 2>/dev/null || true
pass "stack torn down"

summary
