#!/usr/bin/env bash
# verify_probe_evidence.sh — check that research docs contain real probe evidence,
# not just documentation copy-paste.
#
# Evidence markers: actual curl/GET commands, HTTP status codes, JSON snippets,
# a "Probed:" date, and non-trivial file length.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_research="$REPO_ROOT/research"

if [[ ! -d "$_research" ]]; then
    fail "research/ directory not found at $_research"
    summary
fi

_file_count=0

for f in "$_research"/*-constraints.md; do
    [[ -f "$f" ]] || continue
    _file_count=$(( _file_count + 1 ))
    _basename=$(basename "$f")
    section "$_basename"

    # Probe evidence: curl commands, HTTP verbs, or status codes
    if grep -qE 'curl |GET |POST |200|403|404|response' "$f"; then
        pass "$_basename: contains probe evidence (curl/GET/status codes/response)"
    else
        fail "$_basename: no probe evidence found — may be documentation copy-paste, not live research"
    fi

    # JSON response examples
    if grep -qE '\{' "$f" && grep -qE '"[a-z_]+"' "$f"; then
        pass "$_basename: contains JSON field snippets (response examples)"
    else
        warn "$_basename: no JSON response examples found"
    fi

    # Probed: date line
    if grep -qiE '^Probed:|^\*\*Probed' "$f"; then
        pass "$_basename: has 'Probed:' date line"
    else
        warn "$_basename: missing 'Probed:' date — cannot confirm when research was done"
    fi

    # Open Questions section (absence is suspicious — agent closed all questions)
    if grep -qiE '^## Open Question|^### Open Question' "$f"; then
        pass "$_basename: has 'Open Questions' section"
    else
        warn "$_basename: missing 'Open Questions' section — all questions closed may indicate shallow research"
    fi

    # Non-trivial file length (> 50 lines)
    _lines=$(wc -l < "$f" | tr -d ' ')
    info "$_basename: $_lines lines"
    if (( _lines > 50 )); then
        pass "$_basename: file is non-trivial ($_lines lines)"
    else
        warn "$_basename: file is very short ($_lines lines) — may be incomplete"
    fi
done

if (( _file_count == 0 )); then
    fail "no *-constraints.md files found in research/"
else
    info "checked $_file_count constraints file(s)"
fi

summary
