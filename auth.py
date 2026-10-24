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
- Sessions are HMAC-signed cookies ("user|expiry|version|session id|sig"). Each user has
  a session version: a new password or "log out everywhere" bumps it and ends only that
  user's sessions. Every session is also listed in sessions.json (device, IP, last use)
  and can be ended on its own. There is always at least one active administrator.
- Optional two-factor login (TOTP app + single-use recovery codes, see totp.py) and passkeys
  (webauthn.py) - a passkey counts as both factors.
- Optional e-mail address per user for "Passwort vergessen" (single-use link, 30 minutes).
- Passwords: at least 10 characters, not a well-known one, not the username.
- Failed logins are rate-limited per client IP and per account.

CLI:  python auth.py set-password | users | public-view on|off | status | 2fa-reset <user>
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
MIN_PASSWORD_LENGTH = 10
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")
# The most common passwords of public leak lists (lower case) - refused as new passwords
COMMON_PASSWORDS = {
    "1234567890", "12345678910", "0123456789", "1234512345", "1q2w3e4r5t", "1qaz2wsx3edc", "qwertyuiop", "qwertzuiop",
    "password12", "password123", "password1!", "passwort12", "passwort123", "iloveyou12", "administrator", "admin12345",
    "admin123456", "raspberry1", "raspberrypi", "raspberry123", "letmein123", "welcome123", "willkommen", "willkommen1",
    "sommer2024", "sommer2025", "sommer2026", "winter2024", "winter2025", "winter2026", "hallo12345", "hallo123456",
    "geheim1234", "passwort1!", "abcdefghij", "abc1234567", "qwerty1234", "qwertz1234", "1111111111", "0000000000",
    "changeme123", "dragon1234", "football12", "baseball12", "superman12", "trustno1234", "monkey1234", "starwars12",
}
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
    ("board.edit", "Dashboard gestalten", "Boards, Kategorien, Apps und Widgets der Startseite anlegen, verschieben und ändern", "Dashboard"),
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
    ("changes.approve", "Änderungen freigeben", "Änderungsanträge anderer genehmigen oder ablehnen (Vier-Augen-Prinzip)", "Verwaltung"),
    ("users.manage", "Benutzer & Berechtigungen", "Benutzer anlegen, Gruppen und Rechte bearbeiten", "Verwaltung"),
]
ALL_PERMISSIONS = [p[0] for p in PERMISSIONS]

