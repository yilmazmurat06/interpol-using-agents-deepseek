#!/usr/bin/env bash
# verify_hard_rules_present.sh — check that research documents are properly structured.
#
# Every *-constraints.md must have: Hard Rules, Endpoints sections.
# At least one must document curl_cffi and <path: slash-ID handling.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_research="$REPO_ROOT/research"

# ---------------------------------------------------------------------------
section "research/ directory exists"
# ---------------------------------------------------------------------------
if [[ ! -d "$_research" ]]; then
    fail "research/ directory not found at $_research"
    summary
fi
pass "research/ directory exists"

# ---------------------------------------------------------------------------
section "Per-file structural checks"
# ---------------------------------------------------------------------------
_file_count=0
_total_hard_rules=0

for f in "$_research"/*-constraints.md; do
    [[ -f "$f" ]] || continue
    _file_count=$(( _file_count + 1 ))
    _basename=$(basename "$f")
    info "checking $f"

    # Required: Hard Rules section
    if grep -qiE '^## Hard Rules|^## Hard Rules for Downstream Code' "$f"; then
        pass "$_basename: has 'Hard Rules for Downstream Code' section"
    else
        fail "$_basename: missing 'Hard Rules for Downstream Code' section"
    fi

    # Required: Endpoints section
    if grep -qiE '^## Endpoint|^## API Endpoint|^### Endpoint' "$f"; then
        pass "$_basename: has 'Endpoints' section"
    else
        fail "$_basename: missing 'Endpoints' section"
    fi

    # Recommended: Pagination section
    if grep -qiE '^## Pagination|^### Pagination' "$f"; then
        pass "$_basename: has 'Pagination' section"
    else
        warn "$_basename: missing 'Pagination' section"
    fi

    # Recommended: Rate Limits section
    if grep -qiE '^## Rate Limit|^### Rate Limit' "$f"; then
        pass "$_basename: has 'Rate Limits' section"
    else
        warn "$_basename: missing 'Rate Limits' section"
    fi

    # Count Hard Rules
    _rules=$(grep -cE '^- \*\*|^\*\*Design rule|^\*\*Hard Rule|^- Design rule' "$f" 2>/dev/null || true)
    _total_hard_rules=$(( _total_hard_rules + _rules ))
    info "$_basename: ~$_rules rule entries found"
done

if (( _file_count == 0 )); then
    fail "no *-constraints.md files found in research/"
else
    pass "$_file_count constraints file(s) found"
fi

# ---------------------------------------------------------------------------
section "Cross-file rule coverage"
# ---------------------------------------------------------------------------
info "total hard rule entries across all files: $_total_hard_rules"
if (( _total_hard_rules < 5 )); then
    info "fewer than 5 rule entries detected — research documents may be incomplete"
fi

# At least one file must document curl_cffi (Akamai bypass)
if grep -rqE 'curl_cffi' "$_research"/ 2>/dev/null; then
    pass "at least one constraints file documents curl_cffi (Akamai bypass)"
else
    fail "no constraints file mentions curl_cffi — Akamai TLS fingerprinting bypass not documented"
fi

# At least one file must document <path: slash-ID hazard
if grep -rqE '<path:|slash.*ID|ID.*slash|entity_id.*slash' "$_research"/ 2>/dev/null; then
    pass "at least one constraints file documents the slash-in-ID hazard"
else
    fail "no constraints file documents <path: or slash-in-ID hazard — Flask routes will 404"
fi

summary
