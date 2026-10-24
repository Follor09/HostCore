"""
Hardening check (System -> Sicherheit -> Härtung): the security basics of this server, each with a
rating and the command that fixes it.

- SSH: root login, password login (with or without keys), empty passwords, fail2ban
- Firewall & network: ufw on, default policy for incoming traffic, services open to the LAN,
  Docker ports that bypass ufw
- Accounts & rights: sudo without password for the dashboard user, automatic login, default
  user "pi"
- Updates: pending security updates, reboot pending, automatic security updates

Read-only and without root (sshd_config and the drop-ins are world-readable). Runs on request
and once a day; failed points with weight "critical" reach the status summary.
"""

import glob
import logging
import os
import pwd
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

logger = logging.getLogger("pi_dashboard.hardening")

RESULT_KEY = "hardening"
CHECK_INTERVAL = 24 * 3600
SSHD_CONFIG = "/etc/ssh/sshd_config"
GROUPS = ["SSH", "Firewall & Netzwerk", "Konten & Rechte", "Updates"]
WEIGHT = {"critical": 3, "warning": 2, "info": 0}
_lock = threading.Lock()
_running = {"active": False}


def _r(cid: str, group: str, title: str, state: str, detail: str = "", fix: str = "", weight: str = "warning") -> Dict[str, Any]:
    """state: ok / fail / info / skip; weight says how much a fail counts (critical, warning, info)."""
    return {"id": cid, "group": group, "title": title, "state": state, "detail": detail, "fix": fix, "weight": weight}


def _run(cmd: List[str], timeout: float = 10) -> tuple:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _active(unit: str) -> bool:
    return shutil.which("systemctl") is not None and _run(["systemctl", "is-active", unit])[1] == "active"


# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------
def sshd_options(path: str = SSHD_CONFIG, seen: Optional[Dict[str, str]] = None, depth: int = 0) -> Dict[str, str]:
    """Global options as sshd reads them: the first value of a keyword wins, Include is read in
    place, everything after the first Match block only applies to some connections."""
    opts = {} if seen is None else seen
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return opts
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key, value = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if key == "match":
            opts["__match__"] = "1"
            break
        if key == "include" and depth < 4:
            for pattern in value.split():
                if not pattern.startswith("/"):
                    pattern = os.path.join("/etc/ssh", pattern)
                for inc in sorted(glob.glob(pattern)):
                    sshd_options(inc, opts, depth + 1)
                    if "__match__" in opts:
                        return opts
            continue
        opts.setdefault(key, value.lower())
    return opts


def _has_keys() -> bool:
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, AttributeError):
        home = os.path.expanduser("~")
    try:
        with open(os.path.join(home, ".ssh", "authorized_keys")) as f:
            return any(line.strip() and not line.startswith("#") for line in f)
    except OSError:
        return False


def _ssh_checks() -> List[Dict[str, Any]]:
    g = "SSH"
    if not os.path.exists(SSHD_CONFIG):
        return [_r("ssh", g, "SSH-Server", "skip", "Kein SSH-Server installiert.")]
    o = sshd_options()
    out = []
    root = o.get("permitrootlogin", "prohibit-password")
    out.append(_r("ssh-root", g, "Root-Anmeldung", "fail" if root == "yes" else "ok",
                  "root darf sich mit Passwort anmelden." if root == "yes" else
                  ("nur mit Schlüssel" if root in ("prohibit-password", "without-password") else "gesperrt" if root == "no" else root),
                  "echo 'PermitRootLogin no' | sudo tee /etc/ssh/sshd_config.d/10-picore-root.conf && sudo systemctl reload ssh", "critical"))
    empty = o.get("permitemptypasswords", "no")
    if empty == "yes":
        out.append(_r("ssh-empty", g, "Leere Passwörter", "fail", "Konten ohne Passwort dürfen sich per SSH anmelden.",
                      "echo 'PermitEmptyPasswords no' | sudo tee /etc/ssh/sshd_config.d/10-picore-empty.conf && sudo systemctl reload ssh", "critical"))
    pw = o.get("passwordauthentication", "yes") != "no" or o.get("kbdinteractiveauthentication", "no") == "yes"
    keys = _has_keys()
    if not pw:
        out.append(_r("ssh-password", g, "Passwort-Anmeldung", "ok", "aus – nur SSH-Schlüssel"))
    elif keys:
        out.append(_r("ssh-password", g, "Passwort-Anmeldung", "fail",
                      "Erlaubt, obwohl für diesen Benutzer ein SSH-Schlüssel hinterlegt ist – Passwörter lassen sich erraten.",
                      "printf 'PasswordAuthentication no\\nKbdInteractiveAuthentication no\\n' | sudo tee /etc/ssh/sshd_config.d/10-picore-password.conf && sudo systemctl reload ssh"))
    else:
        out.append(_r("ssh-password", g, "Passwort-Anmeldung", "fail",
                      "Erlaubt, und es ist noch kein SSH-Schlüssel hinterlegt. Erst einen Schlüssel einrichten (ssh-copy-id vom eigenen Rechner), dann Passwörter abschalten.",
                      "ssh-copy-id <benutzer>@<pi>", "info"))
    if pw:
        f2b = _active("fail2ban")
        out.append(_r("fail2ban", g, "Schutz vor Passwort-Raten (fail2ban)", "ok" if f2b else "fail",
                      "aktiv" if f2b else "Nicht aktiv – bei erlaubter Passwort-Anmeldung sperrt fail2ban IPs nach Fehlversuchen.",
                      "" if f2b else "sudo apt install fail2ban"))
    if o.get("x11forwarding") == "yes":
        out.append(_r("ssh-x11", g, "X11-Weiterleitung", "fail", "an – auf einem Server meist unnötig.",
                      "echo 'X11Forwarding no' | sudo tee /etc/ssh/sshd_config.d/10-picore-x11.conf && sudo systemctl reload ssh", "info"))
    return out


