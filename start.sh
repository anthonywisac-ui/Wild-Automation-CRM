#!/bin/bash

# ── Set FastAPI webhook URL so wa-bridge can forward messages ──────────────────
export FASTAPI_WEBHOOK_URL="http://localhost:${PORT:-8000}/wwebjs/webhook"
echo "[start] FastAPI webhook URL: ${FASTAPI_WEBHOOK_URL}"

# ── Delete stale Chromium SingletonLock from previous container ────────────────
# (volume persists lock files; new container gets Code 21 crash without this)
echo "[start] Clearing stale Chromium locks..."
for lock_name in SingletonLock SingletonCookie SingletonSocket; do
    find /app/wa-bridge/sessions -name "$lock_name" 2>/dev/null | while IFS= read -r f; do
        rm -f "$f"
        echo "[start] Removed: $f"
    done
done

# ── Start wa-bridge (stdout/stderr go directly to Railway logs) ────────────────
start_bridge() {
    cd /app/wa-bridge
    BRIDGE_PORT=3000 node server.js &
    echo $! > /tmp/wa-bridge.pid
    cd /app
    echo "[start] wa-bridge started (PID $(cat /tmp/wa-bridge.pid)) on port 3000"
}

start_bridge

# ── Wait until bridge health endpoint responds (up to 45s) ────────────────────
echo "[start] Waiting for wa-bridge to be ready..."
BRIDGE_READY=0
for i in $(seq 1 45); do
    if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
        echo "[start] wa-bridge ready (${i}s)"
        BRIDGE_READY=1
        break
    fi
    sleep 1
done

if [ "$BRIDGE_READY" = "0" ]; then
    echo "[start] ERROR: wa-bridge did not respond in 45s — check logs above for crash reason"
fi

# ── Watchdog: restart wa-bridge if it crashes ──────────────────────────────────
(
    while true; do
        sleep 15
        PID=$(cat /tmp/wa-bridge.pid 2>/dev/null)
        if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
            echo "[watchdog] wa-bridge down, restarting..."
            cd /app/wa-bridge
            BRIDGE_PORT=3000 node server.js &
            echo $! > /tmp/wa-bridge.pid
            echo "[watchdog] wa-bridge restarted (PID $(cat /tmp/wa-bridge.pid))"
            cd /app
        fi
    done
) &

# ── Start FastAPI ──────────────────────────────────────────────────────────────
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
