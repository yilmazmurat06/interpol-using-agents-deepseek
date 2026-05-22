"""
RabbitMQ producer — publishes enriched notice payloads to the queue.

Connects to RabbitMQ using credentials from RABBITMQ_URL, declares the
main queue and dead-letter queue on startup, and provides `publish_notice`
for streaming per-record publication from the scraper.

PSC-2: heartbeat set to 600 in URLParameters to match the broker-side config.
PSC-3: publish_notice is wrapped in a threading.Lock for thread safety.
"""

import json
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

import pika
from pika.exceptions import AMQPConnectionError, ConnectionClosedByBroker, StreamLostError

logger = logging.getLogger("producer")

RETRYABLE_ERRORS = (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError, OSError)


class Producer:
    """Publishes notice records to RabbitMQ with dead-lettering and reconnection."""

    def __init__(
        self,
        rabbitmq_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        heartbeat: int = 600,
    ):
        self._rabbitmq_url = rabbitmq_url or os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
        self._queue_name = queue_name or os.environ.get("RABBITMQ_QUEUE", "interpol_notices")
        self._heartbeat = heartbeat
        self._dlq_name = f"{self._queue_name}.dlq"

        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        # PSC-3: threading.Lock for thread-safe publish from ThreadPoolExecutor
        self._publish_lock = threading.Lock()
        self._connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        """Establish connection, declare queues. Safe to call multiple times."""
        if self._connected:
            return

        params = pika.URLParameters(self._rabbitmq_url)
        params.heartbeat = self._heartbeat  # PSC-2: match compose-side heartbeat

        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()

        # Declare dead-letter queue
        self._channel.queue_declare(
            queue=self._dlq_name,
            durable=True,
        )

        # Declare main queue with DLQ routing for rejected messages
        args: Dict[str, Any] = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": self._dlq_name,
        }
        self._channel.queue_declare(
            queue=self._queue_name,
            durable=True,
            arguments=args,
        )

        self._connected = True
        logger.info("Producer connected to RabbitMQ, queue=%s, dlq=%s", self._queue_name, self._dlq_name)

    def _reconnect(self):
        """Reconnect on connection drop with exponential backoff."""
        self._connected = False
        self._connection = None
        self._channel = None
        delay = 1.0
        while True:
            try:
                self.connect()
                return
            except Exception as exc:
                logger.warning("Producer reconnect failed: %s. Retrying in %.1fs", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30.0)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_notice(self, record: Dict[str, Any]):
        """
        Publish a single notice payload to the queue.

        Thread-safe (guarded by _publish_lock per PSC-3).
        Auto-reconnects on connection drop.
        """
        # Serialize once outside the lock
        body = json.dumps(record, ensure_ascii=False)

        with self._publish_lock:
            for attempt in range(3):
                try:
                    if not self._connected:
                        self._reconnect()

                    self._channel.basic_publish(
                        exchange="",
                        routing_key=self._queue_name,
                        body=body.encode("utf-8"),
                        properties=pika.BasicProperties(
                            delivery_mode=2,  # persistent
                            content_type="application/json",
                        ),
                    )
                    logger.debug("Published notice_id=%s", record.get("notice_id"))
                    return
                except RETRYABLE_ERRORS as exc:
                    logger.warning(
                        "Publish attempt %d failed for %s: %s",
                        attempt + 1, record.get("notice_id"), exc,
                    )
                    if attempt < 2:
                        self._reconnect()
                    else:
                        logger.error("Failed to publish %s after 3 attempts", record.get("notice_id"))
                        raise

    def close(self):
        """Gracefully close the connection."""
        if self._connection and self._connection.is_open:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connected = False


def make_on_record(producer: Producer) -> Callable[[Dict[str, Any]], None]:
    """
    Return an on_record callback that publishes via the producer.

    Designed for use as the scraper's on_record parameter (PSC-3).
    """
    def _on_record(record: Dict[str, Any]) -> None:
        try:
            producer.publish_notice(record)
        except Exception:
            logger.exception("Failed to publish notice %s", record.get("notice_id"))

    return _on_record


# ------------------------------------------------------------------
# Main entry point (runs container-a)
# ------------------------------------------------------------------

def main():
    """Wire scraper + producer with streaming on_record callback (PSC-3)."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # Build scraper from env
    from scraper import build_scraper_from_env
    scraper = build_scraper_from_env()

    # Build producer from env
    producer = Producer()
    producer.connect()

    # Create streaming callback (PSC-3: on_record wires scraper → producer)
    on_record = make_on_record(producer)

    # Run scrape on configured interval
    scrape_interval = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "3600"))
    logger.info("Starting scrape cycle (interval=%ds)", scrape_interval)

    while True:
        try:
            logger.info("=== Starting new scrape cycle ===")
            records = scraper.scrape(on_record=on_record)
            logger.info("Scrape cycle complete: %d records collected", len(records))
        except Exception:
            logger.exception("Scrape cycle failed — will retry after interval")

        # Wait for the next cycle
        logger.info("Sleeping for %d seconds until next scrape cycle", scrape_interval)
        time.sleep(scrape_interval)


if __name__ == "__main__":
    main()
