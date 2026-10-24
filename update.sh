#!/usr/bin/env bash
# Pull new commits from GitHub and restart the dashboard if anything changed.
# Runs every minute via pi-dashboard-update.timer (as root). Manual:
#   sudo systemctl start pi-dashboard-update.service
#
# Runs from root's own clone (/var/lib/pi-dashboard/src, see install.sh). Two copies
# are updated: the root copy (root-run scripts + unit files, only ever from GitHub)
# and the dashboard folder (the running app, updated as its owner).

set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

STATE=/var/lib/pi-dashboard
SRC="$STATE/src"
CRED="$STATE/github-credentials"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Started from the dashboard folder (old habit): hand over to the root copy
if [ "$SELF" != "$SRC" ]; then
    [ -x "$SRC/update.sh" ] || { echo "❌ No root copy yet - run ./install.sh first."; exit 1; }
    exec "$SRC/update.sh" "$@"
fi

DIR="${PI_DASHBOARD_DIR:-$(cat "$STATE/dir" 2>/dev/null || true)}"
[ -d "$DIR/.git" ] || { echo "❌ Dashboard folder unknown - run install.sh."; exit 1; }
OWNER="$(stat -c %U "$DIR")"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
SERVICE="raspberry-dashboard"

# git/pip in the dashboard folder run as its owner so file ownership and SSH keys stay correct.
# setpriv instead of sudo: no PAM session, so the journal isn't flooded every minute.
as_owner() { setpriv --reuid="$(id -u "$OWNER")" --regid="$(id -g "$OWNER")" --init-groups env HOME="$OWNER_HOME" "$@"; }
git_() { as_owner git -C "$DIR" "$@"; }
src_git() { GIT_TERMINAL_PROMPT=0 git -C "$SRC" "$@"; }

# A new token from ./set-token.sh: copy it to root's credential file (read as the user)
NEW_CRED="$(as_owner cat "$DIR/.git/github-credentials" 2>/dev/null || true)"
if [ -n "$NEW_CRED" ] && [ "$NEW_CRED" != "$(cat "$CRED" 2>/dev/null || true)" ]; then
    ( umask 077; printf '%s\n' "$NEW_CRED" > "$CRED.tmp" )
    mv "$CRED.tmp" "$CRED"
    echo "🔑 Token updated"
fi

# Update channel (Einstellungen -> Auto-Update, file .update-channel in the dashboard folder):
#   main   - every commit of the branch (default for existing installs)
#   stable - only releases: the newest tag vX.Y.Z on the branch, never back to an older one
CHANNEL="$(as_owner cat "$DIR/.update-channel" 2>/dev/null | tr -d '[:space:]' || true)"
[ "$CHANNEL" = "stable" ] || CHANNEL="main"

# Target commit for a repo: the branch head, or on "stable" its newest release tag.
# $1 = git wrapper, $2 = branch, $3 = installed commit
target() {
    local head tag commit
    head="$("$1" rev-parse "origin/$2")"
    [ "$CHANNEL" = "stable" ] || { echo "$head"; return; }
    tag="$("$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "origin/$2" --sort=-v:refname | sed -n 1p)"
    [ -n "$tag" ] || { echo "$3"; return; }          # no release yet: stay where we are
    commit="$("$1" rev-parse "$tag^{commit}")"
    if [ "$commit" != "$3" ] && "$1" merge-base --is-ancestor "$commit" "$3"; then
        echo "$3"                                      # installed version is newer than the release
    else
        echo "$commit"
    fi
}

# --- Root copy ---
SRC_BRANCH="$(src_git rev-parse --abbrev-ref HEAD)"
src_git fetch --quiet --tags --force origin "$SRC_BRANCH"
SRC_OLD="$(src_git rev-parse HEAD)"
SRC_NEW="$(target src_git "$SRC_BRANCH" "$SRC_OLD")"

# --- Dashboard folder ---
BRANCH="$(git_ rev-parse --abbrev-ref HEAD)"
git_ fetch --quiet --tags --force origin "$BRANCH"
LOCAL="$(git_ rev-parse HEAD)"
REMOTE="$(target git_ "$BRANCH" "$LOCAL")"

[ "$LOCAL" = "$REMOTE" ] && [ "$SRC_OLD" = "$SRC_NEW" ] && exit 0

RESTART=0
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "🔄 Update ${LOCAL:0:7} -> ${REMOTE:0:7} [$CHANNEL] ($(git_ log -1 --format=%s "$REMOTE"))"
    # Install new dependencies BEFORE switching code, so a failed pip leaves the
    # old version running and the update is retried on the next run.
    if git_ diff --name-only "$LOCAL" "$REMOTE" | grep -qx "requirements.txt"; then
        echo "📦 requirements.txt changed, installing dependencies..."
        REQ="$(mktemp)"
        trap 'rm -f "$REQ"' EXIT
        git_ show "$REMOTE:requirements.txt" > "$REQ"
        chmod 644 "$REQ"
        as_owner "$DIR/venv/bin/pip" install -q -r "$REQ"
    fi
    # The Pi is a deploy target: GitHub is the source of truth (also handles force-pushes)
    git_ reset --hard --quiet "$REMOTE"
    RESTART=1
fi

if [ "$SRC_OLD" != "$SRC_NEW" ]; then
    CHANGED="$(src_git diff --name-only "$SRC_OLD" "$SRC_NEW")"
    src_git reset --hard --quiet "$SRC_NEW"
    # Units, sudo rules (built from services_manager.py) or install.sh itself changed
    if grep -qE '\.(service|timer)$|^install\.sh$|^(backend/)?services_manager\.py$' <<<"$CHANGED"; then
        echo "⚙️  Setup changed, re-running install.sh..."
        PI_DASHBOARD_DIR="$DIR" "$SRC/install.sh"
        RESTART=0  # install.sh restarts the dashboard
    fi
fi

[ "$RESTART" -eq 1 ] && systemctl restart "$SERVICE"
echo "✅ Now running ${REMOTE:0:7}"