BUILTIN_GROUPS: Dict[str, Dict[str, Any]] = {
    "admin": {"name": "Administrator", "description": "Darf alles – auch Benutzer und Rechte verwalten.",
              "perms": ALL_PERMISSIONS, "locked": True},
    "editor": {"name": "Bearbeiter", "description": "Kann Dienste steuern, Dateien verwalten und Einstellungen ändern – ohne Terminal, Neustart und Benutzerverwaltung.",
               "perms": ["system.view", "logs.view", "files.read", "files.write", "files.share", "board.edit", "notes.edit",
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


def _check_password(password: str, username: str = "") -> None:
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(low)) < 4:
        raise AuthError("Dieses Passwort ist zu leicht zu erraten – bitte ein anderes wählen.")
    if username and len(username) >= 3 and username.lower() in low:
        raise AuthError("Das Passwort darf den Benutzernamen nicht enthalten.")


def _active_admins(cfg: Dict[str, Any], without: Optional[str] = None) -> List[str]:
    return [u for u, d in cfg["users"].items() if d.get("group") == "admin" and _usable(d) and u != without]


def set_password(username: str, password: str) -> None:
    """CLI / first setup: set a password; creates the user (as admin if it is the first one)."""
    _check_password(password, username)
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
    _check_password(password, username)
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
            _check_password(password, name)
            salt = secrets.token_bytes(16)
            u.update(salt=salt.hex(), password_hash=_scrypt(password, salt).hex(), changed=int(time.time()))
            u.pop("sso_only", None)
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
            "expires": u.get("expires"), "expired": _expired(u), "totp": bool((u.get("totp") or {}).get("enabled")),
            "passkeys": len(u.get("passkeys") or []), "email": u.get("email", ""),
            "sso": bool(u.get("sso")), "sso_only": bool(u.get("sso_only")),
            "last_login": (u.get("last_login") or {}).get("ts"), "last_login_method": (u.get("last_login") or {}).get("method", "")}


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
# Sessions (signed cookie: "username|expiry|version|session id|signature"; cookies from
# before session ids - four parts - stay valid until they expire)
# ---------------------------------------------------------------------------
SID_RE = re.compile(r"^[0-9a-f]{16}$")
TOUCH_INTERVAL = 300
_sessions: Dict[str, Any] = {"loaded": False, "items": {}, "saved": 0.0, "dirty": False}
_sess_lock = threading.Lock()


def _sign(secret: str, payload: str) -> str:
    return hmac.new(bytes.fromhex(secret), payload.encode(), hashlib.sha256).hexdigest()


def _sess_items() -> Dict[str, Dict[str, Any]]:
    if not _sessions["loaded"]:
        try:
            with open(SESSIONS_FILE) as f:
                data = json.load(f)
            _sessions["items"] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _sessions["items"] = {}
        _sessions["loaded"] = True
    return _sessions["items"]


def _sess_save(force: bool = False) -> None:
    """Writes sessions.json; plain "last used" updates at most once a minute."""
    if not force and time.time() - _sessions["saved"] < 60:
        _sessions["dirty"] = True
        return
    now = time.time()
    items = {k: v for k, v in _sess_items().items() if v.get("expires", 0) > now}
    _sessions["items"] = items
    tmp = SESSIONS_FILE + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(items, f)
    os.replace(tmp, SESSIONS_FILE)
    _sessions.update(saved=now, dirty=False)


def make_session(username: str, remember: bool, ip: str = "", agent: str = "", method: str = "password") -> tuple:
    cfg = load_config()
    name = _find(cfg, username) or username
    sv = cfg["users"].get(name, {}).get("sv", 1)
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    expires = cfg["users"].get(name, {}).get("expires")
    if expires:
        ttl = max(60, min(ttl, int(expires - time.time())))
    sid = secrets.token_hex(8)
    now = int(time.time())
    payload = f"{name}|{now + ttl}|{sv}|{sid}"
    _record_login(name, now, method)
    with _sess_lock:
        _sess_items()[sid] = {"user": name, "sv": sv, "created": now, "last_seen": now, "expires": now + ttl,
                              "ip": ip[:64], "agent": agent[:200], "remember": bool(remember), "method": method}
        _sess_save(force=True)
    return f"{payload}|{_sign(cfg['secret'], payload)}", ttl


def _record_login(username: str, ts: int, method: str) -> None:
    """Last sign-in per account (for the access review)."""
    with _lock:
        cfg = _cfg_for_write()
        u = cfg["users"].get(username)
        if u is not None:
            u["last_login"] = {"ts": ts, "method": method}
            _save_config(cfg)


def session_id(token: Optional[str]) -> str:
    parts = (token or "").split("|")
    return parts[3] if len(parts) == 5 and SID_RE.match(parts[3]) else ""


def touch_session(sid: str, ip: str = "") -> None:
    if not sid:
        return
    with _sess_lock:
        s = _sess_items().get(sid)
        if not s:
            return
        now = int(time.time())
        if now - s.get("last_seen", 0) >= TOUCH_INTERVAL or (ip and ip != s.get("ip")):
            s["last_seen"] = now
            if ip:
                s["ip"] = ip[:64]
            _sess_save()
        elif _sessions["dirty"] and time.time() - _sessions["saved"] >= 60:
            _sess_save()


def list_sessions(username: Optional[str] = None) -> List[Dict[str, Any]]:
    """Active sessions (of one user or all), newest use first."""
    cfg = load_config() or {}
    users = cfg.get("users", {})
    now = time.time()
    out = []
    with _sess_lock:
        for sid, s in _sess_items().items():
            u = users.get(s["user"])
            if s.get("expires", 0) <= now or not _usable(u) or u.get("sv", 1) != s.get("sv"):
                continue
            if username and s["user"] != username:
                continue
            out.append({"id": sid, **{k: s.get(k) for k in ("user", "created", "last_seen", "expires", "ip", "agent", "remember", "method")}})
    return sorted(out, key=lambda x: -(x["last_seen"] or 0))


def session_method(sid: str) -> str:
    """How a session was opened: "password", "passkey" or "sso"."""
    with _sess_lock:
        return (_sess_items().get(sid) or {}).get("method", "password") if sid else ""


def revoke_session(sid: str, username: Optional[str] = None) -> bool:
    """End one session (username given: only if it belongs to that user)."""
    with _sess_lock:
        s = _sess_items().get(sid)
        if not s or (username and s["user"] != username):
            return False
        del _sess_items()[sid]
        _sess_save(force=True)
    return True


def known_ips(username: str) -> List[str]:
    with _sess_lock:
        return [s.get("ip", "") for s in _sess_items().values() if s["user"] == username]


def active_user(username: str) -> Optional[str]:
    """The stored spelling of an existing, enabled, not expired user - else None."""
    cfg = load_config()
    if not cfg or not username:
        return None
    key = _find(cfg, username)
    return key if key and _usable(cfg["users"].get(key)) else None


def user_from_token(token: Optional[str]) -> Optional[str]:
    cfg = load_config()
    if not token or not cfg or not cfg.get("secret"):
        return None
    parts = token.split("|")
    try:
        if len(parts) == 5:
            username, expiry, sv, sid, sig = parts
            payload = f"{username}|{expiry}|{sv}|{sid}"
        else:
            username, expiry, sv, sig = parts
            sid, payload = "", f"{username}|{expiry}|{sv}"
        expiry_ts, sv_num = int(expiry), int(sv)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(cfg["secret"], payload)):
        return None
    user = cfg.get("users", {}).get(username)
    if expiry_ts < time.time() or not _usable(user) or user.get("sv", 1) != sv_num:
        return None
    if sid:
        with _sess_lock:
            if sid not in _sess_items():
                return None     # ended in the session list
    return username


