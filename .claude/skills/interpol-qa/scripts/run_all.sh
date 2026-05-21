#!/usr/bin/env bash
# run_all.sh — drive every QA verification script in sequence and aggregate.
#
# Static checks (always run):
#   audit_hard_rules, check_env_consistency, check_requirements, check_engineering_decisions
#
# Runtime checks (graceful skip if Docker stack not running):
#   check_container_health, check_api_smoke, check_container_logs
#
# Exit code 0 = all static checks passed (runtime failures only fail if stack confirmed running).

set -u
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/../../_lib/common.sh"

_overall=0

# ── Static checks (a FAIL here = Verdict must be FAIL) ──────────────────────
printf "\n%s══ STATIC CHECKS ══%s\n" "$C_BLU" "$C_RST"
for script in \
    audit_hard_rules.sh \
    check_env_consistency.sh \
    check_requirements.sh \
    check_engineering_decisions.sh; do

    section "Running $script"
    if bash "$_here/$script"; then
        info "$script OK"
    else
        _overall=1
        info "$script FAILED"
    fi
done

# ── Runtime checks (WARN-only if stack not up; FAIL if stack up + broken) ───
printf "\n%s══ RUNTIME CHECKS (skipped gracefully if stack not running) ══%s\n" "$C_BLU" "$C_RST"
for script in \
    check_container_health.sh \
    check_api_smoke.sh \
    check_container_logs.sh; do

    section "Running $script"
    if bash "$_here/$script"; then
        info "$script OK"
    else
        _overall=1
        info "$script FAILED"
    fi
done

printf "\n%s═══ OVERALL ═══%s\n" "$C_BLU" "$C_RST"
if (( _overall == 0 )); then
    printf "%sALL CHECKS PASSED%s\n" "$C_GREEN" "$C_RST"
else
    printf "%sONE OR MORE CHECKS FAILED — Verdict must be FAIL%s\n" "$C_RED" "$C_RST"
fi
exit $_overall
