#!/usr/bin/env bash
# Troubleshooting for known problems of a HostCore installation - checks, explains and on request repairs.
#   ./doctor.sh            check only (any user; as root it can look a bit deeper)
#   ./doctor.sh --fix      check, then repair what it can - asks before every step
#   ./doctor.sh --fix --yes   repair without asking (e.g. over a non-interactive ssh) - only the
#                             harmless steps; anything that interrupts (Docker, tasks, a running
#                             update) or weakens protection is still asked, or skipped without a terminal
#   ./doctor.sh --ip <addr>   also check whether this address is locked out (fail2ban, country
#                             blocking) - e.g. the phone that cannot reach the dashboard any more
#
# Known problems it recognises:
#   - Docker missing in the dashboard although it runs: the dashboard user was put into the
#     docker group after the dashboard started (groups only apply to newly started processes),
#     never put into it (old hint added root instead), Docker from snap without a docker group,
#     Docker daemon stopped; the same for tasks/terminal (the user's own systemd manager)
#   - system logs empty (systemd-journal/adm group), VMs (libvirt/kvm group), Kubernetes
#     (no readable kubeconfig)
#   - service stopped/crashed, port 8000 taken, broken venv, root copy / sudo rules / update
#     timer missing, lingering off, files owned by root after a "sudo git pull", clock not synced,
#     disk full, no login set
#   - updates that silently stop: release not signed by a trusted key, GitHub token expired or DNS
#     broken, failed or hanging update run, service running from another folder than the updater
#     updates; dpkg interrupted, apt held for a long time by unattended-upgrades
#   - crashes: restart loop, out-of-memory kills, settings.json restored from its backup
#   - hardware: read-only file system and I/O errors (SD card), no free inodes, Raspberry Pi
#     under-voltage / throttling
#   - Docker: snap Docker only reads below /home (compose projects elsewhere fail), another Docker
#     context or DOCKER_HOST, only rootless Docker running, podman instead of Docker
#   - locked out: own address banned by fail2ban or in a blocked country; agent refusing the main
#     dashboard's new address
#
# Repairs run as root, and root never runs code from the dashboard folder (it belongs to the
# dashboard user): "--fix" hands over to root's own copy in /var/lib/pi-dashboard/src, like update.sh.

set -uo pipefail
export LC_ALL=C

STATE=/var/lib/pi-dashboard
SRC="$STATE/src"
SERVICE=raspberry-dashboard
PORT=8000
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Like the service (app.py): Docker from snap and tools in /usr/local are found
export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/snap/bin"

IP_RX='^([0-9]{1,3}\.){3}[0-9]{1,3}$|^[0-9A-Fa-f]{0,4}(:[0-9A-Fa-f]{0,4}){2,7}$'
FIX=""; YES=""; CHECK_IPS=()
ARGV=("$@")
for ((i = 0; i < ${#ARGV[@]}; i++)); do
    a="${ARGV[$i]}"
    case "$a" in
        --fix) FIX=1 ;;
        --yes|-y) YES=1 ;;
        --ip) i=$((i + 1)); v="${ARGV[$i]:-}"
              [[ "$v" =~ $IP_RX ]] || { echo "--ip: keine gültige IP-Adresse ($v)" >&2; exit 2; }
              CHECK_IPS+=("$v") ;;
        -h|--help) sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $a (./doctor.sh --help)" >&2; exit 2 ;;
    esac
done
# The address this ssh session comes from (sudo drops SSH_CLIENT - hand it over as --ip)
if [ -n "${SSH_CLIENT:-}" ] && [[ "${SSH_CLIENT%% *}" =~ $IP_RX ]]; then
    CHECK_IPS+=("${SSH_CLIENT%% *}")
    set -- "$@" --ip "${SSH_CLIENT%% *}"
fi

# --- Root (and every repair) runs root's own copy only -------------------------------------
if [ "$SELF" != "$SRC" ] && { [ -n "$FIX" ] || [ "$(id -u)" -eq 0 ]; }; then
    if [ -x "$SRC/doctor.sh" ]; then
        [ "$(id -u)" -eq 0 ] && exec "$SRC/doctor.sh" "$@"
        exec sudo "$SRC/doctor.sh" "$@"
    fi
fi
if [ -n "$FIX" ] && { [ "$(id -u)" -ne 0 ] || [ "$SELF" != "$SRC" ]; }; then
    echo "❌ Für --fix fehlt $SRC/doctor.sh (Root-Kopie noch ohne dieses Skript oder nicht installiert)."
    if [ -d "$SRC/.git" ]; then
        echo "   Root-Kopie aktualisieren: sudo systemctl start pi-dashboard-update.service – dann erneut."
    else
        echo "   Zuerst installieren: ./install.sh"
    fi
    echo "   Ohne --fix prüft das Skript nur und zeigt die Befehle zum Beheben."
    exit 1
fi

IS_ROOT=""; [ "$(id -u)" -eq 0 ] && IS_ROOT=1
HAS_SYSTEMD=""; [ -d /run/systemd/system ] && HAS_SYSTEMD=1

# --- Output -----------------------------------------------------------------------------
if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'; else B=""; G=""; Y=""; R=""; D=""; N=""; fi
PROBLEMS=0
FIX_DESC=(); FIX_CMD=(); FIX_RISKY=()
NEED_RESTART=""; NEED_USER_RESTART=""

section() { echo; echo "${B}$*${N}"; }
ok()      { echo "  ${G}✔${N} $*"; }
info()    { echo "  ${D}·${N} $*"; }
warn()    { echo "  ${Y}⚠${N} $*"; PROBLEMS=$((PROBLEMS + 1)); }
bad()     { echo "  ${R}✖${N} $*"; PROBLEMS=$((PROBLEMS + 1)); }
hint()    { echo "      ${D}→${N} $*"; }
# fix "<what it does>" <command...>   - offered with --fix, otherwise shown as command
fix() {
    local desc="$1"; shift
    FIX_DESC+=("$desc"); FIX_CMD+=("$(printf '%q ' "$@")"); FIX_RISKY+=("${RISKY:-}")
    [ -n "$FIX" ] || hint "$desc:  sudo ${FIX_CMD[-1]% }"
}
# A fix that interrupts something running or weakens protection: never applied by --yes alone
fix_risky() { RISKY=1 fix "$@"; }
# A fix that only needs the dashboard (or the user's systemd manager) restarted
need_restart()      { NEED_RESTART=1; [ -n "$FIX" ] || hint "sudo systemctl restart $SERVICE"; }
# install.sh as root only from the root copy; without it the user has to start it
fix_install() {
    if [ -x "$SRC/install.sh" ]; then fix "$1" env PI_DASHBOARD_DIR="$DIR" "$SRC/install.sh"
    else hint "als $OWNER ausführen: $DIR/install.sh"
    fi
}
need_user_restart() { NEED_USER_RESTART=1; [ -n "$FIX" ] || hint "sudo systemctl restart user@$OUID.service   ${D}(beendet laufende Aufgaben und Terminal-Sitzungen)${N}"; }

