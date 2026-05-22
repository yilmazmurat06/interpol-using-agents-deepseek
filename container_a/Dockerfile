# =============================================================================
# Container A — Scraper / RabbitMQ producer
# Multi-stage build: builder installs deps, slim final image for runtime.
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --user --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# =============================================================================
# Runtime stage
# =============================================================================
FROM python:3.11-slim

# Create non-root user
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code
COPY scraper.py producer.py ./

# Ensure PATH includes user site-packages
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER appuser

# Healthcheck: verify producer can reach RabbitMQ via socket test
# (no curl bundled in slim image; use python socket)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('rabbitmq', 5672), 2); exit(0)"

CMD ["python", "producer.py"]
