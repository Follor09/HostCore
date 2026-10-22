"""
Docker app store: a small catalog of self-hosted apps that run on the Pi (arm64 images).

Installing an app writes a docker-compose.yml into its own folder (default ~/picore-apps/<name>)
with the ports, folders and settings chosen in the form, then runs `docker compose up -d`
as a background job (same job list and output as the compose stacks on the containers page).
After that the app is an ordinary compose stack: update, stop and remove work as for any other.
"""

import json
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional

import psutil

import containers
import settings_store

APPS_DIR = os.path.expanduser("~/picore-apps")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_PORTS = {8000}          # the dashboard itself
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))


class AppStoreError(Exception):
    pass


def _tz() -> str:
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or "Europe/Berlin"
    except OSError:
        link = os.path.realpath("/etc/localtime")
        return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else "Europe/Berlin"


# ---------------------------------------------------------------------------
# Catalog
#   ports:   {key, label, container, default, proto?}          -> "host:container"
#   folders: {key, label, container, default?, ro?, user?}    -> bind mount; default is below the app folder,
#            "user": the form asks for an existing folder on the Pi (e.g. media)
#   env:     {key, label, default?, secret?}                   -> environment variable
#   open:    port key (or literal port) for the "Öffnen" link, path optional
# ---------------------------------------------------------------------------
CATALOG: List[Dict[str, Any]] = [
    {
        "id": "jellyfin", "name": "Jellyfin", "icon": "🎬", "category": "Medien",
        "description": "Eigener Streaming-Server für Filme, Serien und Musik – wie Netflix, nur mit deinen Dateien.",
        "image": "jellyfin/jellyfin:latest", "ram": "≈ 300 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 8096, "default": 8096}],
        "folders": [{"key": "config", "label": "Einstellungen", "container": "/config"},
                    {"key": "cache", "label": "Cache", "container": "/cache"},
                    {"key": "media", "label": "Medien-Ordner", "container": "/media", "user": True, "ro": True, "default": "/media"}],
        "env": [],
        "open": {"port": "web"},
    },
    {
        "id": "pihole", "name": "Pi-hole", "icon": "🚫", "category": "Netzwerk",
        "description": "Werbe- und Tracker-Blocker fürs ganze Heimnetz. Danach im Router den Pi als DNS-Server eintragen.",
        "image": "pihole/pihole:latest", "ram": "≈ 100 MB",
        "ports": [{"key": "dns", "label": "DNS (TCP+UDP)", "container": 53, "default": 53, "proto": "both"},
                  {"key": "web", "label": "Weboberfläche", "container": 80, "default": 8053}],
        "folders": [{"key": "etc", "label": "Einstellungen", "container": "/etc/pihole"}],
        "env": [{"key": "FTLCONF_webserver_api_password", "label": "Admin-Passwort", "secret": True},
                {"key": "FTLCONF_dns_listeningMode", "label": "DNS-Modus", "default": "all", "hidden": True}],
        "open": {"port": "web", "path": "/admin"},
    },
    {
        "id": "adguard", "name": "AdGuard Home", "icon": "🛡️", "category": "Netzwerk",
        "description": "Werbeblocker und DNS-Server mit moderner Oberfläche, DNS-over-HTTPS und Kindersicherung.",
        "image": "adguard/adguardhome:latest", "ram": "≈ 60 MB",
        "ports": [{"key": "dns", "label": "DNS (TCP+UDP)", "container": 53, "default": 53, "proto": "both"},
                  {"key": "setup", "label": "Einrichtung & Weboberfläche", "container": 3000, "default": 3053}],
        "folders": [{"key": "work", "label": "Daten", "container": "/opt/adguardhome/work"},
                    {"key": "conf", "label": "Einstellungen", "container": "/opt/adguardhome/conf"}],
        "env": [],
        "open": {"port": "setup"},
    },
    {
        "id": "uptime-kuma", "name": "Uptime Kuma", "icon": "📈", "category": "Überwachung",
        "description": "Schicke Überwachung für Websites, Server und Dienste mit Benachrichtigungen (Telegram, Discord, E-Mail …).",
        "image": "louislam/uptime-kuma:1", "ram": "≈ 120 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3001, "default": 3001}],
        "folders": [{"key": "data", "label": "Daten", "container": "/app/data"}],
        "env": [],
        "open": {"port": "web"},
    },
    {
        "id": "vaultwarden", "name": "Vaultwarden", "icon": "🔐", "category": "Sicherheit",
        "description": "Passwort-Manager, kompatibel mit den Bitwarden-Apps. Die Weboberfläche braucht HTTPS (z. B. über einen Reverse-Proxy).",
        "image": "vaultwarden/server:latest", "ram": "≈ 30 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 80, "default": 8081}],
        "folders": [{"key": "data", "label": "Daten (Tresor)", "container": "/data"}],
        "env": [{"key": "SIGNUPS_ALLOWED", "label": "Registrierung erlauben (true/false)", "default": "true"}],
        "open": {"port": "web"},
    },
    {
        "id": "homeassistant", "name": "Home Assistant", "icon": "🏠", "category": "Smart Home",
        "description": "Die Zentrale fürs Smart Home: Lampen, Sensoren, Steckdosen und Automationen. Läuft im Host-Netzwerk (Port 8123).",
        "image": "ghcr.io/home-assistant/home-assistant:stable", "ram": "≈ 400 MB",
        "ports": [], "host_network": True,
        "folders": [{"key": "config", "label": "Einstellungen", "container": "/config"}],
        "env": [],
        "open": {"port": 8123},
    },
    {
        "id": "nodered", "name": "Node-RED", "icon": "🧩", "category": "Smart Home",
        "description": "Automationen per Drag & Drop zusammenklicken – verbindet MQTT, HTTP, Home Assistant und vieles mehr.",
        "image": "nodered/node-red:latest", "ram": "≈ 120 MB",
        "ports": [{"key": "web", "label": "Editor", "container": 1880, "default": 1880}],
        "folders": [{"key": "data", "label": "Flows & Daten", "container": "/data", "uid": 1000}],
        "env": [],
        "open": {"port": "web"},
    },
    {
        "id": "grafana", "name": "Grafana", "icon": "📊", "category": "Überwachung",
        "description": "Dashboards und Diagramme für alles mit Zahlen – Prometheus, InfluxDB, Home Assistant …",
        "image": "grafana/grafana-oss:latest", "ram": "≈ 150 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3000, "default": 3000}],
        "folders": [{"key": "data", "label": "Daten", "container": "/var/lib/grafana", "uid": 472}],
        "env": [{"key": "GF_SECURITY_ADMIN_PASSWORD", "label": "Admin-Passwort", "secret": True}],
        "open": {"port": "web"},
    },
    {
        "id": "syncthing", "name": "Syncthing", "icon": "🔁", "category": "Dateien",
        "description": "Ordner zwischen PC, Handy und Pi synchronisieren – ohne Cloud, direkt von Gerät zu Gerät.",
        "image": "syncthing/syncthing:latest", "ram": "≈ 80 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 8384, "default": 8384},
                  {"key": "sync", "label": "Synchronisation (TCP+UDP)", "container": 22000, "default": 22000, "proto": "both"}],
        "folders": [{"key": "data", "label": "Daten & Sync-Ordner", "container": "/var/syncthing"}],
        "env": [{"key": "PUID", "label": "Benutzer-ID", "default": "1000", "hidden": True},
                {"key": "PGID", "label": "Gruppen-ID", "default": "1000", "hidden": True}],
        "open": {"port": "web"},
    },
    {
        "id": "gitea", "name": "Gitea", "icon": "🍵", "category": "Entwicklung",
        "description": "Eigener Git-Server wie GitHub: Repositories, Issues, Pull Requests – leicht genug für den Pi.",
        "image": "gitea/gitea:latest", "ram": "≈ 150 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3000, "default": 3002},
                  {"key": "ssh", "label": "Git über SSH", "container": 22, "default": 2222}],
        "folders": [{"key": "data", "label": "Daten & Repositories", "container": "/data"}],
        "env": [{"key": "USER_UID", "label": "Benutzer-ID", "default": "1000", "hidden": True},
                {"key": "USER_GID", "label": "Gruppen-ID", "default": "1000", "hidden": True}],
        "open": {"port": "web"},
    },
]
_BY_ID = {a["id"]: a for a in CATALOG}


