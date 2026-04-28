#!/bin/bash

# wa-bridge uses its own port (3000), never Railway's PORT variable
cd /app/wa-bridge
BRIDGE_PORT=3000 node server.js &
BRIDGE_PID=$!
echo "[start] wa-bridge started (PID $BRIDGE_PID) on port 3000"

sleep 2

if kill -0 $BRIDGE_PID 2>/dev/null; then
    echo "[start] wa-bridge running OK"
else
    echo "[start] WARNING: wa-bridge exited"
fi

# FastAPI uses Railway's PORT
cd /app
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
