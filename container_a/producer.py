"""
RabbitMQ producer for enriched notice payloads.

Connects to RabbitMQ, declares queue + dead-letter queue, and publishes
each enriched notice as a JSON message.  Wraps publish in a threading.Lock
for thread safety (PSC-3).

Implements PSC-2: heartbeat=600 on pika URLParameters.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pika
from pika.exceptions import AMQPConnectionError, ConnectionClosedByBroker, StreamLostError

logger = logging.getLogger(__name__)

# Defaults
RABBITMQ_URL: str = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
RABBITMQ_QUEUE: str = os.environ.get("RABBITMQ_QUEUE", "red_notices")
HEARTBEAT_SECONDS: int = int(os.environ.get("RABBITMQ_HEARTBEAT", "600"))
SCRAPE_INTERVAL_SECONDS: int = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "3600"))


class RabbitMQProducer:
    """Publishes enriched notice payloads to RabbitMQ.

    - Declares a main queue and a dead-letter queue on startup.
    - Thread-safe publish via ``threading.Lock``.
    - Reconnects on connection drop.
    """

    def __init__(
        self,
        rabbitmq_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        heartbeat: Optional[int] = None,
    ) -> None:
        self._url: str = rabbitmq_url or RABBITMQ_URL
        self._queue: str = queue_name or RABBITMQ_QUEUE
        self._dlq: str = f"{self._queue}.dlq"
        self._heartbeat: int = heartbeat if heartbeat is not None else HEARTBEAT_SECONDS
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._lock: threading.Lock = threading.Lock()
        self._connected: bool = False
        # Parse the virtual host from the URL for pika URLParameters
        self._params: pika.URLParameters = self._build_parameters()

    def _build_parameters(self) -> pika.URLParameters:
        params = pika.URLParameters(self._url)
        # PSC-2: heartbeat negotiated with server; must match compose-side heartbeat.
        # params.heartbeat = 600  (default from RABBITMQ_HEARTBEAT env)
        params.heartbeat = self._heartbeat
        return params

    def connect(self) -> None:
        """Establish connection, declare queues + DLQ."""
        self._params = self._build_parameters()
        self._connection = pika.BlockingConnection(self._params)
        self._channel = self._connection.channel()

        # Declare dead-letter queue first
        self._channel.queue_declare(
            queue=self._dlq,
            durable=True,
        )

        # Declare main queue with DLQ routing
        dlq_args = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": self._dlq,
        }
        self._channel.queue_declare(
            queue=self._queue,
            durable=True,
            arguments=dlq_args,
        )

        self._connected = True
        logger.info(
            "RabbitMQ producer connected. queue=%s, dlq=%s, heartbeat=%d",
            self._queue,
            self._dlq,
            self._heartbeat,
        )

    def _ensure_connection(self) -> None:
        """Reconnect if the connection is dead."""
        if self._connected and self._connection and self._connection.is_open:
            # Check if channel is still open
            if self._channel and self._channel.is_open:
                return
        logger.warning("RabbitMQ connection lost — reconnecting ...")
        self._try_reconnect()

    def _try_reconnect(self) -> None:
        """Keep trying to reconnect with backoff."""
        backoff = 1
        max_backoff = 60
        while True:
            try:
                if self._connection:
                    try:
                        self._connection.close()
                    except Exception:
                        pass
                self.connect()
                return
            except (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError) as exc:
                logger.warning(
                    "RabbitMQ reconnect failed (retrying in %ds): %s", backoff, exc
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except Exception as exc:
                logger.warning(
                    "RabbitMQ reconnect unexpected error (retrying in %ds): %s",
                    backoff,
                    exc,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    def publish(self, payload: Dict[str, Any]) -> bool:
        """Publish a single notice payload to the queue.

        Thread-safe — acquires internal lock.
        Returns True on success, False on failure.
        """
        with self._lock:
            try:
                self._ensure_connection()
                message = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    **payload,
                }
                body = json.dumps(message, ensure_ascii=False)
                self._channel.basic_publish(  # type: ignore[union-attr]
                    exchange="",
                    routing_key=self._queue,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # persistent
                        content_type="application/json",
                    ),
                )
                logger.debug("Published notice %s to queue %s", payload.get("notice_id"), self._queue)
                return True
            except (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError) as exc:
                logger.error("RabbitMQ publish error for %s: %s", payload.get("notice_id"), exc)
                self._connected = False
                # Try to reconnect once and retry
                try:
                    self._try_reconnect()
                    message = {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        **payload,
                    }
                    body = json.dumps(message, ensure_ascii=False)
                    self._channel.basic_publish(  # type: ignore[union-attr]
                        exchange="",
                        routing_key=self._queue,
                        body=body,
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            content_type="application/json",
                        ),
                    )
                    return True
                except Exception:
                    logger.exception(
                        "Failed to publish after reconnect for %s", payload.get("notice_id")
                    )
                    return False
            except Exception:
                logger.exception("Unexpected publish error for %s", payload.get("notice_id"))
                return False

    def close(self) -> None:
        """Gracefully close the connection."""
        self._connected = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
        logger.info("RabbitMQ producer closed.")


# ---------------------------------------------------------------------------
# Main — runs the scrape loop
# ---------------------------------------------------------------------------


def main() -> None:
    log_level_name = os.environ.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    interval = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "3600"))

    producer = RabbitMQProducer()
    # Initial connect
    try:
        producer.connect()
    except Exception:
        logger.exception("Failed to connect RabbitMQ producer — will retry in loop")

    # Import scraper here to keep module load light
    from scraper import RedNoticeScraper  # type: ignore[import-untyped]

    scraper = RedNoticeScraper()

    while True:
        try:
            logger.info("=== Starting scrape cycle ===")
            result = scraper.scrape(on_record=producer.publish)
            logger.info("Scrape cycle finished: %s", result)
        except Exception:
            logger.exception("Scrape cycle crashed — sleeping %ds before retry", interval)

        logger.info("Sleeping %d seconds until next scrape cycle ...", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
