#!/usr/bin/env bash
# check_container_health.sh — runtime health check for the Docker stack.
#
# Requires the stack to be running. If Docker is not available or no containers
# are up, emits WARN and exits 0 (graceful skip — not a static analysis failure).

set -u
source "$(dirname "$0")/../../_lib/common.sh"

# ---------------------------------------------------------------------------
section "Docker availability"
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found — skipping runtime health check"
    summary
fi

# Check if any containers are running for this project
_project_dir="$REPO_ROOT"
_running=$(docker compose -f "$_project_dir/docker-compose.yml" ps --status running 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')

if [[ "$_running" == "0" ]]; then
    warn "no running containers detected — stack not up, skipping runtime health check"
    summary
fi

pass "docker available and stack appears to be running ($_running container(s))"

# ---------------------------------------------------------------------------
section "Per-service status checks"
# ---------------------------------------------------------------------------
_services=("container-a" "container-b" "rabbitmq" "postgres" "minio")

for svc in "${_services[@]}"; do
    _status=$(docker compose -f "$_project_dir/docker-compose.yml" ps "$svc" 2>/dev/null \
        | tail -n +2 | awk '{print $NF}' | head -1 || true)

    if [[ -z "$_status" ]]; then
        warn "$svc: container not found (may not be started yet)"
        continue
    fi

    if echo "$_status" | grep -qiE 'Exit|exited|unhealthy'; then
        fail "$svc: status=$_status"
        info "Last 20 log lines for $svc:"
        docker compose -f "$_project_dir/docker-compose.yml" logs --tail=20 "$svc" 2>/dev/null || true
    elif echo "$_status" | grep -qiE 'Up|running'; then
        pass "$svc: status=$_status"
    else
        warn "$svc: unexpected status=$_status"
    fi
done

# ---------------------------------------------------------------------------
section "Healthcheck status for stateful services"
# ---------------------------------------------------------------------------
_healthcheck_services=("rabbitmq" "postgres" "minio")

for svc in "${_healthcheck_services[@]}"; do
    _health=$(docker inspect \
        "$(docker compose -f "$_project_dir/docker-compose.yml" ps -q "$svc" 2>/dev/null)" \
        --format='{{.State.Health.Status}}' 2>/dev/null || true)

    if [[ -z "$_health" || "$_health" == "<no value>" ]]; then
        warn "$svc: no healthcheck info available"
    elif [[ "$_health" == "healthy" ]]; then
        pass "$svc: healthcheck=healthy"
    elif [[ "$_health" == "starting" ]]; then
        warn "$svc: healthcheck=starting (may still be initializing)"
    elif [[ "$_health" == "unhealthy" ]]; then
        fail "$svc: healthcheck=unhealthy"
        info "Last 20 log lines for $svc:"
        docker compose -f "$_project_dir/docker-compose.yml" logs --tail=20 "$svc" 2>/dev/null || true
    else
        warn "$svc: healthcheck=$_health"
    fi
done

summary
