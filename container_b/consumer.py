"""RabbitMQ consumer for enriched notice messages.

Continuously listens to the queue, upserts notices into PostgreSQL,
stores raw payloads in MinIO, and sends failed messages to DLQ.

Implements PSC-2: heartbeat=600 in pika URLParameters.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import pika
from pika.exceptions import AMQPConnectionError, ConnectionClosedByBroker, StreamLostError

from app import notify_sse
from db import Database
from storage import MinIOStorage

logger = logging.getLogger(__name__)

RABBITMQ_URL: str = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
RABBITMQ_QUEUE: str = os.environ.get("RABBITMQ_QUEUE", "red_notices")
HEARTBEAT_SECONDS: int = int(os.environ.get("RABBITMQ_HEARTBEAT", "600"))


class NoticeConsumer:
    """Consumes notice messages from RabbitMQ, persists to DB + MinIO.

    Uses a dedicated ``Database`` instance for DB writes.
    Failed messages are sent to the dead-letter queue via ``basic_nack(requeue=False)``.
    """

    def __init__(
        self,
        rabbitmq_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        heartbeat: Optional[int] = None,
    ) -> None:
        self._url: str = rabbitmq_url or RABBITMQ_URL
        self._queue: str = queue_name or RABBITMQ_QUEUE
        self._heartbeat: int = heartbeat if heartbeat is not None else HEARTBEAT_SECONDS
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._db: Database = Database()
        self._storage: MinIOStorage = MinIOStorage()
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Connect to DB + MinIO, then start the consumer in a background thread."""
        self._db.connect()
        self._storage.connect()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="rabbitmq-consumer")
        self._thread.start()
        logger.info("Consumer started on queue=%s", self._queue)

    def stop(self) -> None:
        """Signal the consumer thread to stop and clean up."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
        if self._channel:
            try:
                self._channel.stop_consuming()
            except Exception:
                pass
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
        self._db.close()
        self._storage.close()
        logger.info("Consumer stopped.")

    # ------------------------------------------------------------------
    # PSC-2 reconnect loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main consumer loop — reconnects on connection drop."""
        while self._running:
            try:
                self._connect_and_consume()
            except (AMQPConnectionError, ConnectionClosedByBroker, StreamLostError) as exc:
                logger.warning("Consumer connection lost: %s — reconnecting in 5s ...", exc)
                time.sleep(5)
            except Exception:
                logger.exception("Consumer unexpected error — reconnecting in 10s ...")
                time.sleep(10)

    def _connect_and_consume(self) -> None:
        """Establish connection, declare queues, and start consuming."""
        params = pika.URLParameters(self._url)
        # PSC-2: heartbeat negotiated with server; must match compose-side heartbeat.
        # params.heartbeat = 600  (default from RABBITMQ_HEARTBEAT env)
        params.heartbeat = self._heartbeat

        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()

        # Declare dead-letter queue first
        dlq_name = f"{self._queue}.dlq"
        self._channel.queue_declare(queue=dlq_name, durable=True)

        # Declare main queue with DLQ routing
        dlq_args = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": dlq_name,
        }
        self._channel.queue_declare(
            queue=self._queue,
            durable=True,
            arguments=dlq_args,
        )

        # Set QoS to 1 (process one message at a time)
        self._channel.basic_qos(prefetch_count=1)

        self._channel.basic_consume(
            queue=self._queue,
            on_message_callback=self._handle_message,
            auto_ack=False,
        )

        logger.info(
            "Consumer listening on queue=%s (heartbeat=%d)", self._queue, self._heartbeat
        )

        try:
            self._channel.start_consuming()
        except KeyboardInterrupt:
            self._channel.stop_consuming()

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def _handle_message(
        self,
        channel: pika.channel.Channel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        """Parse, persist, and ack a notice message.

        On failure: nack with requeue=False to send to DLQ.
        """
        notice_id = "unknown"
        try:
            payload = json.loads(body.decode("utf-8"))
            notice_id = payload.get("notice_id", "unknown")
            logger.info("Received message for notice: %s", notice_id)

            # Persist to DB
            result = self._db.upsert_notice(payload)

            # Store raw payload in MinIO
            object_key = self._storage.store_payload(notice_id, payload)

            # Push SSE notification for live UI updates
            notice_data = {
                **payload,
                "is_alarm": result.get("is_alarm", False),
                "received_at": datetime.utcnow().isoformat(),
            }
            notify_sse(notice_data, is_alarm=result.get("is_alarm", False))

            channel.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(
                "Notice %s processed: DB=%s alarm=%s MinIO=%s",
                notice_id,
                result.get("action", "unknown"),
                result.get("is_alarm", False),
                object_key or "NONE",
            )

        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in message for %s: %s — sending to DLQ", notice_id, exc)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        except Exception:
            logger.exception("Failed to process notice %s — sending to DLQ", notice_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
