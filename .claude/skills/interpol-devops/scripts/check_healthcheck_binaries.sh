#!/usr/bin/env bash
# check_healthcheck_binaries.sh — verify each healthcheck command uses a
# binary documented to be bundled in its image. Minimal/distroless images
# (especially ARM64 MinIO) often lack curl/wget — generic-looking checks
# silently fail at runtime.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_compose="$REPO_ROOT/docker-compose.yml"
[[ -f "$_compose" ]] || { fail "docker-compose.yml not found"; summary; }

# Known-safe binaries per image family.
# Format: image_substring|binary[,binary,...]
_KNOWN_BINS=(
    "postgres|pg_isready,psql"
    "rabbitmq|rabbitmq-diagnostics,rabbitmqctl"
    "minio/minio|mc"
    "python|python,python3"
    "node|node"
    "nginx|nginx"
    "redis|redis-cli"
)

# Risky binaries that often are NOT in minimal images.
_RISKY="curl wget"

# Parse compose to extract (service, image, healthcheck_test).
# Note: simple awk-based parser; assumes one healthcheck.test line per service.
awk '
    /^  [a-z][a-z0-9_-]*:$/ { svc=$1; sub(":","",svc); image=""; test=""; next }
    /^    image:/             { image=$2 }
    /^      test:/            { sub(/^[[:space:]]*test:[[:space:]]*/,""); test=$0; print svc "|" image "|" test }
' "$_compose" | while IFS='|' read -r svc image test; do
    [[ -z "$test" ]] && continue
    section "$svc ($image)"
    info "test: $test"

    # First arg in CMD array (after CMD/CMD-SHELL) is the binary
    bin=$(echo "$test" | sed -E 's/\[\s*"?CMD(-SHELL)?"?,\s*"?([^",]+)"?.*/\2/' | awk '{print $1}')
    info "first binary: $bin"

    # Find expected bin list for this image
    expected=""
    for pair in "${_KNOWN_BINS[@]}"; do
        sub="${pair%%|*}"
        bins="${pair#*|}"
        if [[ "$image" == *"$sub"* ]]; then
            expected="$bins"
            break
        fi
    done

    if [[ -z "$expected" ]]; then
        warn "no known-binary list for image '$image' — cannot auto-verify"
        continue
    fi

    if echo ",$expected," | grep -q ",$bin,"; then
        pass "$bin is in the known-bundled set for $image"
    else
        for risky in $_RISKY; do
            if [[ "$bin" == "$risky" ]]; then
                fail "$svc uses '$risky' but it is NOT documented in $image (often missing on ARM64)"
                continue 2
            fi
        done
        warn "$bin is not in our allowlist for $image — verify manually"
    fi
done

summary
