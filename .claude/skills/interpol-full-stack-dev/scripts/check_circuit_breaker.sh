#!/usr/bin/env bash
# check_circuit_breaker.sh — verify circuit breaker implementation in scraper.py.
#
# After N consecutive 403s, the scraper must open the circuit and pause globally.
# Without this, retries during an Akamai penalty window deepen the block.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_scraper="$REPO_ROOT/container_a/scraper.py"

if [[ ! -f "$_scraper" ]]; then
    fail "container_a/scraper.py not found"
    summary
fi

# ---------------------------------------------------------------------------
section "Circuit breaker counter for consecutive 403s"
# ---------------------------------------------------------------------------
if grep -qE '(consecutive|_consecutive|circuit|_circuit|CIRCUIT)' "$_scraper"; then
    pass "circuit breaker counter/state variable present in scraper.py"
else
    fail "circuit breaker missing from scraper.py — Akamai penalty window will spiral"
fi

# ---------------------------------------------------------------------------
section "Global pause / sleep when circuit is open"
# ---------------------------------------------------------------------------
if grep -qE '_circuit_open|circuit_open_until|circuit_until|_pausing|PAUSING' "$_scraper"; then
    pass "scraper.py has circuit-open pause/sleep variable"
else
    fail "scraper.py missing circuit-open pause variable — no global pause on 403 burst"
fi

# ---------------------------------------------------------------------------
section "HTTP 403 caught specifically"
# ---------------------------------------------------------------------------
if grep -qE '403' "$_scraper"; then
    pass "HTTP 403 handled specifically in scraper.py"
else
    fail "scraper.py never checks for HTTP 403 — circuit cannot trigger on Akamai blocks"
fi

# ---------------------------------------------------------------------------
section "Configurable threshold constant (default 5)"
# ---------------------------------------------------------------------------
if grep -qE 'CIRCUIT_THRESHOLD|CIRCUIT_BREAKER_THRESHOLD|consecutive.*=.*5|_THRESHOLD.*=.*5|threshold.*5' "$_scraper"; then
    pass "circuit breaker threshold constant found (default ~5)"
else
    warn "no explicit circuit threshold constant — should be configurable (default 5)"
fi

# ---------------------------------------------------------------------------
section "Recovery / pause duration (300–600s)"
# ---------------------------------------------------------------------------
if grep -qE '\b(600|300|360|480)\b' "$_scraper"; then
    pass "recovery pause duration (300–600s range) present in scraper.py"
else
    warn "no 300–600s recovery pause duration found — circuit may open but not wait long enough"
fi

# ---------------------------------------------------------------------------
section "threading.Lock protecting circuit state"
# ---------------------------------------------------------------------------
if grep -qE 'threading\.Lock|Lock\(\)' "$_scraper"; then
    pass "scraper.py uses threading.Lock (protects circuit state in threaded env)"
else
    fail "scraper.py missing threading.Lock — circuit state is not thread-safe"
fi

summary
