#!/usr/bin/env bash
# check_imports.sh — Import-level runtime check for developer output files.
#
# Run after writing all files in a container but before archiving.
# Catches circular imports, missing dependencies, NameErrors, and cross-file
# name mismatches that py_compile cannot detect.
#
# Environment variables:
#   OUTPUT_DIR  path to output files (default: /mnt/session/outputs)

set -u
source "$(dirname "$0")/../../_lib/common.sh"

OUTPUT_DIR="${OUTPUT_DIR:-/mnt/session/outputs}"

# ── container_a ─────────────────────────────────────────────────────────────
section "Import check — container_a"
if [[ -d "$OUTPUT_DIR/container_a" ]]; then
    _out=$(cd "$OUTPUT_DIR/container_a" && python -c "import scraper, producer" 2>&1)
    _rc=$?
    if (( _rc == 0 )); then
        pass "container_a: scraper, producer import OK"
    else
        fail "container_a: import failed"
        printf '%s\n' "$_out"
    fi
else
    warn "container_a not found at $OUTPUT_DIR/container_a — skipping"
fi

# ── container_b ─────────────────────────────────────────────────────────────
section "Import check — container_b"
if [[ -d "$OUTPUT_DIR/container_b" ]]; then
    _out=$(cd "$OUTPUT_DIR/container_b" && python -c "import models, db, storage, consumer, app" 2>&1)
    _rc=$?
    if (( _rc == 0 )); then
        pass "container_b: models, db, storage, consumer, app import OK"
    else
        fail "container_b: import failed"
        printf '%s\n' "$_out"
    fi
else
    warn "container_b not found at $OUTPUT_DIR/container_b — skipping"
fi

summary
