#!/usr/bin/env bash
# check_streaming_publish.sh — verify streaming per-record publish (PSC-3).
#
# Checks: on_record callback in scrape(), per-record call, producer wiring,
# threading.Lock for publish serialization, no end-of-cycle batch publish.

set -u
source "$(dirname "$0")/../../_lib/common.sh"

_scraper="$REPO_ROOT/container_a/scraper.py"
_producer="$REPO_ROOT/container_a/producer.py"

# ---------------------------------------------------------------------------
section "scraper.py: on_record parameter in scrape() method"
# ---------------------------------------------------------------------------
if [[ ! -f "$_scraper" ]]; then
    fail "container_a/scraper.py not found"
else
    if grep -qE 'def scrape.*on_record|on_record.*def scrape' "$_scraper"; then
        pass "scraper.py scrape() method has on_record parameter"
    elif grep -qE 'on_record' "$_scraper"; then
        pass "scraper.py references on_record (callback present)"
    else
        fail "scraper.py scrape() method missing on_record callback parameter"
    fi

    if grep -qE 'on_record\(' "$_scraper"; then
        pass "scraper.py calls on_record() inside the scrape loop"
    else
        fail "scraper.py never calls on_record() — records not streamed per-record"
    fi
fi

# ---------------------------------------------------------------------------
section "producer.py: passes on_record= to scrape()"
# ---------------------------------------------------------------------------
if [[ ! -f "$_producer" ]]; then
    fail "container_a/producer.py not found"
else
    if grep -qE 'on_record=' "$_producer"; then
        pass "producer.py passes on_record= when calling scrape()"
    else
        fail "producer.py does not pass on_record= to scraper — no streaming publish"
    fi

    # ---------------------------------------------------------------------------
    section "producer.py: threading.Lock for publish serialization"
    # ---------------------------------------------------------------------------
    if grep -qE 'threading\.Lock|Lock\(\)' "$_producer"; then
        pass "producer.py has threading.Lock for publish serialization"
    else
        fail "producer.py missing threading.Lock — pika BlockingConnection is not thread-safe"
    fi

    # ---------------------------------------------------------------------------
    section "producer.py: imports threading"
    # ---------------------------------------------------------------------------
    if grep -qE '^import threading' "$_producer"; then
        pass "producer.py imports threading"
    else
        fail "producer.py does not import threading"
    fi

    # ---------------------------------------------------------------------------
    section "producer.py: no bulk end-of-cycle batch publish pattern"
    # ---------------------------------------------------------------------------
    # Heuristic: look for a list that gets appended then iterated and published
    _bulk_pattern=$(grep -nE 'notices\.append|records\.append|batch\.append' "$_producer" 2>/dev/null || true)
    if [[ -n "$_bulk_pattern" ]]; then
        # Check if there is also a loop that publishes from that list
        if grep -qE 'for.*in.*notices|for.*in.*records|for.*in.*batch' "$_producer" 2>/dev/null; then
            warn "producer.py may use bulk/batch publish pattern — verify on_record is the primary path"
        else
            pass "producer.py appends to list but no bulk publish loop detected"
        fi
    else
        pass "no bulk append+publish pattern detected in producer.py"
    fi
fi

summary
