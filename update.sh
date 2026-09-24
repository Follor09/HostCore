#!/usr/bin/env bash
# Pull new commits from GitHub and restart the dashboard if anything changed.
# Runs every minute via pi-dashboard-update.timer (as root). Manual: sudo ./update.sh

set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="$(stat -c %U "$DIR")"
SERVICE="raspberry-dashboard"

# git/pip run as the repo owner so file ownership and SSH keys stay correct
as_owner() { sudo -u "$OWNER" -H -- "$@"; }
git_() { as_owner git -C "$DIR" "$@"; }

BRANCH="$(git_ rev-parse --abbrev-ref HEAD)"
git_ fetch --quiet origin "$BRANCH"

LOCAL="$(git_ rev-parse HEAD)"
REMOTE="$(git_ rev-parse "origin/$BRANCH")"
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "🔄 Update ${LOCAL:0:7} -> ${REMOTE:0:7} ($(git_ log -1 --format=%s "origin/$BRANCH"))"
CHANGED="$(git_ diff --name-only "$LOCAL" "$REMOTE")"

# Install new dependencies BEFORE switching code, so a failed pip leaves the
# old version running and the update is retried on the next run.
if grep -qx "requirements.txt" <<<"$CHANGED"; then
    echo "📦 requirements.txt changed, installing dependencies..."
    git_ show "origin/$BRANCH:requirements.txt" > /tmp/pi-dashboard-requirements.txt
    as_owner "$DIR/venv/bin/pip" install -q -r /tmp/pi-dashboard-requirements.txt
fi

# The Pi is a deploy target: GitHub is the source of truth (also handles force-pushes)
git_ reset --hard --quiet "origin/$BRANCH"

if grep -qE '\.(service|timer)$|^install\.sh$' <<<"$CHANGED"; then
    echo "⚙️  systemd units changed, re-running install.sh..."
    "$DIR/install.sh"
else
    systemctl restart "$SERVICE"
fi

echo "✅ Now running ${REMOTE:0:7}"