# ---------------------------------------------------------------------------
# Firewall & network
# ---------------------------------------------------------------------------
def _network_checks() -> List[Dict[str, Any]]:
    g = "Firewall & Netzwerk"
    import netscan
    import security
    out = []
    fw = security.firewall_status()
    if not fw.get("installed"):
        out.append(_r("ufw", g, "Firewall", "fail", "ufw ist nicht installiert – jeder lauschende Dienst ist im Netz erreichbar.", "sudo apt install ufw"))
    elif fw.get("error"):
        out.append(_r("ufw", g, "Firewall", "skip", f"Status nicht lesbar: {fw['error'][:140]}"))
    elif not fw.get("active"):
        out.append(_r("ufw", g, "Firewall", "fail", "ufw ist ausgeschaltet.", "System-Werkzeuge → Firewall → einschalten (SSH-Regel vorher prüfen)"))
    else:
        incoming = (fw.get("defaults") or {}).get("incoming", "")
        ok = incoming in ("deny", "reject")
        out.append(_r("ufw", g, "Firewall", "ok" if ok else "fail",
                      f"aktiv, eingehend: {incoming}" if ok else f"aktiv, aber eingehender Verkehr ist standardmäßig erlaubt ({incoming or '?'}).",
                      "" if ok else "sudo ufw default deny incoming"))
    try:
        pr = netscan.port_reachability()
    except Exception as e:
        logger.warning(f"Hardening (ports): {e}")
        return out
    ssh, dash = security.ssh_port(), security.DASHBOARD_PORT
    exposed = sorted({(p["port"], p["proto"], p.get("process") or p.get("service") or "") for p in pr["ports"]
                      if p["lan"] == "open" and p["port"] not in (ssh, dash) and not p.get("docker")})
    if exposed:
        names = ", ".join(f"{port}/{proto.lower()}{f' ({name})' if name else ''}" for port, proto, name in exposed[:10])
        out.append(_r("exposed", g, "Im Netz erreichbare Dienste", "fail",
                      f"{len(exposed)} weitere(r) Port(s) aus dem LAN erreichbar: {names}{' …' if len(exposed) > 10 else ''}. Nur öffnen, was gebraucht wird.",
                      "System-Werkzeuge → Port-Check", "info"))
    else:
        out.append(_r("exposed", g, "Im Netz erreichbare Dienste", "ok", "nur SSH und Dashboard (und Docker, siehe unten)"))
    docker = sorted({p["port"] for p in pr["ports"] if p.get("docker") and p["lan"] == "open"})
    if docker and pr["firewall"] == "active":
        out.append(_r("docker-ufw", g, "Docker umgeht die Firewall", "fail",
                      f"Veröffentlichte Container-Ports ({', '.join(map(str, docker[:12]))}) sind trotz ufw aus dem Netz erreichbar – Docker trägt seine Regeln vor ufw ein.",
                      "Ports in der Compose-Datei an 127.0.0.1 binden (\"127.0.0.1:8080:80\") oder über einen Reverse-Proxy freigeben"))
    return out


