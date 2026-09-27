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
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger("pi_dashboard.security")

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

    import geoip
    for item in top + events[:100]:
        item["country"] = geoip.lookup(item["ip"])
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
    import geoip
    return {"installed": True, "active": True,
            "jails": [{"name": name, "banned": ips} for name, ips in sorted(jails.items())],
            "countries": geoip.lookup_many(ip for ips in jails.values() for ip in ips)}


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


_RE_ATTRIBS = re.compile(r"\s*\(((?:log-all|log|out)(?:,\s*(?:log-all|log|out))*)\)\s*$")
_RE_ON = re.compile(r"\s+on\s+(\S+)")


def _rule_fields(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Pull "on eth0" and "(log, out)" out of ufw's To/From columns into own fields."""
    rule["log"] = False
    for key in ("to", "from"):
        text = rule[key]
        m = _RE_ATTRIBS.search(text)
        if m:
            rule["log"] = rule["log"] or "log" in m.group(1)
            text = text[:m.start()]
        m = _RE_ON.search(text)
        if m:
            rule["iface"] = m.group(1)
            text = text[:m.start()] + text[m.end():]
        rule[key] = text.strip()
    rule.setdefault("iface", "")
    return rule


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
        rules.append(_rule_fields({"num": int(num), "to": to.strip(), "action": action.lower(),
                                   "direction": (direction or "IN").lower(), "from": source.strip(),
                                   "comment": (comment or "").strip(), "v6": "(v6)" in to}))
    if not active:
        # An inactive ufw lists no rules in `status`; `show added` still has the saved ones
        for line in added.splitlines():
            am = _RE_ADDED.match(line.strip())
            if am:
                spec = am.group(2)
                direction = "out" if re.match(r"out\b", spec) else "in"
                spec = re.sub(r"^(in|out)\s+", "", spec)
                iface = ""
                m = re.match(r"on (\S+)\s*", spec)
                if m:
                    iface, spec = m.group(1), spec[m.end():]
                log = bool(re.match(r"log(-all)?\b", spec))
                spec = re.sub(r"^log(-all)?\s+", "", spec)
                rules.append({"num": None, "to": spec, "action": am.group(1), "direction": direction,
                              "from": "", "comment": am.group(3) or "", "v6": False, "iface": iface, "log": log})
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
                  interfaces=sorted(i for i in psutil.net_if_addrs()
                                    if i != "lo" and not i.startswith(("veth", "cali", "flannel.", "cni", "br-"))),
                  docker=bool(shutil.which("docker")))
    return status


def _rule_ports(rule: Dict[str, Any]) -> set:
    m = re.match(r"^(\d+)(?:/(tcp|udp))?", rule["to"])
    return {int(m.group(1))} if m else set()


_RE_PORTS = re.compile(r"\d{1,5}(?::\d{1,5})?(?:,\d{1,5}(?::\d{1,5})?){0,14}")


def _parse_ports(value: Any, label: str) -> Optional[List[range]]:
    """"" / any -> None, else 443, 6000:6010 or 80,443 -> list of ranges."""
    text = str(value or "").replace(" ", "")
    if text.lower() in ("", "any", "alle", "*"):
        return None
    if not _RE_PORTS.fullmatch(text):
        raise ValueError(f"{label}: Port 1–65535, Bereich wie 6000:6010 oder Liste wie 80,443 (max. 15).")
    out = []
    for part in text.split(","):
        a, _, b = part.partition(":")
        lo, hi = int(a), int(b or a)
        if not (1 <= lo <= 65535 and 1 <= hi <= 65535) or (b and lo >= hi):
            raise ValueError(f"{label}: Port 1–65535, Bereich wie 6000:6010 oder Liste wie 80,443 (max. 15).")
        out.append(range(lo, hi + 1))
    return out


def _parse_addr(value: Any, label: str):
    text = str(value or "").strip()
    if text.lower() in ("", "any", "alle", "*"):
        return None
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        raise ValueError(f"{label} muss leer (alle), eine IP oder ein Netz wie 192.168.1.0/24 sein.")


