#!/usr/bin/env bash
# check_pagination_shape.sh — verify API pagination contract (PSC-4).
#
# Checks: response envelope keys, count_notices, offset, UI pagination controls,
# page-reset on filter change, SSE page-1 guard, total_notices in /api/filters.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_app="$REPO_ROOT/container_b/app.py"
_db="$REPO_ROOT/container_b/db.py"
_tmpl="$REPO_ROOT/container_b/templates/index.html"

# ---------------------------------------------------------------------------
section "app.py /api/notices response envelope"
# ---------------------------------------------------------------------------
if [[ ! -f "$_app" ]]; then
    fail "container_b/app.py not found"
else
    for key in '"notices"' '"total"' '"page"' '"page_size"' '"pages"'; do
        if grep -qE "$key" "$_app"; then
            pass "app.py response includes $key"
        else
            fail "app.py /api/notices response missing $key"
        fi
    done
fi

# ---------------------------------------------------------------------------
section "db.py: count_notices and offset support"
# ---------------------------------------------------------------------------
if [[ ! -f "$_db" ]]; then
    fail "container_b/db.py not found"
else
    if grep -qE 'def count_notices' "$_db"; then
        pass "count_notices() function exists in db.py"
    else
        fail "count_notices() missing from db.py — pagination total cannot be computed"
    fi

    if grep -qE '\boffset\b' "$_db"; then
        pass "get_all_notices accepts offset parameter in db.py"
    else
        fail "no offset parameter in db.py — server-side pagination impossible"
    fi
fi

# ---------------------------------------------------------------------------
section "/api/filters includes total_notices"
# ---------------------------------------------------------------------------
if [[ -f "$_app" ]]; then
    if grep -qE 'total_notices' "$_app"; then
        pass "/api/filters endpoint includes total_notices"
    else
        fail "/api/filters missing total_notices — live DB count not available to UI"
    fi
fi

# ---------------------------------------------------------------------------
section "templates/index.html pagination controls"
# ---------------------------------------------------------------------------
if [[ ! -f "$_tmpl" ]]; then
    warn "container_b/templates/index.html not found — skipping UI checks"
else
    if grep -qiE 'pagination|pager|page-nav' "$_tmpl"; then
        pass "index.html has pagination controls"
    else
        warn "index.html missing pagination CSS/HTML class — pagination may not be rendered"
    fi

    # Page reset on filter change: look for fetchPage(1) or equivalent
    if grep -qE 'fetchPage\(1\)|page\s*=\s*1|currentPage\s*=\s*1' "$_tmpl"; then
        pass "index.html resets to page 1 on filter change"
    else
        fail "index.html does not reset to page 1 on filter change — stale pagination on filter"
    fi

    # SSE page-1 guard: only insert card if on page 1 with no filters
    if grep -qE '(currentPage|page).*[=!]=.*1|page.*===.*1' "$_tmpl"; then
        pass "SSE handler checks page 1 before inserting new card"
    else
        warn "SSE handler may not check for page 1 before inserting card — pagination drift risk"
    fi

    # Prev/Next buttons
    if grep -qiE 'prev|previous|next' "$_tmpl"; then
        pass "index.html has Prev/Next pagination buttons"
    else
        warn "index.html missing Prev/Next pagination buttons"
    fi
fi

summary
