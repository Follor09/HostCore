"""
Login, users and permission groups for the dashboard.

- Several accounts, each in one permission group. Built-in groups: Administrator (all
  permissions, fixed), Bearbeiter, Benutzer, Betrachter; admins can edit those (except
  Administrator) and create their own groups in the dashboard.
- The first password is set on the Pi with ./set-password.sh (never via the web, so
  nobody on the network can claim a fresh install first). Admins add more users in the
  dashboard.
- auth.json (untracked, mode 600) holds scrypt hashes, groups and a signing secret.
  An old single-user auth.json is migrated automatically (that user becomes admin).
- Sessions are stateless HMAC-signed cookies ("user|expiry|version|sig"). Each user has
  a session version: a new password or "log out everywhere" bumps it and ends only that
  user's sessions. There is always at least one active administrator.
- Failed logins are rate-limited per client IP.

CLI:  python auth.py set-password | users | public-view on|off | status
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
import threading
import time
from typing import Any, Dict, List, Optional, Set

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE_DIR, "auth.json")

COOKIE_NAME = "picore_session"
SESSION_TTL = 12 * 3600            # without "stay logged in"
REMEMBER_TTL = 30 * 24 * 3600      # with "stay logged in"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
GROUP_ID_RE = re.compile(r"^[a-z0-9-]{2,32}$")
MIN_PASSWORD_LENGTH = 8
MAX_USERS = 50

# scrypt: ~16 MB memory, ~0.1 s on a Pi 5
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

# ---------------------------------------------------------------------------
# Permission catalog (id, label, description, category) - labels are German, the UI
# translates them. Order = order in the permission editor.
# ---------------------------------------------------------------------------
PERMISSIONS = [
    ("system.view", "System-Werkzeuge ansehen", "Netzwerk, Hardware, Sensoren, Ereignisse, Wochenbericht, Prognosen", "Ansehen"),
    ("logs.view", "Logs lesen", "Log-Suche, Journal von Diensten, Container-Logs", "Ansehen"),
    ("security.view", "Sicherheit ansehen", "SSH-Anmeldungen, Firewall-Status, Aktivitätsprotokoll", "Ansehen"),
    ("files.read", "Dateien ansehen", "Datei-Browser, Vorschau, Herunterladen", "Dateien"),
    ("files.write", "Dateien hochladen", "Dateien in Ordner hochladen, in die der Pi-Benutzer schreiben darf", "Dateien"),
    ("files.share", "Dateien teilen", "Freigabe-Links erstellen und beenden", "Dateien"),
    ("notes.edit", "Notizen & Doku bearbeiten", "Notizen, Aufgaben, Erinnerungen und die Dokumentation (Inventar, Betriebstagebuch, Runbooks, Steckbriefe) ändern", "Dashboard"),
    ("actions.run", "Schnellaktionen ausführen", "Eigene Skripte starten und stoppen", "Dashboard"),
    ("services.control", "Dienste & Container steuern", "Starten, stoppen, neu starten", "System"),
    ("apps.install", "Apps installieren", "Docker-Apps aus dem App-Store einrichten (Ports, Ordner)", "System"),
    ("processes.kill", "Prozesse beenden", "Prozesse des Pi-Benutzers beenden", "System"),
    ("system.config", "System einstellen", "CPU-Modus, Lüfterkurve, Aufräumen, Leistungstest", "System"),
    ("updates.install", "Updates installieren", "apt-Updates und Dashboard-Updates", "System"),
    ("system.power", "Neustart & Herunterfahren", "Den Pi neu starten oder ausschalten", "System"),
    ("network.tools", "Netzwerk-Werkzeuge", "Geräte-Scan, Port-Test, DNS, Traceroute, Wake-on-LAN", "Netzwerk"),
    ("netbox.write", "NetBox bearbeiten", "IP-Adressen für Geräte aus dem Netzwerk-Abgleich in NetBox anlegen", "Netzwerk"),
    ("firewall.manage", "Firewall ändern", "ufw ein-/ausschalten, Regeln, fail2ban entsperren", "Netzwerk"),
    ("settings.manage", "Einstellungen ändern", "Wetter, Dienste, Wake-on-LAN-Geräte, Web-Dienste, Links, Verlauf, Ansehen ohne Login", "Verwaltung"),
    ("terminal", "Web-Terminal", "Volle Shell als Pi-Benutzer (sudo nur mit Passwort)", "Verwaltung"),
    ("users.manage", "Benutzer & Berechtigungen", "Benutzer anlegen, Gruppen und Rechte bearbeiten", "Verwaltung"),
]
ALL_PERMISSIONS = [p[0] for p in PERMISSIONS]

BUILTIN_GROUPS: Dict[str, Dict[str, Any]] = {
    "admin": {"name": "Administrator", "description": "Darf alles – auch Benutzer und Rechte verwalten.",
              "perms": ALL_PERMISSIONS, "locked": True},
    "editor": {"name": "Bearbeiter", "description": "Kann Dienste steuern, Dateien verwalten und Einstellungen ändern – ohne Terminal, Neustart und Benutzerverwaltung.",
               "perms": ["system.view", "logs.view", "files.read", "files.write", "files.share", "notes.edit",
                         "actions.run", "services.control", "network.tools", "netbox.write", "settings.manage"]},
    "user": {"name": "Benutzer", "description": "Sieht alles Wichtige, darf Dateien ansehen und Notizen bearbeiten.",
             "perms": ["system.view", "files.read", "notes.edit"]},
    "viewer": {"name": "Betrachter", "description": "Nur das Dashboard ansehen.", "perms": []},
}

_cache: Dict[str, Any] = {"mtime": None, "cfg": None}
_lock = threading.Lock()


class AuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------
def _migrate(cfg: Dict[str, Any]) -> bool:
    """Single-user auth.json (v1) -> users + groups (v2). Returns True if changed."""
    if "users" in cfg:
        return False
    users = {}
    if cfg.get("password_hash") and cfg.get("username"):
        users[cfg["username"]] = {"salt": cfg["salt"], "password_hash": cfg["password_hash"], "group": "admin",
                                  "created": cfg.get("changed", int(time.time())), "changed": cfg.get("changed", int(time.time())),
                                  "disabled": False, "sv": 1}
    for k in ("username", "salt", "password_hash", "changed"):
        cfg.pop(k, None)
    cfg["users"] = users
    cfg.setdefault("groups", {})
    cfg["version"] = 2
    return True


def load_config() -> Optional[Dict[str, Any]]:
    """auth.json, re-read only when it changes on disk (migrated to v2 on first read)."""
    try:
        mtime = os.path.getmtime(AUTH_FILE)
    except OSError:
        _cache.update(mtime=None, cfg=None)
        return None
    if mtime != _cache["mtime"]:
        try:
            with open(AUTH_FILE) as f:
                cfg = json.load(f)
            if _migrate(cfg):
                _save_config(cfg)
                mtime = os.path.getmtime(AUTH_FILE)
            _cache.update(mtime=mtime, cfg=cfg)
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
    _cache.update(mtime=None)  # re-read on next access


def _cfg_for_write() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(load_config() or {}))  # deep copy
    cfg.setdefault("users", {})
    cfg.setdefault("groups", {})
    cfg.setdefault("secret", secrets.token_hex(32))
    cfg.setdefault("public_view", False)
    cfg["version"] = 2
    return cfg


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg and cfg.get("users"))


def public_view() -> bool:
    cfg = load_config()
    return bool(cfg and cfg.get("public_view"))


def set_public_view(enabled: bool) -> None:
    with _lock:
        cfg = _cfg_for_write()
        cfg["public_view"] = bool(enabled)
        _save_config(cfg)


def rotate_secret() -> None:
    """New signing secret: every session of every user ends."""
    with _lock:
        cfg = _cfg_for_write()
        cfg["secret"] = secrets.token_hex(32)
        _save_config(cfg)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=64 * 1024 * 1024, dklen=32)


MAX_GUEST_DAYS = 365


def _expired(user: Optional[Dict[str, Any]]) -> bool:
    """Guest accounts end at "expires" (epoch seconds); normal accounts have none."""
    return bool(user and user.get("expires") and user["expires"] <= time.time())


def _usable(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and not user.get("disabled") and not _expired(user)


def _check_expires(expires: Optional[int], group: str) -> Optional[int]:
    if not expires:
        return None
    expires = int(expires)
    if expires <= time.time():
        raise AuthError("Das Ablaufdatum muss in der Zukunft liegen.")
    if expires > time.time() + MAX_GUEST_DAYS * 86400:
        raise AuthError(f"Gastzugänge laufen höchstens {MAX_GUEST_DAYS} Tage.")
    if group == "admin":
        raise AuthError("Gastzugänge können keine Administratoren sein.")
    return expires


def _find(cfg: Dict[str, Any], username: str) -> Optional[str]:
    """Stored spelling of a username (case-insensitive lookup)."""
    want = (username or "").strip().casefold()
    return next((u for u in (cfg or {}).get("users", {}) if u.casefold() == want), None)


def verify_login(username: str, password: str) -> Optional[str]:
    """The stored username if the credentials are right (and the account is active)."""
    cfg = load_config()
    if not cfg:
        return None
    name = _find(cfg, username)
    # Always hash once, so timing doesn't reveal whether a username exists
    user = cfg["users"].get(name) if name else None
    salt = bytes.fromhex(user["salt"]) if user else b"\x00" * 16
    actual = _scrypt(password, salt)
    if not _usable(user):
        return None
    return name if hmac.compare_digest(actual, bytes.fromhex(user["password_hash"])) else None


def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")


def _active_admins(cfg: Dict[str, Any], without: Optional[str] = None) -> List[str]:
    return [u for u, d in cfg["users"].items() if d.get("group") == "admin" and _usable(d) and u != without]


def set_password(username: str, password: str) -> None:
    """CLI / first setup: set a password; creates the user (as admin if it is the first one)."""
    _check_password(password)
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        salt = secrets.token_bytes(16)
        if name:
            u = cfg["users"][name]
            u.update(salt=salt.hex(), password_hash=_scrypt(password, salt).hex(), changed=int(time.time()),
                     sv=u.get("sv", 1) + 1, disabled=False)
        else:
            if not USERNAME_RE.match(username):
                raise AuthError("Benutzername: 1–32 Zeichen, nur Buchstaben, Ziffern und . _ -")
            cfg["users"][username] = {"salt": salt.hex(), "password_hash": _scrypt(password, salt).hex(),
                                      "group": "admin" if not _active_admins(cfg) else "user",
                                      "created": int(time.time()), "changed": int(time.time()), "disabled": False, "sv": 1}
        _save_config(cfg)


def create_user(username: str, password: str, group: str, expires: Optional[int] = None) -> Dict[str, Any]:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError("Benutzername: 1–32 Zeichen, nur Buchstaben, Ziffern und . _ -")
    _check_password(password)
    with _lock:
        cfg = _cfg_for_write()
        if _find(cfg, username):
            raise AuthError("Diesen Benutzernamen gibt es schon (Groß-/Kleinschreibung zählt nicht).")
        if group not in all_groups(cfg):
            raise AuthError("Unbekannte Gruppe.")
        if len(cfg["users"]) >= MAX_USERS:
            raise AuthError(f"Höchstens {MAX_USERS} Benutzer.")
        expires = _check_expires(expires, group)
        salt = secrets.token_bytes(16)
        cfg["users"][username] = {"salt": salt.hex(), "password_hash": _scrypt(password, salt).hex(), "group": group,
                                  "created": int(time.time()), "changed": int(time.time()), "disabled": False, "sv": 1}
        if expires:
            cfg["users"][username]["expires"] = expires
        _save_config(cfg)
    return user_info(username)


def update_user(username: str, *, group: Optional[str] = None, disabled: Optional[bool] = None,
                password: Optional[str] = None, logout: bool = False, expires: Optional[int] = None) -> Dict[str, Any]:
    """expires: None = unchanged, 0 = no expiry (normal account), epoch = guest until then."""
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        u = cfg["users"][name]
        if group is not None and group != u["group"]:
            if group not in all_groups(cfg):
                raise AuthError("Unbekannte Gruppe.")
            if u["group"] == "admin" and not _active_admins(cfg, without=name):
                raise AuthError("Das ist der letzte Administrator – erst einen anderen Administrator anlegen.")
            if group == "admin" and u.get("expires") and not _expired(u) and expires is None:
                raise AuthError("Gastzugänge können keine Administratoren sein.")
            u["group"] = group
        if expires is not None:
            if expires == 0:
                u.pop("expires", None)
            else:
                u["expires"] = _check_expires(expires, u["group"])
                u["sv"] = u.get("sv", 1) + 1      # sessions get the new end
        if disabled is not None and bool(disabled) != bool(u.get("disabled")):
            if disabled and u["group"] == "admin" and not _active_admins(cfg, without=name):
                raise AuthError("Der letzte Administrator kann nicht deaktiviert werden.")
            u["disabled"] = bool(disabled)
            u["sv"] = u.get("sv", 1) + 1          # a locked account loses its sessions at once
        if password is not None:
            _check_password(password)
            salt = secrets.token_bytes(16)
            u.update(salt=salt.hex(), password_hash=_scrypt(password, salt).hex(), changed=int(time.time()))
            u["sv"] = u.get("sv", 1) + 1
        if logout:
            u["sv"] = u.get("sv", 1) + 1
        _save_config(cfg)
    return user_info(name)


def delete_user(username: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        if cfg["users"][name]["group"] == "admin" and not _active_admins(cfg, without=name):
            raise AuthError("Der letzte Administrator kann nicht gelöscht werden.")
        del cfg["users"][name]
        _save_config(cfg)


def user_info(username: str) -> Optional[Dict[str, Any]]:
    cfg = load_config()
    name = _find(cfg, username)
    if not name:
        return None
    u = cfg["users"][name]
    return {"username": name, "group": u["group"], "disabled": bool(u.get("disabled")),
            "created": u.get("created"), "changed": u.get("changed"),
            "expires": u.get("expires"), "expired": _expired(u)}


def list_users() -> List[Dict[str, Any]]:
    cfg = load_config() or {}
    return sorted((user_info(u) for u in cfg.get("users", {})), key=lambda x: x["username"].casefold())


def canonical_username(username: str = "") -> str:
    """Stored spelling of a username (sessions always carry this spelling)."""
    cfg = load_config()
    if username:
        return _find(cfg, username) or ""
    users = list((cfg or {}).get("users", {}))
    return users[0] if users else ""


# ---------------------------------------------------------------------------
# Groups & permissions
# ---------------------------------------------------------------------------
def all_groups(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Built-in groups (with saved changes) + custom groups."""
    cfg = cfg if cfg is not None else (load_config() or {})
    saved = cfg.get("groups", {})
    out: Dict[str, Dict[str, Any]] = {}
    for gid, g in BUILTIN_GROUPS.items():
        stored = saved.get(gid, {}) if gid != "admin" else {}
        out[gid] = {"id": gid, "name": stored.get("name", g["name"]), "description": stored.get("description", g["description"]),
                    "perms": ALL_PERMISSIONS if gid == "admin" else [p for p in stored.get("perms", g["perms"]) if p in ALL_PERMISSIONS],
                    "builtin": True, "locked": bool(g.get("locked"))}
    for gid, g in saved.items():
        if gid not in BUILTIN_GROUPS:
            out[gid] = {"id": gid, "name": g.get("name", gid), "description": g.get("description", ""),
                        "perms": [p for p in g.get("perms", []) if p in ALL_PERMISSIONS], "builtin": False, "locked": False}
    return out


