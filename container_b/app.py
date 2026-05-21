"""Flask web application — API + HTML UI for Interpol Red Notices.

Implements:
- PSC-4: Server-side pagination with {notices, total, page, page_size, pages}
- SSE endpoint for live updates (only inserts on page 1 with no filters)
- Image proxy via curl_cffi (NOT 302 redirect)
- Thread-safe: each request handler creates its own Database instance
"""

import json
import logging
import os
import queue
import threading
from datetime import datetime
from typing import Any, Dict

from curl_cffi import requests as curl_requests
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from db import Database

logger = logging.getLogger(__name__)

FLASK_PORT: int = int(os.environ.get("FLASK_PORT", "8080"))
FLASK_HOST: str = os.environ.get("FLASK_HOST", "0.0.0.0")
API_THUMBNAIL_TIMEOUT: int = int(os.environ.get("API_THUMBNAIL_TIMEOUT", "15"))
DEFAULT_PAGE_SIZE: int = int(os.environ.get("DEFAULT_PAGE_SIZE", "20"))
MAX_PAGE_SIZE: int = int(os.environ.get("MAX_PAGE_SIZE", "200"))

app = Flask(__name__)

# Reusable curl_cffi session for image proxy (avoids creating a new
# session per thumbnail request — Akamai TLS handshake overhead).
# Thread-local so concurrent Flask threads each get their own session.
_image_local = threading.local()

def _image_session() -> curl_requests.Session:
    """Return a thread-local curl_cffi session for image proxying."""
    sess = getattr(_image_local, "session", None)
    if sess is None:
        sess = curl_requests.Session(impersonate="chrome120")
        _image_local.session = sess
    return sess

# SSE update queue (global, thread-safe)
_sse_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_sse_lock: threading.Lock = threading.Lock()


def notify_sse(notice_data: Dict[str, Any], is_alarm: bool = False) -> None:
    """Push a notice update to all connected SSE clients.

    Called by the consumer after successful upsert.
    """
    event_data = {
        "notice": notice_data,
        "is_alarm": is_alarm,
        "timestamp": datetime.utcnow().isoformat(),
    }
    _sse_queue.put(event_data)


# ---------------------------------------------------------------------------
# Routes — page rendering
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    """Serve the main HTML UI."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — health
# ---------------------------------------------------------------------------

@app.route("/health")
def health() -> Any:
    """Liveness check."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# ---------------------------------------------------------------------------
# Routes — filters
# ---------------------------------------------------------------------------

