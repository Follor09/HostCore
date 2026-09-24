"""
Login for the dashboard.

- One account; the password is set on the Pi with ./set-password.sh (never via the web,
  so nobody on the network can claim a fresh install first).
- auth.json (untracked, mode 600) holds a scrypt hash and a random signing secret.
- Sessions are stateless HMAC-signed cookies, so dashboard restarts and auto-updates
  don't log anyone out. Changing the password rotates the secret = logs out everyone.
- Failed logins are rate-limited per client IP.

CLI:  python auth.py set-password | public-view on|off | status
"""

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE_DIR, "auth.json")

COOKIE_NAME = "picore_session"
SESSION_TTL = 12 * 3600            # without "stay logged in"
REMEMBER_TTL = 30 * 24 * 3600      # with "stay logged in"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MIN_PASSWORD_LENGTH = 8

# scrypt: ~16 MB memory, ~0.1 s on a Pi 5
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

_cache: Dict[str, Any] = {"mtime": None, "cfg": None}


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------
def load_config() -> Optional[Dict[str, Any]]:
    """auth.json, re-read only when it changes on disk."""
    try:
        mtime = os.path.getmtime(AUTH_FILE)
    except OSError:
        _cache.update(mtime=None, cfg=None)
        return None
    if mtime != _cache["mtime"]:
        try:
            with open(AUTH_FILE) as f:
                _cache.update(mtime=mtime, cfg=json.load(f))
        except (OSError, json.JSONDecodeError):
            _cache.update(mtime=None, cfg=None)
    return _cache["cfg"]


def _save_config(cfg: Dict[str, Any]) -> None:
    tmp = AUTH_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, AUTH_FILE)
    os.chmod(AUTH_FILE, 0o600)


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg and cfg.get("password_hash"))


def public_view() -> bool:
    cfg = load_config()
    return bool(cfg and cfg.get("public_view"))


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=64 * 1024 * 1024, dklen=32)


def verify_login(username: str, password: str) -> bool:
    cfg = load_config()
    if not cfg or not cfg.get("password_hash"):
        return False
    expected = bytes.fromhex(cfg["password_hash"])
    actual = _scrypt(password, bytes.fromhex(cfg["salt"]))
    # Compare both parts without short-circuiting, so timing reveals nothing
    user_ok = hmac.compare_digest(username.encode(), cfg["username"].encode())
    pass_ok = hmac.compare_digest(actual, expected)
    return user_ok and pass_ok


def set_password(username: str, password: str) -> None:
    cfg = load_config() or {}
    salt = secrets.token_bytes(16)
    cfg.update(
        username=username,
        salt=salt.hex(),
        password_hash=_scrypt(password, salt).hex(),
        secret=secrets.token_hex(32),   # new secret -> all existing sessions end
        changed=int(time.time()),
    )
    cfg.setdefault("public_view", False)
    _save_config(cfg)


# ---------------------------------------------------------------------------
# Sessions (signed cookie: "username|expiry|signature")
# ---------------------------------------------------------------------------
def _sign(secret: str, payload: str) -> str:
    return hmac.new(bytes.fromhex(secret), payload.encode(), hashlib.sha256).hexdigest()


def make_session(username: str, remember: bool) -> tuple[str, int]:
    cfg = load_config()
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    payload = f"{username}|{int(time.time()) + ttl}"
    return f"{payload}|{_sign(cfg['secret'], payload)}", ttl


def user_from_token(token: Optional[str]) -> Optional[str]:
    cfg = load_config()
    if not token or not cfg or not cfg.get("secret"):
        return None
    try:
        username, expiry, sig = token.split("|")
        expiry_ts = int(expiry)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(cfg["secret"], f"{username}|{expiry}")):
        return None
    if expiry_ts < time.time() or username != cfg.get("username"):
        return None
    return username


# ---------------------------------------------------------------------------
# Brute-force protection: 5 free attempts per IP, then a growing lockout
# ---------------------------------------------------------------------------
FREE_ATTEMPTS = 5
_failures: Dict[str, Dict[str, float]] = {}


def lockout_remaining(ip: str) -> int:
    entry = _failures.get(ip)
    if not entry:
        return 0
    return max(0, int(entry.get("locked_until", 0) - time.time()))


def record_failure(ip: str) -> None:
    entry = _failures.setdefault(ip, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    if entry["count"] >= FREE_ATTEMPTS:
        # 30 s, 60 s, 120 s ... capped at 15 min
        extra = entry["count"] - FREE_ATTEMPTS
        entry["locked_until"] = time.time() + min(30 * 2 ** extra, 900)


def record_success(ip: str) -> None:
    _failures.pop(ip, None)


# ---------------------------------------------------------------------------
# CLI (used by set-password.sh)
# ---------------------------------------------------------------------------
def _cli() -> int:
    parser = argparse.ArgumentParser(description="PiCore Dashboard Login verwalten")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("set-password", help="Benutzer & Passwort setzen (meldet alle ab)")
    pv = sub.add_parser("public-view", help="Dashboard ohne Login ansehen (Aktionen bleiben geschützt)")
    pv.add_argument("state", choices=["on", "off"])
    sub.add_parser("status", help="Aktuelle Einstellungen anzeigen")
    args = parser.parse_args()

    if args.cmd == "status":
        cfg = load_config()
        if not cfg or not cfg.get("password_hash"):
            print("🔓 Kein Passwort gesetzt – das Dashboard ist gesperrt. ./set-password.sh ausführen.")
        else:
            print(f"🔐 Benutzer: {cfg['username']}")
            print(f"👀 Ansehen ohne Login: {'an' if cfg.get('public_view') else 'aus'}")
        return 0

    if args.cmd == "public-view":
        cfg = load_config()
        if not cfg or not cfg.get("password_hash"):
            print("❌ Zuerst ein Passwort setzen: ./set-password.sh")
            return 1
        cfg["public_view"] = args.state == "on"
        _save_config(cfg)
        print("✅ Ansehen ohne Login: " + ("an (Aktionen brauchen weiterhin Login)" if cfg["public_view"] else "aus"))
        return 0

    # set-password
    current = (load_config() or {}).get("username", "admin")
    username = input(f"Benutzername [{current}]: ").strip() or current
    if not USERNAME_RE.match(username):
        print("❌ Erlaubt sind 1–32 Zeichen: Buchstaben, Ziffern, . _ -")
        return 1
    password = getpass.getpass("Neues Passwort: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"❌ Mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
        return 1
    if getpass.getpass("Passwort wiederholen: ") != password:
        print("❌ Passwörter stimmen nicht überein.")
        return 1
    set_password(username, password)
    print(f"✅ Login für '{username}' gespeichert. Alle bestehenden Sitzungen wurden abgemeldet.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
