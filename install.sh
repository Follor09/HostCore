#!/usr/bin/env bash
# One-time setup on the Raspberry Pi (safe to re-run):
# venv + dependencies, dashboard service, and GitHub auto-update timer.
# Usage (inside the cloned repo): ./install.sh            normal dashboard
#                                  ./install.sh --agent    agent for a main dashboard (no own UI)
#
# Root never runs code from the dashboard folder: that folder belongs to the dashboard
# user, so anything root executes from there would let that user become root.
# Instead root keeps its own clone straight from GitHub in $SRC (root-owned). All
# root-run scripts (update.sh, apt-run.sh, this script) and unit files come from there.

set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

STATE=/var/lib/pi-dashboard
SRC="$STATE/src"
CRED="$STATE/github-credentials"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dashboard folder (the user's clone): where this script was started from, or - when
# running from the root copy - what the last install recorded.
if [ "$SELF" = "$SRC" ]; then
    DIR="${PI_DASHBOARD_DIR:-$(cat "$STATE/dir" 2>/dev/null || true)}"
else
    DIR="$SELF"
fi
[ -d "$DIR/.git" ] || { echo "❌ Dashboard folder not found ($DIR). Run install.sh from the cloned repo."; exit 1; }
OWNER="$(stat -c %U "$DIR")"
[ "$OWNER" != root ] || { echo "❌ $DIR belongs to root. Clone the repo as your normal user."; exit 1; }
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
as_owner() { setpriv --reuid="$(id -u "$OWNER")" --regid="$(id -g "$OWNER")" --init-groups env HOME="$OWNER_HOME" "$@"; }

if ! as_owner env GIT_TERMINAL_PROMPT=0 git -C "$DIR" ls-remote origin >/dev/null 2>&1; then
    echo "🔑 Cannot read the GitHub repo (private?). A token is needed for auto-updates."
    if [ -t 0 ]; then as_owner "$DIR/set-token.sh"; else echo "   Run ./set-token.sh later."; fi
fi

# --- Root copy of the repo ---------------------------------------------------------
if [ "$SELF" != "$SRC" ]; then
    echo "🔒 Root copy of the repo from GitHub ($SRC)..."
    install -d -o root -g root -m 755 "$STATE"
    # The token is read as the user (never follows a link to a root-only file) and
    # stored root-only, outside the repo copy.
    ( umask 077; as_owner cat "$DIR/.git/github-credentials" 2>/dev/null > "$CRED.tmp" || : > "$CRED.tmp" )
    mv "$CRED.tmp" "$CRED"

    URL="$(as_owner git -C "$DIR" remote get-url origin)"
    case "$URL" in
        git@github.com:*) URL="https://github.com/${URL#git@github.com:}" ;;
    esac
    BRANCH="$(as_owner git -C "$DIR" rev-parse --abbrev-ref HEAD)"
    rgit() { GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c credential.helper="store --file=$CRED" "$@"; }

    if [ ! -d "$SRC/.git" ]; then
        rm -rf "$SRC"
        rgit clone --quiet --branch "$BRANCH" "$URL" "$SRC" \
            || { echo "❌ Cannot clone $URL as root. Token set? (./set-token.sh)"; exit 1; }
    else
        git -C "$SRC" remote set-url origin "$URL"
        if rgit -C "$SRC" fetch --quiet origin "$BRANCH"; then
            git -C "$SRC" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
        else
            echo "   ⚠️  GitHub not reachable, using the existing root copy"
        fi
    fi
    git -C "$SRC" config credential.helper "store --file=$CRED"
    chown -R root:root "$SRC"
    chmod -R go-w "$SRC"
    echo "$DIR" > "$STATE/dir"

    # Continue with the GitHub version of this script
    exec env PI_DASHBOARD_DIR="$DIR" "$SRC/install.sh" "$@"
fi

# --- From here on: running from the root copy ---------------------------------------
echo "📦 Python venv & dependencies (user: $OWNER, dir: $DIR)..."
# Recreate a venv that is missing or broken (e.g. copied over from a Mac)
if ! as_owner "$DIR/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "🔧 venv missing or broken, recreating..."
    as_owner rm -rf "$DIR/venv"
    python3 -c 'import ensurepip' 2>/dev/null || apt-get install -y python3-venv
    as_owner python3 -m venv "$DIR/venv"
fi
as_owner "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
as_owner chmod +x "$DIR/start.sh" "$DIR/set-token.sh" "$DIR/set-password.sh" "$DIR/agent.sh"

# Agent mode: agent.json in the dashboard folder (kept across updates)
AGENT_TOKEN=""
case " $* " in
    *" --agent "*) AGENT_TOKEN="$(as_owner "$DIR/venv/bin/python" "$DIR/agent.py" enable)" ;;
