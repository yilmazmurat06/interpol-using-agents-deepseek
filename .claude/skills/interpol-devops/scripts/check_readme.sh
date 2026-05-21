#!/usr/bin/env bash
# check_readme.sh — README has the load-bearing operator instructions.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_readme="$REPO_ROOT/README.md"
[[ -f "$_readme" ]] || { fail "README.md not found"; summary; }

section "README quick-start sections"

declare -A REQ=(
    [Prerequisites]="prerequis|requirement"
    ['Quick start']="quick.?start|getting started"
    ['cp .env.example .env']="cp \.env\.example \.env"
    ['docker compose up']="docker.compose up"
    [Architecture]="architecture|overview"
    [Ports]="port|web ui|management"
    [Troubleshooting]="troubleshoot|common.issues"
)

for name in "${!REQ[@]}"; do
    pat="${REQ[$name]}"
    if grep -iqE "$pat" "$_readme"; then
        pass "README mentions: $name"
    else
        fail "README missing section: $name"
    fi
done

section "README env var reference"
# Count VAR= entries in .env.example, count how many are mentioned in README
mapfile -t vars < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$REPO_ROOT/.env.example" 2>/dev/null | cut -d= -f1 | sort -u)
_total=${#vars[@]}
_mentioned=0
for v in "${vars[@]}"; do
    grep -qw "$v" "$_readme" && _mentioned=$((_mentioned+1))
done
if (( _total == 0 )); then
    warn "no env vars in .env.example to check"
elif (( _mentioned * 2 >= _total )); then
    pass "$_mentioned/$_total env vars referenced in README"
else
    fail "only $_mentioned/$_total env vars mentioned in README — undocumented config"
fi

summary
