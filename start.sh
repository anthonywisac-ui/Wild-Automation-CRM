#!/bin/bash

# Start wa-bridge in background — if it fails, FastAPI still runs
cd /app/wa-bridge
node server.js &
BRIDGE_PID=$!
echo "[start] wa-bridge started (PID $BRIDGE_PID)"

# Give bridge a moment to initialize
sleep 2

# Check if bridge is still running
if kill -0 $BRIDGE_PID 2>/dev/null; then
    echo "[start] wa-bridge running OK"
else
    echo "[start] WARNING: wa-bridge exited — FastAPI will still start"
fi

# Start FastAPI — this is the main process Railway monitors
cd /app
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
