#!/usr/bin/env bash
# check_image_pinning.sh — verify no Docker image uses :latest or an unpinned tag.
#
# Scans docker-compose.yml and all Dockerfiles under the repo root.
# Rules:
#   - image:latest or FROM :latest → FAIL
#   - image without any tag        → WARN
#   - pinned images                → PASS
#   - base Python images must be python:3.11 (not 3.12+ or 3.10-)  → WARN if wrong

set -u
source "$(dirname "$0")/../../_lib/common.sh"

# ---------------------------------------------------------------------------
section "docker-compose.yml image: lines"
# ---------------------------------------------------------------------------
_compose="$REPO_ROOT/docker-compose.yml"
if [[ ! -f "$_compose" ]]; then
    fail "docker-compose.yml not found"
else
    while IFS= read -r line; do
        # Strip leading whitespace and extract value after "image:"
        _img=$(echo "$line" | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | tr -d '"'"'" | xargs)
        [[ -z "$_img" ]] && continue

        if [[ "$_img" == *":latest" ]]; then
            fail "compose: image uses :latest → $line"
        elif [[ "$_img" != *":"* ]]; then
            warn "compose: image has no tag (implicitly :latest) → $line"
        else
            pass "compose: pinned image → $_img"
        fi

        # Python version check
        if echo "$_img" | grep -qE '^python:'; then
            if echo "$_img" | grep -qE '^python:3\.11'; then
                pass "python base image is 3.11 → $_img"
            else
                warn "python base image is NOT 3.11 (project requires 3.11) → $_img"
            fi
        fi
    done < <(grep -E '^\s*image:' "$_compose")
fi

# ---------------------------------------------------------------------------
section "Dockerfiles under repo root"
# ---------------------------------------------------------------------------
_found_any=0
while IFS= read -r df; do
    _found_any=1
    section "  $df"
    while IFS= read -r line; do
        # Match FROM lines
        _img=$(echo "$line" | sed -E 's/^FROM[[:space:]]+//' | awk '{print $1}' | tr -d '"')
        [[ -z "$_img" ]] && continue
        # Skip scratch and builder aliases (AS ...)
        [[ "$_img" == "scratch" ]] && continue

        if [[ "$_img" == *":latest" ]]; then
            fail "$df: FROM uses :latest → $line"
        elif [[ "$_img" != *":"* ]]; then
            warn "$df: FROM has no tag (implicitly :latest) → $line"
        else
            pass "$df: pinned FROM → $_img"
        fi

        # Python version check
        if echo "$_img" | grep -qE '^python:'; then
            if echo "$_img" | grep -qE '^python:3\.11'; then
                pass "$df: python base image is 3.11 → $_img"
            else
                warn "$df: python base image is NOT 3.11 (project requires 3.11) → $_img"
            fi
        fi
    done < <(grep -iE '^FROM ' "$df")
done < <(find "$REPO_ROOT" -name "Dockerfile" -not -path "*/node_modules/*" -not -path "*/.git/*")

if (( _found_any == 0 )); then
    warn "no Dockerfiles found under $REPO_ROOT"
fi

summary
