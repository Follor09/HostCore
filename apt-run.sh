#!/usr/bin/env bash
# Runs apt for the dashboard's System-Updates panel.
# Started as root via pi-dashboard-apt@update.service / pi-dashboard-apt@upgrade.service,
# so it keeps running even if the dashboard restarts mid-upgrade.
# Log + status go to /run/pi-dashboard (world-readable, root-writable).

set -uo pipefail

MODE="${1:-}"
case "$MODE" in
    update|upgrade) ;;
    *) echo "Usage: $0 update|upgrade" >&2; exit 2 ;;
esac

STATE_DIR="${PI_DASHBOARD_STATE_DIR:-/run/pi-dashboard}"
LOG="$STATE_DIR/apt.log"
STATUS="$STATE_DIR/apt.status"
mkdir -p "$STATE_DIR"
chmod 755 "$STATE_DIR"
: > "$LOG"
chmod 644 "$LOG"

STARTED="$(date +%s)"
write_status() {  # state exit_code finished
    printf '{"mode":"%s","state":"%s","exit_code":%s,"started":%s,"finished":%s}\n' \
        "$MODE" "$1" "$2" "$STARTED" "$3" > "$STATUS.tmp"
    chmod 644 "$STATUS.tmp"
    mv "$STATUS.tmp" "$STATUS"
}
write_status running null null

export DEBIAN_FRONTEND=noninteractive
(
    set -e
    echo "▶ apt-get update"
    apt-get update
    if [ "$MODE" = upgrade ]; then
        echo ""
        echo "▶ apt-get full-upgrade"
        # Keep existing config files instead of prompting
        apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade
        echo ""
        echo "▶ apt-get autoremove"
        apt-get -y autoremove
    fi
) >> "$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    echo "" >> "$LOG"
    echo "✅ Fertig." >> "$LOG"
    [ -f /var/run/reboot-required ] && echo "⚠️  Ein Neustart ist erforderlich." >> "$LOG"
    write_status done 0 "$(date +%s)"
else
    echo "" >> "$LOG"
    echo "❌ Fehlgeschlagen (Exit-Code $RC)." >> "$LOG"
    write_status failed "$RC" "$(date +%s)"
fi
exit "$RC"