# --- Dashboard folder and user ------------------------------------------------------------
DIR="$(cat "$STATE/dir" 2>/dev/null || true)"
[ -d "$DIR" ] || DIR="$SELF"
OWNER="$(stat -c %U "$DIR" 2>/dev/null || echo "")"
if [ -z "$OWNER" ] || ! [[ "$OWNER" =~ ^[a-z_][a-z0-9_.-]*\$?$ ]]; then
    echo "❌ Dashboard-Ordner $DIR nicht lesbar."; exit 1
fi
OUID="$(id -u "$OWNER")"
OGROUP="$(id -gn "$OWNER")"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
ME="$(id -un)"

# Run as the dashboard user with its groups as they are in /etc/group now (= after a restart)
as_owner() {
    if [ -n "$IS_ROOT" ]; then
        setpriv --reuid="$OUID" --regid="$(id -g "$OWNER")" --init-groups env HOME="$OWNER_HOME" "$@"
    elif [ "$ME" = "$OWNER" ]; then
        "$@"
    else
        return 99   # cannot tell
    fi
}
in_group()  { id -nG "$1" 2>/dev/null | tr ' ' '\n' | grep -qx "$2"; }
gid_of()    { getent group "$1" | cut -d: -f3; }
pid_has_gid() { awk -v g="$2" '/^Groups:/ { for (i = 2; i <= NF; i++) if ($i == g) f = 1 } END { exit !f }' "/proc/$1/status" 2>/dev/null; }
can_journal() { [ -n "$IS_ROOT" ] || in_group "$ME" systemd-journal || in_group "$ME" adm; }
# Is a process with this pid older than n seconds?
older_than() { local e; e="$(ps -o etimes= -p "$1" 2>/dev/null | tr -d ' ')"; [ -n "$e" ] && [ "$e" -gt "$2" ]; }
join() { local out="$1"; shift; for x in "$@"; do out+=", $x"; done; echo "$out"; }
fmt_age() { local s="$1"; if [ "$s" -ge 3600 ]; then echo "$((s / 3600)) h $((s % 3600 / 60)) min"; else echo "$((s / 60)) min"; fi; }
unit_pid()  { local p; p="$(systemctl show -p MainPID --value "$1" 2>/dev/null)"; [ -n "$p" ] && [ "$p" != 0 ] && echo "$p"; }

echo "${B}HostCore Doctor${N}  –  Ordner: $DIR, Benutzer: $OWNER${IS_ROOT:+, als root}"
[ -n "$IS_ROOT" ] || [ "$ME" = "$OWNER" ] || echo "${D}(als $ME – manche Prüfungen gehen nur als $OWNER oder mit sudo)${N}"

SVC_PID=""; USER_PID=""
if [ -n "$HAS_SYSTEMD" ]; then
    SVC_PID="$(unit_pid "$SERVICE.service" || true)"
    USER_PID="$(unit_pid "user@$OUID.service" || true)"
fi
# Running inside the user's systemd manager (web terminal of the dashboard)? Then do not restart it.
IN_USER_MANAGER=""
grep -q "user@$OUID.service" /proc/self/cgroup 2>/dev/null && IN_USER_MANAGER=1

# =============================================================================================
section "Dienst"
# =============================================================================================
if [ -z "$HAS_SYSTEMD" ]; then
    info "kein systemd – Dienstprüfungen übersprungen"
