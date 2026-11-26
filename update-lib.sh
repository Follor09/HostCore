# Release selection and signature checks for update.sh and install.sh (sourced, runs as root
# from the root copy). Kept apart so tests can run it against a repository with signed tags.
#
# Needs: STATE (folder of the trust anchor), CHANNEL ("stable" or "main").
#
# Trust anchor: $STATE/allowed_signers, the keys releases must be signed with. install.sh
# stores it once from the repo. It only ever changes through a release signed with a key it
# already trusts: such a release may bring a new .github/allowed_signers (new release key) -
# so a key change needs the old key, and nothing on the main channel can change it.

SIGNERS="$STATE/allowed_signers"

# Is this tag signed with a trusted key? Without an anchor every tag counts as fine.
# $1 = git wrapper, $2 = tag
signed_ok() {
    [ -s "$SIGNERS" ] || return 0
    "$1" -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-tag "$2" >/dev/null 2>&1
}

# Target commit for a repo: the branch head, or on "stable" the newest release that is signed
# with a trusted key. A newer release signed with a key the server does not know yet (after a
# key change) is skipped, so an older one that brings the new key is installed first.
# $1 = git wrapper, $2 = branch, $3 = installed commit
target() {
    local head tag commit
    head="$("$1" rev-parse "origin/$2")"
    [ "$CHANNEL" = "stable" ] || { echo "$head"; return; }
    while read -r tag; do
        [ -n "$tag" ] || continue
        commit="$("$1" rev-parse "$tag^{commit}")"
        if [ "$commit" = "$3" ] || "$1" merge-base --is-ancestor "$commit" "$3"; then
            break                                      # installed version is this release or newer
        fi
        if signed_ok "$1" "$tag"; then
            echo "$commit"
            return
        fi
        # Said once per release, not every minute
        if [ ! -e "$STATE/unsigned-$tag" ]; then
            touch "$STATE/unsigned-$tag" 2>/dev/null || true
            echo "⚠️  Release $tag is not signed by a trusted key ($SIGNERS) - not installed" >&2
        fi
    done < <("$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --merged "origin/$2" --sort=-v:refname)
    echo "$3"                                          # nothing newer that may be installed
}

# Key change: the newest release signed with a trusted key decides which keys are trusted.
# Its .github/allowed_signers is read from that signed release itself (not from a working
# copy), so only a release signed with the old key can bring a new one - on any channel.
# Repeats after a change, so several key changes in a row are followed too.
# $1 = git wrapper of the root copy (tags fetched)
rotate_signers() {
    [ -s "$SIGNERS" ] || return 0
    local round tag content
    for round in 1 2 3 4 5; do
        content=""
        while read -r tag; do
            [ -n "$tag" ] && signed_ok "$1" "$tag" || continue
            content="$("$1" show "$tag:.github/allowed_signers" 2>/dev/null || true)"
            break                                      # only the newest trusted release decides
        done < <("$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname)
        if [ -z "$content" ] || ! grep -q 'namespaces="git"' <<<"$content" || [ "$content" = "$(cat "$SIGNERS")" ]; then
            return 0
        fi
        printf '%s\n' "$content" > "$SIGNERS.tmp"
        chmod 644 "$SIGNERS.tmp"
        mv "$SIGNERS.tmp" "$SIGNERS"
        echo "🔏 Release keys changed with $tag (signed with the previous key) - trust anchor updated"
    done
    return 0
}

# First trust anchor (install.sh, once): the keys of the newest release, read from that release
# itself. Taken only if the release is signed with them - or, for a key-change release, with
# the keys of the release before it. A fork with unsigned releases gets no anchor and keeps
# working as before. $1 = git wrapper of the root copy
create_anchor() {
    [ -e "$SIGNERS" ] && return 0
    local newest prev ok=""
    newest="$("$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | sed -n 1p)"
    prev="$("$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | sed -n 2p)"
    [ -n "$newest" ] || return 0
    "$1" show "$newest:.github/allowed_signers" > "$SIGNERS.new" 2>/dev/null || true
    if [ -s "$SIGNERS.new" ]; then
        "$1" -c gpg.ssh.allowedSignersFile="$SIGNERS.new" verify-tag "$newest" >/dev/null 2>&1 && ok=1
        if [ -z "$ok" ] && [ -n "$prev" ] && "$1" show "$prev:.github/allowed_signers" > "$SIGNERS.prev" 2>/dev/null; then
            "$1" -c gpg.ssh.allowedSignersFile="$SIGNERS.prev" verify-tag "$newest" >/dev/null 2>&1 && ok=1
        fi
    fi
    if [ -n "$ok" ]; then
        chmod 644 "$SIGNERS.new"
        mv "$SIGNERS.new" "$SIGNERS"
        echo "🔏 Signed releases: from now on only releases signed with the HostCore release key are installed"
    fi
    rm -f "$SIGNERS.new" "$SIGNERS.prev"
    return 0
}