def _addr_str(net) -> str:
    if net is None:
        return "any"
    return str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)


def _covers(ports: Optional[List[range]], port: int) -> bool:
    return ports is None or any(port in r for r in ports)


def _rule_args(params: Dict[str, Any], client_ip: str = "") -> List[str]:
    """Validate a rule from the form and return the pi-helper.sh ufw-rule arguments."""
    rule_action = str(params.get("action", ""))
    direction = str(params.get("direction", "in"))
    iface = str(params.get("iface", "") or "any").strip()
    proto = str(params.get("proto", "tcp"))
    position = str(params.get("position", "end"))
    log = bool(params.get("log"))
    comment = str(params.get("comment", "")).strip()
    if rule_action not in ("allow", "deny", "reject", "limit"):
        raise ValueError("Ungültige Aktion.")
    if direction not in ("in", "out"):
        raise ValueError("Ungültige Richtung.")
    if iface != "any" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,14}", iface):
        raise ValueError("Ungültige Schnittstelle.")
    if proto not in ("tcp", "udp", "any"):
        raise ValueError("Ungültiges Protokoll.")
    if position not in ("top", "end"):
        raise ValueError("Ungültige Position.")
    if not re.fullmatch(r"[A-Za-z0-9 ._()-]{0,40}", comment):
        raise ValueError("Kommentar: nur Buchstaben, Zahlen, Leerzeichen und . _ - ( ) (max. 40).")
    # "port" is the old single-field form
    dport_text = params.get("dport", params.get("port", ""))
    sport_text = params.get("sport", "")
    dports = _parse_ports(dport_text, "Zielport")
    sports = _parse_ports(sport_text, "Quellport")
    src = _parse_addr(params.get("from"), "Quelle")
    dst = _parse_addr(params.get("to"), "Ziel")
    if src and dst and src.version != dst.version:
        raise ValueError("Quelle und Ziel müssen beide IPv4 oder beide IPv6 sein.")
    if proto == "any" and re.search(r"[:,]", f"{str(sport_text or '').replace(' ', '')}{str(dport_text or '').replace(' ', '')}"):
        raise ValueError("Portbereiche und -listen brauchen TCP oder UDP.")
    if not any([src, dst, sports, dports, iface != "any"]):
        raise ValueError("Die Regel würde für jeden Verkehr gelten – dafür die Standardrichtlinie ändern, keine Regel.")

    if direction == "in" and rule_action in ("deny", "reject") and not sports and proto != "udp":
        # Never lock the admin out: their own address, SSH for everyone
        try:
            me = ipaddress.ip_address(client_ip) if client_ip else None
        except ValueError:
            me = None
        if me and not me.is_loopback and (src is None or (src.version == me.version and me in src)) \
                and (dst is None or dst.version == me.version) and _covers(dports, DASHBOARD_PORT):
            raise ValueError(f"Das würde deine eigene Verbindung ({client_ip}) zum Dashboard sperren.")
        if (src is None or src.prefixlen == 0) and _covers(dports, ssh_port()):
            raise ValueError("Das würde SSH für alle sperren.")
        if (src is None or src.prefixlen == 0) and _covers(dports, DASHBOARD_PORT):
            raise ValueError("Das würde das Dashboard für alle sperren.")
    if direction == "out" and rule_action in ("deny", "reject") and dst is None and dports is None:
        raise ValueError("Das würde fast allen ausgehenden Verkehr sperren (auch DNS und Updates) – Ziel oder Port angeben.")

    fmt_ports = lambda text: "any" if text is None else str(text).replace(" ", "")
    return [rule_action, direction, iface, proto,
            _addr_str(src), fmt_ports(sport_text) if sports else "any",
            _addr_str(dst), fmt_ports(dport_text) if dports else "any",
            position, "log" if log else "nolog", *([comment] if comment else [])]


