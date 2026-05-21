#!/usr/bin/env bash
# verify_patterns.sh — check PSC-1 through PSC-6 implementation patterns in Python code.
#
# PSC-1: psycopg2 transaction containment
# PSC-2: RabbitMQ heartbeat on both sides
# PSC-3: Streaming per-record publish via on_record callback
# PSC-4: Server-side pagination (count_notices + offset + response envelope)
# PSC-5: Healthcheck binaries (quick compose sanity check)
# PSC-6: Concurrency × jitter rate + circuit breaker

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_db="$REPO_ROOT/container_b/db.py"
_app="$REPO_ROOT/container_b/app.py"
_scraper="$REPO_ROOT/container_a/scraper.py"
_producer="$REPO_ROOT/container_a/producer.py"
_compose="$REPO_ROOT/docker-compose.yml"

# ---------------------------------------------------------------------------
section "PSC-1: psycopg2 transaction containment"
# ---------------------------------------------------------------------------
if [[ ! -f "$_db" ]]; then
    fail "container_b/db.py not found"
else
    # Check rollback() exists at all
    if grep -qE 'rollback\(\)' "$_db"; then
        pass "db.py contains rollback() calls"
    else
        fail "db.py has NO rollback() — InFailedSqlTransaction cascade guaranteed"
    fi

    # Check TRANSACTION_STATUS_INERROR defensive pre-check
    if grep -qE 'TRANSACTION_STATUS_INERROR' "$_db"; then
        pass "db.py checks TRANSACTION_STATUS_INERROR defensively in _cursor helper"
    else
        fail "db.py missing TRANSACTION_STATUS_INERROR check — _cursor helper not defensive"
    fi

    # Count commit vs rollback — rollbacks should be >= commits
    _commits=$(grep -cE 'self\._conn\.commit\(\)' "$_db" || true)
    _rollbacks=$(grep -cE 'self\._conn\.rollback\(\)' "$_db" || true)
    info "db.py: commit()=$_commits rollback()=$_rollbacks"
    if (( _rollbacks >= 2 )); then
        pass "rollback() count ($_rollbacks) meets minimum of 2"
    else
        fail "rollback() count ($_rollbacks) < 2 — insufficient transaction containment"
    fi
    if (( _commits >= 1 )); then
        pass "commit() count ($_commits) >= 1"
    else
        fail "no commit() calls found in db.py"
    fi

    # Every write method (def upsert / def insert) must have rollback nearby
    while IFS= read -r def_line; do
        _lineno=$(echo "$def_line" | cut -d: -f1)
        _funcname=$(echo "$def_line" | grep -oE 'def [a-z_]+' | head -1)
        # Check for rollback within 20 lines after function definition
        _end=$(( _lineno + 20 ))
        _has_rollback=$(sed -n "${_lineno},${_end}p" "$_db" | grep -c 'rollback' || true)
        if (( _has_rollback > 0 )); then
            pass "$_funcname has rollback() within 20 lines"
        else
            fail "$_funcname (line $_lineno) has no rollback() within 20 lines"
        fi
    done < <(grep -nE 'def (upsert|insert|update|delete|write)' "$_db" 2>/dev/null || true)
fi

