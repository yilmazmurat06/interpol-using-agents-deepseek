"""
Flask web application for Interpol Red Notice viewer.

Routes:
  GET  /                           — HTML UI
  GET  /api/notices                — filtered, paginated JSON list (PSC-4)
  GET  /api/notices/<path:notice_id>  — single record
  GET  /api/filters                — filter options + total_notices
  GET  /api/thumbnail/<path:notice_id>  — proxied image via curl_cffi
  GET  /api/stream                 — SSE live updates
  GET  /health                     — liveness check
  POST /api/qa-report              — QA error reporting endpoint

All routes that accept notice_id use <path:notice_id> because IDs contain slashes.
Image proxying uses curl_cffi (NOT 302 redirect).
"""

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

# curl_cffi for image proxy (do NOT import standard `requests`)
from curl_cffi import requests as cffi_requests

logger = logging.getLogger("app")


class SSEDispatcher:
    """Thread-safe SSE event dispatcher using per-client queues."""

    def __init__(self):
        self._clients: List[queue.Queue] = []
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        """Register a new SSE client. Returns its message queue."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def unregister(self, q: queue.Queue):
        """Remove a client's queue."""
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def dispatch(self, record: Dict[str, Any]):
        """Push a notice update to all connected SSE clients."""
        payload = json.dumps(record, ensure_ascii=False)
        dead: List[queue.Queue] = []
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for d in dead:
                self._clients.remove(d)