else
    state="$(systemctl is-active "$SERVICE" 2>/dev/null)"
    enabled="$(systemctl is-enabled "$SERVICE" 2>/dev/null)"
    if [ "$state" = active ]; then
        ok "$SERVICE läuft (PID $SVC_PID)"
    elif [ -z "$enabled" ] || [ "$enabled" = not-found ]; then
        bad "$SERVICE ist nicht installiert"
        fix_install "Installation ausführen"
    else
        bad "$SERVICE ist \"$state\""
        if [ -n "$IS_ROOT" ] || in_group "$ME" systemd-journal || in_group "$ME" adm; then
            echo "${D}"; journalctl -u "$SERVICE" -n 12 --no-pager -o cat 2>/dev/null | sed 's/^/      │ /'; echo -n "${N}"
        else
            hint "Fehlermeldung: sudo journalctl -u $SERVICE -n 50 --no-pager"
        fi
        need_restart
    fi
    [ "$enabled" = enabled ] || [ "$state" != active ] || { warn "$SERVICE startet nach einem Neustart nicht von selbst"; fix "Autostart einschalten" systemctl enable "$SERVICE"; }

    # Port 8000 taken by something else (e.g. a second dashboard started by hand)
    if command -v ss >/dev/null; then
        listener="$(ss -Hltnp "sport = :$PORT" 2>/dev/null | head -1)"
        if [ -z "$listener" ]; then
            [ "$state" = active ] && warn "Dienst läuft, aber nichts hört auf Port $PORT (startet gerade neu?)"
        elif [ -n "$SVC_PID" ] && [[ "$listener" =~ pid=${SVC_PID}[,\)] ]]; then
            ok "Port $PORT: Dashboard"
        elif [[ "$listener" == *"pid="* ]]; then
            other="$(grep -o 'users:(("[^"]*",pid=[0-9]*' <<<"$listener" | head -1)"
            bad "Port $PORT gehört einem anderen Prozess (${other#users:((}) – von Hand gestartetes ./start.sh?"
            hint "beenden: sudo kill ${other##*pid=}, dann sudo systemctl restart $SERVICE"
        elif [ "$state" != active ]; then
            bad "Port $PORT ist belegt, obwohl der Dienst nicht läuft – von Hand gestartetes ./start.sh?"
            hint "welcher Prozess: sudo ss -ltnp 'sport = :$PORT'"
        else
            ok "Port $PORT belegt  ${D}(welcher Prozess: nur mit sudo zu sehen)${N}"
        fi
    fi

    t="$(systemctl is-active pi-dashboard-update.timer 2>/dev/null)"
    if [ "$t" = active ]; then ok "Auto-Update-Timer aktiv"
    else warn "Auto-Update-Timer ist aus – Updates kommen nicht mehr an"; fix "Timer einschalten" systemctl enable --now pi-dashboard-update.timer
    fi

    if command -v loginctl >/dev/null; then
        if [ "$(loginctl show-user "$OWNER" -p Linger --value 2>/dev/null)" = yes ]; then
            ok "Lingering an (Aufgaben überleben Neustarts des Dashboards)"
        else
            warn "Lingering aus – Aufgaben und Terminal-Sitzungen enden mit dem Dashboard"
            fix "Lingering für $OWNER einschalten" loginctl enable-linger "$OWNER"
        fi
    fi

    # Crashes: restart loop, killed for lack of memory, settings lost
    nres="$(systemctl show -p NRestarts --value "$SERVICE" 2>/dev/null)"
    if [ "${nres:-0}" -ge 3 ]; then
        bad "$SERVICE ist schon ${nres}-mal abgestürzt und wurde von systemd neu gestartet (Startschleife?)"
        hint "Ursache: sudo journalctl -u $SERVICE -p warning -n 40 --no-pager"
    fi
    if can_journal; then
        kills="$(journalctl -k --since -7d --no-pager -o cat 2>/dev/null | grep -E 'Out of memory: Killed process|oom-kill:' || true)"
        if [ "$(systemctl show -p Result --value "$SERVICE" 2>/dev/null)" = oom-kill ] \
           || grep -qE '\((uvicorn|python3?)\)|raspberry-dashboard' <<<"$kills"; then
            bad "Das Dashboard wurde in den letzten 7 Tagen wegen Speichermangel beendet (OOM-Kill)"
            hint "Speicher frei machen (Container begrenzen: mem_limit), Swap vergrößern: sudo dphys-swapfile / zram"
        elif [ -n "$kills" ]; then
            victims="$(grep -oE 'Killed process [0-9]+ \([^)]+\)' <<<"$kills" | sed -E 's/.*\((.*)\)/\1/' | sort | uniq -c | sort -rn | head -3 | awk '{printf "%s%s (%sx)", (NR > 1 ? ", " : ""), $2, $1}')"
            warn "Speichermangel in den letzten 7 Tagen – der Kernel hat Prozesse beendet: ${victims:-siehe Kernel-Log}"
            hint "sudo journalctl -k --since -7d | grep -i 'killed process'"
        fi
        restored="$(journalctl -u "$SERVICE" --since -30d --no-pager -o short-iso 2>/dev/null | grep 'settings.json unreadable' | tail -1)"
        if [ -n "$restored" ]; then
            warn "settings.json war kaputt (Stromausfall, volles Laufwerk?) – Einstellungen wurden aus dem Backup geholt oder leer gestartet"
            hint "${restored:0:180}"
            hint "die kaputte Datei liegt zur Ansicht daneben: ls -l $DIR/settings.json*"
        fi
    else
        info "Abstürze und Speichermangel nicht geprüft (Journal nur mit sudo lesbar)"
    fi
fi

# =============================================================================================
section "Installation"
# =============================================================================================
if [ -d "$SRC/.git" ] && [ "$(stat -c %U "$SRC")" = root ]; then
    ok "Root-Kopie $SRC vorhanden"
else
    bad "Root-Kopie $SRC fehlt oder gehört nicht root – Updates, Firewall und Paketverwaltung gehen nicht"
    fix_install "Installation erneut ausführen"
fi
if [ -e /etc/sudoers.d/pi-dashboard-restart ]; then
    ok "sudo-Regeln vorhanden"
else
    bad "sudo-Regeln fehlen – Neustart, Updates, Firewall aus dem Dashboard gehen nicht"
    fix_install "Installation erneut ausführen"
fi

venv_rc=0
as_owner "$DIR/venv/bin/python" -c 'import fastapi, uvicorn' >/dev/null 2>&1 || venv_rc=$?
case "$venv_rc" in
    0)  ok "Python-venv funktioniert" ;;
    99) info "venv nicht geprüft (nur als $OWNER oder root)" ;;
    *)  bad "Python-venv fehlt oder ist kaputt (z. B. vom Mac kopiert, Python-Update)"
        fix_install "venv neu anlegen (über install.sh)" ;;
esac

# Files of someone else in the dashboard folder - typically root after "sudo git pull" or "sudo ./start.sh"
# Summary per owner and top folder: "root  .git (12)", "999  recordings/pg (2318)"
foreign="$(find "$DIR" -xdev ! -user "$OWNER" -printf '%u\t%P\n' 2>/dev/null \
           | awk -F'\t' '{ n = split($2, p, "/"); top = (n > 2 ? p[1] "/" p[2] : $2); c[$1 "\t" top]++ } END { for (k in c) print c[k] "\t" k }' \
           | sort -rn)"
if [ -n "$foreign" ]; then
    n_root="$(awk -F'\t' '$2 == "root" { s += $1 } END { print s + 0 }' <<<"$foreign")"
    n_other="$(awk -F'\t' '$2 != "root" { s += $1 } END { print s + 0 }' <<<"$foreign")"
    if [ "$n_root" -gt 0 ]; then
        bad "$n_root Datei(en) im Dashboard-Ordner gehören root (nach \"sudo git pull\" oder \"sudo ./start.sh\"?) – Updates und Speichern scheitern"
        awk -F'\t' '$2 == "root" { printf "%s  (%s)\n", $3, $1 }' <<<"$foreign" | head -15 | while read -r l; do hint "$l"; done
        # Only root's files (other owners are left alone), no symlinks followed (-h), no hard links
        # (could point at a file outside) - only plain entries in the folder
        fix "Besitzer dieser root-Dateien auf $OWNER setzen" find "$DIR" -xdev \( -type d -o -links 1 \) -user root -exec chown -h "$OWNER:$OGROUP" {} +
    fi
    if [ "$n_other" -gt 0 ]; then
        # Usually data of a container (postgres 999, www-data 33 ...): changing it would lock the container out
        warn "$n_other Datei(en) im Dashboard-Ordner gehören anderen Benutzern – oft Daten eines Containers, werden nicht automatisch geändert:"
        awk -F'\t' '$2 != "root" { printf "%s  (%s: %s)\n", $3, $2, $1 }' <<<"$foreign" | head -15 | while read -r l; do hint "$l"; done
        hint "nur wenn sie wirklich dem Dashboard gehören: sudo chown -R $OWNER:$OGROUP <ordner>"
    fi
    [ "$(wc -l <<<"$foreign")" -le 15 ] || hint "… $(( $(wc -l <<<"$foreign") - 15 )) weitere Ordner: sudo find $DIR -xdev ! -user $OWNER"