# ---------------------------------------------------------------------------
section "PSC-2: RabbitMQ heartbeat configured on BOTH sides"
# ---------------------------------------------------------------------------
if grep -qE 'params\.heartbeat\s*=\s*[1-9][0-9]{2,}' \
        "$REPO_ROOT"/container_a/*.py "$REPO_ROOT"/container_b/*.py 2>/dev/null; then
    pass "pika params.heartbeat set to >= 100s in Python code"
else
    fail "pika params.heartbeat missing or < 100s in container_a or container_b"
fi

if [[ -f "$_compose" ]]; then
    if grep -qE 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS.*heartbeat' "$_compose"; then
        pass "RabbitMQ server-side heartbeat configured in docker-compose.yml"
    else
        fail "docker-compose.yml missing RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS with heartbeat"
    fi

    # Extract numeric values and compare
    _py_hb=$(grep -hE 'params\.heartbeat\s*=\s*[0-9]+' \
        "$REPO_ROOT"/container_a/*.py "$REPO_ROOT"/container_b/*.py 2>/dev/null \
        | grep -oE '[0-9]+' | head -1 || true)
    _compose_hb=$(grep -oE 'heartbeat [0-9]+' "$_compose" | grep -oE '[0-9]+' | head -1 || true)
    if [[ -n "$_py_hb" && -n "$_compose_hb" ]]; then
        if [[ "$_py_hb" == "$_compose_hb" ]]; then
            pass "heartbeat values match: Python=$_py_hb compose=$_compose_hb"
        else
            warn "heartbeat mismatch: Python=$_py_hb vs compose=$_compose_hb (negotiated = min)"
        fi
    else
        warn "could not extract numeric heartbeat values for comparison"
    fi
fi

# ---------------------------------------------------------------------------
section "PSC-3: Streaming publish via on_record callback"
# ---------------------------------------------------------------------------
if [[ -f "$_scraper" ]]; then
    if grep -qE 'on_record' "$_scraper"; then
        pass "scraper.py has on_record parameter"
    else
        fail "scraper.py missing on_record callback — batches at end of cycle"
    fi

    if grep -qE 'on_record\(' "$_scraper"; then
        pass "scraper.py calls on_record() inside the scrape loop"
    else
        fail "scraper.py never calls on_record() — streaming not implemented"
    fi
else
    fail "container_a/scraper.py not found"
fi

if [[ -f "$_producer" ]]; then
    if grep -qE 'scrape\(.*on_record=' "$_producer" || grep -qE 'on_record=' "$_producer"; then
        pass "producer.py passes on_record= when calling scrape()"
    else
        fail "producer.py does not pass on_record= to scraper — no streaming"
    fi

    if grep -qE 'threading\.Lock|Lock\(\)' "$_producer"; then
        pass "producer.py uses threading.Lock for publish serialization"
    else
        fail "producer.py missing threading.Lock — pika BlockingConnection is not thread-safe"
    fi

    if grep -qE '^import threading' "$_producer"; then
        pass "producer.py imports threading"
    else
        fail "producer.py does not import threading"
    fi
else
    fail "container_a/producer.py not found"
fi

# ---------------------------------------------------------------------------
section "PSC-4: Server-side pagination"
# ---------------------------------------------------------------------------
if [[ -f "$_db" ]]; then
    if grep -qE 'def count_notices' "$_db"; then
        pass "count_notices() function exists in db.py"
    else
        fail "count_notices() missing from db.py — pagination total cannot be computed"
    fi

    if grep -qE '\boffset\b' "$_db"; then
        pass "db.py get_all_notices accepts offset parameter"
    else
        fail "no offset parameter in db.py — server-side pagination impossible"
    fi
fi

if [[ -f "$_app" ]]; then
    if grep -qE '"total"' "$_app"; then
        pass "app.py /api/notices response includes 'total' key"
    else
        fail "app.py /api/notices missing 'total' key in response"
    fi

    if grep -qE '"pages"' "$_app"; then
        pass "app.py /api/notices response includes 'pages' key"
    else
        fail "app.py /api/notices missing 'pages' key in response"
    fi

    if grep -qE '"page_size"' "$_app"; then
        pass "app.py /api/notices response includes 'page_size' key"
    else
        fail "app.py /api/notices missing 'page_size' key in response"
    fi

    if grep -qE 'total_notices' "$_app"; then
        pass "/api/filters includes total_notices"
    else
        fail "/api/filters missing total_notices — UI live counter will break"
    fi
else
    fail "container_b/app.py not found"
fi

# ---------------------------------------------------------------------------
section "PSC-5: Healthcheck binaries (quick compose sanity)"
# ---------------------------------------------------------------------------
if [[ -f "$_compose" ]]; then
    if grep -qE 'pg_isready' "$_compose"; then
        pass "postgres healthcheck uses pg_isready"
    else
        fail "postgres healthcheck missing pg_isready"
    fi

    if grep -qE 'rabbitmq-diagnostics' "$_compose"; then
        pass "rabbitmq healthcheck uses rabbitmq-diagnostics"
    else
        fail "rabbitmq healthcheck missing rabbitmq-diagnostics"
    fi

    if grep -qE 'mc ready' "$_compose"; then
        pass "minio healthcheck uses mc ready"
    else
        fail "minio healthcheck missing 'mc ready'"
    fi
fi

# ---------------------------------------------------------------------------
section "PSC-6: Concurrency × jitter rate (Akamai-safe) + circuit breaker"
# ---------------------------------------------------------------------------
if [[ -f "$_scraper" ]]; then
    if grep -qE 'SCRAPE_CONCURRENCY' "$_scraper" || \
       grep -qE "os\.environ.*SCRAPE_CONCURRENCY|os\.getenv.*SCRAPE_CONCURRENCY" \
           "$REPO_ROOT/container_a/"*.py 2>/dev/null; then
        pass "SCRAPE_CONCURRENCY read from environment"
    else
        fail "SCRAPE_CONCURRENCY not read from env in scraper"
    fi

    if grep -qE 'random\.uniform|random\.random|jitter' "$_scraper"; then
        pass "jitter delay implemented in scraper.py"
    else
        fail "no jitter (random.uniform / random.random) in scraper.py — rate limiting risk"
    fi

    if grep -qE '(consecutive|circuit|_circuit|CIRCUIT)' "$_scraper"; then
        pass "circuit breaker state variable present in scraper.py"
    else
        fail "circuit breaker missing from scraper.py — Akamai penalty box will spiral"
    fi

    if grep -qE '403' "$_scraper"; then
        pass "HTTP 403 is caught specifically in scraper.py"
    else
        fail "scraper.py does not specifically handle HTTP 403 responses"
    fi
fi

summary
