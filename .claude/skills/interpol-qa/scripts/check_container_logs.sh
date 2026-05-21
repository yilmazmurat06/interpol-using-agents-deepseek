#!/usr/bin/env bash
# check_container_logs.sh — scan Docker container logs for known error patterns.
#
# Requires the stack to be running. Graceful skip if not.
# CRITICAL patterns → FAIL
# WARNING patterns  → WARN
# Notable patterns  → INFO (normal but worth logging)

set -u
source "$(dirname "$0")/../../_lib/common.sh"

# ---------------------------------------------------------------------------
section "Docker availability"
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found — skipping container log scan"
    summary
fi

_project_dir="$REPO_ROOT"
_running=$(docker compose -f "$_project_dir/docker-compose.yml" ps --status running 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')
if [[ "$_running" == "0" ]]; then
    warn "no running containers — stack not up, skipping log scan"
    summary
fi

pass "stack running ($_running container(s)), scanning logs..."

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# CRITICAL: these indicate code bugs / data corruption — always FAIL
_CRITICAL_PATTERNS=(
    "InFailedSqlTransaction"
    "Traceback (most recent call last)"
    "CRITICAL"
    "panic:"
    "OCI runtime"
    "Cannot allocate memory"
    "Segmentation fault"
)

# WARNING: operational issues that may resolve but indicate misconfiguration
_WARN_PATTERNS=(
    "ConnectionClosedByBroker"
    "StreamLostError"
    "AMQPConnectionError"
    "heartbeat"
    " 403 "
    "circuit.*open\|CIRCUIT OPEN\|PAUSING"
    "error connecting"
    "dial tcp.*refused"
)

# NOTABLE: normal operational events worth tracking
_NOTABLE_PATTERNS=(
    "circuit.*open\|circuit breaker"
    "reconnect"
    "retry"
    "rate.limit\|rate limit"
)

# ---------------------------------------------------------------------------
_services=("container-a" "container-b" "rabbitmq" "postgres" "minio")

for svc in "${_services[@]}"; do
    section "Logs: $svc"

    _logs=$(docker compose -f "$_project_dir/docker-compose.yml" logs --tail=100 "$svc" 2>/dev/null || true)
    if [[ -z "$_logs" ]]; then
        warn "$svc: no logs available"
        continue
    fi

    _svc_ok=1

    # Critical checks
    for pat in "${_CRITICAL_PATTERNS[@]}"; do
        _hits=$(echo "$_logs" | grep -iE "$pat" | head -5 || true)
        if [[ -n "$_hits" ]]; then
            fail "$svc: CRITICAL pattern '$pat' found in logs"
            while IFS= read -r line; do
                info "  → $line"
            done <<< "$_hits"
            _svc_ok=0
        fi
    done

    # Warning checks
    for pat in "${_WARN_PATTERNS[@]}"; do
        _hits=$(echo "$_logs" | grep -iE "$pat" | head -3 || true)
        if [[ -n "$_hits" ]]; then
            warn "$svc: warning pattern '$pat' found"
            while IFS= read -r line; do
                info "  → $line"
            done <<< "$_hits"
        fi
    done

    # Notable checks (INFO only)
    for pat in "${_NOTABLE_PATTERNS[@]}"; do
        _hits=$(echo "$_logs" | grep -iE "$pat" | head -2 || true)
        if [[ -n "$_hits" ]]; then
            info "$svc: notable pattern '$pat'"
        fi
    done

    (( _svc_ok )) && pass "$svc: no critical errors in last 100 log lines"
done

# ---------------------------------------------------------------------------
section "container-a specific: scrape progress"
# ---------------------------------------------------------------------------
_a_logs=$(docker compose -f "$_project_dir/docker-compose.yml" logs --tail=50 container-a 2>/dev/null || true)
if [[ -n "$_a_logs" ]]; then
    _phases=$(echo "$_a_logs" | grep -iE "phase|scraping|publishing|fetched|notice_id" | wc -l | tr -d ' ')
    if (( _phases > 0 )); then
        pass "container-a: scrape activity visible in logs ($_phases matching lines)"
    else
        warn "container-a: no scrape activity in last 50 lines (scraper may not have started yet)"
    fi

    # Circuit breaker status
    _circuit=$(echo "$_a_logs" | grep -iE "circuit|403 consecutive" | head -3 || true)
    if [[ -n "$_circuit" ]]; then
        info "container-a circuit breaker activity detected:"
        while IFS= read -r line; do info "  → $line"; done <<< "$_circuit"
    fi
fi

# ---------------------------------------------------------------------------
section "container-b specific: consumer + flask"
# ---------------------------------------------------------------------------
_b_logs=$(docker compose -f "$_project_dir/docker-compose.yml" logs --tail=50 container-b 2>/dev/null || true)
if [[ -n "$_b_logs" ]]; then
    echo "$_b_logs" | grep -qiE "running on|flask|serving" \
        && pass "container-b: Flask started" \
        || warn "container-b: no Flask startup message in last 50 lines"

    echo "$_b_logs" | grep -qiE "upserted|consumed|notice_id" \
        && pass "container-b: consumer activity visible" \
        || info "container-b: no consumer activity yet (may be waiting for messages)"
fi

summary
