#!/usr/bin/env bash
# run_all.sh — drive every full-stack dev verification script in sequence and aggregate.
# Exit code 0 = every check passed; non-zero = at least one failed.
# Run before declaring done — any FAIL must be fixed before handoff.

set -u
_here="$(cd "$(dirname "$0")" && pwd)"
source "$_here/../../_lib/common.sh"

_overall=0
for script in \
    verify_patterns.sh \
    check_curl_cffi_usage.sh \
    check_psycopg2_containment.sh \
    check_pagination_shape.sh \
    check_streaming_publish.sh \
    check_circuit_breaker.sh; do

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
    printf "%sONE OR MORE CHECKS FAILED — fix before handoff%s\n" "$C_RED" "$C_RST"
fi
exit $_overall
