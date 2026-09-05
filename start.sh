#!/bin/bash
set -e

log() {
    echo "[MHTI] $1"
}

cleanup() {
    log "Shutting down..."
    kill -TERM "$CADDY_PID" 2>/dev/null || true
    kill -TERM "$API_PID" 2>/dev/null || true
    wait "$CADDY_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    log "Shutdown complete"
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

mkdir -p "${DATA_DIR:-/app/data}"

# Start API server (port 8001 - internal)
log "Starting API server on port 8001..."
uvicorn server.main:app --host 127.0.0.1 --port 8001 --workers 1 --log-level info &
API_PID=$!

# Wait for API to be ready
log "Waiting for API to be ready..."
for i in {1..30}; do
    if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)" > /dev/null 2>&1; then
        log "API is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        log "ERROR: API failed to start"
        exit 1
    fi
    sleep 1
done

# Start Caddy (port 8000 - main entry)
log "Starting Caddy on port 8000..."

# 验证 Caddy 配置
if ! caddy validate --config /etc/caddy/Caddyfile > /dev/null 2>&1; then
    log "ERROR: Invalid Caddyfile configuration"
    caddy validate --config /etc/caddy/Caddyfile
    exit 1
fi

# 检查静态文件目录
if [ ! -d "/app/static" ] || [ ! -f "/app/static/index.html" ]; then
    log "WARNING: Static files not found at /app/static"
fi

caddy run --config /etc/caddy/Caddyfile &
CADDY_PID=$!

log "MHTI is running on http://localhost:8000"

# 等待任一进程退出
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$CADDY_PID" 2>/dev/null; do
    sleep 1
done
exit 1
