"""
Docker app store: a curated list of recommended apps plus the LinuxServer.io catalog
(api.linuxserver.io - around 200 images with ports, folders, settings and devices),
filtered to images that exist for this server's architecture.

The LinuxServer.io catalog is fetched at most once a day and kept in appstore_cache.json,
so the store keeps working offline. Logos are fetched by the server and cached in
appstore_logos/, the browser never talks to GitHub.

Installing an app writes a docker-compose.yml into its own folder (default ~/picore-apps/<name>)
with the ports, folders and settings chosen in the form, then runs `docker compose up -d`
as a background job (same job list and output as the compose stacks on the containers page).
After that the app is an ordinary compose stack: update, stop and remove work as for any other.
"""

import json
import logging
import os
import platform
import re
import socket
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
import psutil

import containers
import settings_store

APPS_DIR = os.path.expanduser("~/picore-apps")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_PORTS = {8000}          # the dashboard itself
KEEP_LOW_PORTS = {21, 22, 25, 53, 67, 69, 123, 137, 138, 139, 161, 389, 445, 514, 636, 853, 873, 993}
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


LSIO_URL = "https://api.linuxserver.io/api/v1/images?include_config=true&include_deprecated=false"
CACHE_FILE = os.path.join(DASHBOARD_DIR, "appstore_cache.json")
LOGO_DIR = os.path.join(DASHBOARD_DIR, "appstore_logos")
REFRESH_SECONDS = 24 * 3600
LOGO_HOSTS = ("raw.githubusercontent.com", "github.com", "avatars.githubusercontent.com")
LOGO_MAX_BYTES = 512 * 1024

