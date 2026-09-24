"""
Security panels for the system tools page:

- SSH logins: successful and failed logins from the journal (sshd), grouped per
  connection, with the IPs that fail most and who is logged in right now.
- fail2ban: jails and banned IPs, unban.
- Firewall (ufw): status, rules, add/delete, presets.

Anything that needs root goes through pi-helper.sh from root's own repo copy
(sudo rule written by install.sh). The helper re-checks every argument.
"""

import ast
import ipaddress
import glob
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

HELPER = "/var/lib/pi-dashboard/src/pi-helper.sh"
DASHBOARD_PORT = 8000
SSH_DAYS = 7
SSH_MAX_LINES = 20000

_ssh_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
SSH_CACHE_TTL = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _helper(*args: str, timeout: float = 30) -> str:
    """Run pi-helper.sh as root. Raises PermissionError / RuntimeError with a readable message."""
    if not os.path.exists(HELPER):
        raise PermissionError("Helfer fehlt – install.sh auf dem Pi erneut ausführen.")
    res = subprocess.run(["sudo", "-n", HELPER, *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        err = res.stderr.strip()
        if "password" in err.lower() or "not allowed" in err.lower():
            raise PermissionError("Keine Berechtigung – install.sh auf dem Pi erneut ausführen.")
        raise RuntimeError(err.splitlines()[-1][:200] if err else "Fehlgeschlagen.")
    return res.stdout


def ip_scope(ip: str) -> str:
    """lan / vpn / local / internet - where a login came from."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "internet"
    if addr.is_loopback:
        return "local"
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        return "vpn"  # CGNAT range: NetBird / Tailscale
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "internet"


def _valid_ip(value: str, allow_network: bool = False) -> bool:
    try:
        if allow_network and "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# SSH logins
# ---------------------------------------------------------------------------
_RE_ACCEPTED = re.compile(r"Accepted (\S+) for (\S+) from (\S+) port \d+")
_RE_FAILED = re.compile(r"Failed (\S+) for (?:invalid user )?(\S*) from (\S+) port \d+")
_RE_INVALID = re.compile(r"Invalid user (\S*) from (\S+) port \d+")
_RE_PREAUTH = re.compile(r"(?:Connection closed by|Disconnected from) (?:authenticating|invalid) user (\S*) (\S+) port \d+ \[preauth\]")
_RE_MAXAUTH = re.compile(r"maximum authentication attempts exceeded for (?:invalid user )?(\S*) from (\S+) port \d+")

METHOD_LABEL = {"publickey": "SSH-Schlüssel", "password": "Passwort", "keyboard-interactive/pam": "Passwort"}


def _journal_lines(since: str) -> subprocess.CompletedProcess:
    cmd = ["journalctl", "--no-pager", "-o", "json", "--output-fields=MESSAGE,_PID,SYSLOG_PID",
           "--since", since, "-n", str(SSH_MAX_LINES),
           "SYSLOG_IDENTIFIER=sshd", "SYSLOG_IDENTIFIER=sshd-session"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, errors="replace")


def _parse_ssh(lines: List[str]) -> List[Dict[str, Any]]:
    """Journal JSON lines -> one event per connection (sshd PID)."""
    open_conns: Dict[str, Dict[str, Any]] = {}
    conns: List[Dict[str, Any]] = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE", "")
        if isinstance(msg, list):
            msg = bytes(msg).decode("utf-8", errors="replace")
        pid = str(e.get("_PID") or e.get("SYSLOG_PID") or "")
        ts = int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6
        # PIDs get reused: an entry more than an hour after the first one is a new connection
        c = open_conns.get(pid)
        if c is None or not pid or ts - c["first"] > 3600:
            c = {"first": ts, "ts": ts, "kind": None, "user": "", "ip": "", "method": "", "attempts": 0}
            conns.append(c)
            if pid:
                open_conns[pid] = c

        m = _RE_ACCEPTED.search(msg)
        if m:
            c.update(kind="ok", method=m.group(1), user=m.group(2), ip=m.group(3), ts=ts)
            continue
        m = _RE_FAILED.search(msg)
        if m:
            c["attempts"] += 1
            c.update(method=m.group(1), user=c["user"] or m.group(2), ip=m.group(3), ts=ts)
            c["kind"] = c["kind"] or "fail"
            continue
        for rx in (_RE_INVALID, _RE_PREAUTH, _RE_MAXAUTH):
            m = rx.search(msg)
            if m:
                c.update(user=c["user"] or m.group(1), ip=c["ip"] or m.group(2))
                c["kind"] = c["kind"] or "fail"
                break

    events = []
    for c in conns:
        if not c["kind"] or not c["ip"]:
            continue
        events.append({"ts": int(c["ts"]), "kind": c["kind"], "user": c["user"] or "?", "ip": c["ip"],
                       "scope": ip_scope(c["ip"]), "method": METHOD_LABEL.get(c["method"], c["method"]),
                       "attempts": max(1, c["attempts"])})
    events.sort(key=lambda ev: ev["ts"], reverse=True)
    return events


def _active_sessions() -> List[Dict[str, Any]]:
    sessions = []
    for u in psutil.users():
        host = u.host or ""
        sessions.append({"user": u.name, "terminal": u.terminal or "", "host": host or "lokal",
                         "scope": ip_scope(host) if host else "local", "started": int(u.started)})
    return sessions


def ssh_logins() -> Dict[str, Any]:
    now = time.time()
    if _ssh_cache["data"] and now - _ssh_cache["ts"] < SSH_CACHE_TTL:
        return _ssh_cache["data"]
    if not shutil.which("journalctl"):
        return {"supported": False, "active": _active_sessions()}

    try:
        res = _journal_lines(f"{SSH_DAYS} days ago")
    except subprocess.TimeoutExpired:
        return {"supported": True, "error": "Journal antwortet nicht.", "active": _active_sessions()}
    events = _parse_ssh(res.stdout.splitlines())

    hint = ""
    stderr = res.stderr.lower()
    if not events and ("insufficient permissions" in stderr or "not seeing messages" in stderr):
        user = os.environ.get("USER", "$USER")
        hint = f"Keine Berechtigung für System-Logs: sudo usermod -aG systemd-journal {user} && sudo systemctl restart raspberry-dashboard"

    day_ago = now - 86400
    summary = {
        "ok_24h": sum(1 for e in events if e["kind"] == "ok" and e["ts"] >= day_ago),
        "fail_24h": sum(e["attempts"] for e in events if e["kind"] == "fail" and e["ts"] >= day_ago),
        "ok_7d": sum(1 for e in events if e["kind"] == "ok"),
        "fail_7d": sum(e["attempts"] for e in events if e["kind"] == "fail"),
    }

    per_ip: Dict[str, Dict[str, Any]] = {}
    for e in events:
        if e["kind"] != "fail":
            continue
        p = per_ip.setdefault(e["ip"], {"ip": e["ip"], "scope": e["scope"], "attempts": 0, "users": [], "last": e["ts"]})
        p["attempts"] += e["attempts"]
        if e["user"] not in p["users"] and len(p["users"]) < 5:
            p["users"].append(e["user"])
    top = sorted(per_ip.values(), key=lambda p: p["attempts"], reverse=True)[:10]
    summary["fail_ips_7d"] = len(per_ip)

    data = {"supported": True, "days": SSH_DAYS, "summary": summary, "top_ips": top,
            "events": events[:100], "active": _active_sessions(), "hint": hint,
            "truncated": len(res.stdout.splitlines()) >= SSH_MAX_LINES,
            "fail2ban": fail2ban_status()}
    _ssh_cache.update(ts=now, data=data)
    return data


# ---------------------------------------------------------------------------
# fail2ban
# ---------------------------------------------------------------------------
def _parse_banned(out: str) -> Dict[str, List[str]]:
    """`fail2ban-client banned` prints a Python literal: [{'sshd': ['1.2.3.4']}, ...]"""
    try:
        value = ast.literal_eval(out.strip() or "[]")
    except (ValueError, SyntaxError):
        return {}
    jails: Dict[str, List[str]] = {}
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            for jail, ips in item.items():
                jails[str(jail)] = [str(ip) for ip in (ips or [])]
    return jails


def fail2ban_status() -> Dict[str, Any]:
    if not shutil.which("fail2ban-client") and not os.path.exists("/usr/bin/fail2ban-client"):
        return {"installed": False, "hint": "Nicht installiert. Schutz gegen Passwort-Raten: sudo apt install fail2ban"}
    active = subprocess.run(["systemctl", "is-active", "--quiet", "fail2ban"], timeout=5).returncode == 0
    if not active:
        return {"installed": True, "active": False, "jails": []}
    try:
        jails = _parse_banned(_helper("f2b-banned", timeout=15))
    except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"installed": True, "active": True, "jails": [], "error": str(e)}
    return {"installed": True, "active": True,
            "jails": [{"name": name, "banned": ips} for name, ips in sorted(jails.items())]}


def fail2ban_unban(ip: str) -> None:
    if not _valid_ip(ip):
        raise ValueError("Ungültige IP-Adresse.")
    _helper("f2b-unban", ip)
    _ssh_cache["data"] = None


# ---------------------------------------------------------------------------
# Firewall (ufw)
# ---------------------------------------------------------------------------
_RE_RULE = re.compile(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)(?: (IN|OUT|FWD))?\s{2,}(.+?)(?:\s+#\s*(.*))?$")


def ssh_port() -> int:
    """First Port from sshd_config (+ .d drop-ins); 22 if none is set."""
    for path in ["/etc/ssh/sshd_config", *sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf"))]:
        try:
            with open(path) as f:
                for line in f:
                    m = re.match(r"\s*Port\s+(\d+)", line, re.I)
                    if m:
                        return int(m.group(1))
        except OSError:
            continue
    return 22


_RE_ADDED = re.compile(r"^ufw (allow|deny|reject|limit)\s+(.+?)(?:\s+comment '(.*)')?$")


def parse_ufw(out: str) -> Dict[str, Any]:
    verbose, _, rest = out.partition("---NUMBERED---")
    numbered, _, added = rest.partition("---ADDED---")
    active = bool(re.search(r"^Status:\s*active", verbose, re.M))
    defaults = {}
    m = re.search(r"^Default:\s*(.+)$", verbose, re.M)
    if m:
        for part in m.group(1).split(","):
            pm = re.match(r"\s*(\w+)\s*\((\w+)\)", part)
            if pm:
                defaults[pm.group(2)] = pm.group(1)
    logging_ = ""
    m = re.search(r"^Logging:\s*(.+)$", verbose, re.M)
    if m:
        logging_ = m.group(1).strip()

    rules = []
    for line in numbered.splitlines():
        rm = _RE_RULE.match(line.strip())
        if not rm:
            continue
        num, to, action, direction, source, comment = rm.groups()
        rules.append({"num": int(num), "to": to.strip(), "action": action.lower(),
                      "direction": (direction or "IN").lower(), "from": source.strip(),
                      "comment": (comment or "").strip(), "v6": "(v6)" in to})
    if not active:
        # An inactive ufw lists no rules in `status`; `show added` still has the saved ones
        for line in added.splitlines():
            am = _RE_ADDED.match(line.strip())
            if am:
                rules.append({"num": None, "to": am.group(2), "action": am.group(1), "direction": "in",
                              "from": "", "comment": am.group(3) or "", "v6": False})
    return {"active": active, "defaults": defaults, "logging": logging_, "rules": rules}


def _suggestions(rules: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Presets for what runs on this Pi and has no rule yet (ufw would block it)."""
    text = " ".join(f"{r['to']} {r['from']} {r['comment']}" for r in rules)
    out = []
    if (shutil.which("k3s") or os.path.exists("/etc/rancher/k3s")) and "10.42.0.0/16" not in text:
        out.append({"id": "k3s", "label": "Kubernetes (k3s)", "desc": "API-Port 6443 und die Pod-/Service-Netze 10.42/10.43 – sonst funktioniert k3s nicht mehr."})
    if "wt0" in psutil.net_if_addrs() and "wt0" not in text:
        out.append({"id": "netbird", "label": "NetBird VPN", "desc": "Alles, was über das VPN (wt0) kommt, erlauben."})
    listening = {c.laddr.port for c in _safe_connections() if c.status == psutil.CONN_LISTEN}
    if listening & {80, 443} and not re.search(r"\b(80|443)(/tcp)?\b", text):
        out.append({"id": "web", "label": "Webserver", "desc": "HTTP (80) und HTTPS (443) erlauben."})
    return out


def _safe_connections():
    try:
        return psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return []


def firewall_status() -> Dict[str, Any]:
    if not shutil.which("ufw") and not os.path.exists("/usr/sbin/ufw"):
        return {"installed": False, "hint": "ufw ist nicht installiert: sudo apt install ufw"}
    try:
        status = parse_ufw(_helper("ufw-status"))
    except (PermissionError, RuntimeError) as e:
        return {"installed": True, "error": str(e)}
    status.update(installed=True, ssh_port=ssh_port(), dashboard_port=DASHBOARD_PORT,
                  suggestions=_suggestions(status["rules"]),
                  docker=bool(shutil.which("docker")))
    return status


def _rule_ports(rule: Dict[str, Any]) -> set:
    m = re.match(r"^(\d+)(?:/(tcp|udp))?", rule["to"])
    return {int(m.group(1))} if m else set()


def firewall_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "enable":
        _helper("ufw-enable", str(ssh_port()))
    elif action == "disable":
        _helper("ufw-disable")
    elif action == "rule":
        rule_action = str(params.get("action", ""))
        port = str(params.get("port", "")).strip()
        proto = str(params.get("proto", "tcp"))
        source = str(params.get("from", "") or "any").strip()
        comment = str(params.get("comment", "")).strip()
        if rule_action not in ("allow", "deny", "limit"):
            raise ValueError("Ungültige Aktion.")
        pm = re.fullmatch(r"(\d{1,5})(?::(\d{1,5}))?", port)
        if not pm or not all(1 <= int(p) <= 65535 for p in pm.groups() if p) or (pm.group(2) and int(pm.group(1)) >= int(pm.group(2))):
            raise ValueError("Port muss 1–65535 sein (oder ein Bereich wie 6000:6010).")
        if proto not in ("tcp", "udp", "any"):
            raise ValueError("Ungültiges Protokoll.")
        if pm.group(2) and proto == "any":
            raise ValueError("Portbereiche brauchen TCP oder UDP.")
        if source != "any" and not _valid_ip(source, allow_network=True):
            raise ValueError("Quelle muss „any“, eine IP oder ein Netz wie 192.168.1.0/24 sein.")
        if not re.fullmatch(r"[A-Za-z0-9 ._()-]{0,40}", comment):
            raise ValueError("Kommentar: nur Buchstaben, Zahlen, Leerzeichen und . _ - ( ) (max. 40).")
        if rule_action == "deny" and source == "any" and int(pm.group(1)) in (ssh_port(), DASHBOARD_PORT) and not pm.group(2):
            raise ValueError("Das würde SSH oder das Dashboard für alle sperren.")
        _helper("ufw-rule", rule_action, port, proto, source, *([comment] if comment else []))
    elif action == "delete":
        num = int(params.get("num", 0))
        status = parse_ufw(_helper("ufw-status"))
        rule = next((r for r in status["rules"] if r["num"] == num), None)
        if not rule:
            raise ValueError("Regel nicht gefunden – Liste neu laden.")
        if status["active"] and rule["action"] in ("allow", "limit") and rule["from"].startswith("Anywhere"):
            # Last open door for SSH / the dashboard? Then refuse.
            for port in _rule_ports(rule) & {ssh_port(), DASHBOARD_PORT}:
                others = [r for r in status["rules"] if r["num"] != num and r["action"] in ("allow", "limit")
                          and port in _rule_ports(r) and r["from"].startswith("Anywhere") and r["v6"] == rule["v6"]]
                if not others:
                    raise ValueError(f"Das ist die letzte Freigabe für Port {port} – du würdest dich aussperren.")
        _helper("ufw-delete", str(num))
    elif action == "preset":
        preset = str(params.get("preset", ""))
        if preset not in ("k3s", "netbird", "web"):
            raise ValueError("Unbekannte Vorlage.")
        _helper("ufw-preset", preset)
    else:
        raise ValueError("Unbekannte Aktion.")
    return firewall_status()
