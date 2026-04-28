#!/bin/bash
set -e

# Start wa-bridge in background on port 3000
cd /app/wa-bridge
node server.js &
BRIDGE_PID=$!
echo "wa-bridge started (PID $BRIDGE_PID)"

# Start FastAPI in foreground
cd /app
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
