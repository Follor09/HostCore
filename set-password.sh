#!/usr/bin/env bash
# Set the dashboard login (and optionally allow viewing without login).
# Usage: ./set-password.sh                 set user + password (logs out all sessions)
#        ./set-password.sh public-view on  anyone may view; actions still need login
#        ./set-password.sh public-view off
#        ./set-password.sh status
#        ./set-password.sh 2fa-reset <user>  two-factor login off (lost phone)

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="$(stat -c %U "$DIR" 2>/dev/null || stat -f %Su "$DIR")"
[ "$(id -un)" = "$OWNER" ] || exec sudo -u "$OWNER" -H -- "$0" "$@"

PYTHON="$DIR/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

exec "$PYTHON" "$DIR/backend/auth.py" "${@:-set-password}"
