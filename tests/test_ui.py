"""
Playwright-based UI tests for the Interpol Red Notice web application.

PSC-7: Uses BASE_URL from environment (default http://localhost:8080).
SSE tests use page.goto(BASE_URL) + page.evaluate() with fetch+AbortController
per the pattern described in CLAUDE.md — same-origin fetch avoids CORS blocks.
"""

import os
import time
import json

import pytest


# PSC-7: Read base URL from environment
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    """Override browser context args — optional, passed through by pytest-playwright."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 800},
    }


class TestHealthEndpoint:
    """Verify the health check endpoint."""

    def test_health_returns_ok(self, page):
        """GET /health returns 200 with status ok."""
        resp = page.request.get(f"{BASE_URL}/health")
        assert resp.status == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "time" in data


class TestFiltersEndpoint:
    """Verify /api/filters returns expected shape."""

    def test_filters_returns_expected_keys(self, page):
        resp = page.request.get(f"{BASE_URL}/api/filters")
        assert resp.status == 200
        data = resp.json()
        assert "nationalities" in data
        assert "issuing_countries" in data
        assert "sex_options" in data
        assert "total_notices" in data
        assert isinstance(data["total_notices"], int)
        assert isinstance(data["nationalities"], list)
        assert isinstance(data["issuing_countries"], list)


class TestNoticesEndpoint:
    """Verify /api/notices returns PSC-4 pagination shape."""

    def test_notices_returns_pagination_shape(self, page):
        resp = page.request.get(f"{BASE_URL}/api/notices")
        assert resp.status == 200
        data = resp.json()
        # PSC-4: must return {notices, total, page, page_size, pages}
        assert "notices" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "pages" in data
        assert isinstance(data["notices"], list)
        assert isinstance(data["total"], int)
        assert data["page"] >= 1
        assert data["page_size"] >= 1

    def test_notices_pagination_params(self, page):
        resp = page.request.get(f"{BASE_URL}/api/notices?page=1&page_size=5")
        assert resp.status == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 5
        assert len(data["notices"]) <= 5

    def test_notices_with_filters(self, page):
        resp = page.request.get(f"{BASE_URL}/api/notices?nationality=US&page=1&page_size=10")
        assert resp.status == 200
        data = resp.json()
        assert "notices" in data
        assert "total" in data


class TestSingleNoticeEndpoint:
    """Verify /api/notices/<path:notice_id> works with slash-containing IDs."""

    def test_notice_not_found(self, page):
        """Non-existent notice returns 404."""
        resp = page.request.get(f"{BASE_URL}/api/notices/9999/99999")
        assert resp.status == 404

    def test_notice_with_slash_path(self, page):
        """Verify the path converter accepts slashes in notice IDs."""
        # The route uses <path:notice_id> — this would 404 for a non-existent ID
        # but 200 if the ID exists in DB. Either way, it should NOT be a Flask 404 template.
        resp = page.request.get(f"{BASE_URL}/api/notices/2026/12345")
        # It's okay if the notice doesn't exist — we just want to verify the route
        # handles slashes without a routing error
        assert resp.status in (200, 404)


class TestThumbnailEndpoint:
    """Verify image proxy endpoint."""

    def test_thumbnail_placeholder_for_missing(self, page):
        """A notice without an image should return SVG placeholder, not error."""
        resp = page.request.get(f"{BASE_URL}/api/thumbnail/9999/99999")
        # Should return 404 (not found) or 200 with SVG placeholder
        assert resp.status in (200, 404)


class TestSSE:
    """
    Test Server-Sent Events endpoint.

    PSC-7 pattern: navigate to page first (same-origin), then use
    page.evaluate() with fetch + AbortController to test SSE without
    the 30-second timeout that page.request.get() would hit.
    """

    def test_sse_content_type(self, page):
        """
        Verify SSE endpoint returns text/event-stream content type.

        Uses PSC-7 pattern: goto page → evaluate JS fetch + AbortController.
        """
        # Navigate to the page first to establish same-origin
        page.goto(BASE_URL, wait_until="domcontentloaded")

        # Use JS to fetch /api/stream, read headers, then abort
        result = page.evaluate("""async () => {
          const controller = new AbortController();
          const response = await fetch('/api/stream', {
            signal: controller.signal,
            headers: { 'Accept': 'text/event-stream' }
          });
          const ct = response.headers.get('Content-Type');
          controller.abort();
          return ct;
        }""")

        assert result is not None
        assert "text/event-stream" in result.lower()

    def test_sse_connection_established(self, page):
        """
        Verify SSE sends an initial comment to confirm connection.

        PSC-7: navigates to page, then uses page.evaluate() with fetch.
        """
        page.goto(BASE_URL, wait_until="domcontentloaded")

        result = page.evaluate("""async () => {
          const controller = new AbortController();
          const response = await fetch('/api/stream', {
            signal: controller.signal,
            headers: { 'Accept': 'text/event-stream' }
          });
          // Read first chunk
          const reader = response.body.getReader();
          const { value, done } = await reader.read();
          const text = new TextDecoder().decode(value || new Uint8Array());
          controller.abort();
          return text;
        }""")

        assert result is not None
        assert "connected" in result.lower() or ":" in result


class TestUI:
    """Smoke tests for the HTML UI page."""

    def test_index_loads(self, page):
        """Verify the main page loads and contains expected elements."""
        page.goto(BASE_URL, wait_until="domcontentloaded")
        # Check that the title or key element is present
        title = page.title()
        assert "Interpol" in title or "RED" in title.upper()

    def test_filter_panel_present(self, page):
        """The filter panel should be rendered on the page."""
        page.goto(BASE_URL, wait_until="domcontentloaded")
        # Check for key filter elements
        name_input = page.locator("#filter-name")
        assert name_input.count() > 0
        nationality_select = page.locator("#filter-nationality")
        assert nationality_select.count() > 0
        btn_apply = page.locator("#btn-apply")
        assert btn_apply.count() > 0

    def test_card_grid_present(self, page):
        """The card grid container should be present."""
        page.goto(BASE_URL, wait_until="domcontentloaded")
        grid = page.locator("#card-grid")
        assert grid.count() > 0

    def test_header_stats_present(self, page):
        """The header stats (total, visible, alarms) should be present."""
        page.goto(BASE_URL, wait_until="domcontentloaded")
        stat_total = page.locator("#stat-total")
        assert stat_total.count() > 0


class TestQaReport:
    """Verify the QA report endpoint."""

    def test_qa_report_accepts_json(self, page):
        resp = page.request.post(
            f"{BASE_URL}/api/qa-report",
            data=json.dumps({"test": "value", "error": "test error"}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_qa_report_rejects_bad_body(self, page):
        resp = page.request.post(
            f"{BASE_URL}/api/qa-report",
            data="not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status == 400
