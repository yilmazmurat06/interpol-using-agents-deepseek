#!/usr/bin/env bash
# run_all.sh — drive every DevOps verification script in sequence and aggregate.
# Exit code 0 = every check passed; non-zero = at least one failed.

set -u
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/../../_lib/common.sh"

_overall=0
for script in \
    check_env_bidirectional.sh \
    check_healthcheck_binaries.sh \
    check_image_pinning.sh \
    check_no_hardcoded.sh \
    check_protocol_negotiation.sh \
    check_readme.sh; do

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
    printf "%sONE OR MORE CHECKS FAILED — fix before proceeding%s\n" "$C_RED" "$C_RST"
fi
exit $_overall
