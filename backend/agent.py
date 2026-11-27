"""
Agent mode: this installation serves no web pages and has no users - it only answers
a main HostCore dashboard that authenticates with a token. The main dashboard forwards
its API calls, live data and terminal here, so every feature works on this server too.

Enabled by agent.json next to app.py (created by `./install.sh --agent` or
`./agent.sh enable`, readable only by the dashboard user). Optionally only the main
dashboard's address may connect ("allow": IPs / networks / host names, `./agent.sh allow 192.168.1.10`
or `./agent.sh allow dash.example.com` for a main dashboard behind DynDNS) -
on top of the token, so a leaked token alone is not enough.
"""

import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import time
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


HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
RESOLVE_TTL = 300
_resolved: Dict[str, tuple] = {}      # name -> (checked, [ip networks], last successful lookup)


def _lookup(name: str) -> Optional[list]:
    try:
        addrs = {a[4][0].split("%")[0] for a in socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)}
        return [ipaddress.ip_network(a) for a in addrs]
    except (OSError, UnicodeError, ValueError):
        return None


def _refresh(name: str) -> None:
    now = time.time()
    nets = _lookup(name)
    if nets is not None:
        _resolved[name] = (now, nets, now)
        return
    _, old, ok = _resolved.get(name, (0, [], 0))
    # Lookup failed: the last known addresses stay valid for a day after the last success
    _resolved[name] = (now, old if now - ok < 86400 else [], ok)


def _resolve(name: str) -> list:
    """The addresses of a name in the allow list (main dashboard behind DynDNS). Looked up once,
    then refreshed in the background every RESOLVE_TTL seconds - a request never waits for DNS
    again; when lookups fail, the last known addresses stay valid for a day."""
    hit = _resolved.get(name)
    if hit is None:
        _refresh(name)
        hit = _resolved[name]
    elif time.time() - hit[0] > RESOLVE_TTL and name not in _refreshing:
        import threading
        _refreshing.add(name)

        def run():
            try:
                _refresh(name)
            finally:
                _refreshing.discard(name)
        threading.Thread(target=run, daemon=True).start()
    return hit[1]


_refreshing: set = set()


def _networks(entries: list) -> list:
    out = []
    for n in entries:
        try:
            out.append(ipaddress.ip_network(n, strict=False))
        except ValueError:
            out += _resolve(n)            # a host name
    return out


def allowed(ip: str) -> bool:
    """Is this client address one the agent accepts? No list = any address (token only).
    Entries are IPs, networks or host names (resolved, so a main dashboard with a changing
    address behind DynDNS keeps working)."""
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
    return any(addr in n for n in _networks(nets))


def parse_allow(text: str) -> list:
    """"192.168.1.10, 10.0.0.0/24, dash.example.com" -> ["192.168.1.10/32", "10.0.0.0/24", "dash.example.com"];
    "any" or "" -> [] (every address)."""
    text = (text or "").strip()
    if text.lower() in ("", "any", "alle"):
        return []
    out = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        if not part:
            continue
        try:
            out.append(str(ipaddress.ip_network(part, strict=False)))
            continue
        except ValueError:
            pass
        name = part.strip().lower().rstrip(".")
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            name = ""
        if not HOST_RE.match(name):
            raise ValueError(f"Keine gültige IP-Adresse, Netz oder Domain: {part}")
        out.append(name)
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


# --- https for the agent (./agent.sh tls on) -------------------------------------------
# A self-signed certificate: the main dashboard pins exactly this certificate when the agent is
# added (after the fingerprint was compared), so no certificate authority is involved and a
# man in the middle cannot present another one. uvicorn picks it up from agent-tls.env, which
# the systemd unit reads (EnvironmentFile).
TLS_KEY = os.path.join(BASE_DIR, "tls.key")
TLS_CERT = os.path.join(BASE_DIR, "tls.crt")
TLS_ENV = os.path.join(BASE_DIR, "agent-tls.env")


def tls_fingerprint() -> str:
    """SHA-256 of the certificate as AB:CD:… (empty if https is off)."""
    if not (os.path.exists(TLS_ENV) and os.path.exists(TLS_CERT)):
        return ""
    import hashlib
    import ssl
    with open(TLS_CERT) as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def tls_enable(renew: bool = False) -> str:
    """Create key and certificate (kept when they exist, unless renew) and switch uvicorn to https."""
    if renew or not (os.path.exists(TLS_KEY) and os.path.exists(TLS_CERT)):
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"HostCore Agent {socket.gethostname()}"[:64])])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(socket.gethostname() or "hostcore-agent")]), critical=False)
                .sign(key, hashes.SHA256()))
        _write_file(TLS_KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()).decode(), 0o600)
        _write_file(TLS_CERT, cert.public_bytes(serialization.Encoding.PEM).decode(), 0o644)
    _write_file(TLS_ENV, f"UVICORN_SSL_KEYFILE={TLS_KEY}\nUVICORN_SSL_CERTFILE={TLS_CERT}\n", 0o644)
    return tls_fingerprint()


def tls_disable() -> None:
    """Back to http (key and certificate stay, so switching on again keeps the fingerprint)."""
    try:
        os.remove(TLS_ENV)
    except OSError:
        pass


def _write_file(path: str, text: str, mode: int) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


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
    elif cmd == "tls-on":
        print(tls_enable(renew=len(sys.argv) > 2 and sys.argv[2] == "renew"), end="")
    elif cmd == "tls-off":
        tls_disable()
    elif cmd == "tls-fingerprint":
        print(tls_fingerprint(), end="")
    elif cmd == "disable":
        try:
            os.remove(CONFIG)
        except OSError:
            pass
    else:
        print("usage: agent.py enable|token|rotate|allow <ips>|allowed|tls-on [renew]|tls-off|tls-fingerprint|disable")
        sys.exit(2)