def firewall_action(action: str, params: Dict[str, Any], client_ip: str = "") -> Dict[str, Any]:
    if action == "enable":
        _helper("ufw-enable", str(ssh_port()))
    elif action == "disable":
        _helper("ufw-disable")
    elif action == "rule":
        _helper("ufw-rule", *_rule_args(params, client_ip))
    elif action == "logging":
        level = str(params.get("level", ""))
        if level not in ("off", "low", "medium"):
            raise ValueError("Ungültige Stufe.")
        _helper("ufw-logging", level)
        _traffic_cache.clear()
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


# ---------------------------------------------------------------------------
# Firewall traffic: what ufw logged (kernel messages "[UFW BLOCK] IN=eth0 SRC=... DPT=...")
# ---------------------------------------------------------------------------
TRAFFIC_RANGES = {"1h": (3600, 300), "24h": (86400, 3600), "7d": (7 * 86400, 6 * 3600)}  # span, bucket
TRAFFIC_MAX_LINES = 50000
TRAFFIC_CACHE_TTL = 20
_traffic_cache: Dict[str, Any] = {}

_RE_UFW_LOG = re.compile(r"\[UFW (BLOCK|ALLOW|LIMIT BLOCK|AUDIT|AUDIT INVALID)\]\s*(.*)")
_RE_KV = re.compile(r"([A-Z]+)=(\S*)")


def parse_ufw_log(message: str, ts: float) -> Optional[Dict[str, Any]]:
    m = _RE_UFW_LOG.search(message)
    if not m:
        return None
    kind = m.group(1)
    if kind.startswith("AUDIT"):
        return None  # "high" logging: every packet, not useful here
    f = dict(_RE_KV.findall(m.group(2)))
    src, dst = f.get("SRC", ""), f.get("DST", "")
    if not src:
        return None
    iface_in, iface_out = f.get("IN", ""), f.get("OUT", "")
    direction = "fwd" if iface_in and iface_out else "out" if iface_out else "in"
    proto = f.get("PROTO", "").lower()
    if proto == "icmpv6":
        proto = "icmp"
    ev = {"ts": ts, "action": "allow" if kind == "ALLOW" else "limit" if kind == "LIMIT BLOCK" else "block",
          "direction": direction, "iface": iface_in or iface_out, "src": src, "dst": dst, "proto": proto,
          "sport": int(f["SPT"]) if f.get("SPT", "").isdigit() else None,
          "dport": int(f["DPT"]) if f.get("DPT", "").isdigit() else None,
          "len": int(f["LEN"]) if f.get("LEN", "").isdigit() else 0}
    # Which side is "the other one": the sender for incoming, the target for outgoing
    ev["remote"] = dst if direction == "out" else src
    ev["scope"] = ip_scope(ev["remote"])
    ev["syn"] = " SYN " in f" {m.group(2)} " and " ACK " not in f" {m.group(2)} "
    return ev


def _read_ufw_log(since_ts: float) -> Dict[str, Any]:
    """Kernel messages across boots (-k would only show the current boot)."""
    if not shutil.which("journalctl"):
        return _read_ufw_logfile(since_ts)
    base = ["journalctl", "--no-pager", "-o", "json", "--output-fields=MESSAGE", "_TRANSPORT=kernel",
            "--since", f"@{int(since_ts)}", "-n", str(TRAFFIC_MAX_LINES)]
    try:
        res = subprocess.run(base + ["--grep", "UFW "], capture_output=True, text=True, timeout=25, errors="replace")
        if res.returncode != 0 and "pattern" in res.stderr.lower():
            res = subprocess.run(base, capture_output=True, text=True, timeout=25, errors="replace")
    except subprocess.TimeoutExpired:
        return {"events": [], "error": "Journal antwortet nicht."}
    events = []
    lines = res.stdout.splitlines()
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE", "")
        if isinstance(msg, list):
            msg = bytes(msg).decode("utf-8", errors="replace")
        ev = parse_ufw_log(msg, int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6)
        if ev:
            events.append(ev)
    hint = ""
    stderr = res.stderr.lower()
    if not events and ("insufficient permissions" in stderr or "not seeing messages" in stderr):
        fallback = _read_ufw_logfile(since_ts)
        if fallback["events"]:
            return fallback
        user = os.environ.get("USER", "$USER")
        hint = f"Keine Berechtigung für System-Logs: sudo usermod -aG systemd-journal {user} && sudo systemctl restart raspberry-dashboard"
    return {"events": events, "hint": hint, "truncated": len(lines) >= TRAFFIC_MAX_LINES, "source": "journal"}


