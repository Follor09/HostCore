"""
Achievements: badges for the homelab ("30 Tage ohne Neustart", "Nie über 70 °C" ...).

Every badge is computed from data the dashboard already keeps (history.db, activity log,
settings.json). Once reached, a badge stays unlocked with the time it was first seen,
stored in settings.json under "achievements" - so uptime badges survive the next reboot.
"""

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

import history
import settings_store

logger = logging.getLogger("pi_dashboard.achievements")

CACHE_SECONDS = 60
TIERS = ("bronze", "silver", "gold")

_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Data sources (each one cheap, read once per evaluation)
# ---------------------------------------------------------------------------
def _audit_counts() -> Dict[str, int]:
    """Successful actions in the activity log, by label."""
    import audit
    try:
        with audit._conn() as conn:
            rows = conn.execute("SELECT action, COUNT(*) FROM audit WHERE status < 400 GROUP BY action").fetchall()
        return {a: n for a, n in rows}
    except Exception as e:
        logger.warning(f"Achievements (audit): {e}")
        return {}


def _night_logins() -> int:
    import audit
    try:
        with audit._conn() as conn:
            rows = conn.execute("SELECT ts FROM audit WHERE action = 'Anmeldung' AND status < 400").fetchall()
        return sum(1 for (ts,) in rows if 0 <= time.localtime(ts).tm_hour < 4)
    except Exception:
        return 0


def _samples_7d() -> Dict[str, Any]:
    if not os.path.exists(history.DB_PATH):
        return {"days": 0, "temp_max": None, "power_avg": None, "cpu_peak": None}
    since = int(time.time()) - 7 * 86400
    with history._connect() as conn:
        first, t_max, p_avg, c_max = conn.execute(
            "SELECT MIN(ts), MAX(temp), AVG(power), MAX(cpu) FROM samples WHERE ts >= ?", (since,)).fetchone()
        all_max = conn.execute("SELECT MAX(temp) FROM samples").fetchone()[0]
    days = (time.time() - first) / 86400 if first else 0
    return {"days": days, "temp_max": t_max, "temp_max_all": all_max, "power_avg": p_avg, "cpu_peak": c_max}


def _dashboard_updates() -> int:
    starts = settings_store.get("dashboard_starts", []) or []
    n, prev = 0, None
    for s in starts:
        c = s.get("commit")
        if prev and c and c != prev:
            n += 1
        prev = c or prev
    return n


def _docker_running() -> int:
    try:
        import containers
        data = containers.docker_list()
        return sum(1 for c in data.get("containers", []) if c["state"] == "running")
    except Exception:
        return 0


def _services_perfect() -> Tuple[float, bool]:
    """Days measured (max 7) and whether no running service went down in that time.
    Services that never ran here (not installed, off on purpose) don't count."""
    import monitor
    up = monitor.service_uptime(7)
    days, ok = None, True
    for s in up["services"]:
        periods = s["periods"]
        first = next((i for i, p in enumerate(periods) if p["status"] == "active"), None)
        if first is None:
            continue
        days = s["measured"] / 86400 if days is None else min(days, s["measured"] / 86400)
        ok = ok and all(p["status"] == "active" for p in periods[first:])
    return (days or 0.0), (days is not None and ok)