logger = logging.getLogger("pi_dashboard.appstore")


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
#            "user": the form asks for an existing folder on the server (e.g. media)
#   env:     {key, label, default?, secret?}                   -> environment variable
#   open:    port key (or literal port) for the "Öffnen" link, path optional
# ---------------------------------------------------------------------------
CATALOG: List[Dict[str, Any]] = [
    {
        "id": "jellyfin", "name": "Jellyfin", "category": "Medien",
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
        "id": "pihole", "name": "Pi-hole", "category": "Netzwerk",
        "description": "Werbe- und Tracker-Blocker fürs ganze Heimnetz. Danach im Router den Server als DNS-Server eintragen.",
        "image": "pihole/pihole:latest", "ram": "≈ 100 MB",
        "ports": [{"key": "dns", "label": "DNS (TCP+UDP)", "container": 53, "default": 53, "proto": "both"},
                  {"key": "web", "label": "Weboberfläche", "container": 80, "default": 8053}],
        "folders": [{"key": "etc", "label": "Einstellungen", "container": "/etc/pihole"}],
        "env": [{"key": "FTLCONF_webserver_api_password", "label": "Admin-Passwort", "secret": True},
                {"key": "FTLCONF_dns_listeningMode", "label": "DNS-Modus", "default": "all", "hidden": True}],
        "open": {"port": "web", "path": "/admin"},
    },
    {
        "id": "adguard", "name": "AdGuard Home", "category": "Netzwerk",
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
        "id": "uptime-kuma", "name": "Uptime Kuma", "category": "Überwachung",
        "description": "Schicke Überwachung für Websites, Server und Dienste mit Benachrichtigungen (Telegram, Discord, E-Mail …).",
        "image": "louislam/uptime-kuma:1", "ram": "≈ 120 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3001, "default": 3001}],
        "folders": [{"key": "data", "label": "Daten", "container": "/app/data"}],
        "env": [],
        "open": {"port": "web"},
    },
    {
        "id": "vaultwarden", "name": "Vaultwarden", "category": "Sicherheit",
        "description": "Passwort-Manager, kompatibel mit den Bitwarden-Apps. Die Weboberfläche braucht HTTPS (z. B. über einen Reverse-Proxy).",
        "image": "vaultwarden/server:latest", "ram": "≈ 30 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 80, "default": 8081}],
        "folders": [{"key": "data", "label": "Daten (Tresor)", "container": "/data"}],
        "env": [{"key": "SIGNUPS_ALLOWED", "label": "Registrierung erlauben (true/false)", "default": "true"}],
        "open": {"port": "web"},
    },
    {
        "id": "homeassistant", "name": "Home Assistant", "category": "Smart Home",
        "description": "Die Zentrale fürs Smart Home: Lampen, Sensoren, Steckdosen und Automationen. Läuft im Host-Netzwerk (Port 8123).",
        "image": "ghcr.io/home-assistant/home-assistant:stable", "ram": "≈ 400 MB",
        "ports": [], "host_network": True,
        "folders": [{"key": "config", "label": "Einstellungen", "container": "/config"}],
        "env": [],
        "open": {"port": 8123},
    },
    {
        "id": "nodered", "name": "Node-RED", "category": "Smart Home",
        "description": "Automationen per Drag & Drop zusammenklicken – verbindet MQTT, HTTP, Home Assistant und vieles mehr.",
        "image": "nodered/node-red:latest", "ram": "≈ 120 MB",
        "ports": [{"key": "web", "label": "Editor", "container": 1880, "default": 1880}],
        "folders": [{"key": "data", "label": "Flows & Daten", "container": "/data", "uid": 1000}],
        "env": [],
        "open": {"port": "web"},
    },
    {
        "id": "grafana", "name": "Grafana", "category": "Überwachung",
        "description": "Dashboards und Diagramme für alles mit Zahlen – Prometheus, InfluxDB, Home Assistant …",
        "image": "grafana/grafana-oss:latest", "ram": "≈ 150 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3000, "default": 3000}],
        "folders": [{"key": "data", "label": "Daten", "container": "/var/lib/grafana", "uid": 472}],
        "env": [{"key": "GF_SECURITY_ADMIN_PASSWORD", "label": "Admin-Passwort", "secret": True}],
        "open": {"port": "web"},
    },
    {
        "id": "syncthing", "name": "Syncthing", "category": "Dateien",
        "description": "Ordner zwischen PC, Handy und Server synchronisieren – ohne Cloud, direkt von Gerät zu Gerät.",
        "image": "syncthing/syncthing:latest", "ram": "≈ 80 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 8384, "default": 8384},
                  {"key": "sync", "label": "Synchronisation (TCP+UDP)", "container": 22000, "default": 22000, "proto": "both"}],
        "folders": [{"key": "data", "label": "Daten & Sync-Ordner", "container": "/var/syncthing"}],
        "env": [{"key": "PUID", "label": "Benutzer-ID", "default": "1000", "hidden": True},
                {"key": "PGID", "label": "Gruppen-ID", "default": "1000", "hidden": True}],
        "open": {"port": "web"},
    },
    {
        "id": "gitea", "name": "Gitea", "category": "Entwicklung",
        "description": "Eigener Git-Server wie GitHub: Repositories, Issues, Pull Requests – leicht genug für den Server.",
        "image": "gitea/gitea:latest", "ram": "≈ 150 MB",
        "ports": [{"key": "web", "label": "Weboberfläche", "container": 3000, "default": 3002},
                  {"key": "ssh", "label": "Git über SSH", "container": 22, "default": 2222}],
        "folders": [{"key": "data", "label": "Daten & Repositories", "container": "/data"}],
        "env": [{"key": "USER_UID", "label": "Benutzer-ID", "default": "1000", "hidden": True},
                {"key": "USER_GID", "label": "Gruppen-ID", "default": "1000", "hidden": True}],
        "open": {"port": "web"},
    },
]
# The curated apps in the same shape as the catalog ones (see _lsio_app)
def _curated_app(a: Dict[str, Any]) -> Dict[str, Any]:
    ports = [{"key": p["key"], "label": p["label"], "container": p["container"], "proto": p.get("proto", "tcp"),
              "default": p["default"], "optional": False} for p in a["ports"]]
    folders = [{"key": f["key"], "label": f["label"], "container": f["container"], "ro": bool(f.get("ro")),
                "default": f.get("default") if f.get("user") else f"./{f['key']}", "user": bool(f.get("user")),
                "optional": False, "uid": f.get("uid")} for f in a["folders"]]
    env = [{"key": e["key"], "label": e["label"], "default": e.get("default", ""), "optional": False,
            "secret": bool(e.get("secret")), "hidden": bool(e.get("hidden"))} for e in a["env"]]
    return {"id": a["id"], "name": a["name"], "source": "curated", "group": a["category"], "categories": [a["category"]],
            "description": a["description"], "image": a["image"], "logo": "", "project_url": "", "setup_url": "",
            "ram": a.get("ram", ""), "pulls": None, "updated": "", "ports": ports, "folders": folders, "env": env,
            "devices": [], "host_network": bool(a.get("host_network")), "privileged": False, "caps": [], "security_opt": [],
            "shm_size": "", "open": a.get("open") or {}}


