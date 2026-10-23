"""
End of life (System -> Lebenszyklus): how long the software on this Pi still gets updates.

Looks up the installed versions - operating system, kernel, Python, Docker, Kubernetes (k3s),
Node.js, the Pi model itself - and the images of running containers (postgres:16, redis:7.2,
nginx:1.27 ...) at endoflife.date, once a day (product data is cached for a week).

Warnings 90 days before the end of security updates, critical 30 days before and after it -
in the events, the status summary and the daily e-mail.
"""

import datetime
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

import settings_store

logger = logging.getLogger("pi_dashboard.eol")

API = "https://endoflife.date/api/v1/products/{product}/"
CACHE_KEY = "eol_products"
STATUS_KEY = "eol_status"
EVENTS_KEY = "eol_events"
CHECK_INTERVAL = 24 * 3600
PRODUCT_TTL = 7 * 24 * 3600
WARN_DAYS = 90
CRITICAL_DAYS = 30
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")
# Image repository (last path part) -> endoflife.date product. Images with their own versioning
# (linuxserver.io, bitnami ...) are left out on purpose.
IMAGE_PRODUCTS = {
    "postgres": "postgresql", "postgresql": "postgresql", "mariadb": "mariadb", "mysql": "mysql", "redis": "redis",
    "valkey": "valkey", "mongo": "mongodb", "memcached": "memcached", "rabbitmq": "rabbitmq", "elasticsearch": "elasticsearch",
    "influxdb": "influxdb", "node": "nodejs", "python": "python", "php": "php", "ruby": "ruby", "golang": "go",
    "eclipse-temurin": "eclipse-temurin", "nginx": "nginx", "httpd": "apache-http-server", "traefik": "traefik",
    "alpine": "alpine-linux", "debian": "debian", "ubuntu": "ubuntu", "nextcloud": "nextcloud", "grafana": "grafana",
    "keycloak": "keycloak",
}
LABELS = {"debian": "Debian", "linux": "Linux-Kernel", "python": "Python", "docker-engine": "Docker Engine",
          "kubernetes": "Kubernetes", "nodejs": "Node.js", "raspberry-pi": "Raspberry Pi"}
_lock = threading.Lock()
_running = {"active": False}


# ---------------------------------------------------------------------------
# endoflife.date
# ---------------------------------------------------------------------------
def _release(r: Dict[str, Any]) -> Dict[str, Any]:
    """Only what the overview needs; dates as YYYY-MM-DD, True = ended without a date, None = no end known."""
    def end(flag: str, date: str) -> Any:
        return r.get(date) or (True if r.get(flag) else None)
    latest = r.get("latest") or {}
    return {"name": str(r.get("name") or ""), "codename": r.get("codename") or "", "label": r.get("label") or "",
            "released": r.get("releaseDate") or "", "lts": bool(r.get("isLts")),
            "eoas": end("isEoas", "eoasFrom"), "eol": end("isEol", "eolFrom"), "eoes": end("isEoes", "eoesFrom"),
            "latest": latest.get("name") or "", "latest_date": latest.get("date") or ""}


def _product(name: str, client: httpx.Client, cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = cache.get(name)
    if entry and time.time() - entry.get("fetched", 0) < PRODUCT_TTL:
        return entry
    try:
        res = client.get(API.format(product=name))
        if res.status_code == 404:
            entry = {"fetched": time.time(), "missing": True, "releases": []}
        else:
            res.raise_for_status()
            data = res.json().get("result") or {}
            entry = {"fetched": time.time(), "label": data.get("label") or name, "labels": data.get("labels") or {},
                     "link": (data.get("links") or {}).get("html", ""),
                     "releases": [_release(r) for r in data.get("releases") or []]}
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"endoflife.date {name}: {e}")
        return entry   # keep the old data while the service is unreachable
    cache[name] = entry
    return entry


