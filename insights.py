"""
Overview features built on top of what the dashboard already records:

- Event feed: service outages and recoveries, throttling, web-service failures, failed
  logins (dashboard + SSH), updates, reboots, dashboard restarts, new LAN devices ...
- Status summary: one line "all good" or exactly what needs attention.
- Disk forecast: daily fill level per partition -> "full in ~N days", plus which
  folders grew the most (daily `du` of the root file system, niced).
- Weekly report: everything above for the last 7 days on one page.
"""

import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

import history
import settings_store

logger = logging.getLogger("pi_dashboard.insights")

LEVELS = {"good": 0, "info": 1, "warning": 2, "critical": 3}
DISK_RETENTION_DAYS = 400
FOLDER_MIN_KB = 50 * 1024           # folders below 50 MB are not stored
FOLDER_KEEP = 300


def _conn() -> sqlite3.Connection:
    conn = history._connect()
    conn.execute("CREATE TABLE IF NOT EXISTS disk_daily (day TEXT, mount TEXT, used INTEGER, total INTEGER, PRIMARY KEY (day, mount))")
    conn.execute("CREATE TABLE IF NOT EXISTS folder_sizes (day TEXT, path TEXT, kb INTEGER, PRIMARY KEY (day, path))")
    return conn


def fmt_duration(sec: float) -> str:
    """Same wording as the system page: "< 1 Min.", "5 Min.", "2 Std. 5 Min.", "3 Tage 2 Std."."""
    if sec < 60:
        return "< 1 Min."
    m = round(sec / 60)
    if m < 60:
        return f"{m} Min."
    h, rm = divmod(m, 60)
    if h < 24:
        return f"{h} Std. {rm} Min." if rm else f"{h} Std."
    d, rh = divmod(h, 24)
    return f"{d} Tage {rh} Std." if rh else f"{d} Tage"


def _day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


# ---------------------------------------------------------------------------
# Boots and dashboard starts (recorded once at startup)
# ---------------------------------------------------------------------------
def record_start(commit: str) -> None:
    boot = int(psutil.boot_time())
    boots = settings_store.get("boots", []) or []
    if not any(abs(b - boot) < 120 for b in boots):
        boots = (boots + [boot])[-100:]
        settings_store.set("boots", boots)
    starts = settings_store.get("dashboard_starts", []) or []
    starts = (starts + [{"ts": int(time.time()), "commit": commit or ""}])[-200:]
    settings_store.set("dashboard_starts", starts)


# ---------------------------------------------------------------------------
# Disk snapshots (hourly from the monitor) and folder sizes (daily du)
# ---------------------------------------------------------------------------
_du_lock = threading.Lock()


def disk_snapshot() -> None:
    import storage
    day = _day()
    with _conn() as conn:
        for disk in storage.overview()["disks"]:
            for p in disk["partitions"]:
                if p.get("mount") and p.get("total"):
                    conn.execute("INSERT OR REPLACE INTO disk_daily (day, mount, used, total) VALUES (?, ?, ?, ?)",
                                 (day, p["mount"], p["used"], p["total"]))
        conn.execute("DELETE FROM disk_daily WHERE day < ?", (_day(time.time() - DISK_RETENTION_DAYS * 86400),))
        done_today = conn.execute("SELECT 1 FROM folder_sizes WHERE day = ? LIMIT 1", (day,)).fetchone()
    if not done_today and not _du_lock.locked():
        threading.Thread(target=_folder_snapshot, daemon=True).start()


def _folder_snapshot() -> None:
    """Folder sizes two levels deep on the root file system, at the lowest priority."""
    if not _du_lock.acquire(blocking=False):
        return
    try:
        cmd = ["du", "-x", "-k", "-d", "2", "/"]
        if os.path.exists("/usr/bin/ionice"):
            cmd = ["ionice", "-c", "3"] + cmd
        cmd = ["nice", "-n", "19"] + cmd
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900).stdout
        rows = []
        for line in out.splitlines():
            m = re.match(r"^(\d+)\s+(/.*)$", line)
            if m and int(m.group(1)) >= FOLDER_MIN_KB and m.group(2) != "/":
                rows.append((int(m.group(1)), m.group(2)))
        rows.sort(reverse=True)
        day = _day()
        with _conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO folder_sizes (day, path, kb) VALUES (?, ?, ?)",
                             [(day, path, kb) for kb, path in rows[:FOLDER_KEEP]])
            conn.execute("DELETE FROM folder_sizes WHERE day < ?", (_day(time.time() - DISK_RETENTION_DAYS * 86400),))
        logger.info(f"Folder sizes stored ({len(rows)} folders)")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Folder size snapshot failed: {e}")
    finally:
        _du_lock.release()


