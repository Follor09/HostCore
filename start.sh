#!/usr/bin/env bash
# Raspberry Pi Dashboard Launcher

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Check if venv exists
if [ -d "venv" ]; then
    PYTHON_BIN="venv/bin/python3"
    UVICORN_BIN="venv/bin/uvicorn"
else
    PYTHON_BIN="python3"
    UVICORN_BIN="uvicorn"
fi

echo "=========================================="
echo "⚡ Starting Raspberry Pi Dashboard..."
echo "📍 Directory: $DIR"
echo "🌐 URL: http://0.0.0.0:8000"
echo "=========================================="

exec "$UVICORN_BIN" app:app --host 0.0.0.0 --port 8000 --reload
