#!/usr/bin/env bash
# One-time setup on the Raspberry Pi (safe to re-run):
# venv + dependencies, dashboard service, and GitHub auto-update timer.
# Usage (inside the cloned repo): ./install.sh

set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="$(stat -c %U "$DIR")"
as_owner() { sudo -u "$OWNER" -H -- "$@"; }

if ! as_owner env GIT_TERMINAL_PROMPT=0 git -C "$DIR" ls-remote origin >/dev/null 2>&1; then
    echo "🔑 Cannot read the GitHub repo (private?). A token is needed for auto-updates."
    if [ -t 0 ]; then "$DIR/set-token.sh"; else echo "   Run ./set-token.sh later."; fi
fi

echo "📦 Python venv & dependencies (user: $OWNER, dir: $DIR)..."
# Recreate a venv that is missing or broken (e.g. copied over from a Mac)
if ! as_owner "$DIR/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "🔧 venv missing or broken, recreating..."
    rm -rf "$DIR/venv"
    python3 -c 'import ensurepip' 2>/dev/null || apt-get install -y python3-venv
    as_owner python3 -m venv "$DIR/venv"
fi
as_owner "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
chmod +x "$DIR/update.sh" "$DIR/start.sh" "$DIR/set-token.sh" "$DIR/apt-run.sh"

echo "⚙️  Installing systemd units..."
for unit in raspberry-dashboard.service pi-dashboard-update.service pi-dashboard-update.timer pi-dashboard-apt@.service; do
    sed -e "s|__USER__|$OWNER|g" -e "s|__DIR__|$DIR|g" "$DIR/$unit" > "/etc/systemd/system/$unit"
done

# NVMe health: smartctl needs root, so allow exactly one read-only command
if [ -e /dev/nvme0 ] && ! command -v smartctl >/dev/null; then
    echo "💽 Installing smartmontools for NVMe health..."
    apt-get install -y -qq smartmontools || echo "   ⚠️  smartmontools install failed, NVMe health stays limited"
fi

# Narrow sudo rules: restart the dashboard (Update button / webhook), run apt via its
# systemd unit (System-Updates panel), read NVMe SMART data
SYSTEMCTL="$(command -v systemctl)"
SUDOERS=/etc/sudoers.d/pi-dashboard-restart
{
    echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL restart raspberry-dashboard"
    echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL start --no-block pi-dashboard-apt@update.service"
    echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL start --no-block pi-dashboard-apt@upgrade.service"
    # Service tiles: start/stop/restart exactly the units the dashboard tracks, plus "run update now"
    echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL start --no-block pi-dashboard-update.service"
    # CPU mode: write the governor file of each cpufreq policy (exact paths, no wildcards)
    for policy in /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do
        [ -e "$policy" ] && echo "$OWNER ALL=(root) NOPASSWD: $(command -v tee) $policy"
    done
    for unit in $(as_owner env PYTHONDONTWRITEBYTECODE=1 "$DIR/venv/bin/python" -c "import sys; sys.path.insert(0, '$DIR'); import services_manager as s; print(' '.join(s.controllable_units()))"); do
        for action in start stop restart; do
            echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL $action --no-block $unit"
        done
    done
    if [ -e /dev/nvme0 ] && command -v smartctl >/dev/null; then
        echo "$OWNER ALL=(root) NOPASSWD: $(command -v smartctl) -j -a /dev/nvme0"
    fi
} > "$SUDOERS.tmp"
visudo -cqf "$SUDOERS.tmp" && mv "$SUDOERS.tmp" "$SUDOERS" && chmod 440 "$SUDOERS"

systemctl daemon-reload
systemctl enable --now pi-dashboard-update.timer
systemctl enable raspberry-dashboard
systemctl restart raspberry-dashboard

# Login: ask for a password on first install (interactive runs only)
chmod +x "$DIR/set-password.sh"
if [ ! -f "$DIR/auth.json" ]; then
    if [ -t 0 ]; then
        echo "🔐 Dashboard-Login einrichten:"
        "$DIR/set-password.sh" set-password || echo "   ⚠️  Später nachholen: ./set-password.sh"
    else
        echo "🔐 Noch kein Login gesetzt – das Dashboard bleibt gesperrt, bis du ./set-password.sh ausführst."
    fi
fi

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "✅ Done! Dashboard: http://${IP}:8000"
echo "🔄 Auto-update: checks GitHub every minute -> journalctl -u pi-dashboard-update -f"
