#!/usr/bin/env bash
# check_requirements.sh — verify each container's requirements.txt matches its imports.
#
# For each container_X/requirements.txt:
#   - List declared packages (one per line, normalized)
#   - List imports in that container's *.py files
#   - Diff: missing (imported but not declared), unused (declared but not imported)
#
# Also flags any 'import requests' in a container that talks to Akamai —
# should be curl_cffi instead.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

# Stdlib modules we should never flag (small allowlist).
_STDLIB="os sys re json time datetime threading queue logging math random typing dataclasses pathlib hashlib base64 functools collections itertools urllib socket ssl io abc enum copy traceback uuid argparse subprocess pickle warnings concurrent contextlib email html http csv tempfile shutil glob mimetypes signal weakref"

# Map import top-level → pip package (handles common mismatches).
declare -A PKG_MAP=(
    [psycopg2]="psycopg2-binary"
    [PIL]="Pillow"
    [yaml]="PyYAML"
    [cv2]="opencv-python"
    [bs4]="beautifulsoup4"
    [dotenv]="python-dotenv"
    [jwt]="PyJWT"
    [curl_cffi]="curl-cffi"
    [google]="google-cloud-storage"
)

audit_container() {
    local dir="$1"
    local req="$dir/requirements.txt"
    local name="$(basename "$dir")"

    section "$name"
    if [[ ! -f "$req" ]]; then
        fail "$req not found"
        return
    fi

    # Declared packages (normalize: lowercase, strip versions/extras/comments)
    mapfile -t declared < <(grep -vE '^\s*(#|$)' "$req" \
        | sed -E 's/[<>=!~].*//; s/\[.*\]//; s/[[:space:]]//g' \
        | tr '[:upper:]' '[:lower:]' | sort -u)

    # Imports (top-level module name; lowercase)
    mapfile -t imports < <(grep -hEo '^[[:space:]]*(import|from)[[:space:]]+[a-zA-Z_][a-zA-Z0-9_]*' "$dir"/*.py 2>/dev/null \
        | awk '{print $2}' \
        | awk -F. '{print $1}' \
        | tr '[:upper:]' '[:lower:]' | sort -u)

    # Filter out stdlib
    local third_party=()
    for imp in "${imports[@]}"; do
        if ! grep -qw "$imp" <<< "$_STDLIB"; then
            third_party+=("$imp")
        fi
    done

    # Translate import name → expected pip package
    local expected=()
    for imp in "${third_party[@]}"; do
        if [[ -n "${PKG_MAP[$imp]:-}" ]]; then
            expected+=("${PKG_MAP[$imp]}")
        else
            expected+=("$imp")
        fi
    done

    # Missing: in expected but not in declared
    local missing=()
    for pkg in "${expected[@]}"; do
        local pkg_lc; pkg_lc=$(echo "$pkg" | tr '[:upper:]' '[:lower:]')
        if ! grep -qFx "$pkg_lc" <<< "$(printf '%s\n' "${declared[@]}")"; then
            missing+=("$pkg")
        fi
    done

    # Unused: in declared but not in expected
    local unused=()
    for pkg in "${declared[@]}"; do
        if ! grep -qFxi "$pkg" <<< "$(printf '%s\n' "${expected[@]}")"; then
            unused+=("$pkg")
        fi
    done

    if (( ${#missing[@]} == 0 )); then
        pass "no missing packages"
    else
        for m in "${missing[@]}"; do
            fail "imported but not in requirements.txt: $m"
        done
    fi

    if (( ${#unused[@]} == 0 )); then
        pass "no unused packages"
    else
        for u in "${unused[@]}"; do
            warn "declared but not imported: $u (could be optional/runtime dep)"
        done
    fi

    # Hard rule: no plain 'requests' against Akamai host
    if grep -qFx "requests" <<< "$(printf '%s\n' "${declared[@]}")"; then
        if grep -RIlE 'ws-public\.interpol\.int' "$dir" >/dev/null 2>&1; then
            fail "'requests' declared in container that talks to Akamai-fronted host — use curl-cffi"
        fi
    fi

    # Hard rule: every package must be pinned
    local unpinned=()
    while IFS= read -r line; do
        if [[ -n "$line" && ! "$line" =~ [\<\>=!~] ]]; then
            unpinned+=("$line")
        fi
    done < <(grep -vE '^\s*(#|$)' "$req" | sed -E 's/[[:space:]]//g')
    if (( ${#unpinned[@]} > 0 )); then
        for u in "${unpinned[@]}"; do
            warn "unpinned dependency: $u"
        done
    else
        pass "all dependencies pinned"
    fi
}

for c in "$REPO_ROOT"/container_*; do
    [[ -d "$c" ]] && audit_container "$c"
done

summary