def list_groups() -> List[Dict[str, Any]]:
    cfg = load_config() or {}
    counts: Dict[str, int] = {}
    for u in cfg.get("users", {}).values():
        counts[u["group"]] = counts.get(u["group"], 0) + 1
    order = list(BUILTIN_GROUPS)
    groups = list(all_groups(cfg).values())
    groups.sort(key=lambda g: (order.index(g["id"]) if g["id"] in order else 99, g["name"].casefold()))
    return [{**g, "members": counts.get(g["id"], 0)} for g in groups]


def save_group(group_id: Optional[str], name: str, description: str, perms: List[str]) -> Dict[str, Any]:
    name = (name or "").strip()[:40]
    if not name:
        raise AuthError("Bitte einen Gruppennamen angeben.")
    perms = [p for p in ALL_PERMISSIONS if p in set(perms or [])]
    with _lock:
        cfg = _cfg_for_write()
        groups = all_groups(cfg)
        if group_id:
            if group_id not in groups:
                raise AuthError("Unbekannte Gruppe.")
            if groups[group_id]["locked"]:
                raise AuthError("Die Administrator-Gruppe hat immer alle Rechte und kann nicht geändert werden.")
        else:
            base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:24] or "gruppe"
            group_id, n = base if len(base) >= 2 else f"g-{base}", 2
            while group_id in groups:
                group_id = f"{base}-{n}"
                n += 1
        if any(g["name"].casefold() == name.casefold() and gid != group_id for gid, g in groups.items()):
            raise AuthError("Eine Gruppe mit diesem Namen gibt es schon.")
        cfg["groups"][group_id] = {"name": name, "description": (description or "").strip()[:200], "perms": perms}
        _save_config(cfg)
    return next(g for g in list_groups() if g["id"] == group_id)


