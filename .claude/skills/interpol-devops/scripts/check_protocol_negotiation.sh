#!/usr/bin/env bash
# check_protocol_negotiation.sh — verify negotiated protocol values are
# configured on BOTH sides. The classic trap: pika client requests heartbeat=600
# but RabbitMQ server is at default 60s → negotiated value is 60 → idle
# connections drop after 60s.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_compose="$REPO_ROOT/docker-compose.yml"
[[ -f "$_compose" ]] || { fail "docker-compose.yml not found"; summary; }

section "RabbitMQ heartbeat (negotiated = min(server, client))"

# Server side: RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS must contain "-rabbit heartbeat N"
server_hb=$(grep -oE 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS[^"]*"[^"]*-rabbit heartbeat ([0-9]+)' "$_compose" | grep -oE '[0-9]+$' | head -1)
if [[ -n "$server_hb" ]]; then
    pass "server heartbeat configured: ${server_hb}s"
else
    fail "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS missing -rabbit heartbeat → server uses default 60s"
    server_hb=60
fi

# Client side: every params.heartbeat = N in container_*/*.py
mapfile -t client_hbs < <(grep -hRoE 'params\.heartbeat\s*=\s*[0-9]+' "$REPO_ROOT"/container_*/ 2>/dev/null \
    | grep -oE '[0-9]+$' | sort -u)

if (( ${#client_hbs[@]} == 0 )); then
    fail "no params.heartbeat found in any pika client — server value will be used"
else
    for hb in "${client_hbs[@]}"; do
        info "client heartbeat: ${hb}s"
        if (( hb < 100 )); then
            warn "client heartbeat ${hb}s is short — idle drops likely"
        fi
        if (( hb != server_hb )); then
            warn "client ${hb}s != server ${server_hb}s — negotiated = min($hb, $server_hb) = $(( hb < server_hb ? hb : server_hb ))s"
        else
            pass "client ${hb}s matches server"
        fi
    done
fi

section "Pika connection reconnect loop"
if grep -RInE 'AMQPConnectionError|ConnectionClosedByBroker|StreamLostError' "$REPO_ROOT"/container_*/*.py >/dev/null 2>&1; then
    pass "code catches pika connection-loss exceptions"
else
    fail "no pika reconnect handling — a single network blip kills the consumer"
fi

summary