# ---------------------------------------------------------------------------
# Accounts & rights
# ---------------------------------------------------------------------------
def _account_checks() -> List[Dict[str, Any]]:
    g = "Konten & Rechte"
    out = []
    if shutil.which("sudo"):
        rc, _ = _run(["sudo", "-n", "true"], timeout=5)
        nopw = glob.glob("/etc/sudoers.d/*nopasswd*")
        out.append(_r("sudo", g, "sudo ohne Passwort", "fail" if rc == 0 else "ok",
                      "Der Dashboard-Benutzer wird ohne Passwort root – wer das Dashboard oder diesen Benutzer übernimmt, hat den ganzen Server."
                      if rc == 0 else "sudo verlangt ein Passwort (außer für die eng begrenzten Dashboard-Befehle)",
                      (f"sudo rm {nopw[0]}  # vorher sicherstellen, dass das eigene Passwort bekannt ist" if nopw else "sudo visudo  # NOPASSWD-Zeile für diesen Benutzer entfernen")
                      if rc == 0 else "", "critical"))
    autologin = []
    try:
        with open("/etc/lightdm/lightdm.conf") as f:
            if re.search(r"^\s*autologin-user\s*=\s*\S+", f.read(), re.M):
                autologin.append("Desktop (lightdm)")
    except OSError:
        pass
    if glob.glob("/etc/systemd/system/getty@tty1.service.d/*autologin*"):
        autologin.append("Konsole (tty1)")
    out.append(_r("autologin", g, "Automatische Anmeldung", "fail" if autologin else "ok",
                  f"an: {', '.join(autologin)} – wer Bildschirm und Tastatur anschließt, ist angemeldet." if autologin else "aus",
                  "sudo raspi-config nonint do_boot_behaviour B1" if autologin else ""))
    try:
        pwd.getpwnam("pi")
        out.append(_r("user-pi", g, "Standardbenutzer „pi“", "fail", "Das Konto „pi“ existiert – ein bekannter Name ist das erste Ziel beim Passwort-Raten.",
                      "Eigenen Benutzer verwenden und „pi“ sperren: sudo usermod -L pi", "info"))
    except KeyError:
        out.append(_r("user-pi", g, "Standardbenutzer „pi“", "ok", "nicht vorhanden"))
    return out


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
def _update_checks() -> List[Dict[str, Any]]:
    g = "Updates"
    out = []
    import homelab
    up = homelab.get_updates()
    if up.get("supported") and not up.get("error"):
        sec = up.get("security_count") or 0
        out.append(_r("security-updates", g, "Sicherheitsupdates", "fail" if sec else "ok",
                      f"{sec} ausstehend" if sec else "keine ausstehend", "Dashboard → System-Updates → installieren" if sec else ""))
        if up.get("reboot_required"):
            out.append(_r("reboot", g, "Neustart ausstehend", "fail", "Installierte Updates (z.B. Kernel) wirken erst nach einem Neustart.",
                          "sudo reboot"))
    auto = ""
    try:
        with open("/etc/apt/apt.conf.d/20auto-upgrades") as f:
            auto = f.read()
    except OSError:
        pass
    on = bool(re.search(r'Unattended-Upgrade\s+"1"', auto)) and shutil.which("unattended-upgrade") is not None
    out.append(_r("unattended", g, "Automatische Sicherheitsupdates", "ok" if on else "fail",
                  "unattended-upgrades ist aktiv" if on else "Aus – Sicherheitsupdates kommen nur, wenn jemand sie installiert.",
                  "" if on else "sudo apt install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades", "info"))
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run() -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for fn in (_ssh_checks, _network_checks, _account_checks, _update_checks):
        try:
            results.extend(fn())
        except Exception as e:
            logger.warning(f"Hardening {fn.__name__}: {e}")
    settings_store.set(RESULT_KEY, {"checked": int(time.time()), "results": results})
    return overview()


def overview() -> Dict[str, Any]:
    d = settings_store.get(RESULT_KEY) or {}
    results = d.get("results", [])
    rated = [r for r in results if r["state"] in ("ok", "fail") and WEIGHT.get(r["weight"], 0)]
    total = sum(WEIGHT[r["weight"]] for r in rated)
    good = sum(WEIGHT[r["weight"]] for r in rated if r["state"] == "ok")
    return {"checked": d.get("checked", 0), "running": _running["active"], "groups": GROUPS, "results": results,
            "score": round(100 * good / total) if total else None,
            "failed": sum(1 for r in results if r["state"] == "fail" and r["weight"] != "info"),
            "critical": sum(1 for r in results if r["state"] == "fail" and r["weight"] == "critical")}


def alerts() -> List[Dict[str, Any]]:
    return [{"level": "warning", "text": f"Härtung: {r['title']}"} for r in overview()["results"]
            if r["state"] == "fail" and r["weight"] == "critical"]


def _thread() -> None:
    try:
        run()
    except Exception as e:
        logger.warning(f"Hardening check failed: {e}")
    finally:
        _running["active"] = False


def start() -> Dict[str, Any]:
    with _lock:
        if not _running["active"]:
            _running["active"] = True
            threading.Thread(target=_thread, daemon=True).start()
    return overview()


def check_if_due() -> None:
    if time.time() - (settings_store.get(RESULT_KEY) or {}).get("checked", 0) >= CHECK_INTERVAL - 60:
        start()
