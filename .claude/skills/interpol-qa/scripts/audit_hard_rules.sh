#!/usr/bin/env bash
# audit_hard_rules.sh — verify every PSC-N pattern and Interpol-specific
# Hard Rule has a grep-verified implementation in the codebase.
#
# This is QA's Step 0 — BLOCKING. A failure here means PASS verdict is
# forbidden no matter what the tests say.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

section "Akamai TLS bypass (curl_cffi for API + images)"
if grep_code 'from curl_cffi import requests' "$REPO_ROOT/container_a" >/dev/null; then
    pass "container_a uses curl_cffi"
else
    fail "container_a does not import curl_cffi (Akamai will 403)"
fi
if grep_code 'impersonate=' "$REPO_ROOT/container_a" >/dev/null; then
    pass "curl_cffi Session uses impersonate=..."
else
    fail "curl_cffi imported but no impersonate= argument found"
fi
if grep -RInE '^import requests$|^from requests' "$REPO_ROOT/container_a" --include='*.py' >/dev/null 2>&1; then
    fail "container_a still has bare 'import requests' — must be curl_cffi"
else
    pass "no bare 'import requests' in container_a"
fi
if grep_code 'from curl_cffi' "$REPO_ROOT/container_b" >/dev/null; then
    pass "container_b uses curl_cffi (for image proxy)"
else
    fail "container_b does not use curl_cffi — image proxy will fail with browser-side 403s"
fi

section "PSC-1: psycopg2 transaction containment"
if [[ -f "$REPO_ROOT/container_b/db.py" ]]; then
    if grep -nE 'rollback\(\)' "$REPO_ROOT/container_b/db.py" >/dev/null; then
        pass "db.py contains rollback() calls"
    else
        fail "db.py has NO rollback() — InFailedSqlTransaction cascade guaranteed"
    fi
    if grep -nE 'TRANSACTION_STATUS_INERROR' "$REPO_ROOT/container_b/db.py" >/dev/null; then
        pass "_cursor() clears INERROR state defensively"
    else
        warn "no INERROR pre-cursor clearing — recommended but not strictly required"
    fi
    # Sanity: every commit() in db.py should be paired with rollback() in same file
    _commits=$(grep -c 'self\._conn\.commit()' "$REPO_ROOT/container_b/db.py" || true)
    _rollbacks=$(grep -c 'self\._conn\.rollback()' "$REPO_ROOT/container_b/db.py" || true)
    if (( _rollbacks >= _commits )); then
        pass "rollback() count ($_rollbacks) >= commit() count ($_commits)"
    else
        fail "more commits ($_commits) than rollbacks ($_rollbacks) in db.py"
    fi
else
    fail "container_b/db.py not found"
fi

section "PSC-2: RabbitMQ heartbeat configured on BOTH sides"
if grep -nE 'params\.heartbeat\s*=\s*[1-9][0-9]{2,}' "$REPO_ROOT"/container_*/[!_]*.py >/dev/null 2>&1; then
    pass "pika params.heartbeat set to >= 100s"
else
    fail "pika params.heartbeat missing or too low (<100s)"
fi
if grep -nE 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS.*heartbeat' "$REPO_ROOT/docker-compose.yml" >/dev/null 2>&1; then
    pass "RabbitMQ server-side heartbeat configured in compose"
else
    fail "compose lacks RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS heartbeat → server still 60s"
fi

section "PSC-3: Streaming publish (per-record callback)"
if grep -nE 'on_record' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "scrape() accepts on_record callback"
else
    fail "no on_record callback — scraper batches at end of cycle"
fi
if grep -nE 'threading\.Lock|Lock\(\)' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "publish call is serialized with a Lock"
else
    fail "concurrent publish without Lock — pika BlockingConnection is not thread-safe"
fi

section "PSC-4: Server-side pagination"
if grep -nE 'def count_notices' "$REPO_ROOT/container_b/db.py" >/dev/null 2>&1; then
    pass "count_notices() exists in db.py"
else
    fail "count_notices() missing — pagination total cannot be computed"
fi
if grep -nE 'offset' "$REPO_ROOT/container_b/db.py" >/dev/null 2>&1; then
    pass "get_all_notices accepts offset"
else
    fail "no offset support in db.py — pagination impossible"
fi
if grep -nE '"total"\s*:|"pages"\s*:|"page_size"\s*:' "$REPO_ROOT/container_b/app.py" >/dev/null 2>&1; then
    pass "/api/notices returns {total, pages, page_size}"
else
    fail "/api/notices response does not include pagination envelope"
fi

section "PSC-5: Healthcheck binaries (no curl/wget on minimal images)"
_compose="$REPO_ROOT/docker-compose.yml"
if grep -nE 'mc ready local|rabbitmq-diagnostics|pg_isready' "$_compose" >/dev/null 2>&1; then
    pass "healthchecks use image-native binaries"
else
    fail "healthchecks may rely on curl/wget — check image bundles"
fi
if grep -nE '"CMD-SHELL".*curl |"CMD".*curl ' "$_compose" >/dev/null 2>&1; then
    warn "compose has a curl-based healthcheck — verify image bundles curl"
fi