else
    ok "alle Dateien gehören $OWNER"
fi

if [ -f "$DIR/agent.json" ]; then
    info "Agent-Modus an – kein eigener Login nötig"
elif [ -f "$DIR/auth.json" ]; then
    ok "Login eingerichtet"
else
    bad "Kein Login gesetzt – das Dashboard bleibt gesperrt"
    hint "$DIR/set-password.sh   (fragt nach Benutzer und Passwort, interaktiv)"
fi

# =============================================================================================
section "Updates"
# =============================================================================================
CHANNEL="$(tr -d '[:space:]' < "$DIR/.update-channel" 2>/dev/null || true)"
[ "$CHANNEL" = stable ] || CHANNEL=main
# The service must run from the folder the updater updates (e.g. after cloning a second time)
if [ -n "$HAS_SYSTEMD" ]; then
    wd="$(systemctl show -p WorkingDirectory --value "$SERVICE" 2>/dev/null)"
    if [ -n "$wd" ] && [ "$wd" != "$DIR" ]; then
        bad "Der Dienst läuft aus $wd, Updates gehen aber nach $DIR"
        fix_install "Dienst auf $DIR umstellen (install.sh)"
    fi
fi
if [ "$SELF" != "$SRC" ] && [ "$SELF" != "$DIR" ] && [ -f "$SELF/app.py" ]; then
    warn "doctor.sh läuft aus $SELF – installiert ist aber $DIR (zweiter Clone?)"
fi

local_tag=""
gerr="$(as_owner git -C "$DIR" rev-parse HEAD 2>&1 >/dev/null)"; grc=$?
if [ "$grc" = 99 ]; then
    info "Git nicht geprüft (nur als $OWNER oder root)"
elif [ "$grc" != 0 ]; then
    bad "Git-Arbeitskopie nicht nutzbar: $(tail -1 <<<"$gerr" | cut -c1-160)"
    grep -q 'dubious ownership' <<<"$gerr" && hint "Ordner gehört nicht $OWNER – siehe Dateibesitzer oben"
else
    local_tag="$(as_owner git -C "$DIR" describe --tags --abbrev=0 2>/dev/null || true)"
    if ! getent hosts github.com >/dev/null 2>&1; then
        bad "github.com lässt sich nicht auflösen (DNS) – keine Updates"
        hint "cat /etc/resolv.conf; resolvectl status"
    else
        rerr="$(as_owner env GIT_TERMINAL_PROMPT=0 timeout 30 git -C "$DIR" ls-remote --tags --refs origin 'v*' 2>&1)"; rrc=$?
        if [ "$rrc" = 0 ]; then
            newest="$(grep -oE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' <<<"$rerr" | sed 's|refs/tags/||' | sort -V | tail -1)"
            ok "GitHub erreichbar (Kanal $CHANNEL${local_tag:+, installiert $local_tag})"
            if [ "$CHANNEL" = stable ] && [ -n "$newest" ] && [ -n "$local_tag" ] \
               && [ "$(printf '%s\n%s\n' "$local_tag" "$newest" | sort -V | tail -1)" != "$local_tag" ]; then
                info "neuerer Release $newest auf GitHub – kommt mit dem nächsten Lauf des Update-Timers (jede Minute)"
            fi
        elif grep -qiE 'authentication failed|could not read username|terminal prompts disabled|403|repository not found' <<<"$rerr"; then
            bad "GitHub lehnt den Zugriff ab – Token abgelaufen oder ohne Rechte (privates Repo)"
            hint "neuen Token hinterlegen: $DIR/set-token.sh"
        else
            warn "GitHub nicht erreichbar: $(tail -1 <<<"$rerr" | cut -c1-160)"
        fi
    fi
fi

if [ -n "$HAS_SYSTEMD" ]; then
    # Update run that hangs (pip, git over a dead connection)
    upid="$(unit_pid pi-dashboard-update.service || true)"
    if [ -n "$upid" ] && older_than "$upid" 1800; then
        warn "Der Update-Lauf hängt seit $(fmt_age "$(ps -o etimes= -p "$upid" | tr -d ' ')")"
        fix_risky "Update-Lauf abbrechen (der Timer startet ihn neu; mitten in git/pip abgebrochen kann der nächste Lauf nötig sein)" systemctl stop pi-dashboard-update.service
    fi
    if can_journal; then
        ulog="$(journalctl -u pi-dashboard-update --since -7d --no-pager -o cat 2>/dev/null)"
        unsigned="$(grep 'is not signed by a trusted key' <<<"$ulog" | tail -1)"
        if [ -n "$unsigned" ]; then
            bad "Ein Release wird nicht installiert, weil er nicht mit einem vertrauten Schlüssel signiert ist – der Server bleibt auf ${local_tag:-der alten Version}"
            hint "${unsigned#⚠️  }"
            hint "vertraute Schlüssel: cat $STATE/allowed_signers   (eigener Fork mit unsignierten Releases: sudo touch $STATE/allowed_signers.off)"
        fi
        res="$(systemctl show -p Result --value pi-dashboard-update.service 2>/dev/null)"
        if [ -n "$res" ] && [ "$res" != success ]; then
            bad "Der letzte Update-Lauf ist fehlgeschlagen ($res)"
            journalctl -u pi-dashboard-update -n 6 --no-pager -o cat 2>/dev/null | sed "s/^/      ${D}│${N} /"
        fi
    fi
fi

# apt/dpkg: an interrupted dpkg blocks every installation; a long-running apt blocks system updates
if command -v dpkg >/dev/null; then
    # unattended-upgrade-shutdown waits permanently and holds nothing - not counted
    busy="$(ps -eo pid=,etimes=,comm=,args= 2>/dev/null | awk '$3 ~ /^(apt|apt-get|aptitude|dpkg|unattended-upgr|packagekitd)$/ && $0 !~ /shutdown/ { print $1, $2, $3 }' | sort -k2 -rn | head -1)"
    # Pending entries in dpkg/updates are normal while apt runs - interrupted only when nothing runs
    if [ -z "$busy" ] && [ -n "$(ls -A /var/lib/dpkg/updates 2>/dev/null)" ]; then
        bad "dpkg wurde mitten in einer Installation unterbrochen – apt verweigert jede weitere Installation"
        fix "dpkg-Konfiguration abschließen" dpkg --configure -a
    fi
    if [ -n "$busy" ]; then
        read -r bpid bage bcmd <<<"$busy"
        if [ "$bage" -gt 1800 ]; then
            warn "$bcmd (PID $bpid) läuft seit $(fmt_age "$bage") – Paket-Installationen im Dashboard warten so lange"
            hint "was es tut: sudo tail -f /var/log/apt/term.log /var/log/unattended-upgrades/unattended-upgrades-dpkg.log"
        else
            info "$bcmd läuft gerade (seit $(fmt_age "$bage")) – Paket-Installationen warten darauf"
        fi
    fi