class _Ctx:
    """Lazily loaded data, shared by all badge checks of one evaluation."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def get(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._data:
            try:
                self._data[key] = fn()
            except Exception as e:
                logger.warning(f"Achievements ({key}): {e}")
                self._data[key] = None
        return self._data[key]

    @property
    def audit(self) -> Dict[str, int]:
        return self.get("audit", _audit_counts) or {}

    @property
    def samples(self) -> Dict[str, Any]:
        return self.get("samples", _samples_7d) or {}


# ---------------------------------------------------------------------------
# Badges: (id, title, description, icon, tier, category, check) - check returns (value, target)
# ---------------------------------------------------------------------------
def _uptime_days(_ctx) -> float:
    return (time.time() - psutil.boot_time()) / 86400


def _cool(ctx) -> Tuple[float, float]:
    s = ctx.samples
    if s.get("temp_max") is None or s["temp_max"] >= 70:
        return 0, 7
    return min(7, s["days"]), 7


def _frugal(ctx) -> Tuple[float, float]:
    s = ctx.samples
    if s.get("power_avg") is None or s["power_avg"] >= 5 or s["days"] < 1:
        return 0, 1
    return 1, 1


def _hot(ctx) -> Tuple[float, float]:
    t = ctx.samples.get("temp_max_all")
    return (1 if t is not None and t >= 80 else 0), 1


def _full_throttle(ctx) -> Tuple[float, float]:
    c = ctx.samples.get("cpu_peak")
    return (1 if c is not None and c >= 99 else 0), 1


def _count(*labels: str) -> Callable[[Any], int]:
    return lambda ctx: sum(ctx.audit.get(label, 0) for label in labels)


def _setting_len(key: str) -> Callable[[Any], int]:
    return lambda _ctx: len(settings_store.get(key, []) or [])


def _notes(_ctx) -> int:
    text = (settings_store.get("notes", {}) or {}).get("text", "")
    return text.count("- [x]") + text.count("- [X]")


def _status_page(_ctx) -> int:
    return 1 if (settings_store.get("status_page", {}) or {}).get("enabled") else 0


def _perfect_week(_ctx) -> Tuple[float, float]:
    days, ok = _services_perfect()
    return (min(7, days) if ok else 0), 7


BADGES: List[Tuple[str, str, str, str, str, str, Callable[[Any], Any]]] = [
    # --- Ausdauer
    ("uptime-7", "Dauerläufer", "7 Tage ohne Neustart", "🏃", "bronze", "Ausdauer", lambda c: (_uptime_days(c), 7)),
    ("uptime-30", "Marathon", "30 Tage ohne Neustart", "🏅", "silver", "Ausdauer", lambda c: (_uptime_days(c), 30)),
    ("uptime-100", "Unkaputtbar", "100 Tage ohne Neustart", "🗿", "gold", "Ausdauer", lambda c: (_uptime_days(c), 100)),
    ("perfect-week", "Fehlerfreie Woche", "7 Tage lang ist kein Dienst ausgefallen", "✨", "silver", "Ausdauer", _perfect_week),

    # --- Hardware
    ("cool-7", "Cool bleiben", "Eine ganze Woche nie über 70 °C", "🧊", "silver", "Hardware", _cool),
    ("frugal", "Sparfuchs", "Im Schnitt unter 5 Watt (letzte 7 Tage)", "🦊", "bronze", "Hardware", _frugal),
    ("hot", "Feuertaufe", "Einmal über 80 °C – und überlebt", "🔥", "bronze", "Hardware", _hot),
    ("full-throttle", "Vollgas", "Die CPU war einmal bei 100 %", "🚀", "bronze", "Hardware", _full_throttle),
    ("benchmark", "Messlatte", "Einen Leistungstest gemacht", "📏", "bronze", "Hardware", lambda c: (_count("Leistungstest")(c), 1)),
    ("fan-tuner", "Lüfterflüsterer", "Die Lüfterkurve angepasst", "🌀", "bronze", "Hardware", lambda c: (_count("Lüfterkurve geändert")(c), 1)),

    # --- Pflege
    ("updates-1", "Frisch gemacht", "Das erste System-Update installiert", "🧼", "bronze", "Pflege", lambda c: (_count("System-Update gestartet")(c), 1)),
    ("updates-25", "Patch-Profi", "25 System-Updates gestartet", "🛠️", "gold", "Pflege", lambda c: (_count("System-Update gestartet")(c), 25)),
    ("dash-10", "Immer aktuell", "10 Dashboard-Updates bekommen", "🔄", "bronze", "Pflege", lambda c: (_dashboard_updates(), 10)),
    ("dash-100", "Versions-Sammler", "100 Dashboard-Updates bekommen", "📚", "gold", "Pflege", lambda c: (_dashboard_updates(), 100)),
    ("cleanup", "Aufgeräumt", "Speicher aufgeräumt", "🧹", "bronze", "Pflege", lambda c: (_count("Speicher aufgeräumt")(c), 1)),
    ("todo-10", "Erledigt!", "10 Aufgaben in den Notizen abgehakt", "✅", "bronze", "Pflege", lambda c: (_notes(c), 10)),

    # --- Homelab
    ("docker-5", "Kapitän", "5 Container laufen gleichzeitig", "🐳", "bronze", "Homelab", lambda c: (c.get("docker", _docker_running) or 0, 5)),
    ("docker-15", "Reederei", "15 Container laufen gleichzeitig", "🚢", "gold", "Homelab", lambda c: (c.get("docker", _docker_running) or 0, 15)),
    ("app-store", "Shopping-Tour", "Eine App aus dem App-Store installiert", "🛍️", "bronze", "Homelab", lambda c: (len(settings_store.get("appstore_installed", {}) or {}), 1)),
    ("health-5", "Wachposten", "5 Web-Dienste werden überwacht", "🛰️", "silver", "Homelab", lambda c: (_setting_len("health_checks")(c), 5)),
    ("status-page", "Offenes Haus", "Die öffentliche Statusseite eingeschaltet", "📣", "bronze", "Homelab", lambda c: (_status_page(c), 1)),
    ("fleet", "Flottenchef", "Einen weiteren Pi verbunden", "🛸", "silver", "Homelab", lambda c: (_setting_len("remotes")(c), 1)),
    ("actions-25", "Knöpfchendrücker", "25 Schnellaktionen ausgeführt", "⚡", "silver", "Homelab", lambda c: (_count("Schnellaktion ausgeführt")(c), 25)),

    # --- Netzwerk & Sicherheit
    ("lan-20", "Netzwerker", "20 Geräte im Heimnetz entdeckt", "📡", "silver", "Netzwerk", lambda c: (len(settings_store.get("lan_devices", {}) or {}), 20)),
    ("wol", "Weckdienst", "Ein Gerät per Wake-on-LAN geweckt", "⏰", "bronze", "Netzwerk", lambda c: (_count("Gerät per Wake-on-LAN geweckt")(c), 1)),
    ("firewall", "Türsteher", "Die Firewall eingerichtet", "🛡️", "bronze", "Netzwerk", lambda c: (_count("Firewall geändert")(c), 1)),
    ("geoblock", "Grenzkontrolle", "Länder gesperrt", "🌍", "silver", "Netzwerk", lambda c: (_count("Länder-Sperre geändert")(c), 1)),
    ("night-owl", "Nachteule", "Zwischen 0 und 4 Uhr angemeldet", "🦉", "bronze", "Netzwerk", lambda c: (c.get("night", _night_logins) or 0, 1)),
]


def _evaluate() -> Dict[str, Any]:
    ctx = _Ctx()
    unlocked: Dict[str, int] = dict(settings_store.get("achievements", {}) or {})
    now = int(time.time())
    changed = False
    out = []
    for bid, title, desc, icon, tier, category, check in BADGES:
        value: Optional[float] = None
        target: float = 1
        try:
            res = check(ctx)
            value, target = res if isinstance(res, tuple) else (res, 1)
        except Exception as e:
            logger.warning(f"Achievement {bid}: {e}")
        reached = value is not None and value >= target
        if reached and bid not in unlocked:
            unlocked[bid] = now
            changed = True
        out.append({
            "id": bid, "title": title, "description": desc, "icon": icon, "tier": tier, "category": category,
            "unlocked": unlocked.get(bid),
            "progress": None if value is None else round(min(1.0, value / target), 3) if target else 1.0,
            "value": None if value is None else round(value, 1), "target": target,
        })
    if changed:
        settings_store.set("achievements", unlocked)
    points = {"bronze": 10, "silver": 25, "gold": 50}
    done = [b for b in out if b["unlocked"]]
    return {
        "badges": out,
        "unlocked": len(done), "total": len(out),
        "score": sum(points[b["tier"]] for b in done), "max_score": sum(points[b["tier"]] for b in out),
        "recent": [b["id"] for b in sorted(done, key=lambda b: b["unlocked"], reverse=True)[:3]],
    }


def overview(force: bool = False) -> Dict[str, Any]:
    if force or not _cache["data"] or time.time() - _cache["ts"] > CACHE_SECONDS:
        _cache.update(ts=time.time(), data=_evaluate())
    return _cache["data"]