def _read_ufw_logfile(since_ts: float) -> Dict[str, Any]:
    """Fallback for systems with rsyslog: /var/log/ufw.log (readable for the adm group)."""
    path = "/var/log/ufw.log"
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()[-TRAFFIC_MAX_LINES:]
    except OSError:
        return {"events": [], "hint": "", "source": "none"}
    events = []
    year = time.localtime().tm_year
    for line in lines:
        ts = None
        m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", line)  # rsyslog high-precision format
        if m:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
        else:
            m = re.match(r"(\w{3}\s+\d+ \d\d:\d\d:\d\d)", line)
            if m:
                try:
                    ts = time.mktime(time.strptime(f"{year} {m.group(1)}", "%Y %b %d %H:%M:%S"))
                except ValueError:
                    ts = None
        if ts is None or ts < since_ts:
            continue
        ev = parse_ufw_log(line, ts)
        if ev:
            events.append(ev)
    return {"events": events, "hint": "", "truncated": len(lines) >= TRAFFIC_MAX_LINES, "source": "file"}


def _service_name(port: Optional[int], proto: str) -> str:
    if not port:
        return ""
    try:
        return socket.getservbyport(port, proto if proto in ("tcp", "udp") else "tcp")
    except OSError:
        return ""


def _lan_names() -> Dict[str, str]:
    try:
        import netscan
        return {d["ip"]: d.get("alias") or d.get("hostname") or d.get("vendor") or ""
                for d in netscan.devices()["devices"] if d.get("ip")}
    except Exception:
        return {}


