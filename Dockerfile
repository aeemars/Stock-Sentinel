# ==============================================================================
# Stock Sentinel — Production Container Image
# Optimized for Cloudflare Tunnel, Containers, and Cloud Platform deployments
# ==============================================================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies for C-extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final Lean Runtime Image
FROM python:3.12-slim

WORKDIR /app

# Install runtime PostgreSQL library and curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python dependencies
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application codebase
COPY . .

# Expose standard ports (5000 default, 8000 for standard PaaS containers)
ENV PORT=5000
EXPOSE 5000 8000

# Healthcheck endpoint (respects dynamic PORT)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT:-5000}/offline || exit 1

# Start Uvicorn ASGI server respecting dynamic cloud $PORT
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-5000} --workers 2

