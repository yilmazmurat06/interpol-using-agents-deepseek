"""Container B entrypoint — start consumer thread + Flask web server.

The consumer runs in a background daemon thread.  Flask request handlers
each get their own Database instance (thread safety).
"""

import logging
import os
import signal
import sys

from app import app
from consumer import NoticeConsumer

logger = logging.getLogger(__name__)


def main() -> None:
    log_level_name = os.environ.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting Container B — web server + consumer")

    # Start the RabbitMQ consumer in a background thread
    consumer = NoticeConsumer()
    try:
        consumer.start()
    except Exception:
        logger.exception("Failed to start consumer — will continue with web server only")

    # Graceful shutdown handler
    def shutdown(signum: int, frame: object) -> None:
        logger.info("Shutdown signal received. Stopping consumer ...")
        consumer.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Start Flask
    port = int(os.environ.get("FLASK_PORT", "8080"))
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    logger.info("Flask running on %s:%d (debug=%s)", host, port, debug)
    app.run(host=host, port=port, threaded=True, debug=debug)


if __name__ == "__main__":
    main()