# ---------------------------------------------------------------------------
# Two-factor login (TOTP)
# ---------------------------------------------------------------------------
def totp_enabled(username: str) -> bool:
    cfg = load_config() or {}
    name = _find(cfg, username)
    return bool(name and (cfg["users"][name].get("totp") or {}).get("enabled"))


def totp_begin(username: str) -> Dict[str, Any]:
    """New secret waiting for confirmation with a first code (the active one stays until then)."""
    import totp
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        secret = totp.new_secret()
        cfg["users"][name]["totp_pending"] = {"secret": secret, "ts": int(time.time())}
        _save_config(cfg)
    uri = totp.uri(secret, name)
    return {"secret": secret, "uri": uri, "qr": totp.qr_svg(uri)}


def totp_enable(username: str, code: str) -> List[str]:
    """Confirm the pending secret with a code; returns the recovery codes (shown once)."""
    import totp
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        u = cfg["users"].get(name or "", {})
        pending = u.get("totp_pending") or {}
        if not pending or time.time() - pending.get("ts", 0) > 1800:
            raise AuthError("Einrichtung abgelaufen – bitte neu beginnen.")
        step = totp.verify(pending["secret"], code)
        if step is None:
            raise AuthError("Code stimmt nicht – Uhrzeit von Pi und Handy prüfen.")
        codes = totp.recovery_codes()
        u["totp"] = {"enabled": True, "secret": pending["secret"], "last_step": step, "since": int(time.time()),
                     "recovery": [totp.hash_recovery(c) for c in codes]}
        u.pop("totp_pending", None)
        _save_config(cfg)
    return codes


def totp_check(username: str, code: str) -> Optional[str]:
    """"totp" / "recovery" if the code is right (a recovery code is used up), else None."""
    import totp
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        t = (cfg["users"].get(name or "", {}).get("totp") or {})
        if not t.get("enabled"):
            return None
        step = totp.verify(t["secret"], code, t.get("last_step", 0))
        if step is not None:
            t["last_step"] = step
            _save_config(cfg)
            return "totp"
        if totp.is_recovery_format(code):
            h = totp.hash_recovery(code)
            if h in t.get("recovery", []):
                t["recovery"].remove(h)
                _save_config(cfg)
                return "recovery"
    return None