fi

# =============================================================================================
section "System & Hardware"
# =============================================================================================
pct="$(df -P "$DIR" 2>/dev/null | awk 'NR == 2 { gsub("%", "", $5); print $5 }')"
if [ -n "$pct" ]; then
    if [ "$pct" -ge 95 ]; then bad "Laufwerk zu ${pct}% voll – Einstellungen und Verlauf können nicht mehr gespeichert werden"
        hint "Platz schaffen: Einstellungen → Speicher aufräumen, docker system prune, sudo journalctl --vacuum-time=14d"
    elif [ "$pct" -ge 90 ]; then warn "Laufwerk zu ${pct}% voll"
    else ok "Laufwerk zu ${pct}% belegt"
    fi
fi
if command -v timedatectl >/dev/null; then
    if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then
        ok "Uhrzeit per NTP synchronisiert"
    else
        warn "Uhrzeit nicht synchronisiert – Anmeldung (2FA), Zertifikate und Zeitstempel können falsch sein"
        fix "NTP einschalten" timedatectl set-ntp true
    fi
fi


# Read-only file system: the kernel switches to it after errors of the SD card / SSD - every save fails
declare -A SEEN_MNT=()
for pth in / "$DIR" /var/lib/docker; do
    [ -e "$pth" ] || continue
    mnt="$(findmnt -no TARGET -T "$pth" 2>/dev/null)"; [ -n "$mnt" ] || continue
    [ -n "${SEEN_MNT[$mnt]:-}" ] && continue; SEEN_MNT[$mnt]=1
    if findmnt -no OPTIONS -T "$pth" | tr ',' '\n' | grep -qx ro; then
        bad "Dateisystem $mnt ist nur lesbar (ro) – meist nach Fehlern der SD-Karte oder SSD; nichts lässt sich speichern"
        hint "Daten sichern, Karte/SSD prüfen oder tauschen; Ursache: sudo journalctl -k -b -p err"
    fi
    # Free inodes: "No space left on device" although df shows space (many small files, Docker layers)
    iuse="$(df -iP "$pth" 2>/dev/null | awk 'NR == 2 { gsub("%", "", $5); print $5 }')"
    if [[ "$iuse" =~ ^[0-9]+$ ]] && [ "$iuse" -ge 90 ]; then
        bad "Auf $mnt sind ${iuse}% der Inodes belegt – \"No space left on device\" trotz freiem Platz"
        hint "größte Verursacher: sudo du --inodes -x $mnt 2>/dev/null | sort -n | tail; bei Docker: docker system prune"
    fi
done
if can_journal; then
    ioerr="$(journalctl -k -b -p err --no-pager -o cat 2>/dev/null | grep -iE 'I/O error|EXT4-fs error|mmc[0-9].*(error|timeout)|blk_update_request|nvme.*(timeout|reset)|Buffer I/O' || true)"
    if [ -n "$ioerr" ]; then
        bad "$(wc -l <<<"$ioerr" | tr -d ' ') Lese-/Schreibfehler des Laufwerks seit dem Start – SD-Karte/SSD stirbt womöglich"
        hint "$(tail -1 <<<"$ioerr" | cut -c1-160)"
        hint "jetzt sichern (Einstellungen → Externe Sicherung), Laufwerk tauschen"
    fi
fi

