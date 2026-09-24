"""
Alerts & push notifications (ntfy).

Every minute the rules below are evaluated. An alert fires only after its condition
held for a few consecutive checks (no pings for short spikes) and resolves after it
cleared twice. Firing and resolving each send one ntfy message; active alerts also
show up in the dashboard's bell menu.

Config lives in settings.json under "notifications" (edited on the settings page).
"""

import asyncio
import logging
import socket
import time
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
import psutil

import homelab
import services_manager
import settings_store
import system_info

logger = logging.getLogger("pi_dashboard.alerts")

CHECK_INTERVAL = 60  # seconds

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "ntfy_url": "",          # e.g. https://ntfy.sh/my-secret-topic
    "token": "",             # optional access token for protected ntfy servers
    "rules": {
        "cpu_temp": 75,      # °C, 0 = off
        "disk_percent": 90,  # %, 0 = off
        "nvme": True,        # SMART errors, heavy wear, NVMe temperature > 70 °C
        "services": True,    # a service tile in "Fehler"
        "updater": True,     # auto-update failed
        "workloads": True,   # crashed containers / pods
        "reboot": False,     # reboot required after updates
    },
}

# consecutive checks before an alert fires / resolves
FIRE_AFTER = {"cpu_temp": 3}
FIRE_DEFAULT = 2
RESOLVE_AFTER = 2

_state: Dict[str, Dict[str, Any]] = {}
_events: deque = deque(maxlen=50)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def get_config() -> Dict[str, Any]:
    stored = settings_store.get("notifications", {}) or {}
    cfg = {**DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k != "rules"}}
    cfg["rules"] = {**DEFAULT_CONFIG["rules"], **(stored.get("rules") or {})}
    return cfg


def public_config() -> Dict[str, Any]:
    """Config for the settings page - never sends the token back."""
    cfg = get_config()
    return {"enabled": cfg["enabled"], "ntfy_url": cfg["ntfy_url"],
            "token_set": bool(cfg["token"]), "rules": cfg["rules"]}


