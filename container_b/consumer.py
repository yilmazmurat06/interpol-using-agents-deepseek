"""
RabbitMQ consumer — reads enriched notice messages and persists to PostgreSQL.

Continuously listens to the queue, upserts records into the database,
stores raw payloads in MinIO, and dead-letters failed messages.

PSC-2: heartbeat=600 in URLParameters; outer reconnect loop for connection loss.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import pika
from pika.exceptions import AMQPConnectionError, ConnectionClosedByBroker, StreamLostError

logger = logging.getLogger("consumer")

RETRYABLE_ERRORS = (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError, OSError)


class Consumer:
    """Consumes Interpol notice messages from RabbitMQ and persists them."""

    def __init__(
        self,
        rabbitmq_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        database=None,        # Database instance (duck-typed)
        storage=None,         # MinioStorage instance (duck-typed)
        sse_dispatcher=None,  # Callable to notify SSE listeners
        heartbeat: int = 600,
    ):
        self._rabbitmq_url = rabbitmq_url or os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
        self._queue_name = queue_name or os.environ.get("RABBITMQ_QUEUE", "interpol_notices")
        self._heartbeat = heartbeat
        self._db = database
        self._storage = storage
        self._sse_dispatcher = sse_dispatcher

        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self):
        """Establish channel and start consuming."""
        params = pika.URLParameters(self._rabbitmq_url)
        params.heartbeat = self._heartbeat  # PSC-2: match compose-side heartbeat

        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()

        # Declare dead-letter queue (matches producer's declaration)
        dlq_name = f"{self._queue_name}.dlq"
        self._channel.queue_declare(queue=dlq_name, durable=True)

        # Declare main queue with matching dead-letter arguments.
        # Must match the producer's declaration — RabbitMQ rejects mismatched
        # re-declarations with PRECONDITION_FAILED (code 406).
        args = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": dlq_name,
        }
        self._channel.queue_declare(
            queue=self._queue_name,
            durable=True,
            arguments=args,
        )

        # Set QoS — one message at a time for safe processing
        self._channel.basic_qos(prefetch_count=1)

        # Register consumer callback
        self._channel.basic_consume(
            queue=self._queue_name,
            on_message_callback=self._on_message,
            auto_ack=False,  # Manual ack after successful DB write
        )

        logger.info("Consumer connected to queue '%s'", self._queue_name)

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def _on_message(self, channel, method, properties, body: bytes):
        """
        Callback for each incoming RabbitMQ message.

        Deserialises JSON, upserts to DB, stores in MinIO, acks on success,
        nacks (requeue=False → DLQ) on failure.
        """
        notice_id = "unknown"
        try:
            record: Dict[str, Any] = json.loads(body.decode("utf-8"))
            notice_id = record.get("notice_id", "unknown")
            logger.info("Processing notice %s", notice_id)

            # Upsert to PostgreSQL
            if self._db:
                self._db.upsert_notice(record)
                logger.info("Upserted notice %s to DB", notice_id)

            # Store raw payload in MinIO
            if self._storage:
                self._storage.store_payload(notice_id, record)

            # Notify SSE listeners
            if self._sse_dispatcher:
                try:
                    self._sse_dispatcher(record)
                except Exception:
                    logger.exception("SSE dispatch failed for %s", notice_id)

            # Acknowledge
            channel.basic_ack(delivery_tag=method.delivery_tag)
            logger.info("Acknowledged notice %s", notice_id)

        except json.JSONDecodeError:
            logger.error("Invalid JSON in message — sending to DLQ")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            logger.exception("Failed to process notice %s — sending to DLQ", notice_id)
            try:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                logger.exception("Failed to nack message")

    # ------------------------------------------------------------------
    # Run loop (PSC-2: outer reconnect)
    # ------------------------------------------------------------------

    def run(self):
        """
        Start consuming in the current thread. Blocks until stop() is called.

        Implements PSC-2 outer reconnect loop.
        """
        self._running = True
        while self._running:
            try:
                self._connect()
                logger.info("Consumer entering consume loop")
                self._channel.start_consuming()
            except RETRYABLE_ERRORS as exc:
                if not self._running:
                    break
                logger.warning("Consumer connection lost: %s. Reconnecting in 5s...", exc)
                time.sleep(5)
            except Exception as exc:
                if not self._running:
                    break
                logger.error("Unexpected consumer error: %s. Reconnecting in 10s...", exc)
                time.sleep(10)

    def start_in_background(self):
        """Start the consumer in a daemon background thread."""
        self._thread = threading.Thread(target=self.run, name="rabbitmq-consumer", daemon=True)
        self._thread.start()
        logger.info("Consumer background thread started")

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._channel and self._channel.is_open:
            try:
                self._channel.stop_consuming()
            except Exception:
                pass
        if self._connection and self._connection.is_open:
            try:
                self._connection.close()
            except Exception:
                pass
        logger.info("Consumer stopped")
