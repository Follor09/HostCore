#!/usr/bin/env bash
# HostCore in one step on a fresh server - Debian, Ubuntu or Raspberry Pi OS:
# installs git, clones the repo for a normal user, checks out the newest release and runs
# install.sh (venv, service, auto-update, first admin). Safe to run again.
#
#   curl -fsSL https://github.com/Follor09/HostCore/releases/latest/download/setup.sh | bash
#   ... | bash -s -- --agent --from 192.168.1.10      agent for a main dashboard
#
# Options: --user <name>   who owns the dashboard (default: you; as plain root: "hostcore", created if missing)
#          --dir <path>    where to clone (default: ~<user>/pi-dashboard)
#          --main          newest commit on main instead of the newest release
#          everything else (--agent, --from <ip>) goes to install.sh

set -euo pipefail

REPO="${HOSTCORE_REPO:-https://github.com/Follor09/HostCore.git}"
BRANCH=main
USER_NAME=""
DIR=""
LATEST=""
PASS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME="${2:?--user braucht einen Namen}"; shift 2 ;;
        --dir) DIR="${2:?--dir braucht einen Pfad}"; shift 2 ;;
        --main) LATEST=1; shift ;;
        -h|--help) sed -n '2,12p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) PASS+=("$1"); shift ;;
    esac
done

[ "$(uname -s)" = Linux ] || { echo "❌ Nur für Linux-Server (Debian, Ubuntu, Raspberry Pi OS)."; exit 1; }
command -v apt-get >/dev/null || { echo "❌ Braucht ein Debian-basiertes System (Debian, Ubuntu, Raspberry Pi OS)."; exit 1; }

# --- Root or sudo ------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
    if [ -z "$USER_NAME" ]; then
        if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then USER_NAME="$SUDO_USER"; else USER_NAME=hostcore; fi
    fi
else
    command -v sudo >/dev/null || { echo "❌ sudo fehlt – als root ausführen oder sudo installieren."; exit 1; }
    SUDO=(sudo)
    [ -z "$USER_NAME" ] && USER_NAME="$(id -un)"
fi
[ "$USER_NAME" != root ] || { echo "❌ Das Dashboard läuft nie als root – --user <name> angeben."; exit 1; }
[[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "❌ Ungültiger Benutzername: $USER_NAME"; exit 1; }

# The dashboard needs its own normal user (the web terminal runs as it)
if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "👤 Lege Benutzer $USER_NAME an..."
    "${SUDO[@]}" useradd --create-home --shell /bin/bash "$USER_NAME"
    echo "   Kein Passwort gesetzt – Anmeldung per SSH-Key oder später: sudo passwd $USER_NAME"
fi
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
DIR="${DIR:-$USER_HOME/pi-dashboard}"

as_user() {
    if [ "$(id -un)" = "$USER_NAME" ]; then
        "$@"
    elif [ "$(id -u)" -eq 0 ]; then
        runuser -u "$USER_NAME" -- env HOME="$USER_HOME" "$@"
    else
        sudo -u "$USER_NAME" -H "$@"
    fi
}

# --- git -----------------------------------------------------------------------------
MISSING=()
command -v git >/dev/null || MISSING+=(git)
[ -e /etc/ssl/certs/ca-certificates.crt ] || MISSING+=(ca-certificates)
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "📦 Installiere ${MISSING[*]}..."
    "${SUDO[@]}" apt-get update -qq || true
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
fi

# --- Clone (or keep an existing clone) -----------------------------------------------
if [ -d "$DIR/.git" ]; then
    echo "📁 $DIR gibt es schon – behalte es (Updates macht der Auto-Update-Timer)."
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    echo "❌ $DIR existiert und ist kein HostCore-Repo – anderen Ordner mit --dir wählen."; exit 1
else
    echo "⬇️  Klone $REPO nach $DIR..."
    as_user mkdir -p "$(dirname "$DIR")"
    as_user git clone --quiet --branch "$BRANCH" "$REPO" "$DIR"
    if [ -z "$LATEST" ]; then
        TAG="$(as_user git -C "$DIR" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "origin/$BRANCH" --sort=-v:refname | sed -n 1p)"
        if [ -n "$TAG" ]; then
            echo "🏷️  Neuester Release: $TAG"
            as_user git -C "$DIR" checkout --quiet -B "$BRANCH" "$TAG"
            as_user git -C "$DIR" branch --quiet --set-upstream-to "origin/$BRANCH" "$BRANCH"
        fi
    else
        as_user sh -c 'echo main > "$1/.update-channel"' sh "$DIR"
        echo "📦 Update-Kanal: main (jeder Commit)"
    fi
fi

# --- install.sh (asks for the first admin: give it the terminal even under curl | bash)
echo "⚙️  Starte install.sh..."
if [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then
    "${SUDO[@]}" "$DIR/install.sh" "${PASS[@]+"${PASS[@]}"}" < /dev/tty
else
    "${SUDO[@]}" "$DIR/install.sh" "${PASS[@]+"${PASS[@]}"}"
fi