def match_release(version: str, releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """16.4 -> "16", 11.4.2 -> "11.4", 7 -> newest "7.x" (floating tag), bookworm -> codename."""
    v = (version or "").strip().lower()
    if not v:
        return None
    for r in releases:
        if r["name"].lower() == v or (r["codename"] and r["codename"].lower() == v):
            return r
    prefix = [r for r in releases if v.startswith(r["name"].lower() + ".")]
    if prefix:
        return max(prefix, key=lambda r: len(r["name"]))
    floating = [r for r in releases if r["name"].lower().startswith(v + ".")]
    return floating[0] if floating else None   # endoflife.date lists the newest first


# ---------------------------------------------------------------------------
# What is installed
# ---------------------------------------------------------------------------
def _run(cmd: List[str], timeout: float = 10) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _os_release() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k:
                    out[k] = v.strip('"')
    except OSError:
        pass
    return out


def _pi_model_release() -> str:
    """Model string from the device tree -> release name of the product raspberry-pi."""
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().strip("\x00 \n")
    except OSError:
        return ""
    m = model.replace("Raspberry Pi ", "")
    rules = [(r"^Compute Module 5", "cm5"), (r"^Compute Module 4S", "cm4s"), (r"^Compute Module 4", "cm4"),
             (r"^Compute Module 3 Plus", "cm3+"), (r"^Compute Module 3", "cm3"), (r"^500\+", "5-500+"), (r"^500", "5-500"),
             (r"^400", "4-400"), (r"^5 ", "5"), (r"^4 Model B", "4-b"), (r"^3 Model B Plus", "3-b+"),
             (r"^3 Model A Plus", "3-a+"), (r"^3 Model B", "3-b"), (r"^Zero 2 W", "zero-2-w"), (r"^Zero W", "zero-w"),
             (r"^Zero", "zero"), (r"^2 Model B", "2-b")]
    for rx, name in rules:
        if re.match(rx, m + " "):
            return name
    return ""


def _components() -> List[Dict[str, Any]]:
    """[{key, product, label, version, kind}] - kind: system or image."""
    out: List[Dict[str, Any]] = []
    osr = _os_release()
    if osr.get("ID") in ("debian", "raspbian") or "debian" in osr.get("ID_LIKE", ""):
        rpi = os.path.exists("/etc/rpi-issue")
        name = "Raspberry Pi OS" if rpi else "Debian"
        if osr.get("ID") == "debian" or rpi:
            out.append({"key": "os", "product": "debian", "label": name, "version": osr.get("VERSION_ID", ""), "kind": "system"})
    if osr.get("ID") == "ubuntu":
        out.append({"key": "os", "product": "ubuntu", "label": "Ubuntu", "version": osr.get("VERSION_ID", ""), "kind": "system"})
    model = _pi_model_release()
    if model:
        out.append({"key": "pi", "product": "raspberry-pi", "label": "Raspberry Pi (Hardware)", "version": model, "kind": "hardware"})
    if platform.system() == "Linux":
        out.append({"key": "kernel", "product": "linux", "label": "Linux-Kernel", "version": platform.release(), "kind": "system"})
    out.append({"key": "python", "product": "python", "label": "Python (Dashboard)", "version": platform.python_version(), "kind": "system"})
    if shutil.which("docker"):
        v = _run(["docker", "version", "--format", "{{.Server.Version}}"])
        if v:
            out.append({"key": "docker", "product": "docker-engine", "label": "Docker Engine", "version": v, "kind": "system"})
    if shutil.which("k3s"):
        m = re.search(r"v(\d+\.\d+\.\d+)", _run(["k3s", "--version"]))
        if m:
            out.append({"key": "k3s", "product": "kubernetes", "label": "Kubernetes (k3s)", "version": m.group(1), "kind": "system"})
    if shutil.which("node"):
        v = _run(["node", "--version"])
        if v:
            out.append({"key": "node", "product": "nodejs", "label": "Node.js", "version": v, "kind": "system"})
    for img in _images():
        out.append(img)
    return out


def _images() -> List[Dict[str, Any]]:
    """Running images whose product endoflife.date knows, once per image reference."""
    import imagecheck
    images = (imagecheck.status().get("images") or [])
    if not images:
        try:
            images = [{**u["ref"], "users": [u["user"]]} for u in imagecheck._docker_images() + imagecheck._pod_images()]
        except Exception as e:
            logger.warning(f"EOL images: {e}")
            images = []
    out, seen = [], set()
    for i in images:
        repo = i.get("repo", "")
        base = repo.rsplit("/", 1)[-1]
        # only official images (docker.io/library/...) and the vendors' own (grafana/grafana ...)
        product = IMAGE_PRODUCTS.get(base) if (repo.startswith("library/") or "/" not in repo or repo.split("/")[0] == base) else None
        if not product or i.get("ref") in seen:
            continue
        seen.add(i.get("ref"))
        users = sorted({u.get("name", "") for u in i.get("users", []) if u.get("name")})
        out.append({"key": f"image:{i['ref']}", "product": product, "label": base, "version": i.get("tag", ""),
                    "kind": "image", "image": i["ref"].replace("docker.io/library/", "").replace("docker.io/", ""), "users": users})
    return out


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _days(end: Any, today: datetime.date) -> Optional[int]:
    if end is True:
        return -1
    if not end or end is False:
        return None
    try:
        return (datetime.date.fromisoformat(str(end)[:10]) - today).days
    except ValueError:
        return None


def _evaluate(c: Dict[str, Any], prod: Optional[Dict[str, Any]], today: datetime.date) -> Dict[str, Any]:
    out = {**c, "cycle": "", "eol": None, "eoas": None, "eoes": None, "days": None, "latest": "", "newest": "",
           "state": "unknown", "note": "", "link": (prod or {}).get("link", "")}
    version = c["version"]
    if c["kind"] == "image":
        m = VERSION_RE.match(version)
        word = version.split("-")[0].lower()
        version = m.group(1) if m else word
        if version in ("latest", "stable", "lts", "mainline", ""):
            out.update(state="floating", note="Tag ohne Versionsnummer – folgt der jeweils neuesten Version")
            return out
    elif c["kind"] == "system" and c["key"] not in ("os",):
        m = VERSION_RE.match(version)
        version = m.group(1) if m else version
    if not prod or prod.get("missing"):
        out["note"] = "Bei endoflife.date nicht verfügbar" if prod else "Noch nicht abgefragt"
        return out
    releases = prod.get("releases", [])
    if releases:
        out["newest"] = releases[0]["name"]
    r = match_release(version, releases)
    if not r:
        out["note"] = f"Version {version} nicht in der Liste von endoflife.date"
        return out
    days = _days(r["eol"], today)
    out.update(cycle=r["label"] or r["name"], eol=r["eol"], eoas=r["eoas"], eoes=r["eoes"], days=days, latest=r["latest"],
               lts=r["lts"])
    eoas_days = _days(r["eoas"], today)
    if c["kind"] == "hardware":
        # Production guarantee of the model, not a support end: never more than a hint
        out["state"] = "ok" if days is None or days > 365 else "info"
        out["note"] = "Produktion zugesichert bis" if r["eol"] else ""
        return out
    if days is None:
        out["state"] = "ok"
    elif days < 0:
        out["state"] = "expired"
    elif days <= CRITICAL_DAYS:
        out["state"] = "critical"
    elif days <= WARN_DAYS:
        out["state"] = "warning"
    elif eoas_days is not None and eoas_days < 0:
        out["state"] = "lts"
        labels = prod.get("labels") or {}
        out["note"] = f"{labels.get('eoas') or 'Regulärer Support'} beendet – nur noch {labels.get('eol') or 'Langzeit-Support'}"
    else:
        out["state"] = "ok"
    # Debian backports fixes into its own Python and the Pi kernel follows its own branch: no hint there
    if version and r["latest"] and c["key"] in ("docker", "k3s", "node") and _older(version, r["latest"]):
        out["update"] = r["latest"]
    return out


def _older(a: str, b: str) -> bool:
    def parts(v: str) -> List[int]:
        return [int(x) for x in re.findall(r"\d+", v)[:4]]
    return parts(a) < parts(b)


def overview() -> Dict[str, Any]:
    st = settings_store.get(STATUS_KEY) or {}
    items = st.get("items", [])
    order = {"expired": 0, "critical": 1, "warning": 2, "lts": 3, "info": 4, "unknown": 5, "floating": 6, "ok": 7}
    items = sorted(items, key=lambda i: (order.get(i["state"], 9), i["days"] if i.get("days") is not None else 99999, i["label"]))
    return {"items": items, "checked": st.get("checked", 0), "running": _running["active"], "error": st.get("error", ""),
            "warn_days": WARN_DAYS, "critical_days": CRITICAL_DAYS,
            "problems": sum(1 for i in items if i["state"] in ("expired", "critical", "warning")),
            "attribution": "endoflife.date"}


def _name(i: Dict[str, Any]) -> str:
    return f"{i['label']} {i['cycle']}".strip() if i["kind"] != "image" else f"{i.get('image', i['label'])}"


def _de(iso: Any) -> str:
    s = str(iso)
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if len(s) >= 10 else s


def alerts() -> List[Dict[str, Any]]:
    out = []
    for i in overview()["items"]:
        if i["state"] == "expired":
            out.append({"level": "critical", "text": f"{_name(i)}: keine Sicherheitsupdates mehr" + (f" (seit {_de(i['eol'])})" if i["eol"] is not True else "")})
        elif i["state"] == "critical":
            out.append({"level": "critical", "text": f"{_name(i)}: Support endet in {i['days']} Tagen ({_de(i['eol'])})"})
        elif i["state"] == "warning":
            out.append({"level": "warning", "text": f"{_name(i)}: Support endet in {i['days']} Tagen ({_de(i['eol'])})"})
    return out


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
TITLES = {"warning": ("warning", "{name}: Support endet in {days} Tagen"), "critical": ("critical", "{name}: Support endet in {days} Tagen"),
          "expired": ("critical", "{name}: Support beendet")}


def check() -> Dict[str, Any]:
    today = datetime.date.today()
    cache = settings_store.get(CACHE_KEY) or {}
    error = ""
    comps = _components()
    items = []
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "PiCore-Dashboard"}) as client:
        for c in comps:
            prod = _product(c["product"], client, cache)
            if prod is None:
                error = "endoflife.date nicht erreichbar"
            items.append(_evaluate(c, prod, today))
    used = {c["product"] for c in comps}
    settings_store.set(CACHE_KEY, {k: v for k, v in cache.items() if k in used})
    with _lock:
        old = {i["key"]: i["state"] for i in (settings_store.get(STATUS_KEY) or {}).get("items", [])}
        settings_store.set(STATUS_KEY, {"checked": int(time.time()), "items": items, "error": error})
        new_events = []
        for i in items:
            prev = old.get(i["key"])
            if prev is not None and prev != i["state"] and i["state"] in TITLES:
                level, title = TITLES[i["state"]]
                new_events.append({"ts": int(time.time()), "level": level, "title": title.format(name=_name(i), days=i["days"]),
                                   "detail": f"Ende der Sicherheitsupdates: {_de(i['eol'])}" if i["eol"] not in (None, True) else ""})
        if new_events:
            settings_store.set(EVENTS_KEY, ((settings_store.get(EVENTS_KEY, []) or []) + new_events)[-200:])
    return overview()


def _thread() -> None:
    try:
        check()
    except Exception as e:
        logger.warning(f"EOL check failed: {e}")
    finally:
        _running["active"] = False


def start_check() -> Dict[str, Any]:
    with _lock:
        if not _running["active"]:
            _running["active"] = True
            threading.Thread(target=_thread, daemon=True).start()
    return overview()


def check_if_due() -> None:
    st = settings_store.get(STATUS_KEY) or {}
    if time.time() - st.get("checked", 0) >= CHECK_INTERVAL - 60:
        start_check()