CURATED = [_curated_app(a) for a in CATALOG]

# LinuxServer.io categories -> the store's groups
GROUPS = {
    "Medien": ["Media Servers", "Music", "Audiobooks", "Media Management", "Media Tools", "Photos", "Books", "Video Editor",
               "Media Requesters", "Indexers", "Downloaders", "Audio Processing"],
    "Netzwerk": ["Network", "DNS", "VPN", "Reverse Proxy", "FTP"],
    "Überwachung": ["Monitoring", "Administration", "Docker", "Dashboard"],
    "Dateien": ["File Sharing", "Storage", "Backup", "Cloud", "Documents"],
    "Entwicklung": ["Programming", "Databases", "Web Tools", "Machine Learning", "AI", "Science"],
    "Smart Home": ["Home Automation", "Automation", "3D Printing"],
    "Sicherheit": ["Security", "Password Manager"],
    "Kommunikation": ["Chat", "IRC", "Email", "Social", "RSS"],
    "Desktop & Kreativ": ["Web Browser", "Remote Desktop", "Image Editor", "3D Modeling"],
    "Büro & Alltag": ["Content Management", "Business", "Finance", "Recipes", "Family"],
    "Spiele": ["Games"],
}
_GROUP_OF = {cat: g for g, cats in GROUPS.items() for cat in cats}
# Catalog entries that the curated list already covers with the upstream image
CURATED_DUPLICATES = {"jellyfin", "homeassistant", "syncthing"}
WEB_HINT = re.compile(r"web\s*ui|webui|web interface|http|gui|dashboard", re.I)

_state: Dict[str, Any] = {"data": None, "loading": False, "error": ""}
_state_lock = threading.Lock()


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("aarch64", "arm64") else "x86_64" if m in ("x86_64", "amd64") else m


def _read_cache() -> Optional[Dict[str, Any]]:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("images"), list) else None
    except (OSError, ValueError):
        return None


def refresh() -> Dict[str, Any]:
    """Fetch the LinuxServer.io catalog now (blocking) and store it."""
    try:
        res = httpx.get(LSIO_URL, timeout=30, follow_redirects=True, headers={"User-Agent": "HostCore-Dashboard"})
        res.raise_for_status()
        images = res.json()["data"]["repositories"]["linuxserver"]
        if not isinstance(images, list) or not images:
            raise ValueError("leerer Katalog")
        data = {"fetched": int(time.time()), "images": images}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
        with _state_lock:
            _state.update(data=data, error="")
        logger.info(f"App store: {len(images)} LinuxServer.io images loaded")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as e:
        with _state_lock:
            _state["error"] = f"Katalog von LinuxServer.io nicht erreichbar ({type(e).__name__})."
        logger.warning(f"App store refresh failed: {e}")
    finally:
        with _state_lock:
            _state["loading"] = False
    return _state


def _catalog_data() -> Optional[Dict[str, Any]]:
    """Cached catalog; starts a background refresh when it is missing or older than a day."""
    with _state_lock:
        if _state["data"] is None:
            _state["data"] = _read_cache()
        data = _state["data"]
        stale = not data or time.time() - data.get("fetched", 0) > REFRESH_SECONDS
        if stale and not _state["loading"]:
            _state["loading"] = True
            threading.Thread(target=refresh, daemon=True).start()
    return data


def _num(text: Any) -> Optional[int]:
    m = re.match(r"^\s*(\d{1,5})", str(text or ""))
    return int(m.group(1)) if m else None


