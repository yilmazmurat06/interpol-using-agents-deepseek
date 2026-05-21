#!/usr/bin/env bash
# check_curl_cffi_usage.sh — verify curl_cffi is used correctly everywhere
# Akamai is hit. Standard requests library is blocked by Akamai TLS fingerprinting.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_ca="$REPO_ROOT/container_a"
_cb="$REPO_ROOT/container_b"

# ---------------------------------------------------------------------------
section "No bare 'import requests' in container_a or container_b"
# ---------------------------------------------------------------------------
_bare=$(grep -RnE '^import requests$|^from requests ' \
    "$_ca" "$_cb" --include='*.py' 2>/dev/null || true)
if [[ -z "$_bare" ]]; then
    pass "no bare 'import requests' found in container_a or container_b"
else
    while IFS= read -r line; do
        fail "bare requests import (Akamai will block): $line"
    done <<< "$_bare"
fi

# ---------------------------------------------------------------------------
section "curl_cffi import present in both containers"
# ---------------------------------------------------------------------------
if grep -RqE 'from curl_cffi import requests' "$_ca" --include='*.py' 2>/dev/null; then
    pass "container_a imports curl_cffi.requests"
else
    fail "container_a does not import from curl_cffi — Akamai TLS fingerprinting will 403"
fi

if grep -RqE 'from curl_cffi import requests|from curl_cffi' "$_cb" --include='*.py' 2>/dev/null; then
    pass "container_b imports from curl_cffi (for image proxy)"
else
    fail "container_b does not import from curl_cffi — image proxy will fail with 403s"
fi

# ---------------------------------------------------------------------------
section "impersonate= argument present in container_a (API hits Akamai)"
# ---------------------------------------------------------------------------
if grep -RqE 'impersonate=["\x27]chrome' "$_ca" --include='*.py' 2>/dev/null; then
    pass "container_a uses impersonate=\"chrome...\" with curl_cffi"
else
    fail "container_a missing impersonate= argument — TLS fingerprint mismatch will 403"
fi

# ---------------------------------------------------------------------------
section "No requests.HTTPError or requests.RequestException in container_a"
# (curl_cffi does not expose these exception classes)
# ---------------------------------------------------------------------------
_exc=$(grep -RnE 'requests\.(HTTPError|RequestException)' "$_ca" --include='*.py' 2>/dev/null || true)
if [[ -z "$_exc" ]]; then
    pass "no requests.HTTPError / requests.RequestException in container_a"
else
    while IFS= read -r line; do
        fail "curl_cffi doesn't expose this exception class: $line"
    done <<< "$_exc"
fi

# ---------------------------------------------------------------------------
section "curl-cffi in requirements.txt for both containers"
# ---------------------------------------------------------------------------
_req_a="$_ca/requirements.txt"
_req_b="$_cb/requirements.txt"

if [[ -f "$_req_a" ]]; then
    if grep -qiE '^curl.cffi' "$_req_a"; then
        pass "container_a/requirements.txt includes curl-cffi"
    else
        fail "container_a/requirements.txt missing curl-cffi"
    fi
else
    fail "container_a/requirements.txt not found"
fi

if [[ -f "$_req_b" ]]; then
    if grep -qiE '^curl.cffi' "$_req_b"; then
        pass "container_b/requirements.txt includes curl-cffi"
    else
        fail "container_b/requirements.txt missing curl-cffi"
    fi
else
    fail "container_b/requirements.txt not found"
fi

# ---------------------------------------------------------------------------
section "No requests.get / requests.Session from non-curl_cffi in container_a or container_b"
# (any file that does 'import requests' and uses requests.get is a risk)
# ---------------------------------------------------------------------------
# Files that have bare requests import (already caught above, but cross-check for .get/.Session)
_risky_files=$(grep -RlE '^import requests$' "$_ca" "$_cb" --include='*.py' 2>/dev/null || true)
if [[ -n "$_risky_files" ]]; then
    while IFS= read -r f; do
        if grep -qE 'requests\.(get|post|put|delete|Session)' "$f" 2>/dev/null; then
            fail "$f calls requests.get/Session without curl_cffi"
        fi
    done <<< "$_risky_files"
else
    pass "no files use bare requests.get / requests.Session"
fi

summary
