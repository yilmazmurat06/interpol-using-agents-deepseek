#!/usr/bin/env bash
# check_engineering_decisions.sh — verify CLAUDE.md "Engineering Decisions"
# section is honored by the actual code.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

section "Python 3.11 base image"
if grep -RE 'FROM python:3\.11' "$REPO_ROOT/container_a/Dockerfile" "$REPO_ROOT/container_b/Dockerfile" 2>/dev/null | wc -l | grep -qE '^\s*2\s*$'; then
    pass "both Dockerfiles use python:3.11"
else
    fail "Dockerfile(s) do not pin python:3.11"
fi

section "No :latest tags"
if grep -nE ':latest' "$REPO_ROOT/docker-compose.yml" "$REPO_ROOT"/container_*/Dockerfile 2>/dev/null; then
    fail ":latest tag found above"
else
    pass "no :latest tags anywhere"
fi

section "Non-root user in Dockerfiles"
for dockerfile in "$REPO_ROOT"/container_*/Dockerfile; do
    if grep -nE '^USER ' "$dockerfile" >/dev/null; then
        pass "$(basename $(dirname $dockerfile))/Dockerfile drops to non-root user"
    else
        fail "$(basename $(dirname $dockerfile))/Dockerfile runs as root"
    fi
done

section "Healthchecks on stateful services"
for svc in rabbitmq postgres minio; do
    # Within each service block, find healthcheck:
    if awk -v svc="$svc" '
        $1==svc":" { in_svc=1; next }
        in_svc && /^[a-z]/ && !/^[[:space:]]/ { in_svc=0 }
        in_svc && /healthcheck:/ { found=1; exit }
        END { exit !found }
    ' "$REPO_ROOT/docker-compose.yml"; then
        pass "$svc has healthcheck"
    else
        fail "$svc missing healthcheck"
    fi
done

section "Named volumes for stateful services"
for vol in rabbitmq_data postgres_data minio_data; do
    if grep -qE "^\s*$vol:" "$REPO_ROOT/docker-compose.yml"; then
        pass "named volume $vol declared"
    else
        fail "named volume $vol missing"
    fi
done

section "depends_on: service_healthy graph"
if grep -A1 'depends_on:' "$REPO_ROOT/docker-compose.yml" | grep -qE 'condition:\s*service_healthy'; then
    pass "depends_on uses service_healthy condition"
else
    fail "depends_on lacks service_healthy — startup order not enforced"
fi

section "Five services exactly"
_svc_count=$(awk '/^services:/{f=1;next} f && /^[a-z][a-z0-9-]*:$/ {n++} END{print n+0}' "$REPO_ROOT/docker-compose.yml")
if [[ "$_svc_count" == "5" ]]; then
    pass "exactly 5 services declared"
else
    fail "expected 5 services, found $_svc_count"
fi

summary