def _listening_ports() -> Dict[int, str]:
    """Ports something on the Pi already listens on (TCP and UDP), with the program if known."""
    out: Dict[int, str] = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return out
    for c in conns:
        if not c.laddr:
            continue
        if c.type == socket.SOCK_STREAM and c.status != psutil.CONN_LISTEN:
            continue
        if c.type == socket.SOCK_DGRAM and c.raddr:
            continue
        name = ""
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        out.setdefault(c.laddr.port, name)
    return out


def _port_busy(port: int) -> bool:
    """Bind test for what the process list can't see (other users' programs without root)."""
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        for addr in ("0.0.0.0", "127.0.0.1"):
            with socket.socket(socket.AF_INET, kind) as s:
                if kind == socket.SOCK_STREAM:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # ignore TIME_WAIT leftovers, not listeners
                try:
                    s.bind((addr, port))
                except PermissionError:
                    return False        # below 1024 without root: can't tell, docker will
                except OSError:
                    return True
    return False


def _installed() -> Dict[str, Dict[str, Any]]:
    """Apps installed from here that still exist as stacks."""
    known = settings_store.get("appstore_installed", {}) or {}
    stacks = {s["name"]: s for s in containers.stacks()}
    return {name: {**info, "stack": stacks.get(name)} for name, info in known.items()}


