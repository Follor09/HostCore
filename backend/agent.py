"""
Agent mode: this installation serves no web pages and has no users - it only answers
a main HostCore dashboard that authenticates with a token. The main dashboard forwards
its API calls, live data and terminal here, so every feature works on this server too.

Enabled by agent.json next to app.py (created by `./install.sh --agent` or
`./agent.sh enable`, readable only by the dashboard user). Optionally only the main
dashboard's address may connect ("allow": IPs / networks, `./agent.sh allow 192.168.1.10`) -
on top of the token, so a leaked token alone is not enough.
"""

import hmac
import ipaddress
import json
import os
import secrets
import socket
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE_DIR, "agent.json")
_config: Optional[Dict[str, Any]] = None


def _load() -> Optional[Dict[str, Any]]:
    try:
        with open(CONFIG) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and len(str(data.get("token", ""))) >= 32 else None
    except (OSError, ValueError):
        return None


def enabled() -> bool:
    global _config
    if _config is None:
        _config = _load() or {}
    return bool(_config)


def check(header: str) -> bool:
    """Authorization: Bearer <token> - constant-time compare."""
    if not enabled():
        return False
    scheme, _, token = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip().encode(), str(_config["token"]).encode())


def allowed(ip: str) -> bool:
    """Is this client address one the agent accepts? No list = any address (token only)."""
    if not enabled():
        return False
    nets = _config.get("allow") or []
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)


def parse_allow(text: str) -> list:
    """"192.168.1.10, 10.0.0.0/24" -> ["192.168.1.10/32", "10.0.0.0/24"]; "any" or "" -> [] (every address)."""
    text = (text or "").strip()
    if text.lower() in ("", "any", "alle"):
        return []
    out = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        if part:
            try:
                out.append(str(ipaddress.ip_network(part, strict=False)))
            except ValueError:
                raise ValueError(f"Keine gültige IP-Adresse oder Netz: {part}")
    return out[:20]


def set_allow(text: str) -> list:
    cfg = _load()
    if not cfg:
        raise ValueError("Agent-Modus ist aus (./agent.sh enable).")
    cfg["allow"] = parse_allow(text)
    _write(cfg)
    return cfg["allow"]


def remote_user(headers) -> str:
    """Who is acting, as told by the (authenticated) main dashboard - for the activity log."""
    name = (headers.get("x-picore-user") or "").strip()[:40]
    return f"{name}@remote" if name else "remote"


def create(rotate: bool = False) -> str:
    """Write agent.json (0600) and return the token."""
    cfg = _load() or {}
    if rotate or not cfg:
        cfg = {"token": secrets.token_urlsafe(32), "allow": cfg.get("allow", [])}
    _write(cfg)
    return cfg["token"]


def _write(cfg: Dict[str, Any]) -> None:
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    os.replace(tmp, CONFIG)


def landing_html() -> str:
    host = socket.gethostname()
    return f"""<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HostCore Agent · {host}</title>
<body style="font-family:system-ui,sans-serif;background:#07090e;color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:520px;padding:32px;border:1px solid #1f2937;border-radius:16px;background:#0f1422">
<h1 style="margin-top:0;font-size:1.4rem">HostCore Agent läuft</h1>
<p>Dieser Server (<b>{host}</b>) wird über ein anderes HostCore-Dashboard gesteuert und hat keine eigene Oberfläche.</p>
<p>Im Haupt-Dashboard unter <i>Einstellungen → Remote-Server</i> diese Adresse und den Token eintragen.
Den Token zeigt <code>./agent.sh token</code> im Dashboard-Ordner auf diesem Server.</p>
<p style="color:#8b949e;font-size:.85rem">This server runs as a HostCore agent and is controlled from another dashboard.</p>
</div></body></html>"""


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "token"
    if cmd == "enable":
        print(create())
    elif cmd == "rotate":
        print(create(rotate=True))
    elif cmd == "token":
        cfg = _load()
        print(cfg["token"] if cfg else "", end="")
        sys.exit(0 if cfg else 1)
    elif cmd == "allow":
        try:
            nets = set_allow(" ".join(sys.argv[2:]))
        except ValueError as e:
            print(e, file=sys.stderr)
            sys.exit(2)
        print(", ".join(nets) or "any", end="")
    elif cmd == "allowed":
        cfg = _load()
        print(", ".join((cfg or {}).get("allow") or []) or "any", end="")
    elif cmd == "disable":
        try:
            os.remove(CONFIG)
        except OSError:
            pass
    else:
        print("usage: agent.py enable|token|rotate|allow <ips>|allowed|disable")
        sys.exit(2)
