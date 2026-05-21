#!/usr/bin/env bash
# check_env_bidirectional.sh — 3-way bidirectional env-var consistency check.
#
# Direction A→B: every ${VAR} in docker-compose.yml must appear in .env.example
# Direction B→A: every VAR= in .env.example must appear in compose OR Python
# Direction C:   every os.environ["VAR"] / os.getenv("VAR") must be in .env.example

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_compose="$REPO_ROOT/docker-compose.yml"
_envex="$REPO_ROOT/.env.example"

[[ -f "$_compose" ]] || { fail "docker-compose.yml not found at $_compose"; summary; }
[[ -f "$_envex"  ]] || { fail ".env.example not found at $_envex"; summary; }

# ---------------------------------------------------------------------------
# Extract variable sets
# ---------------------------------------------------------------------------

# Compose: all ${VAR} and $VAR (bare, word-boundary) references
_compose_vars=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}|\$[A-Z_][A-Z0-9_]+' "$_compose" \
    | grep -oE '[A-Z_][A-Z0-9_]+' \
    | sort -u)

# .env.example: all VAR= lines (ignoring comments and blank lines)
_env_keys=$(grep -E '^[A-Z_][A-Z0-9_]*=' "$_envex" \
    | cut -d= -f1 \
    | sort -u)

# Python: os.environ["VAR"], os.environ.get("VAR"), os.getenv("VAR")
_py_vars=$(grep -RhE "os\.environ\[[\'\"]([A-Z_][A-Z0-9_]*)[\'\"]|os\.environ\.get\([\'\"]([A-Z_][A-Z0-9_]*)|os\.getenv\([\'\"]([A-Z_][A-Z0-9_]*)" \
    "$REPO_ROOT/container_a" "$REPO_ROOT/container_b" 2>/dev/null \
    | grep -oE "[\'\"][A-Z_][A-Z0-9_]*[\'\"]" \
    | tr -d "'\""  \
    | sort -u)

# Write to temp files for comm
_tmp=$(mktemp -d)
echo "$_compose_vars" > "$_tmp/compose.txt"
echo "$_env_keys"    > "$_tmp/env.txt"
echo "$_py_vars"     > "$_tmp/py.txt"

# Union of compose + python (for B→A direction check)
cat "$_tmp/compose.txt" "$_tmp/py.txt" | sort -u > "$_tmp/compose_or_py.txt"

# ---------------------------------------------------------------------------
section "Direction A→B: every compose \${VAR} must be in .env.example"
# ---------------------------------------------------------------------------
_missing_in_env=$(comm -23 "$_tmp/compose.txt" "$_tmp/env.txt")
if [[ -z "$_missing_in_env" ]]; then
    pass "all compose variables are documented in .env.example"
else
    while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        fail "compose references \${$v} but it is not in .env.example"
    done <<< "$_missing_in_env"
fi

# ---------------------------------------------------------------------------
section "Direction B→A: every .env.example VAR must appear in compose OR Python"
# ---------------------------------------------------------------------------
_missing_in_code=$(comm -23 "$_tmp/env.txt" "$_tmp/compose_or_py.txt")
if [[ -z "$_missing_in_code" ]]; then
    pass "all .env.example variables are referenced in compose or Python code"
else
    while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        fail ".env.example defines $v but it is not referenced in compose or Python"
    done <<< "$_missing_in_code"
fi

# ---------------------------------------------------------------------------
section "Direction C: every Python os.environ/os.getenv VAR must be in .env.example"
# ---------------------------------------------------------------------------
_py_not_in_env=$(comm -23 "$_tmp/py.txt" "$_tmp/env.txt")
if [[ -z "$_py_not_in_env" ]]; then
    pass "all Python env reads are documented in .env.example"
else
    while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        fail "Python reads $v via os.environ/getenv but it is not in .env.example"
    done <<< "$_py_not_in_env"
fi

# ---------------------------------------------------------------------------
section "Summary of extracted sets (informational)"
# ---------------------------------------------------------------------------
info "compose variables  : $(echo "$_compose_vars" | grep -c . || echo 0)"
info ".env.example keys  : $(echo "$_env_keys"     | grep -c . || echo 0)"
info "Python env reads   : $(echo "$_py_vars"       | grep -c . || echo 0)"

rm -rf "$_tmp"
summary