def catalog() -> Dict[str, Any]:
    forget_missing()
    installed = _installed()
    by_app: Dict[str, List[Dict[str, Any]]] = {}
    for name, info in installed.items():
        s = info.get("stack")
        by_app.setdefault(info.get("app", ""), []).append({
            "name": name, "ports": info.get("ports", {}), "open": info.get("open"),
            "level": s["level"] if s else "missing", "running": s["running"] if s else 0, "total": s["total"] if s else 0,
        })
    apps = []
    for a in CATALOG:
        apps.append({k: a[k] for k in ("id", "name", "icon", "category", "description", "image", "ram")}
                    | {"installed": by_app.get(a["id"], [])})
    import shutil
    return {"apps": apps, "dir": APPS_DIR, "docker": bool(shutil.which("docker")),
            "categories": sorted({a["category"] for a in CATALOG})}


def form(app_id: str) -> Dict[str, Any]:
    """Fields for the install form, with defaults that don't clash with what's running."""
    app = _BY_ID.get(app_id)
    if not app:
        raise AppStoreError("Unbekannte App.")
    used = _listening_ports()
    taken_names = {s["name"] for s in containers.stacks()} | set(settings_store.get("appstore_installed", {}) or {})
    name = app["id"]
    n = 2
    while name in taken_names or os.path.exists(os.path.join(APPS_DIR, name)):
        name = f"{app['id']}-{n}"
        n += 1
    ports = []
    for p in app["ports"]:
        port = p["default"]
        # Suggest the next free port, except for well-known ones (DNS must stay 53)
        if p["default"] > 1024:
            while port in used or port in RESERVED_PORTS or _port_busy(port):
                port += 1
        ports.append({**p, "value": port, "in_use": used.get(p["default"]) if p["default"] in used else None})
    folders = [{**f, "value": f["default"] if f.get("user") else f"./{f['key']}"} for f in app["folders"]]
    env = [{**e, "value": e.get("default", "")} for e in app["env"] if not e.get("hidden")]
    return {"app": {k: app[k] for k in ("id", "name", "icon", "description", "image")},
            "name": name, "ports": ports, "folders": folders, "env": env, "dir": APPS_DIR,
            "host_network": bool(app.get("host_network"))}


def _q(v: Any) -> str:
    """YAML scalar: JSON strings are valid YAML and safely quoted."""
    return json.dumps(str(v), ensure_ascii=False)


def _build(app: Dict[str, Any], name: str, ports: Dict[str, int], folders: Dict[str, str], env: Dict[str, str]) -> str:
    lines = [f"# Installiert über den PiCore App-Store ({app['name']}) am {time.strftime('%d.%m.%Y %H:%M')}",
             "services:", f"  {app['id']}:",
             f"    image: {_q(app['image'])}", f"    container_name: {_q(name)}", "    restart: unless-stopped"]
    if app.get("host_network"):
        lines.append("    network_mode: host")
    port_lines = []
    for p in app["ports"]:
        mapping = f"{ports[p['key']]}:{p['container']}"
        protos = ["/tcp", "/udp"] if p.get("proto") == "both" else [""]
        port_lines += [f"      - {_q(mapping + proto)}" for proto in protos]
    if port_lines:
        lines += ["    ports:"] + port_lines
    lines += ["    volumes:"] + [f"      - {_q(folders[f['key']] + ':' + f['container'] + (':ro' if f.get('ro') else ''))}"
                                  for f in app["folders"]]
    all_env = {"TZ": _tz(), **{e["key"]: e.get("default", "") for e in app["env"] if e.get("hidden")}, **env}
    lines += ["    environment:"] + [f"      {k}: {_q(v)}" for k, v in all_env.items()]
    return "\n".join(lines) + "\n"