def _lsio_app(i: Dict[str, Any], arch: str) -> Optional[Dict[str, Any]]:
    cfg = i.get("config") or {}
    if not cfg or i.get("deprecated") or i["name"] in CURATED_DUPLICATES:
        return None
    if not any(a.get("arch") == arch for a in i.get("architectures") or []):
        return None
    cats = [c.strip() for c in (i.get("category") or "").split(",") if c.strip()]
    ports = []
    for n, p in enumerate(cfg.get("ports") or []):
        container, external = _num(p.get("internal")), _num(p.get("external"))
        if not container:
            continue
        ports.append({"key": f"p{n}", "label": p.get("desc") or "", "container": container,
                      "proto": "udp" if "/udp" in str(p.get("internal")) else "tcp", "default": external or container,
                      "optional": bool(p.get("optional"))})
    folders = []
    for n, v in enumerate(cfg.get("volumes") or []):
        path = str(v.get("path") or "")
        if not path.startswith("/"):
            continue
        if path in ("/var/run/docker.sock", "/run/docker.sock"):
            continue    # Docker access is root on the server: never offered as a checkbox
        sub = re.sub(r"[^\w.-]+", "-", path.strip("/")) or f"v{n}"
        # Real host paths (/lib/modules, /dev/shm …) are kept; "/path/to/…" placeholders become a subfolder
        host = str(v.get("host_path") or "")
        default = host if host.startswith("/") and "/path/to" not in host and os.path.exists(host) else f"./{sub}"
        folders.append({"key": f"v{n}", "label": v.get("desc") or "", "container": path, "ro": False,
                        "default": default, "user": path != "/config", "optional": bool(v.get("optional")), "uid": None})
    env = []
    for e in cfg.get("env_vars") or []:
        name = str(e.get("name") or "")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            continue
        hidden = name in ("PUID", "PGID", "TZ")
        env.append({"key": name, "label": e.get("desc") or name, "default": "" if e.get("optional") else str(e.get("value") or ""),
                    "example": str(e.get("value") or ""), "optional": bool(e.get("optional")),
                    "secret": bool(re.search(r"PASS|SECRET|TOKEN|KEY", name)) and not e.get("optional"), "hidden": hidden})
    devices = [{"key": f"d{n}", "path": d.get("path"), "host_path": d.get("host_path") or d.get("path"), "label": d.get("desc") or "",
                "optional": bool(d.get("optional"))}
               for n, d in enumerate(cfg.get("devices") or []) if str(d.get("path") or "").startswith("/dev/")]
    caps = []
    for c in cfg.get("caps") or []:
        name = c.get("cap_add") if isinstance(c, dict) else c
        if name and re.match(r"^[A-Z_]+$", str(name)):
            caps.append({"name": name, "optional": bool(c.get("optional")) if isinstance(c, dict) else False})
    sec = [{"value": str(x.get("compose_var") or "").replace(":", "=", 1), "optional": bool(x.get("optional"))}
           for x in cfg.get("security_opt") or [] if x.get("compose_var")]
    shm = next((str(c.get("value")) for c in cfg.get("custom") or [] if c.get("name_compose") == "shm_size"
                and re.match(r"^\d+[kmg]b?$", str(c.get("value")), re.I)), "")
    web = next((p for p in ports if not p["optional"] and WEB_HINT.search(p["label"])), None) or next((p for p in ports if not p["optional"]), None)
    return {
        "id": f"lsio-{i['name']}", "name": i["name"], "source": "lsio",
        "group": next((_GROUP_OF[c] for c in cats if c in _GROUP_OF), "Sonstiges"), "categories": cats,
        "description": (i.get("description") or "").strip(), "image": f"lscr.io/linuxserver/{i['name']}:latest",
        "logo": i.get("project_logo") or "", "project_url": i.get("project_url") or "", "setup_url": cfg.get("application_setup") or "",
        "ram": "", "pulls": i.get("monthly_pulls"), "updated": (i.get("version_timestamp") or "")[:10],
        "ports": ports, "folders": folders, "env": env, "devices": devices,
        "host_network": cfg.get("networking") == "host", "privileged": bool(cfg.get("privileged")),
        "caps": caps, "security_opt": sec, "shm_size": shm,
        "open": {"port": web["key"]} if web else {},
    }