# Raspberry Pi: under-voltage and throttling explain random crashes and a slow dashboard
if command -v vcgencmd >/dev/null; then
    thr="$(vcgencmd get_throttled 2>/dev/null | sed -n 's/^throttled=//p')"
    if [[ "$thr" =~ ^0x[0-9a-fA-F]+$ ]]; then
        v=$((thr)); now=(); past=()
        [ $((v & 0x1)) -ne 0 ] && now+=("Unterspannung");     [ $((v & 0x10000)) -ne 0 ] && past+=("Unterspannung")
        [ $((v & 0x4)) -ne 0 ] && now+=("gedrosselt");        [ $((v & 0x40000)) -ne 0 ] && past+=("gedrosselt")
        [ $((v & 0x2)) -ne 0 ] && now+=("Takt begrenzt");     [ $((v & 0x20000)) -ne 0 ] && past+=("Takt begrenzt")
        [ $((v & 0x8)) -ne 0 ] && now+=("Temperaturgrenze");  [ $((v & 0x80000)) -ne 0 ] && past+=("Temperaturgrenze")
        if [ ${#now[@]} -gt 0 ]; then
            bad "Raspberry Pi gerade: $(join "${now[@]}") ($thr)"
        elif [ ${#past[@]} -gt 0 ]; then
            warn "Raspberry Pi seit dem Start: $(join "${past[@]}") ($thr)"
        else
            ok "Raspberry Pi: Spannung und Temperatur in Ordnung"
        fi
        if [ $((v & 0x10001)) -ne 0 ]; then
            hint "Netzteil zu schwach oder Kabel zu dünn/lang: offizielles Netzteil (Pi 4: 5,1 V/3 A, Pi 5: 5,1 V/5 A); USB-Platten mit eigener Stromversorgung"
        elif [ $((v & 0x80008)) -ne 0 ]; then
            hint "Kühlung verbessern (Kühlkörper, Lüfter, Gehäuse mit Luftzufuhr)"
        fi
    elif [ -z "$IS_ROOT" ] && ! in_group "$ME" video; then
        info "Spannung des Pi nicht geprüft (vcgencmd braucht die Gruppe video oder sudo)"
    fi
fi

# =============================================================================================
# Group access: is the user in the group, and do the running processes already have it?
#   group_access <label> <what fails without it> <group> [alternative group...]
# =============================================================================================
group_access() {
    local label="$1" why="$2"; shift 2
    local g have="" first=""
    for g in "$@"; do getent group "$g" >/dev/null && { [ -n "$first" ] || first="$g"; in_group "$OWNER" "$g" && { have="$g"; break; }; }; done
    if [ -z "$first" ]; then
        bad "$label: Gruppe $1 gibt es nicht"
        return 1
    fi
    if [ -z "$have" ]; then
        bad "$label: $OWNER ist nicht in der Gruppe $first – $why"
        fix "$OWNER in die Gruppe $first aufnehmen" usermod -aG "$first" "$OWNER"
        need_restart
        [ -n "$USER_PID" ] && need_user_restart
        return 1
    fi
    local gid; gid="$(gid_of "$have")"
    local stale=""
    if [ -n "$SVC_PID" ] && ! pid_has_gid "$SVC_PID" "$gid"; then
        bad "$label: $OWNER ist in der Gruppe $have, aber das Dashboard lief schon vorher – Gruppen gelten erst nach einem Neustart"
        need_restart; stale=1
    fi
    if [ -n "$USER_PID" ] && ! pid_has_gid "$USER_PID" "$gid"; then
        warn "$label: Aufgaben und Web-Terminal (systemd-Benutzerdienst von $OWNER) haben die Gruppe $have noch nicht"
        need_user_restart; stale=1
    fi
    [ -n "$stale" ] || ok "$label: $OWNER ist in der Gruppe $have${SVC_PID:+, der Dienst hat sie}"
}

# =============================================================================================
section "Docker"
# =============================================================================================
if ! command -v docker >/dev/null; then
    info "Docker ist nicht installiert (oder nicht im PATH) – Container-Bereiche bleiben leer"
else
    info "$(command -v docker)$( [[ "$(command -v docker)" == /snap/* ]] && echo " (snap)")"
    sock=/var/run/docker.sock
    snap_docker=""; [[ "$(command -v docker)" == /snap/* ]] && snap_docker=1
    # Where the dashboard's docker calls really go: DOCKER_HOST in the service, or a docker context
    # of the user (~/.docker/config.json - the service has the user's HOME, so the context counts)
    dhost=""
    [ -n "$SVC_PID" ] && dhost="$(tr '\0' '\n' < "/proc/$SVC_PID/environ" 2>/dev/null | sed -n 's/^DOCKER_HOST=//p')"
    dctx="$(as_owner cat "$OWNER_HOME/.docker/config.json" 2>/dev/null | grep -oE '"currentContext"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*"([^"]*)"$/\1/')"
    if [ -n "$dhost" ]; then
        info "das Dashboard nutzt DOCKER_HOST=$dhost"
        [[ "$dhost" == unix://* ]] && sock="${dhost#unix://}"
    elif [ -n "$dctx" ] && [ "$dctx" != default ]; then
        warn "Docker-Kontext \"$dctx\" ist für $OWNER aktiv – das Dashboard spricht mit diesem Docker, nicht mit dem lokalen"
        hint "zurück zum lokalen Docker (als $OWNER): docker context use default"
    fi
    dstate=""
    if [ -n "$HAS_SYSTEMD" ]; then
        for u in docker.service snap.docker.dockerd.service; do
            s="$(systemctl is-active "$u" 2>/dev/null)"; [ "$s" = active ] && { dstate=active; break; }
            [ -z "$dstate" ] && systemctl cat "$u" >/dev/null 2>&1 && dstate="$s ($u)"
        done
    fi
    if docker --version 2>/dev/null | grep -qi podman; then
        info "\"docker\" ist hier podman (podman-docker) – Container-Bereiche funktionieren nur eingeschränkt, Compose und Statistiken können fehlen"
    elif [ -n "$dhost" ] && [[ "$dhost" != unix://* ]]; then
        info "Docker auf $dhost – Socket und Gruppen werden nicht geprüft"
    elif [ ! -S "$sock" ] && [ -S "/run/user/$OUID/docker.sock" ]; then
        bad "Nur rootless Docker von $OWNER läuft – das Dashboard sucht den System-Docker ($sock)"
        hint "Dashboard auf rootless umstellen: sudo systemctl edit $SERVICE  →  [Service] Environment=DOCKER_HOST=unix:///run/user/$OUID/docker.sock"
        hint "danach: sudo systemctl restart $SERVICE   (die Ergänzung übersteht Updates)"
    elif [ ! -S "$sock" ]; then
        bad "Docker-Daemon läuft nicht (kein $sock${dstate:+, Dienst: $dstate})"
        if [ -n "$snap_docker" ]; then fix "Docker starten" snap start --enable docker
        else fix "Docker starten" systemctl enable --now docker
        fi
    else
        sgroup="$(stat -c %G "$sock")"
        ok "Docker-Daemon läuft (Socket-Gruppe: $sgroup)"
        if [ "$sgroup" = root ]; then
            # Docker from snap gives the socket to the docker group only if that group exists at its start
            bad "Der Docker-Socket gehört der Gruppe root – kein Benutzer außer root kommt an Docker$( [ -n "$snap_docker" ] && echo " (snap legt die Gruppe docker nicht selbst an)")"
            getent group docker >/dev/null || fix "Gruppe docker anlegen" groupadd --system docker
            fix "$OWNER in die Gruppe docker aufnehmen" usermod -aG docker "$OWNER"
            ncont=""; [ -n "$IS_ROOT" ] && ncont="$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
            if [ -n "$snap_docker" ]; then fix_risky "Docker neu starten (Socket bekommt die Gruppe docker) – ${ncont:-alle} laufende Container starten dabei neu" snap restart docker
            else fix_risky "Docker neu starten (Socket bekommt die Gruppe docker) – ${ncont:-alle} laufende Container starten dabei neu" sh -c 'systemctl restart docker.socket 2>/dev/null; systemctl restart docker'
            fi
            need_restart; [ -n "$USER_PID" ] && need_user_restart
        else
            group_access "Docker" "die Container-Bereiche bleiben leer (\"Keine Berechtigung für Docker\")" "$sgroup"
        fi
        # Docker from snap is confined: it only reads below /home (and /media, /mnt with the
        # removable-media interface) - compose projects elsewhere fail with "no such file or directory"
        if [ -n "$snap_docker" ]; then
            allowed='^/home/'
            snap connections docker 2>/dev/null | grep -qE '^removable-media .*:removable-media' && allowed='^/(home|media|mnt)/'
            dirs="$( { if [ -n "$IS_ROOT" ]; then docker ps -a --format '{{.Label "com.docker.compose.project.working_dir"}}'; else as_owner docker ps -a --format '{{.Label "com.docker.compose.project.working_dir"}}'; fi; } 2>/dev/null | grep -v '^$' | sort -u | grep -vE "$allowed" || true)"
            if [ -n "$dirs" ]; then
                bad "Docker aus snap darf nur unter /home lesen – diese Compose-Projekte liegen woanders und scheitern (\"no such file or directory\"):"
                while read -r d; do hint "$d"; done <<<"$(head -5 <<<"$dirs")"
                hint "Projekte nach $OWNER_HOME verschieben, oder Docker aus dem apt-Paket statt snap: https://docs.docker.com/engine/install/"
            fi
            case "$OWNER_HOME" in /home/*) ;; *)
                warn "Das Home von $OWNER ($OWNER_HOME) liegt nicht unter /home – Docker aus snap kann Apps aus dem App-Store (~/picore-apps) nicht lesen" ;;
            esac
        fi
        # Final word: does docker answer for the dashboard user with its current group list?
        if [ -n "$IS_ROOT" ]; then
            if err="$(as_owner timeout 15 docker info --format '{{.ServerVersion}}' 2>&1 >/dev/null)"; then
                ok "docker info als $OWNER funktioniert"
            else
                warn "docker info als $OWNER scheitert: $(tail -1 <<<"$err" | cut -c1-160)"
            fi
        fi
    fi
    # Left over from the old hint (before v1.12.2): "usermod -aG docker \$USER" under sudo/systemd added root
    if getent group docker | cut -d: -f4 | tr ',' '\n' | grep -qx root && [ "$OWNER" != root ]; then
        info "root steht in der Gruppe docker (alter Hinweis vor v1.12.2 hat root statt $OWNER eingetragen) – unschädlich"
    fi
fi

# =============================================================================================
section "Weitere Zugriffe"
# =============================================================================================
group_access "System-Logs" "Logs, Log-Auswertung und Dienst-Journale bleiben leer" systemd-journal adm || true

if command -v virsh >/dev/null; then
    if getent group libvirt >/dev/null; then
        group_access "VMs (libvirt)" "die VM-Seite findet keine VMs" libvirt || true
        getent group kvm >/dev/null && { group_access "VMs (kvm)" "VMs starten nicht mit Hardware-Beschleunigung" kvm || true; }
    else
        warn "VMs: virsh da, aber Gruppe libvirt fehlt – Paket libvirt-daemon-system installieren"
    fi
fi

if command -v k3s >/dev/null || command -v kubectl >/dev/null; then
    kube=""
    for c in "$OWNER_HOME/.kube/config" /etc/rancher/k3s/k3s.yaml /etc/rancher/rke2/rke2.yaml; do
        rc=0; as_owner test -r "$c" 2>/dev/null || rc=$?
        [ "$rc" = 0 ] && { kube="$c"; break; }
        [ "$rc" = 99 ] && { kube="?"; break; }
    done
    if [ "$kube" = "?" ]; then
        info "Kubernetes: kubeconfig nicht geprüft (nur als $OWNER oder root)"
    elif [ -n "$kube" ]; then
        ok "Kubernetes: kubeconfig $kube lesbar"
    else
        bad "Kubernetes: keine für $OWNER lesbare kubeconfig – Pods und Workloads fehlen"
        if [ -z "$FIX" ]; then
            hint "als $OWNER: mkdir -p ~/.kube && sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/config && chmod 600 ~/.kube/config"
        elif [ -e /etc/rancher/k3s/k3s.yaml ]; then
            # Written as the user (never follows a link the user planted in its own home), only readable by it
            # shellcheck disable=SC2016  # expanded by the inner sh
            fix "k3s.yaml nach $OWNER_HOME/.kube/config kopieren" sh -c \
                'cat /etc/rancher/k3s/k3s.yaml | setpriv --reuid="$1" --regid="$2" --init-groups env HOME="$3" sh -c "umask 077; mkdir -p ~/.kube && cat > ~/.kube/config.new && { [ ! -e ~/.kube/config ] || mv ~/.kube/config ~/.kube/config.bak; } && mv ~/.kube/config.new ~/.kube/config"' \
                sh "$OUID" "$(id -g "$OWNER")" "$OWNER_HOME"
        fi
    fi
fi

# =============================================================================================
section "Zugang"
# =============================================================================================
# Addresses to check: --ip, this ssh session, everyone logged in over ssh right now
while read -r h; do [[ "$h" =~ $IP_RX ]] && CHECK_IPS+=("$h"); done < <(who 2>/dev/null | grep -oE '\([^)]*\)' | tr -d '()')
mapfile -t CHECK_IPS < <(printf '%s\n' ${CHECK_IPS[@]+"${CHECK_IPS[@]}"} | grep -v '^$' | sort -u)
if [ ${#CHECK_IPS[@]} -eq 0 ]; then
    info "keine Adresse zum Prüfen bekannt – ausgesperrtes Gerät prüfen: ./doctor.sh --ip <adresse>"
else
    info "geprüfte Adressen: $(join "${CHECK_IPS[@]}")   ${D}(weitere: --ip <adresse>)${N}"
fi

if [ ${#CHECK_IPS[@]} -gt 0 ] && command -v fail2ban-client >/dev/null && systemctl is-active -q fail2ban 2>/dev/null; then
    if [ -z "$IS_ROOT" ]; then
        info "fail2ban nicht geprüft (nur mit sudo)"
    else
        hit=""
        for j in $(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' '); do
            banned=" $(fail2ban-client status "$j" 2>/dev/null | sed -n 's/.*Banned IP list:[[:space:]]*//p') "
            for ip in "${CHECK_IPS[@]}"; do
                if [[ "$banned" == *" $ip "* ]]; then
                    bad "fail2ban sperrt $ip (Jail $j)"
                    fix "$ip in $j entsperren" fail2ban-client set "$j" unbanip "$ip"
                    hit=1
                fi
            done
        done
        [ -n "$hit" ] || ok "fail2ban sperrt keine der Adressen"
    fi
fi

# Country blocking (ipset picore-geo4/6, see pi-helper.sh geoblock-*)
if [ ${#CHECK_IPS[@]} -gt 0 ] && command -v ipset >/dev/null && [ -f "$STATE/geoblock.ipset" ]; then
    if [ -z "$IS_ROOT" ]; then
        info "Ländersperre nicht geprüft (nur mit sudo)"
    else
        hit=""
        for ip in "${CHECK_IPS[@]}"; do
            set_name=picore-geo4; [[ "$ip" == *:* ]] && set_name=picore-geo6
            if ipset test "$set_name" "$ip" >/dev/null 2>&1; then
                bad "$ip liegt in einem Land, das die Ländersperre blockiert"
                fix_risky "Ländersperre aufheben (danach im Dashboard unter Firewall die Länder anpassen – sonst setzt das nächste GeoIP-Update sie wieder)" "$SRC/pi-helper.sh" geoblock-clear
                hit=1
            fi
        done
        [ -n "$hit" ] || ok "Ländersperre trifft keine der Adressen"
    fi
fi

if [ -f "$DIR/agent.json" ]; then
    # Agent refused requests with the right token from an address not on its list (uvicorn's
    # access log: token checked first -> 401, address second -> 403): the main dashboard's address changed?
    if can_journal; then
        refused="$(journalctl -u "$SERVICE" --since -1d --no-pager -o cat 2>/dev/null \
                   | sed -nE 's/^INFO: +([0-9a-fA-F.:]+):[0-9]+ - ".*" 403 .*/\1/p' | sort | uniq -c | sort -rn | head -5)"
        shown=""
        while read -r n ip; do
            [ -n "$ip" ] || continue
            # Addresses that are allowed got their 403 from somewhere else
            as_owner "$DIR/venv/bin/python" -c 'import sys; sys.path.insert(0, sys.argv[1]); import agent; sys.exit(0 if agent.allowed(sys.argv[2]) else 1)' "$DIR/backend" "$ip" >/dev/null 2>&1 && continue
            [ -n "$shown" ] || warn "Agent hat in den letzten 24 h Anfragen mit gültigem Token abgelehnt – die Adresse ist nicht freigegeben (neue IP des Haupt-Dashboards?):"
            shown=1
            hint "$ip  (${n}x)   →  falls das das Haupt-Dashboard ist: $DIR/agent.sh allow $ip"
        done <<<"$refused"
        [ -z "$shown" ] || hint "freigegeben sind: $(as_owner "$DIR/venv/bin/python" "$DIR/backend/agent.py" allowed 2>/dev/null || echo '?')"
    fi
    # Agent behind an active firewall: the main dashboard must reach port 8000
    if [ -n "$IS_ROOT" ] && command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
        if ufw status 2>/dev/null | grep -qE "(^| )$PORT(/tcp)?( |$)"; then
            ok "Agent: ufw lässt Port $PORT zu"
        else
            bad "Agent: ufw ist aktiv und Port $PORT ist zu – das Haupt-Dashboard erreicht den Agent nicht"
            hint "sudo ufw allow from <IP des Haupt-Dashboards> to any port $PORT proto tcp"
        fi
    fi
fi

# =============================================================================================
echo
if [ "$PROBLEMS" -eq 0 ]; then
    echo "${G}${B}Keine bekannten Probleme gefunden.${N}"
    exit 0
fi
echo "${B}$PROBLEMS Problem(e) gefunden.${N}"
if [ -z "$FIX" ]; then
    if [ ${#FIX_CMD[@]} -gt 0 ] || [ -n "$NEED_RESTART$NEED_USER_RESTART" ]; then
        echo "Automatisch beheben: ${B}$DIR/doctor.sh --fix${N}  (fragt vor jedem Schritt)"
    fi
    exit 1
fi

# --- Repairs ---------------------------------------------------------------------------------
# ask "<step>" [risky]: --yes answers harmless steps; risky ones always need a person at the terminal
ask() {
    if [ -n "$YES" ] && [ -z "${2:-}" ]; then echo "  → $1"; return 0; fi
    if ! [ -t 0 ]; then
        if [ -n "${2:-}" ]; then echo "  · übersprungen (unterbricht Laufendes – nur mit Rückfrage im Terminal): $1"
        else echo "  · übersprungen (nicht interaktiv, --yes fehlt): $1"; fi
        return 1
    fi
    local a; read -rp "  → $1? [j/N] " a
    [[ "$a" =~ ^[jJyY] ]]
}
# Running tasks (picore-task-*) and terminal sessions in the user's systemd manager
user_units() {
    { systemctl --user -M "$OWNER@" list-units --plain --no-legend --state=active 'picore-*' 2>/dev/null | awk '{print $1}'
      find "/sys/fs/cgroup/user.slice/user-$OUID.slice/user@$OUID.service" -maxdepth 2 -type d -name 'picore-*' -printf '%f\n' 2>/dev/null
    } | sort -u
}
section "Beheben"
FAILED=0
declare -A SEEN=()
for i in "${!FIX_CMD[@]}"; do
    cmd="${FIX_CMD[$i]}"
    [ -n "${SEEN[$cmd]:-}" ] && continue   # e.g. install.sh offered by several checks
    SEEN[$cmd]=1
    ask "${FIX_DESC[$i]}" "${FIX_RISKY[$i]}" || continue
    if eval "$cmd"; then echo "    ${G}✔${N} erledigt"; else echo "    ${R}✖${N} fehlgeschlagen"; FAILED=$((FAILED + 1)); fi
done
if [ -n "$NEED_USER_RESTART" ]; then
    if [ -n "$IN_USER_MANAGER" ]; then
        echo "  ${Y}⚠${N} Der Benutzerdienst von $OWNER wird nicht neu gestartet – dieses Terminal läuft darin."
        echo "      Später über SSH: sudo systemctl restart user@$OUID.service"
    else
        units="$(user_units)"
        tasks="$(grep '^picore-task-' <<<"$units" || true)"
        if [ -n "$tasks" ]; then
            # A task stopped halfway (compose update, system update) is worse than a missing group
            echo "  ${Y}⚠${N} Benutzerdienst von $OWNER nicht neu gestartet – es laufen noch Aufgaben:"
            while read -r t; do echo "      ${D}→${N} ${t%.service}"; done <<<"$tasks"
            echo "      Wenn sie fertig sind (Aufgaben-Leiste im Dashboard): $DIR/doctor.sh --fix"
        else
            what="Aufgaben und Web-Terminal bekommen die neuen Gruppen"
            grep -q '^picore-term' <<<"$units" && what="$what – offene Terminal-Sitzungen enden"
            if ask "systemd-Benutzerdienst von $OWNER neu starten ($what)" risky; then
                if systemctl restart "user@$OUID.service"; then echo "    ${G}✔${N} erledigt"; else echo "    ${R}✖${N} fehlgeschlagen"; FAILED=$((FAILED + 1)); fi
            fi
        fi
    fi
fi
if [ -n "$NEED_RESTART" ] && ask "Dashboard neu starten, damit es die neuen Gruppen bekommt"; then
    if systemctl restart "$SERVICE"; then echo "    ${G}✔${N} erledigt"; else echo "    ${R}✖${N} fehlgeschlagen"; FAILED=$((FAILED + 1)); fi
fi

echo
if [ "$FAILED" -gt 0 ]; then
    echo "${R}$FAILED Schritt(e) fehlgeschlagen.${N} Zur Kontrolle: $DIR/doctor.sh"
    exit 1
fi
echo "Fertig. Zur Kontrolle noch einmal prüfen: $DIR/doctor.sh"