section "PSC-6: Concurrency × jitter rate ≤ ~10 req/s"
if [[ -f "$REPO_ROOT/.env" || -f "$REPO_ROOT/.env.example" ]]; then
    _envfile="$REPO_ROOT/.env"
    [[ -f "$_envfile" ]] || _envfile="$REPO_ROOT/.env.example"
    _conc=$(grep -E '^SCRAPE_CONCURRENCY=' "$_envfile" | head -1 | cut -d= -f2 | tr -d ' ')
    _jmin=$(grep -E '^JITTER_MIN_SECONDS=' "$_envfile" | head -1 | cut -d= -f2 | tr -d ' ')
    _jmax=$(grep -E '^JITTER_MAX_SECONDS=' "$_envfile" | head -1 | cut -d= -f2 | tr -d ' ')
    if [[ -n "$_conc" && -n "$_jmin" && -n "$_jmax" ]]; then
        _avg=$(awk -v a="$_jmin" -v b="$_jmax" 'BEGIN{printf "%.4f",(a+b)/2}')
        _rate=$(awk -v c="$_conc" -v j="$_avg" 'BEGIN{printf "%.2f",c/j}')
        info "concurrency=$_conc, avg jitter=${_avg}s → ~${_rate} req/s"
        if awk -v r="$_rate" 'BEGIN{exit !(r<=12)}'; then
            pass "request rate within Akamai-safe band"
        else
            fail "sustained rate ~${_rate} req/s exceeds Akamai ~10 req/s threshold"
        fi
    else
        warn "could not parse SCRAPE_CONCURRENCY / JITTER_* from $_envfile"
    fi
fi

section "Anti-bot circuit breaker"
if grep -nE 'consecutive_403|circuit_open|_CIRCUIT_' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "circuit breaker state present in scraper"
else
    fail "no circuit breaker — Akamai penalty box will spiral retries"
fi

section "160-record cap workaround (nationality sweep)"
if grep -nE '_ISO2_COUNTRIES|ISO2_COUNTRIES|country_codes' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "ISO-2 country list present"
else
    fail "no country list — Phase 1 will only collect 160 records"
fi
if grep -nE 'sexId|sex_id.*=|_SEX_BUCKETS' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "sex sub-slicing implemented"
else
    warn "no sex sub-slicing — high-volume countries (RU, etc.) will be incomplete"
fi
if grep -nE 'ageMin|ageMax|_AGE_BUCKETS' "$REPO_ROOT/container_a"/*.py >/dev/null 2>&1; then
    pass "age sub-slicing implemented"
else
    warn "no age sub-slicing — sex-only sub-slicing may not be enough for RU/M etc."
fi

section "Image proxy via curl_cffi (NOT 302 redirect)"
if grep -nE 'def api_thumbnail|/api/thumbnail' "$REPO_ROOT/container_b/app.py" >/dev/null 2>&1; then
    if grep -nE 'return redirect\(.*image_url' "$REPO_ROOT/container_b/app.py" >/dev/null 2>&1; then
        fail "thumbnail route 302-redirects to CDN — browser will be blocked by Akamai"
    else
        pass "thumbnail route does not 302 to CDN"
    fi
    if grep -nE 'upstream\.content|_image_session' "$REPO_ROOT/container_b/app.py" >/dev/null 2>&1; then
        pass "thumbnail route streams bytes via curl_cffi"
    else
        fail "thumbnail route does not stream bytes — check implementation"
    fi
else
    fail "/api/thumbnail route missing"
fi

section "Notice ID slash-handling in Flask routes"
if grep -nE '<path:notice_id>' "$REPO_ROOT/container_b/app.py" >/dev/null 2>&1; then
    pass "<path:notice_id> converter used"
else
    fail "Flask routes use <notice_id> not <path:notice_id> — IDs with slashes will 404"
fi

section "Playwright tests: BASE_URL env var (required for live orchestrator runs)"
_test_ui="$REPO_ROOT/tests/test_ui.py"
if [[ ! -f "$_test_ui" ]]; then
    warn "tests/test_ui.py not found — Playwright tests not yet written"
else
    # Must read BASE_URL from environment, not hardcode localhost
    if grep -nE 'BASE_URL|os\.environ|os\.getenv' "$_test_ui" >/dev/null 2>&1; then
        pass "test_ui.py reads BASE_URL from environment"
    else
        fail "test_ui.py does not read BASE_URL — hardcoded localhost will break orchestrator live runs"
    fi

    # Must NOT have hardcoded http://localhost without env fallback
    if grep -nE '"http://localhost:[0-9]+"' "$_test_ui" >/dev/null 2>&1; then
        fail "test_ui.py has hardcoded localhost URL — use os.environ.get('BASE_URL', 'http://localhost:PORT')"
    else
        pass "no hardcoded localhost URL found in test_ui.py"
    fi

    # BASE_URL should be used as the page navigation target
    if grep -nE 'goto.*BASE_URL|page\.goto.*BASE_URL|BASE_URL.*goto' "$_test_ui" >/dev/null 2>&1; then
        pass "Playwright page.goto() uses BASE_URL"
    else
        warn "test_ui.py: BASE_URL declared but page.goto() may not use it — verify manually"
    fi

    # pytest-playwright or playwright must be in test requirements
    _test_req="$REPO_ROOT/tests/requirements.txt"
    if [[ -f "$_test_req" ]]; then
        if grep -iqE 'playwright' "$_test_req"; then
            pass "playwright in tests/requirements.txt"
        else
            fail "playwright not in tests/requirements.txt — live test runner will fail to install"
        fi
    else
        warn "tests/requirements.txt not found — playwright install may fail at runtime"
    fi
fi

summary