esac
[ -z "$AGENT_TOKEN" ] && [ -f "$DIR/agent.json" ] && AGENT_TOKEN="$(as_owner "$DIR/venv/bin/python" "$DIR/agent.py" token || true)"

echo "⚙️  Installing systemd units..."
for unit in raspberry-dashboard.service pi-dashboard-update.service pi-dashboard-update.timer pi-dashboard-apt@.service pi-dashboard-geoblock.service; do
    sed -e "s|__USER__|$OWNER|g" -e "s|__DIR__|$DIR|g" -e "s|__SRC__|$SRC|g" "$SRC/$unit" > "/etc/systemd/system/$unit"
done

# Country blocking in the firewall uses ipset (small, only with ufw)
if command -v ufw >/dev/null && ! command -v ipset >/dev/null; then
    echo "🌍 Installing ipset for country blocking..."
    apt-get install -y -qq ipset || echo "   ⚠️  ipset install failed, country blocking stays unavailable"
fi

# Persistent web terminal: the shell lives in tmux, so a dropped connection does not end it
if ! command -v tmux >/dev/null; then
    echo "🖥️  Installing tmux for persistent terminal sessions..."
    apt-get install -y -qq tmux || echo "   ⚠️  tmux install failed, terminal sessions end with the connection"
fi

# Tasks and terminal sessions run as systemd user units of the dashboard user, so they keep
# running when the dashboard restarts - that needs the user's own systemd manager (lingering)
if command -v loginctl >/dev/null && [ "$(loginctl show-user "$OWNER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "⏳ Enabling lingering for $OWNER (background tasks survive dashboard restarts)..."
    loginctl enable-linger "$OWNER" || echo "   ⚠️  enable-linger failed, tasks end when the dashboard restarts"
fi

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
    # Storage cleanup: exactly these two commands
    echo "$OWNER ALL=(root) NOPASSWD: $(command -v journalctl) --vacuum-time=14d"
    echo "$OWNER ALL=(root) NOPASSWD: $(command -v apt-get) clean"
    # CPU mode: write the governor file of each cpufreq policy (exact paths, no wildcards)
    for policy in /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do
        [ -e "$policy" ] && echo "$OWNER ALL=(root) NOPASSWD: $(command -v tee) $policy"
    done
    # Unit list from the root copy; names are checked so nothing else can end up in the rule
    for unit in $(as_owner env PYTHONDONTWRITEBYTECODE=1 "$DIR/venv/bin/python" -c "import sys; sys.path.insert(0, '$SRC'); import services_manager as s; print(' '.join(s.controllable_units()))"); do
        [[ "$unit" =~ ^[A-Za-z0-9@._-]+$ ]] || continue
        for action in start stop restart; do
            echo "$OWNER ALL=(root) NOPASSWD: $SYSTEMCTL $action --no-block $unit"
        done
    done
    # Firewall + fail2ban panels: one helper from the root copy that checks every argument
    echo "$OWNER ALL=(root) NOPASSWD: $SRC/pi-helper.sh *"
    if [ -e /dev/nvme0 ] && command -v smartctl >/dev/null; then
        echo "$OWNER ALL=(root) NOPASSWD: $(command -v smartctl) -j -a /dev/nvme0"
    fi
} > "$SUDOERS.tmp"
visudo -cqf "$SUDOERS.tmp" && mv "$SUDOERS.tmp" "$SUDOERS" && chmod 440 "$SUDOERS"

systemctl daemon-reload
systemctl enable --now pi-dashboard-update.timer
systemctl enable raspberry-dashboard
systemctl restart raspberry-dashboard

# Login: ask for a password on first install (interactive runs only) - an agent has no login
if [ -z "$AGENT_TOKEN" ] && [ ! -f "$DIR/auth.json" ]; then
    if [ -t 0 ]; then
        echo "🔐 Dashboard-Login einrichten:"
        as_owner "$DIR/set-password.sh" set-password || echo "   ⚠️  Später nachholen: ./set-password.sh"
    else
        echo "🔐 Noch kein Login gesetzt – das Dashboard bleibt gesperrt, bis du ./set-password.sh ausführst."
    fi
fi

IP="$(hostname -I | awk '{print $1}')"
echo ""
if [ -n "$AGENT_TOKEN" ]; then
    echo "✅ Done! PiCore-Agent läuft. Im Haupt-Dashboard unter Einstellungen → Remote-Pis eintragen:"
    echo "   Adresse: http://${IP}:8000"
    echo "   Token:   $AGENT_TOKEN      (später wieder anzeigen: ./agent.sh token)"
else
echo "✅ Done! Dashboard: http://${IP}:8000"
fi
echo "🔄 Auto-update: checks GitHub every minute -> journalctl -u pi-dashboard-update -f"