def totp_recovery_left(username: str) -> int:
    cfg = load_config() or {}
    name = _find(cfg, username)
    return len(((cfg.get("users", {}).get(name or "", {})).get("totp") or {}).get("recovery", []))


def totp_new_recovery(username: str) -> List[str]:
    import totp
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        t = cfg["users"].get(name or "", {}).get("totp") or {}
        if not t.get("enabled"):
            raise AuthError("Zwei-Faktor-Anmeldung ist nicht eingerichtet.")
        codes = totp.recovery_codes()
        t["recovery"] = [totp.hash_recovery(c) for c in codes]
        _save_config(cfg)
    return codes


def totp_disable(username: str) -> None:
    """Switch off (own account after confirmation, an admin for a lost phone, or the CLI)."""
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        cfg["users"][name].pop("totp", None)
        cfg["users"][name].pop("totp_pending", None)
        _save_config(cfg)


def reset_factors(username: str) -> None:
    """Lost phone / key: two-factor login and all passkeys off (set up again at the next login)."""
    totp_disable(username)
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if name:
            cfg["users"][name].pop("passkeys", None)
            _save_config(cfg)


# ---------------------------------------------------------------------------
# Passkeys
# ---------------------------------------------------------------------------
MAX_PASSKEYS = 10


def passkey_handle(username: str) -> str:
    """The stable WebAuthn user handle of an account (created on first use)."""
    import webauthn
    cfg = load_config() or {}
    name = _find(cfg, username)
    if not name:
        raise AuthError("Benutzer nicht gefunden.")
    handle = cfg["users"][name].get("webauthn_id")
    if handle:
        return handle
    with _lock:
        cfg = _cfg_for_write()
        u = cfg["users"][name]
        u.setdefault("webauthn_id", webauthn.user_handle())
        _save_config(cfg)
        return u["webauthn_id"]


def passkeys(username: str) -> List[Dict[str, Any]]:
    cfg = load_config() or {}
    name = _find(cfg, username)
    keys = (cfg.get("users", {}).get(name or "", {}).get("passkeys") or [])
    return [{k: p.get(k) for k in ("id", "name", "created", "last_used", "rp_id", "transports")} for p in keys]