def _validate(app_id: str, req: Dict[str, Any], strict: bool = True) -> Dict[str, Any]:
    app = _BY_ID.get(app_id)
    if not app:
        raise AppStoreError("Unbekannte App.")
    name = str(req.get("name", "")).strip().lower()
    if not NAME_RE.match(name):
        raise AppStoreError("Name: nur Kleinbuchstaben, Ziffern und Bindestriche (max. 40 Zeichen).")
    if name in {s["name"] for s in containers.stacks()} or name in (settings_store.get("appstore_installed", {}) or {}):
        raise AppStoreError(f"Es gibt schon einen Stack „{name}“.")
    app_dir = os.path.join(APPS_DIR, name)
    if os.path.exists(app_dir):
        raise AppStoreError(f"Der Ordner {app_dir} existiert schon.")

    used = _listening_ports()
    ports: Dict[str, int] = {}
    for p in app["ports"]:
        try:
            port = int((req.get("ports") or {}).get(p["key"], p["default"]))
        except (TypeError, ValueError):
            raise AppStoreError(f"{p['label']}: ungültiger Port.")
        if not 1 <= port <= 65535:
            raise AppStoreError(f"{p['label']}: Port muss zwischen 1 und 65535 liegen.")
        if port in RESERVED_PORTS:
            raise AppStoreError(f"Port {port} gehört dem Dashboard.")
        if port in used or _port_busy(port):
            who = f" ({used[port]})" if used.get(port) else ""
            raise AppStoreError(f"Port {port} ist schon belegt{who}.")
        if port in ports.values():
            raise AppStoreError(f"Port {port} ist doppelt vergeben.")
        ports[p["key"]] = port

    folders: Dict[str, str] = {}
    for f in app["folders"]:
        if f.get("user"):
            path = os.path.realpath(str((req.get("folders") or {}).get(f["key"], f.get("default", ""))).strip() or "/")
            if not os.path.isdir(path):
                raise AppStoreError(f"{f['label']}: Ordner {path} gibt es nicht.")
            blocked = ("/etc", "/root", "/boot", "/proc", "/sys", "/dev", "/var/lib/pi-dashboard", DASHBOARD_DIR,
                       os.path.expanduser("~/.ssh"), os.path.expanduser("~/.kube"))
            if path == "/" or any(path == b or path.startswith(b + "/") for b in blocked):
                raise AppStoreError(f"{f['label']}: {path} darf nicht eingebunden werden.")
            folders[f["key"]] = path
        else:
            folders[f["key"]] = f"./{f['key']}"

    env: Dict[str, str] = {}
    for e in app["env"]:
        if e.get("hidden"):
            continue
        v = str((req.get("env") or {}).get(e["key"], e.get("default", "")))
        if "\n" in v or len(v) > 200:
            raise AppStoreError(f"{e['label']}: ungültiger Wert.")
        if strict and e.get("secret") and len(v) < 8:
            raise AppStoreError(f"{e['label']}: mindestens 8 Zeichen.")
        env[e["key"]] = v
    return {"app": app, "name": name, "dir": app_dir, "ports": ports, "folders": folders, "env": env}


def preview(app_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    v = _validate(app_id, req, strict=False)   # the preview works before a password is typed
    shown = {k: ("••••••••" if any(e["key"] == k and e.get("secret") for e in v["app"]["env"]) else val)
             for k, val in v["env"].items()}
    return {"compose": _build(v["app"], v["name"], v["ports"], v["folders"], shown), "dir": v["dir"]}


def install(app_id: str, req: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    import shutil
    if not shutil.which("docker"):
        raise AppStoreError("Docker ist nicht installiert.")
    v = _validate(app_id, req)
    app = v["app"]
    os.makedirs(v["dir"], mode=0o750)
    for f in app["folders"]:
        if not f.get("user"):
            sub = os.path.join(v["dir"], f["key"])
            os.makedirs(sub, exist_ok=True)
            if f.get("uid") is not None and f["uid"] != os.getuid():
                # Image runs as another user: make the folder writable for it
                os.chmod(sub, 0o777)
    compose = os.path.join(v["dir"], "docker-compose.yml")
    with open(compose, "w") as fh:
        fh.write(_build(app, v["name"], v["ports"], v["folders"], v["env"]))
    os.chmod(compose, 0o600)   # may contain passwords

    open_cfg = app.get("open") or {}
    open_port = v["ports"].get(open_cfg.get("port")) if isinstance(open_cfg.get("port"), str) else open_cfg.get("port")
    installed = settings_store.get("appstore_installed", {}) or {}
    installed[v["name"]] = {"app": app["id"], "ts": int(time.time()), "user": user, "dir": v["dir"], "ports": v["ports"],
                            "open": {"port": open_port, "path": open_cfg.get("path", "")} if open_port else None}
    settings_store.set("appstore_installed", installed)
    return containers.compose_job(v["name"], [compose], v["dir"], "install", user)


def forget_missing() -> None:
    """Drop apps whose folder was removed by hand."""
    installed = settings_store.get("appstore_installed", {}) or {}
    keep = {n: i for n, i in installed.items() if os.path.isdir(i.get("dir", ""))}
    if len(keep) != len(installed):
        settings_store.set("appstore_installed", keep)
