#!/usr/bin/env bash
# PiCore agent mode (this Pi is controlled from another PiCore dashboard).
#   ./agent.sh enable    switch this installation to agent mode, print the token
#   ./agent.sh token     show the token (to enter in the main dashboard)
#   ./agent.sh rotate    new token (the main dashboard needs the new one)
#   ./agent.sh disable   back to a normal dashboard with its own login
#   ./agent.sh status
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY=venv/bin/python; [ -x "$PY" ] || PY=python3
SYSTEMCTL="$(command -v systemctl || echo /usr/bin/systemctl)"
restart() { sudo -n "$SYSTEMCTL" restart raspberry-dashboard 2>/dev/null || echo "   (Dashboard bitte neu starten: sudo systemctl restart raspberry-dashboard)"; }
address() { echo "http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"; }

show() {
    echo ""
    echo "🛰️  PiCore-Agent aktiv. Im Haupt-Dashboard unter Einstellungen → Remote-Pis eintragen:"
    echo "   Adresse: $(address)"
    echo "   Token:   $1"
    echo ""
}

case "${1:-status}" in
    enable)  TOKEN="$("$PY" agent.py enable)"; restart; show "$TOKEN" ;;
    rotate)  TOKEN="$("$PY" agent.py rotate)"; restart; show "$TOKEN"; echo "⚠️  Den neuen Token auch im Haupt-Dashboard eintragen." ;;
    token)   "$PY" agent.py token && echo || { echo "Agent-Modus ist aus (./agent.sh enable)."; exit 1; } ;;
    disable) "$PY" agent.py disable; restart; echo "Agent-Modus aus – das Dashboard hat wieder eine eigene Oberfläche (Login: ./set-password.sh)." ;;
    status)  if "$PY" agent.py token >/dev/null 2>&1; then echo "Agent-Modus: an ($(address))"; else echo "Agent-Modus: aus"; fi ;;
    *) sed -n '2,8p' "$0"; exit 2 ;;
esac