def _apps() -> List[Dict[str, Any]]:
    data = _catalog_data()
    arch = _arch()
    lsio = []
    for i in (data or {}).get("images", []):
        # One entry with an unexpected shape must not take the whole store down
        try:
            a = _lsio_app(i, arch) if isinstance(i, dict) else None
        except Exception as e:
            logger.warning(f"App store: skipping {i.get('name', '?') if isinstance(i, dict) else '?'} ({type(e).__name__}: {e})")
            a = None
        if a:
            lsio.append(a)
    return CURATED + sorted(lsio, key=lambda a: -(a["pulls"] or 0) if isinstance(a["pulls"], (int, float)) else 0)


def _app(app_id: str) -> Dict[str, Any]:
    app = next((a for a in _apps() if a["id"] == app_id), None)
    if not app:
        raise AppStoreError("Unbekannte App.")
    return app


def _advanced(app: Dict[str, Any]) -> List[str]:
    """Settings that give the container more than usual - shown as a warning."""
    out = []
    if app["privileged"]:
        out.append("Privilegierter Modus (voller Zugriff auf den Server)")
    if app["host_network"]:
        out.append("Host-Netzwerk (nutzt die Ports des Servers direkt)")
    for c in app["caps"]:
        if not c["optional"]:
            out.append(f"Zusatzrecht {c['name']}")
    for d in app["devices"]:
        if not d["optional"]:
            out.append(f"Gerät {d['path']}")
    for s in app["security_opt"]:
        if not s["optional"]:
            out.append(f"Sicherheitsoption {s['value']}")
    return out


def _listening_ports() -> Dict[int, str]:
    """Ports something on the server already listens on (TCP and UDP), with the program if known."""
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
    installed: Dict[str, Dict[str, Any]] = {}
    installed_error = ""
    try:
        forget_missing()
        installed = _installed()
    except Exception as e:   # e.g. docker compose not answering: the catalog still works
        logger.warning(f"App store: installed apps unknown ({type(e).__name__}: {e})")
        installed_error = f"Installierte Apps nicht ermittelbar ({type(e).__name__})."
    by_app: Dict[str, List[Dict[str, Any]]] = {}
    for name, info in installed.items():
        s = info.get("stack")
        by_app.setdefault(info.get("app", ""), []).append({
            "name": name, "ports": info.get("ports", {}), "open": info.get("open"),
            "level": s["level"] if s else "missing", "running": s["running"] if s else 0, "total": s["total"] if s else 0,
        })
    apps = []
    for a in _apps():
        apps.append({"id": a["id"], "name": a["name"], "source": a["source"], "group": a["group"], "categories": a["categories"],
                     "description": a["description"][:400], "image": a["image"], "ram": a["ram"], "pulls": a["pulls"],
                     "updated": a["updated"], "logo": bool(a["logo"]), "advanced": bool(_advanced(a)),
                     "installed": by_app.get(a["id"], [])})
    import shutil
    data = _state.get("data") or {}
    groups = [g for g in list(GROUPS) + ["Sonstiges"] if any(a["group"] == g for a in apps)]
    for g in sorted({a["group"] for a in CURATED}):
        if g not in groups:
            groups.append(g)
    return {"apps": apps, "dir": APPS_DIR, "docker": bool(shutil.which("docker")), "groups": groups, "arch": _arch(),
            "catalog": {"fetched": data.get("fetched"), "count": sum(1 for a in apps if a["source"] == "lsio"),
                        "loading": _state["loading"], "error": _state["error"] or installed_error}}


