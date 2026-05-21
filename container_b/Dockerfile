# =============================================================================
# Container B — Flask Web Server + RabbitMQ Consumer
# =============================================================================
# Single-stage build: install deps system-wide as root, then switch to non-root
# appuser.  No multi-stage + --user because pip --user installs to /root/.local
# which is NOT in appuser's sys.path → ModuleNotFoundError at runtime.
# Base image: python:3.11-slim (pinned, no :latest)
# Exposes port defined by FLASK_PORT (default 8080) for healthcheck + web UI.
# =============================================================================

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --gid 1001 appuser

WORKDIR /app

# Install Python dependencies system-wide (as root, before switching users)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py consumer.py db.py models.py storage.py main.py ./
COPY templates/ templates/

# Switch to non-root user
USER appuser

# Healthcheck via Python socket on the configured port (PSC-5)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', ${FLASK_PORT:-8080}), 2)"

ENTRYPOINT ["python", "main.py"]