def _slope(points: List[tuple]) -> float:
    """Least squares slope (bytes/day) for [(day_index, used), ...]."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den if den else 0.0


def disk_forecast() -> Dict[str, Any]:
    with _conn() as conn:
        rows = conn.execute("SELECT day, mount, used, total FROM disk_daily WHERE day >= ? ORDER BY day",
                            (_day(time.time() - 60 * 86400),)).fetchall()
        folder_days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM folder_sizes ORDER BY day DESC").fetchall()]
        latest = folder_days[0] if folder_days else None

        def sizes(day: Optional[str]) -> Dict[str, int]:
            return dict(conn.execute("SELECT path, kb FROM folder_sizes WHERE day = ?", (day,)).fetchall()) if day else {}

        def closest(days_back: int) -> Optional[str]:
            target = _day(time.time() - days_back * 86400)
            older = [d for d in folder_days if d <= target]
            return older[0] if older else None

        now_sizes = sizes(latest)
        growth = {}
        for label, back in (("week", 7), ("month", 30)):
            old_day = closest(back)
            old = sizes(old_day)
            if old_day and old_day != latest:
                changes = [{"path": p, "kb": kb, "delta_kb": kb - old.get(p, 0), "new": p not in old}
                           for p, kb in now_sizes.items()]
                # only the deepest folders that changed, parents would repeat their children
                changes = [c for c in changes if abs(c["delta_kb"]) >= 10 * 1024]
                changes.sort(key=lambda c: c["delta_kb"], reverse=True)
                growth[label] = {"since": old_day, "folders": changes[:10]}

    by_mount: Dict[str, List[tuple]] = {}
    for day, mount, used, total in rows:
        by_mount.setdefault(mount, []).append((day, used, total))
    out = []
    for mount, pts in by_mount.items():
        day0 = time.mktime(time.strptime(pts[0][0], "%Y-%m-%d"))
        series = [((time.mktime(time.strptime(d, "%Y-%m-%d")) - day0) / 86400, u) for d, u, _ in pts[-30:]]
        used, total = pts[-1][1], pts[-1][2]
        item = {"mount": mount, "used": used, "total": total, "free": max(0, total - used),
                "percent": round(used / total * 100, 1) if total else None, "days_tracked": len(pts),
                "history": [{"day": d, "used": u} for d, u, _ in pts]}
        if len(series) >= 3 and series[-1][0] - series[0][0] >= 2:
            per_day = _slope(series)
            item["per_day"] = int(per_day)
            if per_day > 10 * 1024 * 1024:          # grows by more than 10 MB a day
                item["days_left"] = int(item["free"] / per_day)
        out.append(item)
    out.sort(key=lambda i: (i["mount"] != "/", i["mount"]))
    return {"mounts": out, "folders_day": latest, "growth": growth, "top_folders": sorted(
        ({"path": p, "kb": kb} for p, kb in now_sizes.items() if p.count("/") == 1), key=lambda f: -f["kb"])[:10]}


# ---------------------------------------------------------------------------
# Event feed
# ---------------------------------------------------------------------------
AUDIT_EVENTS = {
    "System-Update gestartet": ("info", "System-Update gestartet"),
    "Dashboard-Update installiert": ("info", "Dashboard-Update installiert"),
    "Firewall geändert": ("info", "Firewall geändert"),
    "Lüfterkurve geändert": ("info", "Lüfterkurve geändert"),
    "Neustart": ("info", "Neustart ausgelöst"),
    "Herunterfahren": ("info", "Herunterfahren ausgelöst"),
    "CPU-Modus geändert": ("info", "CPU-Modus geändert"),
    "Eigenes Passwort geändert": ("info", "Passwort geändert"),
    "Benutzer angelegt": ("info", "Benutzer angelegt"),
    "Benutzer gelöscht": ("warning", "Benutzer gelöscht"),
    "Gruppenrechte geändert": ("info", "Gruppenrechte geändert"),
    "IP entsperrt (fail2ban)": ("info", "IP entsperrt (fail2ban)"),
}
THROTTLE_TITLE = {"undervoltage": ("critical", "Unterspannung"), "throttled": ("critical", "Gedrosselt wegen Hitze"),
                  "freq_capped": ("warning", "Takt begrenzt"), "soft_temp": ("warning", "Temperaturgrenze erreicht")}


def _ev(ts, level, category, title, detail="", link="") -> Dict[str, Any]:
    return {"ts": int(ts), "level": level, "category": category, "title": title, "detail": detail, "link": link}


def events(days: int = 7, limit: int = 300) -> Dict[str, Any]:
    import audit
    import healthchecks
    import monitor
    now = time.time()
    since = now - days * 86400
    out: List[Dict[str, Any]] = []

    for b in settings_store.get("boots", []) or []:
        if b >= since:
            out.append(_ev(b, "info", "system", "Pi gestartet", "", "/system#hardware"))
    prev_commit = None
    for s in settings_store.get("dashboard_starts", []) or []:
        if s["ts"] >= since:
            if prev_commit and s.get("commit") and s["commit"] != prev_commit:
                out.append(_ev(s["ts"], "good", "system", "Dashboard aktualisiert", f"{prev_commit} → {s['commit']}", ""))
        prev_commit = s.get("commit") or prev_commit

    try:
        for svc in monitor.service_uptime(days)["services"]:
            periods = svc["periods"]
            if not any(p["status"] == "active" for p in periods):
                continue  # never running here: not installed / off on purpose
            for prev, cur in zip(periods, periods[1:]):
                if prev["status"] == "active" and cur["status"] != "active":
                    level = "critical" if cur["status"] == "error" else "warning"
                    out.append(_ev(cur["start"], level, "services", f"{svc['name']} ausgefallen",
                                   "Fehler" if cur["status"] == "error" else "Inaktiv", "/system#verfuegbarkeit"))
                elif prev["status"] != "active" and cur["status"] == "active":
                    out.append(_ev(cur["start"], "good", "services", f"{svc['name']} wieder aktiv",
                                   f"nach {fmt_duration(cur['start'] - prev['start'])}", "/system#verfuegbarkeit"))
    except Exception as e:
        logger.warning(f"Events (services): {e}")

    try:
        for t in monitor.throttle_log(days)["events"]:
            level, title = THROTTLE_TITLE.get(t["kind"], ("warning", t["kind"]))
            detail = "seit dem letzten Neustart" if t["since_boot"] else ("< 1 Min." if t["short"] else fmt_duration(t["duration"]))
            out.append(_ev(t["start"], level, "hardware", title, detail, "/system#drosselung"))
    except Exception as e:
        logger.warning(f"Events (throttle): {e}")

    try:
        with healthchecks._conn() as conn:
            names = {c["id"]: c["name"] for c in healthchecks.checks()}
            rows = conn.execute("SELECT ts, check_id, ok, error FROM health_results WHERE ts >= ? ORDER BY check_id, ts",
                                (int(since) - 86400,)).fetchall()
        last: Dict[str, int] = {}
        for ts, cid, ok, error in rows:
            if cid in names and cid in last and last[cid] != ok and ts >= since:
                out.append(_ev(ts, "good" if ok else "warning", "services",
                               f"{names[cid]} wieder erreichbar" if ok else f"{names[cid]} nicht erreichbar",
                               "" if ok else (error or ""), "/settings#web-dienste"))
            last[cid] = ok
    except Exception as e:
        logger.warning(f"Events (health): {e}")

    try:
        with audit._conn() as conn:
            rows = conn.execute("SELECT ts, user, ip, action, detail, status FROM audit WHERE ts >= ? ORDER BY ts",
                                (int(since),)).fetchall()
        for ts, user, ip, action, detail, status in rows:
            if action == "Anmeldung" and status and status >= 400:
                out.append(_ev(ts, "warning", "security", "Fehlgeschlagene Anmeldung", f"{user or '?'} · {ip}", "/system#aktivitaet"))
            elif action in AUDIT_EVENTS and status and status < 400:
                level, title = AUDIT_EVENTS[action]
                out.append(_ev(ts, level, "changes", title, f"{detail} · {user}" if detail else user, "/system#aktivitaet"))
    except Exception as e:
        logger.warning(f"Events (audit): {e}")

    try:
        import security
        ssh = security.ssh_logins()
        per_day: Dict[str, int] = {}
        for e in ssh.get("events", []):
            if e["kind"] == "fail" and e["ts"] >= since:
                per_day[_day(e["ts"])] = per_day.get(_day(e["ts"]), 0) + e["attempts"]
        for day, n in per_day.items():
            ts = time.mktime(time.strptime(day, "%Y-%m-%d")) + 12 * 3600
            out.append(_ev(min(ts, now), "warning" if n >= 20 else "info", "security",
                           f"{n} fehlgeschlagene SSH-Anmeldungen", day, "/system#ssh"))
    except Exception as e:
        logger.warning(f"Events (ssh): {e}")

    devices = list((settings_store.get("lan_devices", {}) or {}).values())
    if devices:
        first_scan = min(d.get("first_seen", now) for d in devices)
        for d in devices:
            fs = d.get("first_seen", 0)
            if fs >= since and fs - first_scan > 600 and not d.get("self"):
                name = d.get("alias") or d.get("hostname") or d.get("ip")
                out.append(_ev(fs, "info", "network", "Neues Gerät im Netzwerk", f"{name} · {d.get('mac')}", "/system#geraete"))

    out.sort(key=lambda e: e["ts"], reverse=True)
    counts = {lvl: sum(1 for e in out if e["level"] == lvl) for lvl in LEVELS}
    return {"days": days, "events": out[:limit], "counts": counts, "total": len(out)}


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------
def status_summary() -> Dict[str, Any]:
    import healthchecks
    import homelab
    import monitor
    import services_manager
    import storage
    import system_info
    import updater
    items: List[Dict[str, Any]] = []

    def add(level, text, link=""):
        items.append({"level": level, "text": text, "link": link})

    for s in services_manager.get_services_status():
        if s["status"] == "error":
            add("critical", f"{s['name']}: Fehler", "#service:" + s["id"])
    for c in healthchecks.status(24)["checks"]:
        if c["last"] and not c["last"]["ok"]:
            add("warning", f"{c['name']} nicht erreichbar", "/settings#web-dienste")

    for disk in storage.overview()["disks"]:
        for p in disk["partitions"]:
            pct = p.get("percent")
            if pct is not None and pct >= 85:
                add("critical" if pct >= 95 else "warning", f"{p['mount']} zu {pct:.0f}% voll", "/system#speicherprognose")
    try:
        for m in disk_forecast()["mounts"]:
            if m.get("days_left") is not None and m["days_left"] < 30:
                add("warning", f"{m['mount']} voll in ~{m['days_left']} Tagen", "/system#speicherprognose")
    except Exception:
        pass

    temp = system_info.get_cpu_temp()
    if not temp.get("is_simulated") and temp.get("celsius") is not None:
        t = temp["celsius"]
        if t >= 75:
            add("critical" if t >= 80 else "warning", f"CPU {t:.0f} °C", "/system#sensoren")
    th = system_info.get_throttled_status()
    if th.get("undervoltage"):
        add("critical", "Unterspannung – Netzteil prüfen", "/system#drosselung")
    elif th.get("throttled"):
        add("critical", "CPU wird gerade gedrosselt", "/system#drosselung")
    else:
        try:
            recent = [e for e in monitor.throttle_log(1)["events"] if e["kind"] == "undervoltage"]
            if recent:
                add("warning", "Unterspannung in den letzten 24 Std.", "/system#drosselung")
        except Exception:
            pass
    mem = psutil.virtual_memory().percent
    if mem >= 92:
        add("warning", f"Arbeitsspeicher zu {mem:.0f}% belegt", "/system#ressourcen")

    nvme = homelab.get_nvme_health()
    if nvme.get("present") and nvme.get("smart"):
        if nvme.get("passed") is False or nvme.get("critical_warning"):
            add("critical", "NVMe meldet ein Problem", "#nvme")
        elif (nvme.get("percentage_used") or 0) >= 80:
            add("warning", f"NVMe zu {nvme['percentage_used']}% verschlissen", "#nvme")

    # Never wait for `apt list` here: use the last result (the updates card refreshes it)
    apt = homelab._cache.peek("apt")
    if apt is None:
        threading.Thread(target=homelab.get_updates, daemon=True).start()
    if apt and apt.get("supported") and not apt.get("error"):
        if apt.get("security_count"):
            add("warning", f"{apt['security_count']} Sicherheitsupdates verfügbar", "#updates")
        elif apt.get("count"):
            add("info", f"{apt['count']} Updates verfügbar", "#updates")
        if apt.get("reboot_required"):
            add("info", "Neustart nach Updates nötig", "#updates")
    if updater.update_state.get("update_available"):
        add("info", "Dashboard-Update verfügbar", "/settings#auto-update")

    level = max((i["level"] for i in items), key=lambda l: LEVELS[l], default="good")
    items.sort(key=lambda i: -LEVELS[i["level"]])
    return {"level": level, "items": items, "checked": int(time.time())}


# ---------------------------------------------------------------------------
# Weekly report
# ---------------------------------------------------------------------------
def weekly_report() -> Dict[str, Any]:
    import audit
    import healthchecks
    import monitor
    now = int(time.time())
    since = now - 7 * 86400
    rep: Dict[str, Any] = {"from": since, "to": now}

    with history._connect() as conn:
        row = conn.execute("""SELECT AVG(cpu), MAX(cpu), AVG(temp), MAX(temp), AVG(ram), MAX(ram), MAX(nvme_temp),
                                     AVG(power), COUNT(*) FROM samples WHERE ts >= ?""", (since,)).fetchone()
        peak = conn.execute("SELECT ts FROM samples WHERE ts >= ? AND temp IS NOT NULL ORDER BY temp DESC LIMIT 1", (since,)).fetchone()
    r = lambda v, n=1: None if v is None else round(v, n)
    rep["metrics"] = {"cpu_avg": r(row[0]), "cpu_max": r(row[1]), "temp_avg": r(row[2]), "temp_max": r(row[3]),
                      "temp_max_ts": peak[0] if peak else None, "ram_avg": r(row[4]), "ram_max": r(row[5]),
                      "nvme_temp_max": r(row[6]), "power_avg": r(row[7], 2), "samples": row[8]}

    rep["services"] = [{"name": s["name"], "uptime": s["uptime"], "outages": len(s["outages"]),
                        "never_up": bool(s["periods"]) and all(p["status"] == "inactive" for p in s["periods"])}
                       for s in monitor.service_uptime(7)["services"]]
    rep["web_services"] = [{"name": c["name"], "uptime": c["uptime"], "avg_ms": c["avg_ms"]}
                           for c in healthchecks.status(168)["checks"]]
    t = monitor.throttle_log(7)
    rep["throttle"] = t["counts"]

    usage = monitor.data_usage()
    week_days = {_day(now - i * 86400) for i in range(7)}
    rep["traffic"] = {"rx": sum(d["rx"] for d in usage["days"] if d["day"] in week_days),
                      "tx": sum(d["tx"] for d in usage["days"] if d["day"] in week_days)}

    hogs = monitor.resource_hogs(168)
    rep["top_cpu"] = [{"label": h["label"], "cpu_avg": h["cpu_avg"], "cpu_minutes": h["cpu_minutes"]} for h in hogs["by_cpu"][:5]]
    rep["top_ram"] = [{"label": h["label"], "rss_max": h["rss_max"]} for h in hogs["by_ram"][:5]]

    with audit._conn() as conn:
        a = conn.execute("""SELECT SUM(action = 'Anmeldung' AND status < 400), SUM(action = 'Anmeldung' AND status >= 400)
                            FROM audit WHERE ts >= ?""", (since,)).fetchone()
        changes = conn.execute("""SELECT ts, user, action, detail FROM audit WHERE ts >= ? AND status < 400
                                  AND action IN ({}) ORDER BY ts DESC LIMIT 30""".format(",".join("?" * len(AUDIT_EVENTS))),
                               (since, *AUDIT_EVENTS)).fetchall()
    rep["logins"] = {"ok": a[0] or 0, "failed": a[1] or 0}
    try:
        import security
        rep["logins"]["ssh_failed"] = sum(e["attempts"] for e in security.ssh_logins().get("events", [])
                                          if e["kind"] == "fail" and e["ts"] >= since)
    except Exception:
        rep["logins"]["ssh_failed"] = None
    rep["changes"] = [{"ts": ts, "user": u, "action": act, "detail": d} for ts, u, act, d in changes]

    ev = events(7, 1000)
    rep["events"] = ev["counts"]
    rep["reboots"] = [e["ts"] for e in ev["events"] if e["title"] == "Pi gestartet"]
    rep["new_devices"] = [e["detail"] for e in ev["events"] if e["title"] == "Neues Gerät im Netzwerk"]
    rep["updates"] = [e for e in ev["events"] if e["title"] == "Dashboard aktualisiert"]
    rep["disk"] = disk_forecast()
    rep["status"] = status_summary()
    return rep