def form(app_id: str) -> Dict[str, Any]:
    """Fields for the install form, with defaults that don't clash with what's running."""
    app = _app(app_id)
    used = _listening_ports()
    taken_names = {s["name"] for s in containers.stacks()} | set(settings_store.get("appstore_installed", {}) or {})
    base = re.sub(r"[^a-z0-9-]+", "-", app["name"].lower()).strip("-")[:30] or "app"
    name, n = base, 2
    while name in taken_names or os.path.exists(os.path.join(APPS_DIR, name)):
        name = f"{base}-{n}"
        n += 1
    ports, planned = [], set()
    for p in app["ports"]:
        port = p["default"]
        # Web ports below 1024 (80, 443 …) are usually taken: suggest 8080, 8443 … instead.
        # Protocol ports that clients expect at their number (DNS, DHCP, NTP …) stay.
        if port < 1024 and port not in KEEP_LOW_PORTS:
            port += 8000
        if port > 1024:
            while port in used or port in RESERVED_PORTS or port in planned or _port_busy(port):
                port += 1
        planned.add(port)
        ports.append({**p, "value": port, "in_use": used.get(p["default"]) if p["default"] in used else None})
    return {"app": {k: app[k] for k in ("id", "name", "source", "description", "image", "project_url", "setup_url", "categories")},
            "name": name, "ports": ports, "folders": app["folders"], "env": [e for e in app["env"] if not e["hidden"]],
            "devices": app["devices"], "dir": APPS_DIR, "host_network": app["host_network"], "advanced": _advanced(app),
            "logo": bool(app["logo"])}


def _q(v: Any) -> str:
    """YAML scalar: JSON strings are valid YAML and safely quoted."""
    return json.dumps(str(v), ensure_ascii=False)


def _build(v: Dict[str, Any]) -> str:
    app = v["app"]
    service = re.sub(r"[^a-z0-9-]+", "-", app["name"].lower()).strip("-") or "app"
    lines = [f"# Installiert über den HostCore App-Store ({app['name']}) am {time.strftime('%d.%m.%Y %H:%M')}",
             "services:", f"  {service}:",
             f"    image: {_q(app['image'])}", f"    container_name: {_q(v['name'])}", "    restart: unless-stopped"]
    if app["host_network"]:
        lines.append("    network_mode: host")
    if app["privileged"]:
        lines.append("    privileged: true")
    if app["shm_size"]:
        lines.append(f"    shm_size: {_q(app['shm_size'])}")
    port_lines = []
    if not app["host_network"]:
        for p in app["ports"]:
            host = v["ports"].get(p["key"])
            if host is None:
                continue
            protos = ["/tcp", "/udp"] if p["proto"] == "both" else ["/udp"] if p["proto"] == "udp" else [""]
            port_lines += [f"      - {_q(str(host) + ':' + str(p['container']) + proto)}" for proto in protos]
    if port_lines:
        lines += ["    ports:"] + port_lines
    vols = [f"      - {_q(path + ':' + f['container'] + (':ro' if f['ro'] else ''))}"
            for f in app["folders"] for path in [v["folders"].get(f["key"])] if path]
    if vols:
        lines += ["    volumes:"] + vols
    devices = [f"      - {_q(d['host_path'] + ':' + d['path'])}" for d in app["devices"] if v["devices"].get(d["key"])]
    if devices:
        lines += ["    devices:"] + devices
    caps = [c["name"] for c in app["caps"] if not c["optional"]]
    if caps:
        lines += ["    cap_add:"] + [f"      - {_q(c)}" for c in caps]
    sec = [s["value"] for s in app["security_opt"] if not s["optional"]]
    if sec:
        lines += ["    security_opt:"] + [f"      - {_q(s)}" for s in sec]
    all_env = {"TZ": _tz()}
    for e in app["env"]:
        if e["key"] in ("PUID", "PGID") and app["source"] == "lsio":
            all_env[e["key"]] = str(os.getuid() if e["key"] == "PUID" else os.getgid())
        elif e["hidden"] and e["key"] != "TZ":
            all_env[e["key"]] = e["default"]
    all_env.update(v["env"])
    lines += ["    environment:"] + [f"      {k}: {_q(val)}" for k, val in all_env.items()]
    return "\n".join(lines) + "\n"


_BLOCKED_MOUNTS = ("/etc", "/root", "/boot", "/proc", "/sys", "/dev", "/var/lib/pi-dashboard", "/var/run", "/run")


