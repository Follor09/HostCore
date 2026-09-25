#!/usr/bin/env bash
# Root helper for the dashboard's firewall (ufw) and fail2ban panels.
# Allowed via sudo *only* from root's own repo copy (/var/lib/pi-dashboard/src, see
# install.sh), so the dashboard user can't change it. Every argument is checked
# against a strict pattern here - the dashboard is not trusted to send clean input.
#
#   pi-helper.sh ufw-status
#   pi-helper.sh ufw-enable <ssh-port>            allows SSH + dashboard first, then enables
#   pi-helper.sh ufw-disable
#   pi-helper.sh ufw-rule <allow|deny|limit> <port|a:b> <tcp|udp|any> <any|ip|cidr> [comment]
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
        is_port "${BASH_REMATCH[1]}" && is_port "${BASH_REMATCH[2]}" && [ "${BASH_REMATCH[1]}" -lt "${BASH_REMATCH[2]}" ]
    else
        is_port "$1"
    fi
}
is_ip() {  # IPv4/IPv6 address, optionally with /prefix
    [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$ ]] || [[ "$1" =~ ^[0-9A-Fa-f:]{2,39}(/[0-9]{1,3})?$ ]]
}

need() { command -v "$1" >/dev/null || die "$1 ist nicht installiert."; }

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
        [ $# -ge 4 ] && [ $# -le 5 ] || die "Falsche Anzahl Argumente."
        ACTION="$1" PORT="$2" PROTO="$3" FROM="$4" COMMENT="${5:-}"
        [[ "$ACTION" =~ ^(allow|deny|limit)$ ]] || die "Ungültige Aktion."
        is_port_spec "$PORT" || die "Ungültiger Port."
        [[ "$PROTO" =~ ^(tcp|udp|any)$ ]] || die "Ungültiges Protokoll."
        [[ "$PORT" == *:* && "$PROTO" == any ]] && die "Portbereiche brauchen TCP oder UDP."
        [ "$FROM" = any ] || is_ip "$FROM" || die "Ungültige Quelladresse."
        [[ "$COMMENT" =~ ^[A-Za-z0-9\ ._()-]{0,40}$ ]] || die "Ungültiger Kommentar."
        ARGS=("$ACTION")
        [ "$PROTO" != any ] && ARGS+=(proto "$PROTO")
        ARGS+=(from "$FROM" to any port "$PORT")
        [ -n "$COMMENT" ] && ARGS+=(comment "$COMMENT")
        ufw "${ARGS[@]}"
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