def create_app(
    database=None,
    sse_dispatcher: Optional[SSEDispatcher] = None,
) -> Flask:
    """Factory: create and configure the Flask application."""

    app = Flask(__name__, template_folder="templates")
    app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

    db = database
    sse = sse_dispatcher or SSEDispatcher()

    # curl_cffi session for image proxy
    _image_session = cffi_requests.Session(impersonate="chrome120")
    _image_session.timeout = 30.0

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        """Serve the HTML UI."""
        return render_template("index.html")

    @app.route("/health")
    def health():
        """Liveness check."""
        return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

    # ------------------------------------------------------------------
    # API: Notices (PSC-4: paginated)
    # ------------------------------------------------------------------

    @app.route("/api/notices", methods=["GET"])
    def list_notices():
        """
        Return paginated, filtered notice list.

        Query params:
          page            — 1-indexed page number (default 1)
          page_size       — items per page (default 20, max 100)
          nationality     — ISO-2 country code
          sex             — "M" or "F"
          issuing_country — ISO-2 country code (from arrest_warrants)
          charges         — keyword search in charges
          name            — text search in name/forename
          is_alarm        — "true"/"1" to show alarms only
          sort            — "newest", "name", "nationality"
          order           — "asc" or "desc"
        """
        if db is None:
            return jsonify({"error": "Database not available"}), 503

        # Parse pagination
        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid page or page_size"}), 400

        # Parse filters
        filters: Dict[str, Any] = {}
        if request.args.get("nationality"):
            filters["nationality"] = request.args["nationality"].strip()
        if request.args.get("sex"):
            sex_val = request.args["sex"].strip().upper()
            if sex_val in ("M", "F"):
                filters["sex_id"] = sex_val
        if request.args.get("issuing_country"):
            filters["issuing_country"] = request.args["issuing_country"].strip()
        if request.args.get("charges"):
            filters["charges"] = request.args["charges"].strip()
        if request.args.get("name"):
            filters["name"] = request.args["name"].strip()
        if request.args.get("is_alarm") in ("true", "1"):
            filters["is_alarm_only"] = True

        # Parse sort
        sort_map = {
            "newest": "received_at",
            "name": "name",
            "nationality": "nationality",
        }
        order_by = sort_map.get(request.args.get("sort", "").lower(), "received_at")
        order_dir = request.args.get("order", "DESC").upper()
        if order_dir not in ("ASC", "DESC"):
            order_dir = "DESC"

        # Count and fetch
        try:
            total = db.count_notices(filters)
        except Exception:
            logger.exception("count_notices failed")
            return jsonify({"error": "Database query failed"}), 500

        pages = max(1, (total + page_size - 1) // page_size)
        offset = (page - 1) * page_size

        try:
            notices = db.get_all_notices(
                filters=filters,
                offset=offset,
                limit=page_size,
                order_by=order_by,
                order_dir=order_dir,
            )
        except Exception:
            logger.exception("get_all_notices failed")
            return jsonify({"error": "Database query failed"}), 500

        return jsonify({
            "notices": notices,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        })

    @app.route("/api/notices/<path:notice_id>", methods=["GET"])
    def get_notice(notice_id: str):
        """Return a single notice by ID. Uses <path:notice_id> for slash-containing IDs."""
        if db is None:
            return jsonify({"error": "Database not available"}), 503

        try:
            record = db.get_notice(notice_id)
        except Exception:
            logger.exception("get_notice failed for %s", notice_id)
            return jsonify({"error": "Database query failed"}), 500

        if record is None:
            return jsonify({"error": "Not found"}), 404

        return jsonify(record)

    # ------------------------------------------------------------------
    # API: Filters
    # ------------------------------------------------------------------

    @app.route("/api/filters", methods=["GET"])
    def get_filters():
        """
        Return available filter options:
          - nationalities (distinct ISO-2 codes in DB)
          - issuing_countries (distinct from arrest_warrants)
          - sex_options (["M", "F"])
          - total_notices (unfiltered DB count for live counter)
        """
        if db is None:
            return jsonify({"error": "Database not available"}), 503

        try:
            options = db.get_filter_options()
        except Exception:
            logger.exception("get_filter_options failed")
            return jsonify({"error": "Database query failed"}), 500

        return jsonify(options)

    # ------------------------------------------------------------------
    # API: Thumbnail proxy (curl_cffi — NOT 302 redirect)
    # ------------------------------------------------------------------

    @app.route("/api/thumbnail/<path:notice_id>", methods=["GET"])
    def thumbnail(notice_id: str):
        """
        Proxy image fetch via curl_cffi to bypass Akamai TLS fingerprinting.

        The notice_id is used to look up the image_url from the database,
        then the image is fetched server-side and streamed to the browser.

        Does NOT 302-redirect — Akamai blocks browser fetches too.
        """
        if db is None:
            return jsonify({"error": "Database not available"}), 503

        try:
            record = db.get_notice(notice_id)
        except Exception:
            logger.exception("get_notice failed for thumbnail %s", notice_id)
            return jsonify({"error": "Database error"}), 500

        if record is None:
            return jsonify({"error": "Not found"}), 404

        image_url = record.get("image_url") or ""
        if not image_url:
            # Some notices have no image — return a placeholder
            return _placeholder_svg(), 200, {"Content-Type": "image/svg+xml"}

        for img_attempt in range(2):
            try:
                # Fetch via curl_cffi (with browser TLS impersonation)
                resp = _image_session.get(image_url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("Content-Type", "image/jpeg")
                    return Response(
                        resp.content,
                        status=200,
                        headers={
                            "Content-Type": content_type,
                            "Cache-Control": "public, max-age=86400",
                        },
                    )
                logger.warning(
                    "Image fetch returned %d for %s (attempt %d/2)",
                    resp.status_code, notice_id, img_attempt + 1,
                )
                if img_attempt == 0:
                    time.sleep(1.0)
            except Exception:
                logger.exception(
                    "Image fetch failed for %s (attempt %d/2)",
                    notice_id, img_attempt + 1,
                )
                if img_attempt == 0:
                    time.sleep(1.0)
        return _placeholder_svg(), 200, {"Content-Type": "image/svg+xml"}

    # ------------------------------------------------------------------
    # SSE endpoint
    # ------------------------------------------------------------------

    @app.route("/api/stream", methods=["GET"])
    def stream():
        """
        SSE endpoint for live notice updates.

        Returns text/event-stream. The browser connects once and receives
        notice_update events as new records are processed by the consumer.

        PSC-7: SSE endpoints are infinite streams. Tests must use
        page.goto(BASE_URL) first, then page.evaluate() with fetch+AbortController.
        """
        client_queue = sse.register()

        def generate():
            try:
                # Send an initial comment to establish the connection
                yield ":connected\n\n"
                while True:
                    try:
                        data = client_queue.get(timeout=30)
                        yield f"event: notice_update\ndata: {data}\n\n"
                    except queue.Empty:
                        # Send keepalive comment
                        yield ":keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                sse.unregister(client_queue)

        return Response(
            stream_with_context(generate()),
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # QA report endpoint
    # ------------------------------------------------------------------

    @app.route("/api/qa-report", methods=["POST"])
    def qa_report():
        """Accept QA error reports (for test infrastructure)."""
        try:
            data = request.get_json(force=True)
            logger.info("QA report received: %s", json.dumps(data, ensure_ascii=False)[:500])
        except Exception:
            logger.warning("QA report with unparseable body")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400
        return jsonify({"status": "ok"})

    return app


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _placeholder_svg() -> str:
    """Return an inline SVG placeholder for notices without images."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="240" viewBox="0 0 200 240">'
        '<rect width="200" height="240" fill="#1a1a2e"/>'
        '<text x="100" y="110" text-anchor="middle" fill="#555" font-size="14" font-family="monospace">NO IMAGE</text>'
        '<text x="100" y="135" text-anchor="middle" fill="#444" font-size="11" font-family="monospace">AVAILABLE</text>'
        "</svg>"
    )