def _check_folder(label: str, value: str) -> str:
    value = value.strip()
    if value.startswith("./"):
        rel = value[2:]
        if not re.match(r"^[\w.-]+(/[\w.-]+)*$", rel) or ".." in rel.split("/"):
            raise AppStoreError(f"{label}: ungültiger Unterordner.")
        return "./" + rel
    path = os.path.realpath(value or "/")
    if not os.path.isdir(path):
        raise AppStoreError(f"{label}: Ordner {path} gibt es nicht (oder ./unterordner für einen neuen Ordner im App-Verzeichnis).")
    blocked = _BLOCKED_MOUNTS + (DASHBOARD_DIR, os.path.expanduser("~/.ssh"), os.path.expanduser("~/.kube"))
    if path == "/" or any(path == b or path.startswith(b + "/") for b in blocked):
        raise AppStoreError(f"{label}: {path} darf nicht eingebunden werden.")
    return path


def _validate(app_id: str, req: Dict[str, Any], strict: bool = True) -> Dict[str, Any]:
    app = _app(app_id)
    name = str(req.get("name", "")).strip().lower()
    if not NAME_RE.match(name):
        raise AppStoreError("Name: nur Kleinbuchstaben, Ziffern und Bindestriche (max. 40 Zeichen).")
    if name in {s["name"] for s in containers.stacks()} or name in (settings_store.get("appstore_installed", {}) or {}):
        raise AppStoreError(f"Es gibt schon einen Stack „{name}“.")
    app_dir = os.path.join(APPS_DIR, name)
    if os.path.exists(app_dir):
        raise AppStoreError(f"Der Ordner {app_dir} existiert schon.")

    used = _listening_ports()
    ports: Dict[str, Optional[int]] = {}
    taken: Dict[tuple, str] = {}
    for p in app["ports"]:
        raw = (req.get("ports") or {}).get(p["key"], p["default"] if not p["optional"] else None)
        label = p["label"] or f"Port {p['container']}"
        if raw in (None, "", 0, False):
            if not p["optional"]:
                raise AppStoreError(f"{label}: Port fehlt.")
            continue
        try:
            port = int(raw)
        except (TypeError, ValueError):
            raise AppStoreError(f"{label}: ungültiger Port.")
        if not 1 <= port <= 65535:
            raise AppStoreError(f"{label}: Port muss zwischen 1 und 65535 liegen.")
        if port in RESERVED_PORTS:
            raise AppStoreError(f"Port {port} gehört dem Dashboard.")
        protos = ("tcp", "udp") if p["proto"] == "both" else (p["proto"],)
        if not app["host_network"] and (port in used or (p["proto"] != "udp" and _port_busy(port))):
            who = f" ({used[port]})" if used.get(port) else ""
            raise AppStoreError(f"Port {port} ist schon belegt{who}.")
        for proto in protos:
            if (port, proto) in taken:
                raise AppStoreError(f"Port {port}/{proto} ist doppelt vergeben.")
            taken[(port, proto)] = p["key"]
        ports[p["key"]] = port

    folders: Dict[str, str] = {}
    for f in app["folders"]:
        label = f["label"][:60] or f["container"]
        if not f["user"]:
            folders[f["key"]] = f["default"]
            continue
        value = str((req.get("folders") or {}).get(f["key"], "" if f["optional"] else f["default"]) or "").strip()
        if not value:
            if not f["optional"]:
                raise AppStoreError(f"{label}: Ordner fehlt.")
            continue
        folders[f["key"]] = _check_folder(label, value)

    env: Dict[str, str] = {}
    for e in app["env"]:
        if e["hidden"]:
            continue
        v = str((req.get("env") or {}).get(e["key"], e["default"]))
        if "\n" in v or len(v) > 500:
            raise AppStoreError(f"{e['key']}: ungültiger Wert.")
        if not v and e["optional"]:
            continue
        if strict and e.get("secret") and len(v) < 8:
            raise AppStoreError(f"{e['label'][:60]}: mindestens 8 Zeichen.")
        env[e["key"]] = v

    devices = {}
    for d in app["devices"]:
        on = bool((req.get("devices") or {}).get(d["key"], not d["optional"]))
        if on and not os.path.exists(d["host_path"]):
            if d["optional"]:
                continue
            raise AppStoreError(f"Gerät {d['host_path']} gibt es auf diesem Server nicht.")
        devices[d["key"]] = on
    return {"app": app, "name": name, "dir": app_dir, "ports": ports, "folders": folders, "env": env, "devices": devices}