def add_passkey(username: str, cred: Dict[str, Any], label: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        keys = cfg["users"][name].setdefault("passkeys", [])
        if len(keys) >= MAX_PASSKEYS:
            raise AuthError(f"Höchstens {MAX_PASSKEYS} Passkeys pro Konto.")
        if any(k["id"] == cred["id"] for u in cfg["users"].values() for k in (u.get("passkeys") or [])):
            raise AuthError("Dieser Passkey ist schon hinterlegt.")
        keys.append({**cred, "name": (label or "Passkey").strip()[:60]})
        _save_config(cfg)


def remove_passkey(username: str, cred_id: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        u = cfg["users"].get(name or "", {})
        before = len(u.get("passkeys") or [])
        u["passkeys"] = [k for k in (u.get("passkeys") or []) if k["id"] != cred_id]
        if len(u["passkeys"]) == before:
            raise AuthError("Passkey nicht gefunden.")
        _save_config(cfg)


def find_passkey(cred_id: str) -> Optional[tuple]:
    """(username, stored credential, user handle) for webauthn.verify_authentication - active users only."""
    cfg = load_config() or {}
    for name, u in cfg.get("users", {}).items():
        for k in u.get("passkeys") or []:
            if k["id"] == cred_id and _usable(u):
                return name, k, u.get("webauthn_id", "")
    return None


def passkey_used(username: str, cred_id: str, sign_count: int) -> None:
    with _lock:
        cfg = _cfg_for_write()
        for k in cfg["users"].get(username, {}).get("passkeys") or []:
            if k["id"] == cred_id:
                k.update(sign_count=sign_count, last_used=int(time.time()))
        _save_config(cfg)


# ---------------------------------------------------------------------------
# Single sign-on (oidc.py): the provider's issuer + subject are stored on the account
# ---------------------------------------------------------------------------
def find_sso(iss: str, sub: str) -> Optional[str]:
    cfg = load_config() or {}
    return next((n for n, u in cfg.get("users", {}).items()
                 if (u.get("sso") or {}).get("iss") == iss and (u.get("sso") or {}).get("sub") == sub), None)


def match_sso_candidate(username: str, email: str) -> Optional[str]:
    """An account not linked yet with the same username, else with the same e-mail address."""
    cfg = load_config() or {}
    users = cfg.get("users", {})
    name = _find(cfg, username) if username else None
    if name and not users[name].get("sso"):
        return name
    want = email.strip().casefold()
    hits = [n for n, u in users.items() if want and (u.get("email") or "").casefold() == want and not u.get("sso")]
    return hits[0] if len(hits) == 1 else None


def link_sso(username: str, iss: str, sub: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        if any((u.get("sso") or {}).get("iss") == iss and (u.get("sso") or {}).get("sub") == sub and n != name
               for n, u in cfg["users"].items()):
            raise AuthError("Dieses SSO-Konto ist schon mit einem anderen Benutzer verknüpft.")
        cfg["users"][name]["sso"] = {"iss": iss, "sub": sub, "since": int(time.time())}
        _save_config(cfg)


def unlink_sso(username: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name or not cfg["users"][name].get("sso"):
            raise AuthError("Benutzer ist nicht per SSO verknüpft.")
        u = cfg["users"][name]
        u.pop("sso", None)
        if u.pop("sso_only", None):
            u["disabled"] = True        # no password anyone knows: off until an admin sets one
            u["sv"] = u.get("sv", 1) + 1
        _save_config(cfg)


def create_sso_user(username: str, group: str, iss: str, sub: str, email: str = "") -> str:
    """New account for a provider identity; its password is random and unknown (SSO only)."""
    if not USERNAME_RE.match(username):
        raise AuthError("Ungültiger Benutzername.")
    with _lock:
        cfg = _cfg_for_write()
        if _find(cfg, username):
            raise AuthError("Diesen Benutzernamen gibt es schon.")
        if group not in all_groups(cfg):
            raise AuthError("Unbekannte Gruppe.")
        if len(cfg["users"]) >= MAX_USERS:
            raise AuthError(f"Höchstens {MAX_USERS} Benutzer.")
        salt = secrets.token_bytes(16)
        now = int(time.time())
        u = {"salt": salt.hex(), "password_hash": _scrypt(secrets.token_urlsafe(32), salt).hex(), "group": group,
             "created": now, "changed": now, "disabled": False, "sv": 1, "sso_only": True,
             "sso": {"iss": iss, "sub": sub, "since": now}}
        if email and EMAIL_RE.match(email):
            u["email"] = email
        cfg["users"][username] = u
        _save_config(cfg)
    return username


def sso_seen(username: str) -> None:
    with _lock:
        cfg = _cfg_for_write()
        u = cfg["users"].get(username) or {}
        if u.get("sso"):
            u["sso"]["last"] = int(time.time())
            _save_config(cfg)


def sso_links() -> List[Dict[str, Any]]:
    cfg = load_config() or {}
    return sorted(({"username": n, "sso": u["sso"], "sso_only": bool(u.get("sso_only"))}
                   for n, u in cfg.get("users", {}).items() if u.get("sso")), key=lambda x: x["username"].casefold())


# ---------------------------------------------------------------------------
# E-mail address and "Passwort vergessen"
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
RESET_TTL = 1800


def set_email(username: str, email: str) -> None:
    email = (email or "").strip()
    if email and not EMAIL_RE.match(email):
        raise AuthError("Ungültige E-Mail-Adresse.")
    with _lock:
        cfg = _cfg_for_write()
        name = _find(cfg, username)
        if not name:
            raise AuthError("Benutzer nicht gefunden.")
        if email:
            cfg["users"][name]["email"] = email
        else:
            cfg["users"][name].pop("email", None)
        _save_config(cfg)


def find_by_login(value: str) -> Optional[str]:
    """Username for "Passwort vergessen": the username or the e-mail address (case-insensitive)."""
    cfg = load_config() or {}
    name = _find(cfg, value)
    if name:
        return name
    want = (value or "").strip().casefold()
    return next((u for u, d in cfg.get("users", {}).items() if want and (d.get("email") or "").casefold() == want), None)


def create_reset(username: str) -> str:
    """A single-use token for the reset link (only its hash is stored)."""
    token = secrets.token_urlsafe(32)
    with _lock:
        cfg = _cfg_for_write()
        cfg["users"][username]["reset"] = {"hash": hashlib.sha256(token.encode()).hexdigest(), "expires": int(time.time()) + RESET_TTL}
        _save_config(cfg)
    return token


def reset_user(token: str) -> Optional[str]:
    """Who the token belongs to, if it is still valid."""
    h = hashlib.sha256((token or "").encode()).hexdigest()
    cfg = load_config() or {}
    for name, u in cfg.get("users", {}).items():
        r = u.get("reset") or {}
        if r.get("hash") and hmac.compare_digest(r["hash"], h) and r.get("expires", 0) > time.time() and _usable(u):
            return name
    return None


def consume_reset(token: str, password: str) -> str:
    name = reset_user(token)
    if not name:
        raise AuthError("Der Link ist abgelaufen oder wurde schon benutzt.")
    _check_password(password, name)
    with _lock:
        cfg = _cfg_for_write()
        u = cfg["users"][name]
        salt = secrets.token_bytes(16)
        u.update(salt=salt.hex(), password_hash=_scrypt(password, salt).hex(), changed=int(time.time()), sv=u.get("sv", 1) + 1)
        u.pop("reset", None)
        u.pop("sso_only", None)
        _save_config(cfg)
    return name


# ---------------------------------------------------------------------------
# Brute-force protection: 5 free attempts per IP, then a growing lockout; per account
# 10 free attempts, then a short lockout (capped, so nobody can lock an admin out for long)
# ---------------------------------------------------------------------------
FREE_ATTEMPTS = 5
ACCOUNT_FREE_ATTEMPTS = 10
_failures: Dict[str, Dict[str, float]] = {}
_account_failures: Dict[str, Dict[str, float]] = {}


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


def account_lockout_remaining(username: str) -> int:
    entry = _account_failures.get((username or "").casefold())
    return max(0, int(entry.get("locked_until", 0) - time.time())) if entry else 0


def record_account_failure(username: str) -> None:
    key = (username or "").casefold()
    if not key:
        return
    entry = _account_failures.setdefault(key, {"count": 0, "locked_until": 0, "first": time.time()})
    if time.time() - entry["first"] > 3600:
        entry.update(count=0, first=time.time())
    entry["count"] += 1
    if entry["count"] >= ACCOUNT_FREE_ATTEMPTS:
        extra = entry["count"] - ACCOUNT_FREE_ATTEMPTS
        entry["locked_until"] = time.time() + min(30 * 2 ** extra, 300)


def record_account_success(username: str) -> None:
    _account_failures.pop((username or "").casefold(), None)


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
    tf = sub.add_parser("2fa-reset", help="Zwei-Faktor-Anmeldung eines Benutzers abschalten (Handy verloren)")
    tf.add_argument("user")
    args = parser.parse_args()

    if args.cmd == "2fa-reset":
        name = canonical_username(args.user)
        if not name:
            print("❌ Benutzer nicht gefunden.")
            return 1
        reset_factors(name)
        print(f"✅ Zwei-Faktor-Anmeldung und Passkeys für '{name}' entfernt – bei der nächsten Anmeldung neu einrichten.")
        return 0

    if args.cmd in ("status", "users"):
        if not is_configured():
            print("🔓 Kein Benutzer angelegt – das Dashboard ist gesperrt. ./set-password.sh ausführen.")
            return 0
        groups = all_groups()
        for u in list_users():
            state = (" (deaktiviert)" if u["disabled"] else "") + (" · 2FA" if u.get("totp") else "")
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
    try:
        _check_password(password, username)
    except AuthError as e:
        print(f"❌ {e}")
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
