#!/usr/bin/env bash
# check_env_consistency.sh — bidirectional env-var consistency.
#
# Three sets:
#   A = ${VAR} references in docker-compose.yml
#   B = VAR= lines in .env.example
#   C = os.environ[...] / os.environ.get(...) in Python source
#
# Reports:
#   A - (B ∪ C)  → compose references undocumented var (usually mistake)
#   B - (A ∪ C)  → .env.example defines a dangling var that nothing reads
#   C - (B)      → code reads a var not documented in .env.example

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_compose="$REPO_ROOT/docker-compose.yml"
_envex="$REPO_ROOT/.env.example"
[[ -f "$_compose" ]] || { fail "docker-compose.yml not found"; summary; }
[[ -f "$_envex" ]]   || { fail ".env.example not found";   summary; }

section "Building var sets"

# A: ${VAR} or ${VAR:-default} from compose
mapfile -t SET_A < <(grep -oE '\$\{[A-Z_][A-Z0-9_]*(:-[^}]*)?\}' "$_compose" \
    | sed -E 's/\$\{([A-Z_][A-Z0-9_]*).*/\1/' | sort -u)

# B: VAR= from .env.example (skip comments / blank)
mapfile -t SET_B < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$_envex" \
    | cut -d= -f1 | sort -u)

# C: os.environ references from Python source
mapfile -t SET_C < <(grep -RhEo 'os\.environ(\[[^]]+\]|\.get\([^,)]+)' \
    "$REPO_ROOT/container_a" "$REPO_ROOT/container_b" 2>/dev/null \
    | grep -oE '"[A-Z_][A-Z0-9_]*"|'"'"'[A-Z_][A-Z0-9_]*'"'"'' \
    | tr -d '"'"'"'' | sort -u)

info "compose refs:       ${#SET_A[@]} vars"
info ".env.example:       ${#SET_B[@]} vars"
info "os.environ in code: ${#SET_C[@]} vars"

# Build helper sets as newline strings for grep -Fx
_B_str=$(printf "%s\n" "${SET_B[@]}")
_A_str=$(printf "%s\n" "${SET_A[@]}")
_C_str=$(printf "%s\n" "${SET_C[@]}")
_BC_str=$(printf "%s\n%s\n" "${SET_B[@]}" "${SET_C[@]}" | sort -u)
_AC_str=$(printf "%s\n%s\n" "${SET_A[@]}" "${SET_C[@]}" | sort -u)

section "A - (B ∪ C): compose vars not documented or read"
_orphans=$(printf "%s\n" "${SET_A[@]}" | grep -Fxv -f <(echo "$_BC_str") || true)
if [[ -z "$_orphans" ]]; then
    pass "every \${VAR} in compose is documented or read"
else
    while IFS= read -r v; do
        fail "compose references \${$v} but it's not in .env.example AND not read in Python"
    done <<< "$_orphans"
fi

section "B - (A ∪ C): .env.example vars that nothing reads"
_dangling=$(printf "%s\n" "${SET_B[@]}" | grep -Fxv -f <(echo "$_AC_str") || true)
if [[ -z "$_dangling" ]]; then
    pass "every .env.example var is referenced somewhere"
else
    while IFS= read -r v; do
        fail "$v is in .env.example but nothing uses it (typo? mismatched name?)"
    done <<< "$_dangling"
fi

section "C - B: code reads vars not in .env.example"
_undocumented=$(printf "%s\n" "${SET_C[@]}" | grep -Fxv -f <(echo "$_B_str") || true)
if [[ -z "$_undocumented" ]]; then
    pass "every os.environ read is documented in .env.example"
else
    while IFS= read -r v; do
        # Allow a few well-known auto-populated ones
        case "$v" in
            PATH|HOME|USER|HOSTNAME|PWD) continue ;;
        esac
        warn "code reads $v but .env.example does not document it"
    done <<< "$_undocumented"
fi

summary