def preview(app_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    v = _validate(app_id, req, strict=False)   # the preview works before a password is typed
    secrets = {e["key"] for e in v["app"]["env"] if e.get("secret") or re.search(r"PASS|SECRET|TOKEN", e["key"])}
    shown = {**v, "env": {k: ("••••••••" if k in secrets and val else val) for k, val in v["env"].items()}}
    return {"compose": _build(shown), "dir": v["dir"]}


def install(app_id: str, req: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    import shutil
    if not shutil.which("docker"):
        raise AppStoreError("Docker ist nicht installiert.")
    v = _validate(app_id, req)
    app = v["app"]
    os.makedirs(v["dir"], mode=0o750)
    for f in app["folders"]:
        path = v["folders"].get(f["key"])
        if path and path.startswith("./"):
            sub = os.path.join(v["dir"], path[2:])
            os.makedirs(sub, exist_ok=True)
            if f.get("uid") is not None and f["uid"] != os.getuid():
                # Image runs as another user: make the folder writable for it
                os.chmod(sub, 0o777)
    compose = os.path.join(v["dir"], "docker-compose.yml")
    with open(compose, "w") as fh:
        fh.write(_build(v))
    os.chmod(compose, 0o600)   # may contain passwords

    open_cfg = app.get("open") or {}
    if isinstance(open_cfg.get("port"), str):
        p = next((x for x in app["ports"] if x["key"] == open_cfg["port"]), None)
        open_port = (p["container"] if app["host_network"] else v["ports"].get(open_cfg["port"])) if p else None
    else:
        open_port = open_cfg.get("port")
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


# ---------------------------------------------------------------------------
# Logos: fetched once by the server, served from here
# ---------------------------------------------------------------------------
_LOGO_TYPES = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"GIF8": ("gif", "image/gif"),
               b"RIFF": ("webp", "image/webp")}


def logo(app_id: str) -> Optional[tuple]:
    """(bytes, content type) of the app's logo, or None."""
    if not re.match(r"^lsio-[\w.-]+$", app_id):
        return None
    os.makedirs(LOGO_DIR, exist_ok=True)
    for ext, ctype in [(e, t) for e, t in _LOGO_TYPES.values()] + [("svg", "image/svg+xml")]:
        path = os.path.join(LOGO_DIR, f"{app_id}.{ext}")
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read(), ctype
    if os.path.isfile(os.path.join(LOGO_DIR, f"{app_id}.none")):
        return None
    data = _catalog_data() or {}
    img = next((i for i in data.get("images", []) if f"lsio-{i['name']}" == app_id), None)
    url = (img or {}).get("project_logo") or ""
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in LOGO_HOSTS:
        return None
    try:
        with httpx.stream("GET", url, timeout=15, follow_redirects=True, headers={"User-Agent": "HostCore-Dashboard"}) as res:
            res.raise_for_status()
            body = b""
            for chunk in res.iter_bytes():
                body += chunk
                if len(body) > LOGO_MAX_BYTES:
                    raise ValueError("zu groß")
    except (httpx.HTTPError, ValueError):
        open(os.path.join(LOGO_DIR, f"{app_id}.none"), "w").close()
        return None
    kind = next((v for sig, v in _LOGO_TYPES.items() if body.startswith(sig)), None)
    if not kind and body.lstrip()[:5].lower() in (b"<?xml", b"<svg "):
        kind = ("svg", "image/svg+xml")
    if not kind:
        open(os.path.join(LOGO_DIR, f"{app_id}.none"), "w").close()
        return None
    with open(os.path.join(LOGO_DIR, f"{app_id}.{kind[0]}"), "wb") as f:
        f.write(body)
    return body, kind[1]
