#!/usr/bin/env bash
# check_no_hardcoded.sh — every credential/port/hostname in compose must
# come from ${VAR}, never a literal.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_compose="$REPO_ROOT/docker-compose.yml"
[[ -f "$_compose" ]] || { fail "docker-compose.yml not found"; summary; }

section "Hardcoded passwords / credentials"
# Match: PASSWORD: literal, password=literal, PASS: literal (NOT ${VAR})
_hits=$(grep -nE '(PASS(WORD)?|SECRET|TOKEN|KEY)[A-Z_]*\s*[:=]\s*[^${\$]' "$_compose" \
    | grep -vE '#|\$\{|->.*PASS' || true)
if [[ -z "$_hits" ]]; then
    pass "no hardcoded credentials"
else
    while IFS= read -r line; do fail "hardcoded credential: $line"; done <<< "$_hits"
fi

section "Hardcoded host ports outside \${VAR} substitution"
# Match "1234:5678" style port lines that are not "${PORT}:5678"
_hits=$(grep -nE '^\s*-\s*"?[0-9]+:[0-9]+"?\s*$' "$_compose" || true)
if [[ -z "$_hits" ]]; then
    pass "no fully-hardcoded port mappings"
else
    while IFS= read -r line; do warn "hardcoded port mapping (consider \${VAR}): $line"; done <<< "$_hits"
fi

section "Internal hostnames (must be docker service names, not localhost/IPs)"
_hits=$(grep -nE '://(localhost|127\.0\.0\.1)' "$_compose" || true)
if [[ -z "$_hits" ]]; then
    pass "no localhost / 127.0.0.1 in compose"
else
    while IFS= read -r line; do fail "localhost reference (should be service name): $line"; done <<< "$_hits"
fi

summary