def save_config(enabled: bool, ntfy_url: str, token: Optional[str], rules: Dict[str, Any]) -> None:
    cfg = get_config()
    ntfy_url = ntfy_url.strip().rstrip("/")
    if ntfy_url:
        parsed = urlparse(ntfy_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError("ntfy-URL muss so aussehen: https://ntfy.sh/dein-thema")
    if enabled and not ntfy_url:
        raise ValueError("Zum Aktivieren eine ntfy-URL angeben.")

    clean_rules = {}
    for key, default in DEFAULT_CONFIG["rules"].items():
        value = rules.get(key, cfg["rules"][key])
        if isinstance(default, bool):
            clean_rules[key] = bool(value)
        else:
            try:
                clean_rules[key] = max(0, min(150, int(value)))
            except (TypeError, ValueError):
                clean_rules[key] = default

    settings_store.set("notifications", {
        "enabled": bool(enabled),
        "ntfy_url": ntfy_url,
        "token": cfg["token"] if token is None else token.strip(),  # None = keep
        "rules": clean_rules,
    })


# ---------------------------------------------------------------------------
# Sending (ntfy JSON publishing, so umlauts & emoji in titles work)
# ---------------------------------------------------------------------------
async def send(title: str, message: str, priority: int = 3, tags: Optional[List[str]] = None,
               cfg: Optional[Dict[str, Any]] = None) -> None:
    cfg = cfg or get_config()
    if not cfg["ntfy_url"]:
        raise ValueError("Keine ntfy-URL gesetzt.")
    parsed = urlparse(cfg["ntfy_url"])
    server = f"{parsed.scheme}://{parsed.netloc}"
    topic = parsed.path.strip("/")
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg["token"] else {}
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": tags or []}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(server, json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"ntfy antwortete mit {resp.status_code}: {resp.text[:150]}")


# ---------------------------------------------------------------------------
# Rules -> list of currently true conditions
# ---------------------------------------------------------------------------
def _conditions(rules: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    host = socket.gethostname()
    found: Dict[str, Dict[str, Any]] = {}

    def add(key, title, message, level="warning"):
        found[key] = {"title": title, "message": message, "level": level}

    if rules["cpu_temp"]:
        temp = system_info.get_cpu_temp()
        if not temp.get("is_simulated") and temp["celsius"] >= rules["cpu_temp"]:
            add("cpu_temp", f"🌡️ CPU heiß: {temp['celsius']} °C",
                f"{host}: CPU-Temperatur über {rules['cpu_temp']} °C.", "critical")

    if rules["disk_percent"]:
        usage = psutil.disk_usage("/").percent
        if usage >= rules["disk_percent"]:
            add("disk", f"💾 Speicher fast voll: {usage:.0f} %",
                f"{host}: Systemlaufwerk über {rules['disk_percent']} % belegt.", "critical")

    if rules["nvme"]:
        nvme = homelab.get_nvme_health()
        if nvme.get("present") and nvme.get("smart"):
            if nvme.get("passed") is False or nvme.get("critical_warning") or nvme.get("media_errors"):
                add("nvme_health", "⚠️ NVMe meldet Fehler", f"{host}: SMART-Status prüfen (Einstellungen → System).", "critical")
            elif (nvme.get("percentage_used") or 0) >= 80:
                add("nvme_wear", "⚠️ NVMe stark abgenutzt", f"{host}: {nvme['percentage_used']} % der Lebensdauer verbraucht.")
        if nvme.get("temp") and nvme["temp"] >= 70:
            add("nvme_temp", f"🌡️ NVMe heiß: {nvme['temp']} °C", f"{host}: NVMe über 70 °C.")

    if rules["services"] or rules["updater"]:
        for svc in services_manager.get_services_status(include_hidden=True):
            if svc["status"] != "error":
                continue
            if svc["id"] == "updater" and rules["updater"]:
                add("updater", "🔄 Auto-Update fehlgeschlagen", f"{host}: {svc['detail']}. Log: journalctl -u pi-dashboard-update")
            elif svc["id"] != "updater" and rules["services"]:
                add(f"service:{svc['id']}", f"🔴 {svc['name']}: Fehler", f"{host}: Dienst ist abgestürzt ({svc['detail']}).", "critical")

    if rules["workloads"]:
        wl = homelab.get_workloads()
        for c in wl["docker"].get("containers", []):
            if c["level"] == "error":
                add(f"docker:{c['name']}", f"🐳 Container {c['name']}: Problem", f"{host}: {c['status']}")
        for p in wl["kubernetes"].get("pods", []):
            if p["level"] == "error":
                add(f"pod:{p['namespace']}/{p['name']}", f"☸️ Pod {p['name']}: {p['status']}",
                    f"{host}: {p['namespace']}/{p['name']} · {p['restarts']} Neustarts")

    if rules["reboot"] and homelab.get_updates().get("reboot_required"):
        add("reboot", "🔁 Neustart erforderlich", f"{host}: Nach Updates wird ein Neustart benötigt.", "info")

    return found


# ---------------------------------------------------------------------------
# State machine + background loop
# ---------------------------------------------------------------------------
PRIORITY = {"critical": 5, "warning": 4, "info": 3}


async def _evaluate(cfg: Dict[str, Any]) -> None:
    current = await asyncio.to_thread(_conditions, cfg["rules"])
    now = int(time.time())

    for key in set(_state) | set(current):
        st = _state.setdefault(key, {"active": False, "hits": 0, "misses": 0})
        if key in current:
            st.update(current[key], hits=st["hits"] + 1, misses=0)
            if not st["active"] and st["hits"] >= FIRE_AFTER.get(key, FIRE_DEFAULT):
                st.update(active=True, since=now)
                _events.appendleft({"ts": now, "kind": "fired", **current[key]})
                await _notify(cfg, st["title"], st["message"], PRIORITY.get(st["level"], 3), ["warning"])
        else:
            st.update(hits=0, misses=st["misses"] + 1)
            if st["active"] and st["misses"] >= RESOLVE_AFTER:
                st["active"] = False
                _events.appendleft({"ts": now, "kind": "resolved", "title": f"✅ Behoben: {st['title']}",
                                    "message": st["message"], "level": "good"})
                await _notify(cfg, f"✅ Behoben: {st['title']}", st["message"], 2, ["white_check_mark"])
            if not st["active"] and st["misses"] >= RESOLVE_AFTER:
                _state.pop(key, None)


async def _notify(cfg: Dict[str, Any], title: str, message: str, priority: int, tags: List[str]) -> None:
    if not cfg["enabled"] or not cfg["ntfy_url"]:
        return
    try:
        await send(title, message, priority, tags, cfg)
    except Exception as e:
        logger.warning(f"Notification failed: {e}")


async def background_alerts() -> None:
    await asyncio.sleep(30)  # let the rest of the dashboard start first
    while True:
        try:
            await _evaluate(get_config())
        except Exception as e:
            logger.warning(f"Alert check failed: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


def get_alerts() -> Dict[str, Any]:
    active = [{"key": k, "title": s["title"], "message": s["message"], "level": s["level"], "since": s["since"]}
              for k, s in _state.items() if s.get("active")]
    active.sort(key=lambda a: (a["level"] != "critical", a["since"]))
    return {"active": active, "recent": list(_events)[:15], "notifications_enabled": get_config()["enabled"]}
