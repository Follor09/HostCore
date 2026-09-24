#!/usr/bin/env bash
# First deploy from the Mac: clones the GitHub repo onto the Pi and runs install.sh.
# After that, just `git push` - the Pi pulls every new commit automatically.
# Usage: ./deploy.sh [user@pi-ip-or-hostname] [github-repo-url]

set -e

PI_TARGET="${1}"
REPO_URL="${2:-$(git -C "$(dirname "$0")" remote get-url origin 2>/dev/null || true)}"

if [ -z "$PI_TARGET" ]; then
    read -p "SSH-Ziel deines Pi (z.B. pi@raspberrypi.local): " PI_TARGET
fi
if [ -z "$REPO_URL" ]; then
    read -p "GitHub-Repo URL (z.B. https://github.com/USER/pi-dashboard.git): " REPO_URL
fi
if [ -z "$PI_TARGET" ] || [ -z "$REPO_URL" ]; then
    echo "❌ Pi-Ziel und Repo-URL werden benötigt. Abbruch."
    exit 1
fi

echo "🚀 Installiere ${REPO_URL} auf ${PI_TARGET}:~/pi-dashboard..."
ssh -t "$PI_TARGET" "
    set -e
    command -v git >/dev/null || sudo apt-get install -y git python3-venv
    if [ -d ~/pi-dashboard/.git ]; then
        git -C ~/pi-dashboard pull --ff-only
    else
        git clone '$REPO_URL' ~/pi-dashboard
    fi
    ~/pi-dashboard/install.sh
"
