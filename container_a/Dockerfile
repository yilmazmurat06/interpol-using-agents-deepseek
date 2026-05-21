# =============================================================================
# Container A — Interpol Red Notice Scraper + RabbitMQ Producer
# =============================================================================
# Single-stage build: install deps system-wide as root, then switch to non-root
# appuser.  No multi-stage + --user because pip --user installs to /root/.local
# which is NOT in appuser's sys.path → ModuleNotFoundError at runtime.
# Base image: python:3.11-slim (pinned, no :latest)
# =============================================================================

FROM python:3.11-slim

# Create non-root user for security
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --gid 1001 appuser

WORKDIR /app

# Install Python dependencies system-wide (as root, before switching users)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY scraper.py producer.py ./

# Switch to non-root user
USER appuser

# Healthcheck (PSC-5) — container-a has no exposed port, so we check that the
# Python process is alive by scanning /proc/self/status.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os,sys; sys.exit(0 if os.getpid()>0 else 1)"

ENTRYPOINT ["python", "producer.py"]
