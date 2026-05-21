#!/usr/bin/env bash
# check_psycopg2_containment.sh — deep-check psycopg2 transaction safety in db.py.
#
# Verifies PSC-1: every write method has commit+rollback, INERROR pre-check exists,
# and the consumer uses the db layer rather than raw psycopg2.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_db="$REPO_ROOT/container_b/db.py"
_consumer="$REPO_ROOT/container_b/consumer.py"

if [[ ! -f "$_db" ]]; then
    fail "container_b/db.py not found"
    summary
fi

# ---------------------------------------------------------------------------
section "Methods containing execute() in db.py"
# ---------------------------------------------------------------------------
_exec_methods=$(grep -nE '^\s+def [a-z_]+' "$_db" | while IFS= read -r def_line; do
    _lineno=$(echo "$def_line" | cut -d: -f1)
    # Find method end heuristically: next def at same indent level or EOF
    _funcname=$(echo "$def_line" | grep -oE 'def [a-z_]+\(' | head -1 | sed 's/def //;s/($//')
    # Look for execute( in the next 60 lines
    _end=$(( _lineno + 60 ))
    _has_exec=$(sed -n "${_lineno},${_end}p" "$_db" 2>/dev/null | grep -c '\.execute(' || true)
    if (( _has_exec > 0 )); then
        echo "${_lineno}:${_funcname}"
    fi
done)

if [[ -z "$_exec_methods" ]]; then
    warn "no methods with execute() found in db.py — check if db layer is implemented"
else
    while IFS= read -r entry; do
        _lineno="${entry%%:*}"
        _funcname="${entry#*:}"
        info "found write method: $funcname (line $_lineno)"

        _end=$(( _lineno + 60 ))
        _has_rollback=$(sed -n "${_lineno},${_end}p" "$_db" 2>/dev/null | grep -c 'rollback' || true)
        _has_commit=$(sed -n "${_lineno},${_end}p"  "$_db" 2>/dev/null | grep -c 'commit'   || true)

        if (( _has_rollback > 0 )); then
            pass "$_funcname (line $_lineno): has rollback()"
        else
            fail "$_funcname (line $_lineno): missing rollback() — InFailedSqlTransaction risk"
        fi

        if (( _has_commit > 0 )); then
            pass "$_funcname (line $_lineno): has commit()"
        else
            warn "$_funcname (line $_lineno): no commit() nearby — may rely on auto-commit or outer caller"
        fi
    done <<< "$_exec_methods"
fi

# ---------------------------------------------------------------------------
section "TRANSACTION_STATUS_INERROR defensive pre-check in _cursor helper"
# ---------------------------------------------------------------------------
if grep -qE 'TRANSACTION_STATUS_INERROR' "$_db"; then
    pass "db.py has TRANSACTION_STATUS_INERROR guard in _cursor helper"
else
    fail "db.py missing TRANSACTION_STATUS_INERROR check — connection poisoning goes undetected"
fi

# ---------------------------------------------------------------------------
section "Minimum rollback() count in db.py"
# ---------------------------------------------------------------------------
_rollback_count=$(grep -cE 'self\._conn\.rollback\(\)' "$_db" || true)
info "self._conn.rollback() occurrences: $_rollback_count"
if (( _rollback_count >= 2 )); then
    pass "rollback() count ($_rollback_count) >= 2"
else
    fail "rollback() count ($_rollback_count) < 2 — insufficient coverage"
fi

# ---------------------------------------------------------------------------
section "Minimum commit() count in db.py"
# ---------------------------------------------------------------------------
_commit_count=$(grep -cE 'self\._conn\.commit\(\)' "$_db" || true)
info "self._conn.commit() occurrences: $_commit_count"
if (( _commit_count >= 1 )); then
    pass "commit() count ($_commit_count) >= 1"
else
    fail "no self._conn.commit() found in db.py"
fi

# ---------------------------------------------------------------------------
section "Consumer uses db layer (not raw psycopg2)"
# ---------------------------------------------------------------------------
if [[ -f "$_consumer" ]]; then
    if grep -qE 'upsert_notice|db\.upsert|\.upsert' "$_consumer"; then
        pass "consumer.py delegates to db layer upsert_notice"
    else
        warn "consumer.py does not call upsert_notice — check it isn't writing psycopg2 directly"
    fi

    if grep -qE '^import psycopg2$|^from psycopg2' "$_consumer"; then
        warn "consumer.py imports psycopg2 directly — should use db.py layer instead"
    else
        pass "consumer.py does not import psycopg2 directly (uses db layer)"
    fi
else
    warn "container_b/consumer.py not found — skipping consumer check"
fi

summary
