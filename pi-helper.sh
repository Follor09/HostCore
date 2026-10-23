#!/usr/bin/env bash
# Root helper for the dashboard's firewall (ufw) and fail2ban panels.
# Allowed via sudo *only* from root's own repo copy (/var/lib/pi-dashboard/src, see
# install.sh), so the dashboard user can't change it. Every argument is checked
# against a strict pattern here - the dashboard is not trusted to send clean input.
#
#   pi-helper.sh ufw-status
#   pi-helper.sh ufw-enable <ssh-port>            allows SSH + dashboard first, then enables
#   pi-helper.sh ufw-disable
#   pi-helper.sh ufw-rule <allow|deny|reject|limit> <in|out> <iface|any> <tcp|udp|any>
#                         <from: any|ip|cidr> <src-port|any> <to: any|ip|cidr> <dst-port|any>
#                         <top|end> <log|nolog> [comment]
#                         ports: 443, 6000:6010 or a list like 80,443 (lists/ranges need tcp/udp)
#   pi-helper.sh ufw-logging <off|low|medium>
#   pi-helper.sh connections                     all sockets with their processes (ss -tunap)
#   pi-helper.sh apt-pkg <install|remove|upgrade> <pkg>...   starts apt as its own unit (survives dashboard restarts);
#                                                    upgrade = only newer versions of installed packages
#   pi-helper.sh geoblock-status
#   pi-helper.sh geoblock-apply < networks      one CIDR per line (IPv4 + IPv6), loaded into ipsets
#   pi-helper.sh geoblock-clear
#   pi-helper.sh geoblock-boot                  (pi-dashboard-geoblock.service, before ufw starts)
#   pi-helper.sh ufw-delete <rule-number>
#   pi-helper.sh ufw-preset <k3s|netbird|web>
#   pi-helper.sh fan-curve set <t0> <t1> <t2> <t3> <s0> <s1> <s2> <s3>   (m°C, PWM 0-255)
#   pi-helper.sh fan-curve reset
#   pi-helper.sh f2b-banned
#   pi-helper.sh f2b-unban <ip>

set -euo pipefail
export LC_ALL=C

DASHBOARD_PORT=8000
die() { echo "$*" >&2; exit 2; }

is_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
is_port_spec() {
    if [[ "$1" =~ ^([0-9]{1,5}):([0-9]{1,5})$ ]]; then
        local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"   # is_port overwrites BASH_REMATCH
        is_port "$a" && is_port "$b" && [ "$a" -lt "$b" ]
    else
        is_port "$1"
    fi
}
is_port_list() {  # up to 15 ports/ranges, comma separated
    local IFS=, n=0 p
    [[ "$1" =~ ^[0-9:,]{1,90}$ ]] || return 1
    for p in $1; do
        n=$((n + 1))
        [ "$n" -le 15 ] && is_port_spec "$p" || return 1
    done
    [ "$n" -ge 1 ]
}
is_ip() {  # IPv4/IPv6 address, optionally with /prefix
    [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$ ]] || [[ "$1" =~ ^[0-9A-Fa-f:]{2,39}(/[0-9]{1,3})?$ ]]
}

need() { command -v "$1" >/dev/null || die "$1 ist nicht installiert."; }

GEO_FILE=/var/lib/pi-dashboard/geoblock.ipset
V4_CIDR='([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])'
V6_CIDR='[0-9a-f:]{2,39}/([0-9]|[1-9][0-9]|1[01][0-9]|12[0-8])'

# Managed block in ufw's before.rules / before6.rules: after "accept established", so
# answers to our own connections still get through; blocked packets are logged like ufw's
geo_rules() {
    local f chain set
    for f in /etc/ufw/before.rules /etc/ufw/before6.rules; do
        [ -f "$f" ] || continue
        if [ "$f" = /etc/ufw/before.rules ]; then chain=ufw-before-input set=picore-geo4; else chain=ufw6-before-input set=picore-geo6; fi
        cp -p "$f" "$f.picore-bak"
        awk -v add="$1" -v chain="$chain" -v set="$set" '
            /^# PiCore geoblock start/ { skip = 1; next }
            /^# PiCore geoblock end/   { skip = 0; next }
            skip { next }
            { print }
            add == "add" && !done && $0 ~ "^-A " chain " -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT" {
                print "# PiCore geoblock start (managed by the dashboard)"
                print "-A " chain " -m set --match-set " set " src -m limit --limit 3/min --limit-burst 10 -j LOG --log-prefix \"[UFW BLOCK] \""
                print "-A " chain " -m set --match-set " set " src -j DROP"
                print "# PiCore geoblock end"
                done = 1
            }
        ' "$f" > "$f.picore-tmp"
        chmod --reference="$f" "$f.picore-tmp"
        mv "$f.picore-tmp" "$f"
    done
}

