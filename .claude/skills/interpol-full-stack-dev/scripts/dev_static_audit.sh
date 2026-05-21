#!/usr/bin/env bash
# dev_static_audit.sh — Full static audit for developer self-verification.
#
# Runs in order:
#   1. interpol-full-stack-dev/scripts/run_all.sh  — pattern checks (PSC-*, Hard Rules)
#   2. interpol-qa/scripts/audit_hard_rules.sh     — general Hard Rules
#   3. interpol-qa/scripts/check_requirements.sh   — requirements.txt completeness
#   4. ruff check (if ruff is available)           — lint
#
# Any FAIL from steps 1-3 = must fix before handoff to QA.
# Run once after all files are written.
#
# Environment variables:
#   OUTPUT_DIR  path to output files (default: /mnt/session/outputs)

set -u
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/../../_lib/common.sh"

OUTPUT_DIR="${OUTPUT_DIR:-/mnt/session/outputs}"
_overall=0

# ── Step 1: Developer pattern checks ─────────────────────────────────────────
section "Developer pattern checks (run_all.sh)"
if bash "$_here/run_all.sh"; then
    info "run_all.sh OK"
else
    _overall=1
    info "run_all.sh FAILED"
fi

# ── Step 2: QA hard rules audit ──────────────────────────────────────────────
_qa_scripts="$REPO_ROOT/.claude/skills/interpol-qa/scripts"

section "Hard rules audit (audit_hard_rules.sh)"
if bash "$_qa_scripts/audit_hard_rules.sh"; then
    info "audit_hard_rules.sh OK"
else
    _overall=1
    info "audit_hard_rules.sh FAILED"
fi

# ── Step 3: Requirements completeness check ───────────────────────────────────
section "Requirements audit (check_requirements.sh)"
if bash "$_qa_scripts/check_requirements.sh" 2>/dev/null; then
    info "check_requirements.sh OK"
else
    _overall=1
    info "check_requirements.sh FAILED"
fi

# ── Step 4: Ruff lint (optional) ─────────────────────────────────────────────
section "Ruff lint (optional)"
if command -v ruff >/dev/null 2>&1; then
    if ruff check "$OUTPUT_DIR/container_a/" "$OUTPUT_DIR/container_b/" \
            --select E,F,W --ignore E501 2>/dev/null; then
        pass "ruff: no issues"
    else
        warn "ruff: issues found — review output above (non-blocking)"
    fi
else
    warn "ruff not available — skipping lint"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n%s═══ OVERALL ═══%s\n" "$C_BLU" "$C_RST"
if (( _overall == 0 )); then
    printf "%sALL STATIC CHECKS PASSED%s\n" "$C_GREEN" "$C_RST"
else
    printf "%sONE OR MORE CHECKS FAILED — fix before handoff to QA%s\n" "$C_RED" "$C_RST"
fi
exit $_overall