@app.route("/api/filters")
def api_filters() -> Any:
    """Return available filter options and total_notices for the UI."""
    db = Database()
    try:
        db.connect()
        options = db.get_filter_options()
        return jsonify(options)
    except Exception:
        logger.exception("GET /api/filters failed")
        return jsonify({
            "nationalities": [],
            "issuing_countries": [],
            "sex_options": [
                {"value": "", "label": "All"},
                {"value": "M", "label": "Male"},
                {"value": "F", "label": "Female"},
            ],
            "total_notices": 0,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Routes — notices (PSC-4: pagination)
# ---------------------------------------------------------------------------

@app.route("/api/notices")
def api_notices() -> Any:
    """Return paginated, filtered list of notices.

    Query params:
        page (int, default 1)
        page_size (int, default 20)
        nationality (str)
        sex (str) — sex_id
        issuing_country (str)
        charges (str) — keyword search in charges
        is_alarm (str) — "true" to filter alarms only
        sort (str) — newest, name_asc, nationality_asc
        search (str) — name text search
    """
    db = Database()
    try:
        db.connect()

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", DEFAULT_PAGE_SIZE, type=int)
        # Cap page_size to configured maximum
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        nationality = request.args.get("nationality")
        sex_id = request.args.get("sex")
        issuing_country = request.args.get("issuing_country")
        charges = request.args.get("charges")
        is_alarm_only = request.args.get("is_alarm", "").lower() == "true"
        sort = request.args.get("sort", "newest")
        search = request.args.get("search")

        total = db.count_notices(
            nationality=nationality,
            sex_id=sex_id,
            issuing_country=issuing_country,
            charges=charges,
            is_alarm_only=is_alarm_only,
            search=search,
        )
        pages = max(1, (total + page_size - 1) // page_size)
        # Clamp page to valid range
        page = max(1, min(page, pages))

        notices = db.get_all_notices(
            page=page,
            page_size=page_size,
            nationality=nationality,
            sex_id=sex_id,
            issuing_country=issuing_country,
            charges=charges,
            is_alarm_only=is_alarm_only,
            sort=sort,
            search=search,
        )

        return jsonify({
            "notices": notices,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        })
    except Exception:
        logger.exception("GET /api/notices failed")
        return jsonify({
            "notices": [],
            "total": 0,
            "page": 1,
            "page_size": DEFAULT_PAGE_SIZE,
            "pages": 0,
        }), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Routes — single notice
# ---------------------------------------------------------------------------

@app.route("/api/notices/<path:notice_id>")
def api_notice_detail(notice_id: str) -> Any:
    """Return a single notice record.

    Uses <path:notice_id> because IDs contain slashes (e.g. 2026/30493).
    """
    db = Database()
    try:
        db.connect()
        notice = db.get_notice(notice_id)
        if notice is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(notice)
    except Exception:
        logger.exception("GET /api/notices/%s failed", notice_id)
        return jsonify({"error": "Internal error"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Routes — thumbnail proxy (curl_cffi, NOT 302)
# ---------------------------------------------------------------------------

@app.route("/api/thumbnail/<path:notice_id>")
def api_thumbnail(notice_id: str) -> Any:
    """Proxy image fetch from Interpol CDN via curl_cffi.

    The browser never touches ws-public.interpol.int directly —
    this route fetches the image server-side and streams the bytes.
    """
    db = Database()
    try:
        db.connect()
        notice = db.get_notice(notice_id)
    except Exception:
        notice = None
    finally:
        db.close()

    if not notice or not notice.get("image_url"):
        # Return a transparent 1x1 pixel
        transparent_pixel = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
            b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return Response(transparent_pixel, mimetype="image/png")

    image_url = notice["image_url"]
    try:
        resp = _image_session().get(image_url, timeout=API_THUMBNAIL_TIMEOUT, stream=True)
        if getattr(resp, "status_code", 0) == 200:
            content_type = resp.headers.get("Content-Type", "image/jpeg")

            def stream_bytes():
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            return Response(
                stream_with_context(stream_bytes()),
                mimetype=content_type,
            )
        logger.warning("Thumbnail proxy: HTTP %d for %s", getattr(resp, "status_code", 0), notice_id)
    except Exception:
        logger.exception("Thumbnail proxy failed for %s", notice_id)

    # Fallback: empty PNG
    transparent_pixel = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return Response(transparent_pixel, mimetype="image/png")


# ---------------------------------------------------------------------------
# Routes — SSE
# ---------------------------------------------------------------------------

@app.route("/api/stream")
def api_stream() -> Any:
    """Server-Sent Events endpoint for live notice updates.

    The UI connects here to receive real-time updates as notices are
    processed by the consumer.  SSE inserts are conditional — the frontend
    only prepends new cards when on page 1 with no active filters (PSC-4).
    """
    def generate():
        while True:
            try:
                # Block until a message is available, with a 1s timeout
                # to allow heartbeat / connection check
                event_data = _sse_queue.get(timeout=1.0)
                yield f"data: {json.dumps(event_data, default=str)}\n\n"
            except queue.Empty:
                # Send heartbeat comment to keep connection alive
                yield ": heartbeat\n\n"
            except GeneratorExit:
                logger.debug("SSE client disconnected")
                break
            except Exception:
                logger.exception("SSE error — closing connection")
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