CMD="${1:-}"
[ $# -gt 0 ] && shift
case "$CMD" in
    ufw-status)
        need ufw
        ufw status verbose
        echo "---NUMBERED---"
        ufw status numbered
        echo "---ADDED---"
        ufw show added
        ;;
    ufw-enable)
        need ufw
        [ $# -eq 1 ] && is_port "$1" || die "Ungültiger SSH-Port."
        # Never lock yourself out: SSH and the dashboard stay reachable
        ufw allow "$1/tcp" comment "SSH (PiCore)" >/dev/null
        ufw allow "$DASHBOARD_PORT/tcp" comment "Dashboard (PiCore)" >/dev/null
        ufw --force enable
        ;;
    ufw-disable)
        need ufw
        ufw disable
        ;;
    ufw-rule)
        need ufw
        [ $# -ge 10 ] && [ $# -le 11 ] || die "Falsche Anzahl Argumente."
        ACTION="$1" DIR="$2" IFACE="$3" PROTO="$4" FROM="$5" SPORT="$6" TO="$7" DPORT="$8" POS="$9" LOG="${10}" COMMENT="${11:-}"
        [[ "$ACTION" =~ ^(allow|deny|reject|limit)$ ]] || die "Ungültige Aktion."
        [[ "$DIR" =~ ^(in|out)$ ]] || die "Ungültige Richtung."
        [ "$IFACE" = any ] || [[ "$IFACE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$ ]] || die "Ungültige Schnittstelle."
        [[ "$PROTO" =~ ^(tcp|udp|any)$ ]] || die "Ungültiges Protokoll."
        [ "$FROM" = any ] || is_ip "$FROM" || die "Ungültige Quelladresse."
        [ "$TO" = any ] || is_ip "$TO" || die "Ungültige Zieladresse."
        [ "$SPORT" = any ] || is_port_list "$SPORT" || die "Ungültiger Quellport."
        [ "$DPORT" = any ] || is_port_list "$DPORT" || die "Ungültiger Zielport."
        if [ "$PROTO" = any ]; then
            [[ "$SPORT$DPORT" == *[:,]* ]] && die "Portbereiche und -listen brauchen TCP oder UDP."
        fi
        [[ "$POS" =~ ^(top|end)$ ]] || die "Ungültige Position."
        [[ "$LOG" =~ ^(log|nolog)$ ]] || die "Ungültige Protokollierung."
        [[ "$COMMENT" =~ ^[A-Za-z0-9\ ._()-]{0,40}$ ]] || die "Ungültiger Kommentar."
        ARGS=()
        if [ "$POS" = top ] && ufw status | grep -q '^Status: active'; then
            # "insert 1" fails when the rule list is empty or starts with the other IP family
            if [[ "$FROM$TO" == *:* ]]; then
                FIRST="$(ufw status numbered | grep '(v6)' | head -n1 | sed -n 's/^\[ *\([0-9]*\)\].*/\1/p')"
            else
                FIRST="$(ufw status numbered | grep -v '(v6)' | sed -n 's/^\[ *\([0-9]*\)\].*/\1/p' | head -n1)"
            fi
            [ -n "$FIRST" ] && ARGS+=(insert "$FIRST")
        fi
        ARGS+=("$ACTION" "$DIR")
        [ "$IFACE" != any ] && ARGS+=(on "$IFACE")
        [ "$LOG" = log ] && ARGS+=(log)
        [ "$PROTO" != any ] && ARGS+=(proto "$PROTO")
        ARGS+=(from "$FROM")
        [ "$SPORT" != any ] && ARGS+=(port "$SPORT")
        ARGS+=(to "$TO")
        [ "$DPORT" != any ] && ARGS+=(port "$DPORT")
        [ -n "$COMMENT" ] && ARGS+=(comment "$COMMENT")
        ufw "${ARGS[@]}"
        ;;
    ufw-logging)
        need ufw
        [[ "${1:-}" =~ ^(off|low|medium)$ ]] || die "Ungültige Stufe."
        ufw logging "$1"
        ;;
    geoblock-status)
        echo "ipset=$(command -v ipset >/dev/null && echo yes || echo no)"
        echo "rules=$(grep -q '^# PiCore geoblock start' /etc/ufw/before.rules 2>/dev/null && echo yes || echo no)"
        if command -v ipset >/dev/null; then
            for f in 4 6; do
                N="$(ipset list "picore-geo$f" -terse 2>/dev/null | sed -n 's/^Number of entries: //p')"
                echo "v$f=${N:-0}"
            done
        fi
        [ -f "$GEO_FILE" ] && echo "saved=$(stat -c %Y "$GEO_FILE")"
        ;;
    geoblock-apply)
        need ufw
        need ipset
        TMP="$(mktemp)"
        trap 'rm -f "$TMP" "$TMP.set"' EXIT
        head -c 16000000 > "$TMP"
        # Nothing but CIDRs: every line is checked, one bad line rejects the whole list
        BAD="$(grep -cvE "^($V4_CIDR|$V6_CIDR)\$" "$TMP" || true)"
        [ "$BAD" = 0 ] || die "Ungültige Netzliste."
        LINES="$(wc -l < "$TMP")"
        [ "$LINES" -ge 1 ] && [ "$LINES" -le 600000 ] || die "Netzliste leer oder zu groß."
        {
            echo "create picore-geo4 hash:net family inet hashsize 65536 maxelem 1000000 -exist"
            echo "create picore-geo6 hash:net family inet6 hashsize 65536 maxelem 1000000 -exist"
            echo "create picore-geo4-new hash:net family inet hashsize 65536 maxelem 1000000 -exist"
            echo "create picore-geo6-new hash:net family inet6 hashsize 65536 maxelem 1000000 -exist"
            echo "flush picore-geo4-new"
            echo "flush picore-geo6-new"
            grep -E "^$V4_CIDR\$" "$TMP" | sed 's/^/add picore-geo4-new /'
            grep -E "^$V6_CIDR\$" "$TMP" | sed 's/^/add picore-geo6-new /'
        } > "$TMP.set"
        ipset restore -exist < "$TMP.set"
        ipset swap picore-geo4-new picore-geo4
        ipset swap picore-geo6-new picore-geo6
        ipset destroy picore-geo4-new
        ipset destroy picore-geo6-new
        # Saved for boot (pi-dashboard-geoblock.service loads it before ufw starts)
        install -d -m 700 "$(dirname "$GEO_FILE")"
        ipset save picore-geo4 > "$GEO_FILE.tmp"
        ipset save picore-geo6 >> "$GEO_FILE.tmp"
        mv "$GEO_FILE.tmp" "$GEO_FILE"
        systemctl enable --quiet pi-dashboard-geoblock.service 2>/dev/null || true
        if ! grep -q '^# PiCore geoblock start' /etc/ufw/before.rules; then
            geo_rules add
            ufw status | grep -q '^Status: active' && ufw reload >/dev/null
        fi
        echo "ok $LINES"
        ;;
    geoblock-clear)
        need ufw
        geo_rules remove
        ufw status | grep -q '^Status: active' && ufw reload >/dev/null
        if command -v ipset >/dev/null; then
            ipset destroy picore-geo4 2>/dev/null || true
            ipset destroy picore-geo6 2>/dev/null || true
        fi
        rm -f "$GEO_FILE"
        systemctl disable --quiet pi-dashboard-geoblock.service 2>/dev/null || true
        echo "ok"
        ;;
    geoblock-boot)
        # Must never fail: ufw starts right after and needs the sets its rules refer to
        if command -v ipset >/dev/null; then
            ipset create picore-geo4 hash:net family inet hashsize 65536 maxelem 1000000 -exist || true
            ipset create picore-geo6 hash:net family inet6 hashsize 65536 maxelem 1000000 -exist || true
            if [ -f "$GEO_FILE" ]; then
                ipset restore -exist < "$GEO_FILE" || true
            fi
        fi
        exit 0
        ;;
    connections)
        need ss
        ss -H -tunap
        ;;
    apt-pkg|apt-pkg-run)
        ACTION="${1:-}"
        [ $# -gt 0 ] && shift
        [[ "$ACTION" =~ ^(install|remove|upgrade)$ ]] || die "Unbekannte Aktion."
        MAX=20; [ "$ACTION" = upgrade ] && MAX=200
        [ $# -ge 1 ] && [ $# -le $MAX ] || die "1–$MAX Pakete angeben."
        for P in "$@"; do
            [[ "$P" =~ ^[a-z0-9][a-z0-9+.-]{0,62}(:(arm64|armhf|all))?$ ]] || die "Ungültiger Paketname: $P"
        done
        if [ "$ACTION" = remove ]; then
            # Everything apt would take away (incl. packages depending on these) must be safe to remove
            REMOVED="$( { apt-get -s remove -- "$@" 2>/dev/null || true; } | sed -n 's/^Remv \([^ ]*\).*/\1/p')"
            [ -n "$REMOVED" ] || die "Nichts zu entfernen."
            [ "$(wc -l <<<"$REMOVED")" -le 30 ] || die "Das würde mehr als 30 Pakete entfernen."
            for R in $REMOVED; do
                case "${R%%:*}" in
                    sudo|openssh-server|openssh-sftp-server|systemd|systemd-*|ufw|apt|dpkg|bash|coreutils|libc6|python3|python3-minimal|python3.*|\
                    raspi-firmware|raspberrypi-kernel|raspberrypi-bootloader|linux-image-*|rpi-eeprom|firmware-*|\
                    network-manager|dhcpcd|dhcpcd5|wpasupplicant|netbase|iproute2|iptables|ipset|git|ca-certificates|\
                    docker-ce|docker.io|containerd|containerd.io|login|passwd|util-linux|mount|init|init-system-helpers)
                        die "$R ist für das System oder das Dashboard nötig und wird nicht entfernt." ;;
                esac
                META="$(dpkg-query -W -f='${Essential} ${Priority}' "$R" 2>/dev/null || true)"
                case "$META" in
                    yes*|*required|*important) die "$R ist ein Basis-Paket und wird nicht entfernt." ;;
                esac
            done
        fi
        STATE=/run/pi-dashboard
        if [ "$CMD" = apt-pkg ]; then
            for U in pi-dashboard-apt@update pi-dashboard-apt@upgrade pi-dashboard-pkg; do
                systemctl is-active --quiet "$U.service" && die "apt läuft gerade schon."
            done
            SELF="$(readlink -f "$0")"
            systemctl reset-failed pi-dashboard-pkg.service 2>/dev/null || true
            systemd-run --unit=pi-dashboard-pkg --collect --quiet --setenv=DEBIAN_FRONTEND=noninteractive \
                "$SELF" apt-pkg-run "$ACTION" "$@"
            echo "started"
            exit 0
        fi
        # apt-pkg-run: the actual apt call inside the unit, log + status for the dashboard
        mkdir -p "$STATE" && chmod 755 "$STATE"
        LOG="$STATE/pkg.log"
        STATUS="$STATE/pkg.status"
        STARTED="$(date +%s)"
        PKGS="$*"
        : > "$LOG"; chmod 644 "$LOG"
        printf '{"action":"%s","packages":"%s","state":"running","exit_code":null,"started":%s,"finished":null}\n' "$ACTION" "$PKGS" "$STARTED" > "$STATUS"
        chmod 644 "$STATUS"
        echo "▶ apt-get $ACTION $PKGS" >> "$LOG"
        set +e
        if [ "$ACTION" = upgrade ]; then
            apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install --only-upgrade -- "$@" >> "$LOG" 2>&1
        else
            apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$ACTION" -- "$@" >> "$LOG" 2>&1
        fi
        RC=$?
        set -e
        STATE_WORD=$([ $RC -eq 0 ] && echo done || echo failed)
        printf '{"action":"%s","packages":"%s","state":"%s","exit_code":%s,"started":%s,"finished":%s}\n' "$ACTION" "$PKGS" "$STATE_WORD" "$RC" "$STARTED" "$(date +%s)" > "$STATUS.tmp"
        chmod 644 "$STATUS.tmp"
        mv "$STATUS.tmp" "$STATUS"
        exit $RC
        ;;
    ufw-delete)
        need ufw
        [ $# -eq 1 ] && [[ "$1" =~ ^[0-9]{1,4}$ ]] && [ "$1" -ge 1 ] || die "Ungültige Regelnummer."
        ufw --force delete "$1"
        ;;
    ufw-preset)
        need ufw
        case "${1:-}" in
            k3s)  # https://docs.k3s.io/installation/requirements#inbound-rules-for-k3s-nodes
                ufw allow 6443/tcp comment "k3s API (PiCore)"
                ufw allow from 10.42.0.0/16 to any comment "k3s Pods (PiCore)"
                ufw allow from 10.43.0.0/16 to any comment "k3s Services (PiCore)"
                ;;
            netbird)
                ufw allow in on wt0 comment "NetBird VPN (PiCore)"
                ;;
            web)
                ufw allow 80/tcp comment "HTTP (PiCore)"
                ufw allow 443/tcp comment "HTTPS (PiCore)"
                ;;
            *) die "Unbekannte Vorlage." ;;
        esac
        ;;
    fan-curve)
        CONFIG=/boot/firmware/config.txt
        [ -f "$CONFIG" ] || CONFIG=/boot/config.txt
        [ -f "$CONFIG" ] && [ ! -L "$CONFIG" ] || die "config.txt nicht gefunden."
        MODE="${1:-}"
        [ $# -gt 0 ] && shift
        if [ "$MODE" = set ]; then
            [ $# -eq 8 ] || die "Falsche Anzahl Werte."
            PREV_T=0 PREV_S=0
            for i in 1 2 3 4; do
                T="${!i}"
                [[ "$T" =~ ^[0-9]{5}$ ]] && [ "$T" -ge 30000 ] && [ "$T" -le 90000 ] && [ "$T" -gt "$PREV_T" ] \
                    || die "Temperaturen müssen 30–90 °C sein und ansteigen."
                PREV_T="$T"
            done
            for i in 5 6 7 8; do
                S="${!i}"
                [[ "$S" =~ ^[0-9]{1,3}$ ]] && [ "$S" -le 255 ] && [ "$S" -ge "$PREV_S" ] \
                    || die "Drehzahlen müssen 0–255 sein und dürfen nicht sinken."
                PREV_S="$S"
            done
        elif [ "$MODE" != reset ]; then
            die "Unbekannter Modus."
        fi
        # Backup (keep the last 5), then rewrite without any fan lines and append ours
        cp -p "$CONFIG" "$CONFIG.picore-$(date +%Y%m%d-%H%M%S)"
        ls -1t "$CONFIG".picore-* 2>/dev/null | tail -n +6 | xargs -r rm -f
        TMP="$(mktemp "$(dirname "$CONFIG")/.config.txt.XXXXXX")"
        awk '
            /^# PiCore fan curve start/ { skip = 1; next }
            /^# PiCore fan curve end/   { skip = 0; next }
            skip { next }
            /^[[:space:]]*dtparam=fan_temp[0-3](_hyst|_speed)?=/ { next }
            { print }
        ' "$CONFIG" > "$TMP"
        if [ "$MODE" = set ]; then
            {
                echo "# PiCore fan curve start (managed by the dashboard)"
                echo "[all]"
                for i in 0 1 2 3; do
                    T_ARG=$((i + 1)); S_ARG=$((i + 5))
                    echo "dtparam=fan_temp${i}=${!T_ARG}"
                    echo "dtparam=fan_temp${i}_hyst=5000"
                    echo "dtparam=fan_temp${i}_speed=${!S_ARG}"
                done
                echo "# PiCore fan curve end"
            } >> "$TMP"
        fi
        mv "$TMP" "$CONFIG"
        sync
        echo "ok"
        ;;
    f2b-banned)
        need fail2ban-client
        fail2ban-client banned
        ;;
    f2b-unban)
        need fail2ban-client
        [ $# -eq 1 ] && is_ip "$1" && [[ "$1" != */* ]] || die "Ungültige IP-Adresse."
        fail2ban-client unban "$1"
        ;;
    *)
        die "Unbekannter Befehl."
        ;;
esac
