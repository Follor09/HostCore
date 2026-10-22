"""
Automatic updates for compose stacks (like Watchtower, but per stack and visible):
at the chosen time the stack is updated with the same job as the "Aktualisieren" button
(docker compose pull + up -d). Every run is logged; failures show up as events.

A run that was missed because the Pi was off is caught up within three hours.
"""

import asyncio
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

logger = logging.getLogger("pi_dashboard.autoupdate")

KEY = "auto_updates"
LOG_KEY = "auto_update_log"
LAST_KEY = "auto_update_last"
LOG_KEEP = 100
CATCH_UP = 3 * 3600
JOB_TIMEOUT = 45 * 60
DAYS = ("daily", "weekly")          # weekly = on "weekday" (0 = Monday)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_running: Dict[str, bool] = {}
_lock = threading.Lock()


class AutoUpdateError(Exception):
    pass


def schedules() -> Dict[str, Dict[str, Any]]:
    return dict(settings_store.get(KEY, {}) or {})


def overview() -> Dict[str, Any]:
    last = settings_store.get(LAST_KEY, {}) or {}
    log = list(reversed(settings_store.get(LOG_KEY, []) or []))
    return {"schedules": {k: {**v, "last_day": last.get(k), "running": _running.get(k, False)} for k, v in schedules().items()},
            "log": log[:30]}


def save(stack: str, enabled: bool, at: str = "04:00", days: str = "daily", weekday: int = 6) -> Dict[str, Any]:
    import containers
    if not any(s["name"] == stack for s in containers.stacks()):
        raise AutoUpdateError(f"Stack {stack} nicht gefunden.")
    items = schedules()
    if not enabled:
        items.pop(stack, None)
    else:
        if not TIME_RE.match(at or ""):
            raise AutoUpdateError("Uhrzeit im Format HH:MM.")
        if days not in DAYS:
            raise AutoUpdateError("Unbekannter Rhythmus.")
        items[stack] = {"time": at, "days": days, "weekday": max(0, min(6, int(weekday)))}
    settings_store.set(KEY, items)
    return overview()


def _log(entry: Dict[str, Any]) -> None:
    with _lock:
        log = list(settings_store.get(LOG_KEY, []) or [])
        log.append(entry)
        settings_store.set(LOG_KEY, log[-LOG_KEEP:])


def run(stack: str, trigger: str = "Zeitplan") -> Dict[str, Any]:
    """Update one stack now and wait for the job (runs in a worker thread)."""
    import containers
    if _running.get(stack):
        raise AutoUpdateError("Für diesen Stack läuft schon ein Update.")
    _running[stack] = True
    started = time.time()
    entry: Dict[str, Any] = {"ts": int(started), "stack": stack, "trigger": trigger}
    try:
        job = containers.stack_action(stack, "update", user="Automatische Updates")
        while job["status"] == "running" and time.time() - started < JOB_TIMEOUT:
            time.sleep(2)
            job = containers.get_job(job["id"])
        out = job.get("output", "")
        entry.update(status="ok" if job["status"] == "ok" else "error",
                     changed=bool(re.search(r"Recreat|Creat(ed|ing)", out)),
                     duration=int(time.time() - started),
                     summary=(out.strip().splitlines() or [""])[-1][:200])
    except Exception as e:
        entry.update(status="error", changed=False, duration=int(time.time() - started), summary=str(e)[:200])
    finally:
        _running.pop(stack, None)
    _log(entry)
    logger.info(f"Auto-update {stack}: {entry['status']} (changed: {entry.get('changed')})")
    return entry


def run_in_background(stack: str) -> None:
    threading.Thread(target=run, args=(stack, "Manuell"), daemon=True).start()


def _due(stack: str, s: Dict[str, Any], now: float, last: Dict[str, str]) -> bool:
    t = time.localtime(now)
    today = time.strftime("%Y-%m-%d", t)
    if last.get(stack) == today:
        return False
    if s.get("days") == "weekly" and t.tm_wday != s.get("weekday", 6):
        return False
    hh, mm = map(int, s["time"].split(":"))
    planned = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))
    return planned <= now < planned + CATCH_UP


async def background_runner() -> None:
    logger.info("Auto-updates started")
    while True:
        await asyncio.sleep(30)
        try:
            now = time.time()
            last = dict(settings_store.get(LAST_KEY, {}) or {})
            for stack, s in schedules().items():
                if _due(stack, s, now, last) and not _running.get(stack):
                    last[stack] = time.strftime("%Y-%m-%d", time.localtime(now))
                    settings_store.set(LAST_KEY, last)
                    await asyncio.to_thread(run, stack)
        except Exception as e:
            logger.warning(f"Auto-update loop: {e}")


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(LOG_KEY, []) or [] if e["ts"] >= since]