def firewall_traffic(range_: str = "24h", action: str = "block", direction: str = "all", query: str = "") -> Dict[str, Any]:
    if range_ not in TRAFFIC_RANGES:
        range_ = "24h"
    span, bucket = TRAFFIC_RANGES[range_]
    now = time.time()
    cached = _traffic_cache.get(range_)
    if cached and now - cached["ts"] < TRAFFIC_CACHE_TTL:
        raw = cached["raw"]
    else:
        raw = _read_ufw_log(now - span)
        _traffic_cache[range_] = {"ts": now, "raw": raw}

    fw = {}
    if shutil.which("ufw") or os.path.exists("/usr/sbin/ufw"):
        try:
            fw = parse_ufw(_helper("ufw-status"))
        except (PermissionError, RuntimeError, subprocess.TimeoutExpired):
            fw = {}

    all_events = raw["events"]
    counts = {"block": 0, "allow": 0, "limit": 0}
    for e in all_events:
        counts[e["action"]] += 1

    q = query.strip().lower()
    port_q = int(q) if q.isdigit() else None

    def keep(e):
        if action == "block" and e["action"] == "allow":
            return False
        if action == "allow" and e["action"] != "allow":
            return False
        if direction != "all" and e["direction"] != direction:
            return False
        if q:
            if port_q is not None:
                return e["dport"] == port_q or e["sport"] == port_q
            return q in e["src"].lower() or q in e["dst"].lower() or q == e["iface"].lower()
        return True

    events = [e for e in all_events if keep(e)]

    # Timeline: Internet vs. local network, per bucket
    n = span // bucket
    start = (int(now) // bucket - n + 1) * bucket
    timeline = [{"ts": start + i * bucket, "internet": 0, "local": 0} for i in range(n)]
    for e in events:
        i = int((e["ts"] - start) // bucket)
        if 0 <= i < n:
            timeline[i]["internet" if e["scope"] == "internet" else "local"] += 1

    import geoip
    names = _lan_names()
    country = geoip.lookup_many(e["remote"] for e in events if e["scope"] == "internet")
    countries: Dict[str, Dict[str, Any]] = {}
    for e in events:
        cc = country.get(e["remote"], "")
        if cc:
            c = countries.setdefault(cc, {"cc": cc, "count": 0, "sources": set()})
            c["count"] += 1
            c["sources"].add(e["remote"])
    remotes: Dict[str, Dict[str, Any]] = {}
    ports: Dict[tuple, Dict[str, Any]] = {}
    for e in events:
        r = remotes.setdefault(e["remote"], {"ip": e["remote"], "scope": e["scope"], "count": 0, "ports": {},
                                             "first": e["ts"], "last": e["ts"], "directions": set(),
                                             "name": names.get(e["remote"], ""), "country": country.get(e["remote"], "")})
        r["count"] += 1
        r["last"] = max(r["last"], e["ts"])
        r["first"] = min(r["first"], e["ts"])
        r["directions"].add(e["direction"])
        key = f"{e['dport']}/{e['proto']}" if e["dport"] else e["proto"] or "?"
        r["ports"][key] = r["ports"].get(key, 0) + 1
        pk = (e["dport"], e["proto"])
        p = ports.setdefault(pk, {"port": e["dport"], "proto": e["proto"], "count": 0, "sources": set(),
                                  "service": _service_name(e["dport"], e["proto"]), "directions": set()})
        p["count"] += 1
        p["sources"].add(e["remote"])
        p["directions"].add(e["direction"])

    listening: Dict[tuple, str] = {}
    for c in _safe_connections():
        if c.laddr and c.status in (psutil.CONN_LISTEN, psutil.CONN_NONE):
            key = (c.laddr.port, "udp" if c.type == socket.SOCK_DGRAM else "tcp")
            name = ""
            try:
                name = psutil.Process(c.pid).name() if getattr(c, "pid", None) else ""
            except (psutil.Error, OSError):
                pass
            listening[key] = listening.get(key) or name
    top_sources = []
    for r in sorted(remotes.values(), key=lambda r: r["count"], reverse=True)[:25]:
        r["ports"] = [k for k, _ in sorted(r["ports"].items(), key=lambda kv: kv[1], reverse=True)[:4]]
        r["directions"] = sorted(r["directions"])
        top_sources.append(r)
    top_ports = []
    for p in sorted(ports.values(), key=lambda p: p["count"], reverse=True)[:25]:
        top_ports.append({**p, "sources": len(p["sources"]), "directions": sorted(p["directions"]),
                          "listening": bool(p["port"]) and (p["port"], p["proto"]) in listening,
                          "program": listening.get((p["port"], p["proto"]), "")})

    internet = sum(1 for e in events if e["scope"] == "internet")
    return {
        "range": range_, "bucket": bucket, "timeline": timeline,
        "summary": {"total": len(events), "sources": len(remotes), "ports": len(ports),
                    "internet": internet, "per_hour": round(len(events) / (span / 3600), 1),
                    "counts": counts, "top_port": top_ports[0] if top_ports else None},
        "top_sources": top_sources, "top_ports": top_ports,
        "top_countries": [{**c, "sources": len(c["sources"])} for c in sorted(countries.values(), key=lambda c: -c["count"])[:15]],
        "geoip": geoip.installed(),
        "recent": [{**e, "country": country.get(e["remote"], "")} for e in sorted(events, key=lambda e: e["ts"], reverse=True)[:200]],
        "firewall": {"installed": bool(fw) or bool(shutil.which("ufw")), "active": fw.get("active", False),
                     "logging": fw.get("logging", "")},
        "hint": raw.get("hint", ""), "error": raw.get("error", ""),
        "truncated": raw.get("truncated", False), "source": raw.get("source", ""),
    }


# ---------------------------------------------------------------------------
# Country blocking: ipsets filled from the GeoIP database, dropped in ufw's before.rules
# ---------------------------------------------------------------------------
GEOBLOCK_MAX = 600000


def _helper_input(args: List[str], data: str, timeout: float = 120) -> str:
    if not os.path.exists(HELPER):
        raise PermissionError("Helfer fehlt – install.sh auf dem Pi erneut ausführen.")
    res = subprocess.run(["sudo", "-n", HELPER, *args], input=data, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        err = res.stderr.strip()
        if "password" in err.lower() or "not allowed" in err.lower():
            raise PermissionError("Keine Berechtigung – install.sh auf dem Pi erneut ausführen.")
        raise RuntimeError(err.splitlines()[-1][:200] if err else "Fehlgeschlagen.")
    return res.stdout


def geoblock_status() -> Dict[str, Any]:
    import geoip
    import settings_store
    saved = settings_store.get("geoblock", {}) or {}
    helper: Dict[str, str] = {}
    error = ""
    if shutil.which("ufw") or os.path.exists("/usr/sbin/ufw"):
        try:
            for line in _helper("geoblock-status").splitlines():
                k, _, v = line.partition("=")
                helper[k.strip()] = v.strip()
        except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as e:
            error = str(e)
    countries = saved.get("countries", [])
    return {
        "countries": countries, "applied": saved.get("applied", 0), "networks": saved.get("networks", 0),
        "ipset": helper.get("ipset") == "yes", "rules": helper.get("rules") == "yes",
        "loaded": {"v4": int(helper.get("v4", 0) or 0), "v6": int(helper.get("v6", 0) or 0)},
        "active": bool(countries) and helper.get("rules") == "yes",
        "error": error, "db": geoip.info(),
        "available": [{**c} for c in geoip.countries()],
    }


def geoblock_apply(ccs: List[str], client_ip: str = "") -> Dict[str, Any]:
    import geoip
    import settings_store
    ccs = sorted({str(c).upper() for c in ccs if re.fullmatch(r"[A-Za-z]{2}", str(c))})
    if not ccs:
        return geoblock_clear()
    if not geoip.installed():
        raise ValueError("Erst die Länder-Datenbank laden.")
    known = {c["cc"] for c in geoip.countries()}
    unknown = [c for c in ccs if c not in known]
    if unknown:
        raise ValueError(f"Unbekannte Länder: {', '.join(unknown)}")
    mine = geoip.lookup(client_ip) if client_ip else ""
    if mine and mine in ccs:
        raise ValueError(f"Du bist gerade aus diesem Land verbunden ({mine}) – das würde dich aussperren.")
    nets = geoip.networks(ccs)
    total = len(nets["v4"]) + len(nets["v6"])
    if total > GEOBLOCK_MAX:
        raise ValueError(f"Zu viele Netze ({total:,}) – weniger oder kleinere Länder wählen (max. {GEOBLOCK_MAX:,}).".replace(",", "."))
    _helper_input(["geoblock-apply"], "\n".join(nets["v4"] + nets["v6"]) + "\n")
    settings_store.set("geoblock", {"countries": ccs, "applied": int(time.time()), "networks": total})
    _traffic_cache.clear()
    return geoblock_status()


def geoblock_clear() -> Dict[str, Any]:
    import settings_store
    _helper("geoblock-clear")
    settings_store.set("geoblock", {"countries": [], "applied": int(time.time()), "networks": 0})
    return geoblock_status()


def geoblock_reapply() -> None:
    """After a monthly database update: same countries, fresh networks."""
    import settings_store
    ccs = (settings_store.get("geoblock", {}) or {}).get("countries", [])
    if ccs:
        try:
            geoblock_apply(ccs)
        except Exception as e:
            logger.warning(f"Geoblock re-apply failed: {e}")


# ---------------------------------------------------------------------------
# Live connections: every open TCP/UDP connection with its program (ss via the helper,
# as root, so processes of other users are named too; psutil as a fallback)
# ---------------------------------------------------------------------------
_RE_SS_USER = re.compile(r'\("([^"]+)",pid=(\d+)')
INTERNAL_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "10.42.0.0/16", "10.43.0.0/16", "172.16.0.0/12", "fe80::/10")]


def _split_addr(text: str):
    """'192.168.1.2:22', '[::ffff:1.2.3.4]:443', '*:53', '[fe80::1%eth0]:546' -> (ip, port)"""
    host, _, port = text.rpartition(":")
    host = host.strip("[]").split("%")[0]
    if host.startswith("::ffff:") and "." in host:
        host = host[7:]
    if host in ("*", "0.0.0.0", "::", ""):
        host = "*"
    return host, int(port) if port.isdigit() else None


def _internal(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in INTERNAL_NETS if n.version == addr.version)


def _from_ss(out: str) -> tuple:
    conns, listen_ports = [], set()
    for line in out.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        proto, state, _, _, local, peer = parts[:6]
        procs = _RE_SS_USER.findall(parts[6]) if len(parts) > 6 else []
        lip, lport = _split_addr(local)
        rip, rport = _split_addr(peer)
        if state == "LISTEN" or (proto == "udp" and state == "UNCONN" and rip == "*"):
            if lport:
                listen_ports.add((proto, lport))
            continue
        conns.append({"proto": proto, "state": state, "local": lip, "lport": lport, "remote": rip, "rport": rport,
                      "program": procs[0][0] if procs else "", "pid": int(procs[0][1]) if procs else None})
    return conns, listen_ports


def _from_psutil() -> tuple:
    conns, listen_ports = [], set()
    names: Dict[int, str] = {}
    for c in _safe_connections():
        proto = "udp" if c.type == socket.SOCK_DGRAM else "tcp"
        if c.status == psutil.CONN_LISTEN or (proto == "udp" and not c.raddr):
            if c.laddr:
                listen_ports.add((proto, c.laddr.port))
            continue
        if not c.raddr:
            continue
        name = ""
        if c.pid:
            if c.pid not in names:
                try:
                    names[c.pid] = psutil.Process(c.pid).name()
                except psutil.Error:
                    names[c.pid] = ""
            name = names[c.pid]
        lip, _ = _split_addr(f"{c.laddr.ip}:0")
        rip, _ = _split_addr(f"{c.raddr.ip}:0")
        conns.append({"proto": proto, "state": {"ESTABLISHED": "ESTAB"}.get(c.status, c.status.replace("_", "-")),
                      "local": lip, "lport": c.laddr.port, "remote": rip, "rport": c.raddr.port, "program": name, "pid": c.pid})
    return conns, listen_ports


def connections() -> Dict[str, Any]:
    import geoip
    source, note = "ss", ""
    try:
        conns, listen = _from_ss(_helper("connections", timeout=15))
    except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as e:
        conns, listen = _from_psutil()
        source, note = "psutil", f"Ohne Helfer: Programme anderer Benutzer bleiben unbenannt ({e})."
    lan_names = _lan_names()
    for c in conns:
        c["direction"] = "in" if (c["proto"], c["lport"]) in listen else "out"
        c["scope"] = ip_scope(c["remote"]) if c["remote"] != "*" else "local"
        c["internal"] = _internal(c["remote"]) or _internal(c["local"]) and c["scope"] == "local"
        c["country"] = geoip.lookup(c["remote"]) if c["scope"] == "internet" else ""
        c["name"] = lan_names.get(c["remote"], "")
    conns.sort(key=lambda c: (c["internal"], c["direction"] != "in", c["scope"] != "internet", c["program"] or "~", c["remote"]))
    visible = [c for c in conns if not c["internal"]]
    progs: Dict[str, int] = {}
    for c in visible:
        progs[c["program"] or "?"] = progs.get(c["program"] or "?", 0) + 1
    return {"source": source, "note": note, "connections": conns[:1500], "truncated": len(conns) > 1500,
            "summary": {"total": len(visible), "internal": len(conns) - len(visible),
                        "incoming": sum(1 for c in visible if c["direction"] == "in"),
                        "outgoing": sum(1 for c in visible if c["direction"] == "out"),
                        "internet": sum(1 for c in visible if c["scope"] == "internet"),
                        "countries": len({c["country"] for c in visible if c["country"]}),
                        "listening": len(listen)},
            "programs": sorted(({"name": k, "count": v} for k, v in progs.items()), key=lambda p: -p["count"])[:12]}