def delete_group(group_id: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        if group_id in BUILTIN_GROUPS:
            raise AuthError("Eingebaute Gruppen können nicht gelöscht werden (Rechte lassen sich aber ändern).")
        if group_id not in cfg["groups"]:
            raise AuthError("Unbekannte Gruppe.")
        if any(u["group"] == group_id for u in cfg["users"].values()):
            raise AuthError("Die Gruppe hat noch Mitglieder – erst die Benutzer einer anderen Gruppe zuordnen.")
        del cfg["groups"][group_id]
        _save_config(cfg)


def reset_group(group_id: str) -> Dict[str, Any]:
    """Built-in group back to its default permissions."""
    with _lock:
        cfg = _cfg_for_write()
        if group_id not in BUILTIN_GROUPS or group_id == "admin":
            raise AuthError("Nur eingebaute Gruppen lassen sich zurücksetzen.")
        cfg["groups"].pop(group_id, None)
        _save_config(cfg)
    return next(g for g in list_groups() if g["id"] == group_id)


def permissions(username: Optional[str]) -> Set[str]:
    if not username:
        return set()
    cfg = load_config()
    name = _find(cfg, username)
    if not name or not _usable(cfg["users"][name]):
        return set()
    group = all_groups(cfg).get(cfg["users"][name]["group"])
    return set(group["perms"]) if group else set()


AGENT_MODE = False   # set by app.py: an agent trusts the (token-authenticated) main dashboard


def can(username: Optional[str], perm: str) -> bool:
    if AGENT_MODE and username:
        return True
    return perm in permissions(username)


# ---------------------------------------------------------------------------
# Sessions (signed cookie: "username|expiry|version|signature")
# ---------------------------------------------------------------------------
def _sign(secret: str, payload: str) -> str:
    return hmac.new(bytes.fromhex(secret), payload.encode(), hashlib.sha256).hexdigest()


def make_session(username: str, remember: bool) -> tuple:
    cfg = load_config()
    name = _find(cfg, username) or username
    sv = cfg["users"].get(name, {}).get("sv", 1)
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    expires = cfg["users"].get(name, {}).get("expires")
    if expires:
        ttl = max(60, min(ttl, int(expires - time.time())))
    payload = f"{name}|{int(time.time()) + ttl}|{sv}"
    return f"{payload}|{_sign(cfg['secret'], payload)}", ttl


def user_from_token(token: Optional[str]) -> Optional[str]:
    cfg = load_config()
    if not token or not cfg or not cfg.get("secret"):
        return None
    try:
        username, expiry, sv, sig = token.split("|")
        expiry_ts, sv_num = int(expiry), int(sv)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(cfg["secret"], f"{username}|{expiry}|{sv}")):
        return None
    user = cfg.get("users", {}).get(username)
    if expiry_ts < time.time() or not _usable(user) or user.get("sv", 1) != sv_num:
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
    sub.add_parser("set-password", help="Passwort setzen (legt den Benutzer an; der erste wird Administrator)")
    sub.add_parser("users", help="Benutzer und Gruppen anzeigen")
    pv = sub.add_parser("public-view", help="Dashboard ohne Login ansehen (Aktionen bleiben geschützt)")
    pv.add_argument("state", choices=["on", "off"])
    sub.add_parser("status", help="Aktuelle Einstellungen anzeigen")
    args = parser.parse_args()

    if args.cmd in ("status", "users"):
        if not is_configured():
            print("🔓 Kein Benutzer angelegt – das Dashboard ist gesperrt. ./set-password.sh ausführen.")
            return 0
        groups = all_groups()
        for u in list_users():
            state = " (deaktiviert)" if u["disabled"] else ""
            print(f"👤 {u['username']:<20} {groups.get(u['group'], {}).get('name', u['group'])}{state}")
        print(f"👀 Ansehen ohne Login: {'an' if public_view() else 'aus'}")
        return 0

    if args.cmd == "public-view":
        if not is_configured():
            print("❌ Zuerst einen Benutzer anlegen: ./set-password.sh")
            return 1
        set_public_view(args.state == "on")
        print("✅ Ansehen ohne Login: " + ("an (Aktionen brauchen weiterhin Login)" if args.state == "on" else "aus"))
        return 0

    # set-password: an admin that forgot the password can always recover on the Pi
    default = canonical_username() or "admin"
    username = input(f"Benutzername [{default}]: ").strip() or default
    exists = bool(canonical_username(username))
    if not exists and not USERNAME_RE.match(username):
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
    info = user_info(username)
    if exists:
        print(f"✅ Neues Passwort für '{info['username']}' gespeichert. Seine Sitzungen wurden abgemeldet.")
    else:
        print(f"✅ Benutzer '{info['username']}' angelegt ({all_groups()[info['group']]['name']}).")
    if info["group"] != "admin" and input("Zum Administrator machen? [j/N] ").strip().lower() in ("j", "ja", "y", "yes"):
        update_user(username, group="admin")
        print("✅ Ist jetzt Administrator.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
