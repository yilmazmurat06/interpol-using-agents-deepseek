"""
Container B entrypoint.

Bootstraps the Flask web server + background RabbitMQ consumer.

Usage:
    python main.py
"""

import logging
import os
import signal
import sys
import time

from app import create_app, SSEDispatcher
from consumer import Consumer
from db import Database
from storage import MinioStorage

# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main():
    # Shared SSE dispatcher
    sse = SSEDispatcher()

    # Database — separate connections for consumer thread and Flask request handler.
    # psycopg2 connections are NOT thread-safe; sharing one across threads
    # causes InFailedSqlTransaction cascades and silent data corruption.
    db_consumer = Database()
    db_consumer.connect()

    db_flask = Database()
    db_flask.connect()

    # MinIO storage
    storage = MinioStorage()
    try:
        storage.connect()
    except Exception:
        logger.warning("MinIO connection failed — proceeding without object storage")

    # Consumer (own database connection)
    consumer = Consumer(
        database=db_consumer,
        storage=storage,
        sse_dispatcher=sse.dispatch,
    )
    consumer.start_in_background()

    # Give consumer time to establish connection before Flask starts
    time.sleep(1)

    # Flask app (own database connection — NOT shared with consumer)
    app = create_app(
        database=db_flask,
        sse_dispatcher=sse,
    )

    port = int(os.environ.get("WEB_PORT", "8080"))

    logger.info("Starting Flask on port %d", port)

    # Graceful shutdown handler
    def shutdown(signum, frame):
        logger.info("Shutdown signal received")
        consumer.stop()
        db_consumer.close()
        db_flask.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
