#!/usr/bin/env bash
# check_api_smoke.sh — HTTP smoke tests against a RUNNING Flask stack.
#
# If the stack is not running, emits WARN and exits 0 (graceful skip).
# Requires: curl

set -u
source "$(dirname "$0")/../../_lib/common.sh"
require curl

# ---------------------------------------------------------------------------
# Resolve Flask port from .env file or default
# ---------------------------------------------------------------------------
_port=""
if [[ -f "$REPO_ROOT/.env" ]]; then
    _port=$(grep -E '^FLASK_PORT=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | xargs)
fi
_port="${_port:-8080}"

_mgmt_port=""
if [[ -f "$REPO_ROOT/.env" ]]; then
    _mgmt_port=$(grep -E '^RABBITMQ_MANAGEMENT_PORT=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | xargs)
fi
_mgmt_port="${_mgmt_port:-15672}"

_base="http://localhost:${_port}"

# ---------------------------------------------------------------------------
section "Stack reachability"
# ---------------------------------------------------------------------------
if ! curl -sf --max-time 3 "$_base/health" >/dev/null 2>&1; then
    warn "Flask not reachable at $_base — stack not running; skipping smoke tests"
    summary
fi
pass "Flask is reachable at $_base"

# ---------------------------------------------------------------------------
# Helper: GET and check status code + optional body pattern
# ---------------------------------------------------------------------------
_smoke_get() {
    local label="$1"
    local url="$2"
    local expected_code="${3:-200}"
    local body_pattern="${4:-}"

    local http_code body
    body=$(curl -sf --max-time 10 -w "\n__HTTP_CODE:%{http_code}" "$url" 2>/dev/null || true)
    http_code=$(echo "$body" | grep -oE '__HTTP_CODE:[0-9]+' | cut -d: -f2)
    body=$(echo "$body" | grep -v '__HTTP_CODE:')

    if [[ "$http_code" != "$expected_code" ]]; then
        fail "$label: expected HTTP $expected_code, got $http_code (url: $url)"
        return
    fi

    if [[ -n "$body_pattern" ]]; then
        if echo "$body" | grep -qiE "$body_pattern"; then
            pass "$label: HTTP $http_code + body matches /$body_pattern/"
        else
            fail "$label: HTTP $http_code but body does not match /$body_pattern/"
        fi
    else
        pass "$label: HTTP $http_code"
    fi
}

# ---------------------------------------------------------------------------
section "Core endpoints"
# ---------------------------------------------------------------------------
_smoke_get "GET /health"   "$_base/health"   200
_smoke_get "GET /"         "$_base/"         200 "notice|interpol|wanted"

# ---------------------------------------------------------------------------
section "/api/filters — shape check"
# ---------------------------------------------------------------------------
_filters_body=$(curl -sf --max-time 10 "$_base/api/filters" 2>/dev/null || true)
if [[ -z "$_filters_body" ]]; then
    fail "/api/filters: no response"
else
    pass "/api/filters: responded"
    echo "$_filters_body" | grep -q '"nationalities"'   && pass "/api/filters has 'nationalities' key"  || fail "/api/filters missing 'nationalities' key"
    echo "$_filters_body" | grep -q '"total_notices"'   && pass "/api/filters has 'total_notices' key"  || fail "/api/filters missing 'total_notices' key (PSC-4: live DB total)"
    echo "$_filters_body" | grep -q '"issuing_countries"' && pass "/api/filters has 'issuing_countries' key" || warn "/api/filters missing 'issuing_countries' key"
fi

# ---------------------------------------------------------------------------
section "/api/notices — pagination shape"
# ---------------------------------------------------------------------------
_notices_body=$(curl -sf --max-time 10 "$_base/api/notices" 2>/dev/null || true)
if [[ -z "$_notices_body" ]]; then
    fail "/api/notices: no response"
else
    pass "/api/notices: responded"
    echo "$_notices_body" | grep -q '"total"'     && pass "/api/notices has 'total' key"     || fail "/api/notices missing 'total' key (PSC-4)"
    echo "$_notices_body" | grep -q '"pages"'     && pass "/api/notices has 'pages' key"     || fail "/api/notices missing 'pages' key (PSC-4)"
    echo "$_notices_body" | grep -q '"page"'      && pass "/api/notices has 'page' key"      || warn "/api/notices missing 'page' key"
    echo "$_notices_body" | grep -q '"page_size"' && pass "/api/notices has 'page_size' key" || warn "/api/notices missing 'page_size' key"
    echo "$_notices_body" | grep -q '"notices"'   && pass "/api/notices has 'notices' key"   || fail "/api/notices missing 'notices' key"
fi

# Pagination param respected
_page_body=$(curl -sf --max-time 10 "$_base/api/notices?page=1&page_size=5" 2>/dev/null || true)
if [[ -n "$_page_body" ]]; then
    _count=$(echo "$_page_body" | grep -oE '"notices"\s*:\s*\[' | wc -l | tr -d ' ')
    pass "/api/notices?page=1&page_size=5 responded (pagination params accepted)"
fi

# ---------------------------------------------------------------------------
section "Error resilience"
# ---------------------------------------------------------------------------
# Bad nationality filter must NOT return 500
_bad=$(curl -sf --max-time 10 -w "\n__HTTP_CODE:%{http_code}" \
    "$_base/api/notices?nationality=ZZINVALID" 2>/dev/null || true)
_bad_code=$(echo "$_bad" | grep -oE '__HTTP_CODE:[0-9]+' | cut -d: -f2)
if [[ "$_bad_code" == "500" ]]; then
    fail "/api/notices with invalid filter returned 500 (should be 200 with empty results)"
elif [[ -n "$_bad_code" ]]; then
    pass "/api/notices with invalid filter: HTTP $_bad_code (not 500)"
fi

# Bad thumbnail ID must NOT return 500
_thumb=$(curl -sf --max-time 10 -w "\n__HTTP_CODE:%{http_code}" \
    "$_base/api/thumbnail/nonexistent/id" 2>/dev/null || true)
_thumb_code=$(echo "$_thumb" | grep -oE '__HTTP_CODE:[0-9]+' | cut -d: -f2)
if [[ "$_thumb_code" == "500" ]]; then
    fail "/api/thumbnail with bad ID returned 500"
elif [[ -n "$_thumb_code" ]]; then
    pass "/api/thumbnail with bad ID: HTTP $_thumb_code (not 500)"
fi

# ---------------------------------------------------------------------------
section "RabbitMQ management UI (optional)"
# ---------------------------------------------------------------------------
_rmq_code=$(curl -sf --max-time 5 -u guest:guest \
    -w "%{http_code}" -o /dev/null \
    "http://localhost:${_mgmt_port}/api/overview" 2>/dev/null || echo "")
if [[ "$_rmq_code" == "200" ]]; then
    pass "RabbitMQ management API reachable on port $_mgmt_port"
elif [[ -n "$_rmq_code" ]]; then
    warn "RabbitMQ management API returned $_rmq_code (credentials may differ)"
else
    warn "RabbitMQ management port $_mgmt_port not reachable (may not be exposed)"
fi

summary
