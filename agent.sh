#!/usr/bin/env bash
# HostCore agent mode (this server is controlled from another HostCore dashboard).
# Works the same on a Raspberry Pi, a Debian or Ubuntu machine or VM (x86_64 / arm64).
#   ./agent.sh enable              switch this installation to agent mode, print the token
#   ./agent.sh token               show the token (to enter in the main dashboard)
#   ./agent.sh rotate              new token (the main dashboard needs the new one)
#   ./agent.sh allow <ip|net|any>  only accept the main dashboard's address (e.g. 192.168.1.10), "any" = every address
#   ./agent.sh disable             back to a normal dashboard with its own login
#   ./agent.sh status
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY=venv/bin/python; [ -x "$PY" ] || PY=python3
SYSTEMCTL="$(command -v systemctl || echo /usr/bin/systemctl)"
restart() { sudo -n "$SYSTEMCTL" restart raspberry-dashboard 2>/dev/null || echo "   (Dashboard bitte neu starten: sudo systemctl restart raspberry-dashboard)"; }
address() { echo "http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"; }

show() {
    echo ""
    echo "🛰️  HostCore-Agent aktiv. Im Haupt-Dashboard unter Einstellungen → Remote-Server eintragen:"
    echo "   Adresse: $(address)"
    echo "   Token:   $1"
    echo "   Erlaubt: $("$PY" backend/agent.py allowed)"
    echo ""
}

ufw_hint() {
    if command -v ufw >/dev/null && sudo -n ufw status 2>/dev/null | grep -q '^Status: active'; then
        echo "   ufw ist aktiv – Port 8000 für das Haupt-Dashboard freigeben, z. B.: sudo ufw allow from $1 to any port 8000 proto tcp"
    fi
}

case "${1:-status}" in
    enable)  TOKEN="$("$PY" backend/agent.py enable)"; restart; show "$TOKEN" ;;
    rotate)  TOKEN="$("$PY" backend/agent.py rotate)"; restart; show "$TOKEN"; echo "⚠️  Den neuen Token auch im Haupt-Dashboard eintragen." ;;
    token)   "$PY" backend/agent.py token && echo || { echo "Agent-Modus ist aus (./agent.sh enable)."; exit 1; } ;;
    allow)   shift; NETS="$("$PY" backend/agent.py allow "$@")"; restart; echo "Erlaubt: $NETS"; [ "$NETS" = any ] || ufw_hint "${NETS%%,*}" ;;
    disable) "$PY" backend/agent.py disable; restart; echo "Agent-Modus aus – das Dashboard hat wieder eine eigene Oberfläche (Login: ./set-password.sh)." ;;
    status)  if "$PY" backend/agent.py token >/dev/null 2>&1; then echo "Agent-Modus: an ($(address)), erlaubt: $("$PY" backend/agent.py allowed)"; else echo "Agent-Modus: aus"; fi ;;
    *) sed -n '2,9p' "$0"; exit 2 ;;
esac
