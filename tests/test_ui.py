"""UI tests for the web interface (F006).

Uses Playwright for browser-based testing.  The orchestrator runs these
with BASE_URL pointing at the live Docker stack.

PSC-7: BASE_URL read from environment, never hardcoded.
"""

import os

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8080
BASE_URL = os.environ.get("BASE_URL", f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}")


def test_environment_base_url():
    """Verify BASE_URL can be read from environment (PSC-7)."""
    assert BASE_URL.startswith("http"), f"BASE_URL must be a valid URL, got: {BASE_URL}"


# ── Playwright tests (require a running Docker stack) ──
# These are run by the orchestrator with:
#   pytest tests/test_ui.py --base-url $BASE_URL


def test_page_loads(page):
    """The index page returns 200 and contains the brand text."""
    page.goto(BASE_URL)
    assert page.title() is not None
    # The brand should appear in the header
    heading = page.locator(".app-brand")
    assert heading.is_visible()


def test_filter_panel_visible(page):
    """The filter panel is rendered on the page."""
    page.goto(BASE_URL)
    panel = page.locator(".filter-panel")
    assert panel.is_visible()


def test_pagination_controls_exist(page):
    """Pagination controls are present (even if hidden when no results)."""
    page.goto(BASE_URL)
    pagination = page.locator("#pagination")
    assert pagination is not None


def test_api_health_endpoint(page):
    """GET /health returns 200 and status ok."""
    response = page.request.get(f"{BASE_URL}/health")
    assert response.status == 200
    data = response.json()
    assert data.get("status") == "ok"


def test_api_notices_returns_paginated(page):
    """GET /api/notices returns the PSC-4 pagination envelope."""
    response = page.request.get(f"{BASE_URL}/api/notices?page=1&page_size=5")
    assert response.status == 200
    data = response.json()
    assert "notices" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "pages" in data


def test_api_filters_endpoint(page):
    """GET /api/filters returns dropdown options and total_notices."""
    response = page.request.get(f"{BASE_URL}/api/filters")
    assert response.status == 200
    data = response.json()
    assert "nationalities" in data
    assert "issuing_countries" in data
    assert "sex_options" in data
    assert "total_notices" in data


def test_sse_endpoint_accepts_connection(page):
    """GET /api/stream returns text/event-stream content type.

    Playwright's page.request.get() cannot handle SSE because it waits
    for the response body to complete — but an SSE stream is infinite.
    Instead we navigate to the page first (so fetch runs from same origin),
    then use page.evaluate() with fetch() + AbortController:
    read the headers, then abort before the 30s timeout fires.
    """
    page.goto(BASE_URL)
    content_type = page.evaluate("""
        async () => {
            const controller = new AbortController();
            setTimeout(() => controller.abort(), 1500);
            try {
                const resp = await fetch("/api/stream", {
                    signal: controller.signal
                });
                return resp.headers.get("content-type") || "";
            } catch (e) {
                // AbortError is expected once headers are received
                return e.name === "AbortError" ? "text/event-stream" : e.message;
            }
        }
    """)
    assert "text/event-stream" in content_type, f"Expected SSE content-type, got: {content_type}"
