#!/usr/bin/env bash
# common.sh — shared helpers for skill verification scripts.
#
# Source this from any script with:
#   source "$(dirname "$0")/../../_lib/common.sh"
#
# Provides:
#   - REPO_ROOT  (auto-detected; override via $REPO_ROOT env var)
#   - pass / fail / warn / info  (status printers, color when TTY)
#   - check     (records a test result; updates counters)
#   - summary   (prints final tally; exit code reflects failures)
#   - require   (fail fast if a CLI tool is missing)

set -o pipefail

# --- Repo root --------------------------------------------------------------
if [[ -z "${REPO_ROOT:-}" ]]; then
    # Walk up from script location until we find CLAUDE.md
    _dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    while [[ "$_dir" != "/" && ! -f "$_dir/CLAUDE.md" ]]; do
        _dir="$(dirname "$_dir")"
    done
    if [[ -f "$_dir/CLAUDE.md" ]]; then
        REPO_ROOT="$_dir"
    else
        REPO_ROOT="$(pwd)"
    fi
fi
export REPO_ROOT

# --- Color (only on TTY) ----------------------------------------------------
if [[ -t 1 ]]; then
    C_GREEN=$'\033[0;32m'
    C_RED=$'\033[0;31m'
    C_YEL=$'\033[0;33m'
    C_BLU=$'\033[0;34m'
    C_DIM=$'\033[0;90m'
    C_RST=$'\033[0m'
else
    C_GREEN=""; C_RED=""; C_YEL=""; C_BLU=""; C_DIM=""; C_RST=""
fi

# --- Counters ---------------------------------------------------------------
_PASS_COUNT=0
_FAIL_COUNT=0
_WARN_COUNT=0
_FAIL_MESSAGES=()

pass() { printf "  %sPASS%s %s\n" "$C_GREEN" "$C_RST" "$*"; _PASS_COUNT=$((_PASS_COUNT+1)); }
fail() { printf "  %sFAIL%s %s\n" "$C_RED"   "$C_RST" "$*"; _FAIL_COUNT=$((_FAIL_COUNT+1)); _FAIL_MESSAGES+=("$*"); }
warn() { printf "  %sWARN%s %s\n" "$C_YEL"   "$C_RST" "$*"; _WARN_COUNT=$((_WARN_COUNT+1)); }
info() { printf "  %sinfo%s %s\n" "$C_BLU"   "$C_RST" "$*"; }
note() { printf "  %s%s%s\n"      "$C_DIM"   "$*"     "$C_RST"; }

section() { printf "\n%s== %s ==%s\n" "$C_BLU" "$*" "$C_RST"; }

# check NAME EXPRESSION   (runs expression as shell, pass if exit 0)
check() {
    local name="$1"; shift
    if eval "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

# require CMD [CMD...]   — abort if any required binary missing
require() {
    for c in "$@"; do
        if ! command -v "$c" >/dev/null 2>&1; then
            printf "%sERROR%s required tool not found: %s\n" "$C_RED" "$C_RST" "$c" >&2
            exit 127
        fi
    done
}

# summary — print tally + exit non-zero on any failure
summary() {
    printf "\n%s── Summary ──%s\n" "$C_BLU" "$C_RST"
    printf "  %sPASS%s %d   %sFAIL%s %d   %sWARN%s %d\n" \
        "$C_GREEN" "$C_RST" "$_PASS_COUNT" \
        "$C_RED"   "$C_RST" "$_FAIL_COUNT" \
        "$C_YEL"   "$C_RST" "$_WARN_COUNT"
    if (( _FAIL_COUNT > 0 )); then
        printf "\n%sFailures:%s\n" "$C_RED" "$C_RST"
        for m in "${_FAIL_MESSAGES[@]}"; do printf "  - %s\n" "$m"; done
        exit 1
    fi
    exit 0
}

# --- Convenience greps -----------------------------------------------------
# grep_code PATTERN [PATH...]   — silent ripgrep-ish; returns 0 if found
grep_code() {
    local pattern="$1"; shift
    grep -RInE --include='*.py' --include='*.yml' --include='*.yaml' \
        --include='*.html' --include='*.js' --include='*.txt' --include='*.env*' \
        "$pattern" "${@:-$REPO_ROOT}" 2>/dev/null
}

# count_matches PATTERN [PATH...]
count_matches() {
    grep_code "$@" | wc -l | tr -d ' '
}
