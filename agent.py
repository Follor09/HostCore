"""
Agent mode: this installation serves no web pages and has no users - it only answers
a main PiCore dashboard that authenticates with a token. The main dashboard forwards
its API calls, live data and terminal here, so every feature works on this Pi too.

Enabled by agent.json next to app.py (created by `./install.sh --agent` or
`./agent.sh enable`, readable only by the dashboard user).
"""

import hmac
import json
import os
import secrets
import socket
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


def remote_user(headers) -> str:
    """Who is acting, as told by the (authenticated) main dashboard - for the activity log."""
    name = (headers.get("x-picore-user") or "").strip()[:40]
    return f"{name}@remote" if name else "remote"


def create(rotate: bool = False) -> str:
    """Write agent.json (0600) and return the token."""
    cfg = _load() or {}
    if rotate or not cfg:
        cfg = {"token": secrets.token_urlsafe(32)}
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    os.replace(tmp, CONFIG)
    return cfg["token"]


def landing_html() -> str:
    host = socket.gethostname()
    return f"""<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PiCore Agent · {host}</title>
<body style="font-family:system-ui,sans-serif;background:#07090e;color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:520px;padding:32px;border:1px solid #1f2937;border-radius:16px;background:#0f1422">
<h1 style="margin-top:0;font-size:1.4rem">PiCore Agent läuft</h1>
<p>Dieser Pi (<b>{host}</b>) wird über ein anderes PiCore-Dashboard gesteuert und hat keine eigene Oberfläche.</p>
<p>Im Haupt-Dashboard unter <i>Einstellungen → Remote-Pis</i> diese Adresse und den Token eintragen.
Den Token zeigt <code>./agent.sh token</code> im Dashboard-Ordner auf diesem Pi.</p>
<p style="color:#8b949e;font-size:.85rem">This Pi runs as a PiCore agent and is controlled from another dashboard.</p>
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
    elif cmd == "disable":
        try:
            os.remove(CONFIG)
        except OSError:
            pass
    else:
        print("usage: agent.py enable|token|rotate|disable")
        sys.exit(2)
