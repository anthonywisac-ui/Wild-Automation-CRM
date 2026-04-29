#!/bin/bash

# ── Start wa-bridge ────────────────────────────────────────────────────────────
start_bridge() {
    cd /app/wa-bridge
    BRIDGE_PORT=3000 node server.js >> /tmp/wa-bridge.log 2>&1 &
    echo $! > /tmp/wa-bridge.pid
    cd /app
    echo "[start] wa-bridge started (PID $(cat /tmp/wa-bridge.pid)) on port 3000"
}

> /tmp/wa-bridge.log   # clear log
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
    echo "[start] ERROR: wa-bridge did not start in 45s. Last log output:"
    tail -20 /tmp/wa-bridge.log
fi

# ── Watchdog: restart wa-bridge if it crashes ──────────────────────────────────
(
    while true; do
        sleep 15
        PID=$(cat /tmp/wa-bridge.pid 2>/dev/null)
        if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
            echo "[watchdog] wa-bridge down, restarting..."
            cd /app/wa-bridge
            BRIDGE_PORT=3000 node server.js >> /tmp/wa-bridge.log 2>&1 &
            echo $! > /tmp/wa-bridge.pid
            echo "[watchdog] wa-bridge restarted (PID $(cat /tmp/wa-bridge.pid))"
            cd /app
        fi
    done
) &

# ── Start FastAPI ──────────────────────────────────────────────────────────────
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
