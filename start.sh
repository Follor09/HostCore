#!/usr/bin/env bash
# Start the dashboard by hand, e.g. for development on your own machine.
# On a server it runs as the systemd service raspberry-dashboard (see install.sh) - not via this script.
#   ./start.sh            like the service: port 8000, no auto-reload
#   DEV=1 ./start.sh      development: restarts on every change of a .py file (not on a server -
#                         an auto-update would restart it mid-request, and it costs CPU on a Pi)

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR" || exit 1

if [ -d "venv" ]; then
    UVICORN_BIN="venv/bin/uvicorn"
else
    UVICORN_BIN="uvicorn"
fi

ARGS=(app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}")
[ "${DEV:-}" = "1" ] && ARGS+=(--reload)

echo "=========================================="
echo "⚡ Starting HostCore Dashboard..."
echo "📍 Directory: $DIR"
echo "🌐 URL: http://${HOST:-0.0.0.0}:${PORT:-8000}${DEV:+  (DEV: auto-reload)}"
echo "=========================================="

exec "$UVICORN_BIN" "${ARGS[@]}"
