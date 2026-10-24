#!/usr/bin/env bash
# Store a GitHub access token so the server can pull from a private repo.
# Usage: ./set-token.sh            (asks for the token, input hidden)
#        ./set-token.sh <token>
# Create one at https://github.com/settings/personal-access-tokens/new
#   -> Repository access: only this repo, Permissions: Contents = Read-only

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="$(stat -c %U "$DIR")"
[ "$(id -un)" = "$OWNER" ] || exec sudo -u "$OWNER" -H -- "$0" "$@"

CRED_FILE="$DIR/.git/github-credentials"

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
    read -rsp "🔑 GitHub token: " TOKEN
    echo
fi
[ -n "$TOKEN" ] || { echo "❌ No token given."; exit 1; }

# Tokens only work over HTTPS: turn git@github.com:USER/REPO.git into https://github.com/USER/REPO.git
URL="$(git -C "$DIR" remote get-url origin)"
case "$URL" in
    git@github.com:*) URL="https://github.com/${URL#git@github.com:}" ;;
esac

# Check the token before saving it
if ! GIT_TERMINAL_PROMPT=0 git -c credential.helper= ls-remote \
        "https://x-access-token:${TOKEN}@${URL#https://}" >/dev/null 2>&1; then
    echo "❌ Token rejected for $URL (wrong token, expired, or no access to this repo)."
    exit 1
fi

git -C "$DIR" remote set-url origin "$URL"
umask 077
printf 'https://x-access-token:%s@github.com\n' "$TOKEN" > "$CRED_FILE"
git -C "$DIR" config credential.helper "store --file=$CRED_FILE"

echo "✅ Token saved. The server can now pull from $URL"
