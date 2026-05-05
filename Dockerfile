# syntax=docker/dockerfile:1
# SAHIIX AGI v2.5.0-omega — Production multi-stage build

# ── Builder stage ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install runtime dependencies: curl for healthcheck, libpq5 for PostgreSQL compatibility
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy application code
COPY . .

# Ensure writable directories exist and are owned by appuser
RUN mkdir -p /app/data /app/logs /app/memory \
    && chown -R appuser:appuser /app/data /app/logs /app/memory /app

USER appuser

EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7777/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7777", "--loop", "uvloop", "--workers", "4"]
