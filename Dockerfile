# ============================================
# MHTI - Multi-stage Docker Build
# Caddy (reverse proxy) + FastAPI (API)
# ============================================

# Stage 1: Build frontend
FROM node:24-alpine AS frontend-builder

WORKDIR /app

COPY web/package*.json ./
RUN npm ci --silent

COPY web/ .
RUN npm run build

# Stage 2: Build Python dependencies outside the runtime image
FROM python:3.12-slim AS python-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV" \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libffi-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

# Stage 3: Obtain the static Caddy binary without adding an APT repository.
FROM caddy:2-alpine AS caddy

# Stage 4: Minimal runtime environment
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="MHTI"
LABEL org.opencontainers.image.description="Media metadata scraper with TMDB integration"
LABEL org.opencontainers.image.version="2.1.0"
LABEL org.opencontainers.image.source="https://github.com/sfgawrgarf/MHTI"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

WORKDIR /app

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

# Copy backend source code
COPY server/ ./server/

# Copy frontend build artifacts
COPY --from=frontend-builder /app/dist /app/static/

# Copy Caddy configuration
COPY Caddyfile /etc/caddy/Caddyfile

# Copy startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Create data directory
RUN mkdir -p /app/data && chmod 755 /app/data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)" || exit 1

# Expose ports
EXPOSE 8000

CMD ["/app/start.sh"]
