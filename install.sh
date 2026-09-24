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
chmod +x "$DIR/update.sh" "$DIR/start.sh" "$DIR/set-token.sh"

echo "⚙️  Installing systemd units..."
for unit in raspberry-dashboard.service pi-dashboard-update.service pi-dashboard-update.timer; do
    sed -e "s|__USER__|$OWNER|g" -e "s|__DIR__|$DIR|g" "$DIR/$unit" > "/etc/systemd/system/$unit"
done

# Lets the dashboard's "Update" button / webhook restart itself without a password
SYSTEMCTL="$(command -v systemctl)"
SUDOERS=/etc/sudoers.d/pi-dashboard-restart
echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL restart raspberry-dashboard" > "$SUDOERS.tmp"
visudo -cqf "$SUDOERS.tmp" && mv "$SUDOERS.tmp" "$SUDOERS" && chmod 440 "$SUDOERS"

systemctl daemon-reload
systemctl enable --now pi-dashboard-update.timer
systemctl enable raspberry-dashboard
systemctl restart raspberry-dashboard

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "✅ Done! Dashboard: http://${IP}:8000"
echo "🔄 Auto-update: checks GitHub every minute -> journalctl -u pi-dashboard-update -f"
