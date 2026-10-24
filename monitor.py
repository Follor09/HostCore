"""
Long-running observations, sampled together with the metrics history (once a minute):

- Throttling log: when the Pi hit under-voltage, frequency capping, thermal throttling
  or the soft temperature limit - including short events between two samples (from the
  "has occurred" bits vcgencmd keeps until reboot).
- Service uptime: periods per service (active / inactive / error), for uptime % and a
  timeline. Gaps where the dashboard itself was off count as "unknown".
- Traffic per network interface (LAN, WLAN, VPN ...), 7 days like the other metrics,
  plus daily byte counters per interface for the data usage view (2 years).
- Resource hogs: CPU and RAM per program once a minute (7 days).
- Key sensor values (temperatures, supply voltage) for the sensor history (7 days).

Everything lives in history.db next to the metrics.
"""

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

import os

import history
import sensors
import services_manager
import system_info
import systools

logger = logging.getLogger("pi_dashboard.monitor")

RETENTION_DAYS = 730          # upper limit for the throttling / uptime views (actual: retention "uptime")
PROC_KEEP = 12                 # programs stored per sample (top by CPU + top by RAM)
GAP_SECONDS = 3 * 60           # longer without a sample = dashboard was off

THROTTLE_BITS = {              # kind: (bit active now, bit "has occurred" since boot)
    "undervoltage": (0, 16),
    "freq_capped": (1, 17),
    "throttled": (2, 18),
    "soft_temp": (3, 19),
}


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS throttle_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, start INTEGER, end INTEGER,
        open INTEGER, temp REAL, short INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS service_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, status TEXT, start INTEGER, end INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS service_periods_idx ON service_periods (service, end)")
    conn.execute("""CREATE TABLE IF NOT EXISTS iface_samples (
        ts INTEGER, iface TEXT, down REAL, up REAL, PRIMARY KEY (ts, iface))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS traffic_daily (
        day TEXT, iface TEXT, rx INTEGER, tx INTEGER, PRIMARY KEY (day, iface))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS proc_samples (
        ts INTEGER, label TEXT, cpu REAL, rss_mb REAL, count INTEGER, PRIMARY KEY (ts, label))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sensor_samples (
        ts INTEGER, sensor TEXT, value REAL, PRIMARY KEY (ts, sensor))""")


def connect():
    conn = history._connect()
    _init(conn)
    return conn


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------
class _ThrottleWatch:
    def __init__(self):
        self.prev_sticky = None  # sticky bits seen at the last sample (None = first sample since start)

    def sample(self, conn, now: int, temp: Optional[float]) -> None:
        raw = system_info.read_throttled_raw()
        if raw is None:
            return
        for kind, (now_bit, sticky_bit) in THROTTLE_BITS.items():
            active = bool(raw & (1 << now_bit))
            sticky = bool(raw & (1 << sticky_bit))
            was_sticky = self.prev_sticky is not None and bool(self.prev_sticky & (1 << sticky_bit))
            open_row = conn.execute("SELECT id FROM throttle_events WHERE kind = ? AND open = 1", (kind,)).fetchone()
            if active:
                if open_row:
                    conn.execute("UPDATE throttle_events SET end = ? WHERE id = ?", (now, open_row[0]))
                else:
                    conn.execute("INSERT INTO throttle_events (kind, start, end, open, temp, short) VALUES (?, ?, ?, 1, ?, 0)",
                                 (kind, now, now, temp))
            else:
                if open_row:
                    conn.execute("UPDATE throttle_events SET end = ?, open = 0 WHERE id = ?", (now, open_row[0]))
                elif sticky and self.prev_sticky is not None and not was_sticky:
                    # Happened and ended between two samples: shorter than a minute
                    conn.execute("INSERT INTO throttle_events (kind, start, end, open, temp, short) VALUES (?, ?, ?, 0, ?, 1)",
                                 (kind, now - history.SAMPLE_INTERVAL, now, temp))
        if self.prev_sticky is None:
            # Right after start: events that occurred since boot but before we watched
            for kind, (now_bit, sticky_bit) in THROTTLE_BITS.items():
                if raw & (1 << sticky_bit) and not raw & (1 << now_bit):
                    boot = int(psutil.boot_time())
                    seen = conn.execute("SELECT 1 FROM throttle_events WHERE kind = ? AND end >= ?", (kind, boot)).fetchone()
                    if not seen:
                        conn.execute("INSERT INTO throttle_events (kind, start, end, open, temp, short) VALUES (?, ?, ?, 0, NULL, 2)",
                                     (kind, boot, now))
        self.prev_sticky = raw


def throttle_log(days: int = 30) -> Dict[str, Any]:
    since = int(time.time()) - days * 86400
    with connect() as conn:
        rows = conn.execute("""SELECT kind, start, end, open, temp, short FROM throttle_events
                               WHERE end >= ? ORDER BY start DESC LIMIT 200""", (since,)).fetchall()
    events = [{"kind": k, "start": s, "end": e, "open": bool(o), "temp": t,
               "duration": e - s, "short": sh == 1, "since_boot": sh == 2} for k, s, e, o, t, sh in rows]
    counts = {k: sum(1 for ev in events if ev["kind"] == k) for k in THROTTLE_BITS}
    return {"supported": system_info.is_raspberry_pi(), "days": days, "events": events, "counts": counts,
            "current": system_info.get_throttled_status()}


# ---------------------------------------------------------------------------
# Service uptime
# ---------------------------------------------------------------------------
def _sample_services(conn, now: int) -> None:
    for s in services_manager.get_services_status():
        last = conn.execute("SELECT id, status, end FROM service_periods WHERE service = ? ORDER BY end DESC LIMIT 1",
                            (s["id"],)).fetchone()
        if last and last[1] == s["status"] and now - last[2] <= GAP_SECONDS:
            conn.execute("UPDATE service_periods SET end = ? WHERE id = ?", (now, last[0]))
        else:
            # Status changed (or the dashboard was off): the new period starts where the last ended
            start = last[2] if last and now - last[2] <= GAP_SECONDS else now
            conn.execute("INSERT INTO service_periods (service, status, start, end) VALUES (?, ?, ?, ?)",
                         (s["id"], s["status"], start, now))


def service_uptime(days: int = 7) -> Dict[str, Any]:
    """Uptime per service; time inside maintenance windows is left out of the measurement."""
    import maintenance
    now = int(time.time())
    since = now - days * 86400
    names = {s["id"]: s["name"] for s in services_manager.get_services_status()}
    with connect() as conn:
        rows = conn.execute("""SELECT service, status, start, end FROM service_periods
                               WHERE end >= ? ORDER BY start""", (since,)).fetchall()
    services: Dict[str, Dict[str, Any]] = {}
    for sid, status, start, end in rows:
        if sid not in names:
            continue  # hidden or removed service
        start = max(start, since)
        svc = services.setdefault(sid, {"id": sid, "name": names[sid], "periods": [], "seconds": {}})
        svc["periods"].append({"status": status, "start": start, "end": end})
        svc["seconds"][status] = svc["seconds"].get(status, 0) + (end - start)
    result = []
    for sid, name in names.items():
        svc = services.get(sid, {"id": sid, "name": name, "periods": [], "seconds": {}})
        spans = maintenance.intervals(f"service:{sid}", since, now)
        in_maint = sum(maintenance.overlap(spans, p["start"], p["end"]) for p in svc["periods"] if p["status"] != "active")
        measured = sum(svc["seconds"].values()) - in_maint
        up = svc["seconds"].get("active", 0)
        # Outages: every non-active period, newest first (fully inside maintenance: not an outage)
        outages = [{**p, "maintenance": maintenance.overlap(spans, p["start"], p["end"]) > 0}
                   for p in svc["periods"] if p["status"] != "active"
                   and maintenance.overlap(spans, p["start"], p["end"]) < p["end"] - p["start"]]
        result.append({
            "id": sid, "name": name, "measured": measured, "maintenance_seconds": int(in_maint),
            "uptime": round(up / measured * 100, 2) if measured else None,
            "periods": svc["periods"][-500:],
            "outages": [{**p, "duration": p["end"] - p["start"]} for p in reversed(outages)][:20],
            "current": svc["periods"][-1]["status"] if svc["periods"] else None,
        })
    return {"days": days, "since": since, "now": now, "services": result}


# ---------------------------------------------------------------------------
# Traffic per interface
# ---------------------------------------------------------------------------
_SKIP_IFACE = re.compile(r"^(lo\d*|docker|br-|veth|cni|flannel|cali|kube|virbr|bridge|awdl|llw|anpi|ap\d|gif|stf|utun\d{2,})")


class _IfaceWatch:
    def __init__(self):
        self.prev = psutil.net_io_counters(pernic=True)
        self.t = time.time()

    def sample(self, conn, now: int) -> None:
        cur = psutil.net_io_counters(pernic=True)
        dt = max(time.time() - self.t, 1e-6)
        for name, c in cur.items():
            p = self.prev.get(name)
            if not p or _SKIP_IFACE.match(name):
                continue
            down, up = c.bytes_recv - p.bytes_recv, c.bytes_sent - p.bytes_sent
            if down < 0 or up < 0:
                continue  # counter reset
            conn.execute("INSERT OR REPLACE INTO iface_samples (ts, iface, down, up) VALUES (?, ?, ?, ?)",
                         (now, name, round(down / dt / 1024, 1), round(up / dt / 1024, 1)))
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            conn.execute("""INSERT INTO traffic_daily (day, iface, rx, tx) VALUES (?, ?, ?, ?)
                            ON CONFLICT(day, iface) DO UPDATE SET rx = rx + excluded.rx, tx = tx + excluded.tx""",
                         (day, name, down, up))
        self.prev, self.t = cur, time.time()


def interfaces() -> List[str]:
    """Interfaces with traffic in the stored history (for the chart's selector)."""
    with connect() as conn:
        rows = conn.execute("""SELECT iface, SUM(down + up) AS total FROM iface_samples
                               WHERE ts >= ? GROUP BY iface HAVING total > 0 ORDER BY total DESC""",
                            (int(time.time()) - history.retention_seconds(),)).fetchall()
    return [r[0] for r in rows]


def iface_history(iface: str, since: int, bucket: int) -> Dict[int, Dict[str, float]]:
    with connect() as conn:
        rows = conn.execute(f"""SELECT (ts / {bucket}) * {bucket} AS b, AVG(down), AVG(up) FROM iface_samples
                                WHERE iface = ? AND ts >= ? GROUP BY b""", (iface, since)).fetchall()
    return {b: {"net_down": round(d, 1), "net_up": round(u, 1)} for b, d, u in rows}


# ---------------------------------------------------------------------------
# Resource hogs (CPU / RAM per program)
# ---------------------------------------------------------------------------
INTERPRETERS = re.compile(r"^(python[\d.]*|node|nodejs|java|bash|sh|dash|perl|ruby|php[\d.]*|deno|bun)$")


def program_label(name: str, cmdline: List[str]) -> str:
    """Program name; for interpreters the script too ("python3 app.py" instead of "python3")."""
    if INTERPRETERS.match(name or "") and len(cmdline) > 1:
        for arg in cmdline[1:]:
            if arg and not arg.startswith("-"):
                return f"{name} {os.path.basename(arg)[:40]}"
    return name or "?"


class _ProcWatch:
    def __init__(self):
        self.prev: Dict[tuple, float] = {}
        self.t = time.time()
        self.cores = psutil.cpu_count() or 1

    def sample(self, conn, now: int) -> None:
        cur: Dict[tuple, float] = {}
        groups: Dict[str, Dict[str, float]] = {}
        dt = max(time.time() - self.t, 1e-6)
        for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time", "cpu_times"]):
            info = p.info
            if not info.get("cpu_times") or not info.get("create_time"):
                continue
            key = (info["pid"], info["create_time"])
            total = info["cpu_times"].user + info["cpu_times"].system
            cur[key] = total
            if key not in self.prev:
                continue  # new since the last sample: no rate yet
            cpu = max(0.0, (total - self.prev[key]) / dt * 100 / self.cores)
            rss = (info["memory_info"].rss if info.get("memory_info") else 0) / 1048576
            label = program_label(info.get("name") or "", info.get("cmdline") or [])
            g = groups.setdefault(label, {"cpu": 0.0, "rss": 0.0, "count": 0})
            g["cpu"] += cpu
            g["rss"] += rss
            g["count"] += 1
        self.prev, self.t = cur, time.time()
        if not groups:
            return
        by_cpu = sorted(groups, key=lambda k: groups[k]["cpu"], reverse=True)[:PROC_KEEP]
        by_rss = sorted(groups, key=lambda k: groups[k]["rss"], reverse=True)[:PROC_KEEP]
        for label in set(by_cpu) | set(by_rss):
            g = groups[label]
            conn.execute("INSERT OR REPLACE INTO proc_samples (ts, label, cpu, rss_mb, count) VALUES (?, ?, ?, ?, ?)",
                         (now, label, round(g["cpu"], 2), round(g["rss"], 1), g["count"]))


def resource_hogs(hours: int = 24) -> Dict[str, Any]:
    now = int(time.time())
    since = now - hours * 3600
    bucket = 3600 if hours <= 48 else 6 * 3600
    with connect() as conn:
        minutes = conn.execute("SELECT COUNT(DISTINCT ts) FROM proc_samples WHERE ts >= ?", (since,)).fetchone()[0] or 0
        rows = conn.execute("""SELECT label, SUM(cpu), MAX(cpu), AVG(rss_mb), MAX(rss_mb), MAX(count), COUNT(*)
                               FROM proc_samples WHERE ts >= ? GROUP BY label""", (since,)).fetchall()
        series_rows = conn.execute(f"""SELECT label, (ts / {bucket}) * {bucket} AS b, SUM(cpu), MAX(rss_mb)
                                       FROM proc_samples WHERE ts >= ? GROUP BY label, b""", (since,)).fetchall()
    series: Dict[str, Dict[int, List[float]]] = {}
    for label, b, cpu_sum, rss in series_rows:
        series.setdefault(label, {})[b] = [cpu_sum, rss]
    buckets = list(range((since // bucket) * bucket, now + 1, bucket))
    items = []
    for label, cpu_sum, cpu_max, rss_avg, rss_max, procs, seen in rows:
        s = series.get(label, {})
        items.append({
            "label": label,
            "cpu_avg": round(cpu_sum / minutes, 2) if minutes else 0,     # over the whole range
            "cpu_max": round(cpu_max, 1),
            "cpu_minutes": round(cpu_sum / 100, 1),                       # minutes of full-CPU time
            "rss_avg": round(rss_avg, 1), "rss_max": round(rss_max, 1),
            "processes": procs, "seen_minutes": seen,
            "cpu_series": [round(s[b][0] / (bucket / 60), 2) if b in s else 0 for b in buckets],
            "rss_series": [s[b][1] if b in s else None for b in buckets],
        })
    return {"hours": hours, "minutes": minutes, "bucket": bucket, "buckets": buckets,
            "by_cpu": sorted(items, key=lambda i: i["cpu_minutes"], reverse=True)[:15],
            "by_ram": sorted(items, key=lambda i: i["rss_max"], reverse=True)[:15]}


# ---------------------------------------------------------------------------
# Sensors (history of the key values)
# ---------------------------------------------------------------------------
def _sample_sensors(conn, now: int) -> None:
    for sid, value in sensors.history_values().items():
        conn.execute("INSERT OR REPLACE INTO sensor_samples (ts, sensor, value) VALUES (?, ?, ?)", (now, sid, value))


def sensor_history(hours: int = 24) -> Dict[str, Any]:
    now = int(time.time())
    since = now - hours * 3600
    bucket = 300 if hours <= 24 else 1800
    with connect() as conn:
        rows = conn.execute(f"""SELECT sensor, (ts / {bucket}) * {bucket} AS b, AVG(value), MAX(value), MIN(value)
                                FROM sensor_samples WHERE ts >= ? GROUP BY sensor, b ORDER BY b""", (since,)).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sid, b, avg, mx, mn in rows:
        out.setdefault(sid, []).append({"ts": b, "avg": round(avg, 3), "max": round(mx, 3), "min": round(mn, 3)})
    return {"hours": hours, "bucket": bucket, "series": out}


# ---------------------------------------------------------------------------
# Data usage (daily byte counters)
# ---------------------------------------------------------------------------
def data_usage(iface: str = "") -> Dict[str, Any]:
    """Per day (60 days) and per month (24 months). Without iface: LAN + WLAN only -
    VPN traffic also flows over those and would be counted twice."""
    with connect() as conn:
        rows = conn.execute("SELECT day, iface, rx, tx FROM traffic_daily ORDER BY day").fetchall()
    ifaces = sorted({r[1] for r in rows})
    physical = {i for i in ifaces if systools._iface_kind(i) in ("ethernet", "wlan")}
    days: Dict[str, List[int]] = {}
    for day, name, rx, tx in rows:
        if (iface and name != iface) or (not iface and name not in physical):
            continue
        d = days.setdefault(day, [0, 0])
        d[0] += rx
        d[1] += tx
    months: Dict[str, List[int]] = {}
    for day, (rx, tx) in days.items():
        m = months.setdefault(day[:7], [0, 0])
        m[0] += rx
        m[1] += tx
    today = time.strftime("%Y-%m-%d")
    this_month = today[:7]
    daily = [{"day": d, "rx": v[0], "tx": v[1]} for d, v in sorted(days.items())][-60:]
    return {"iface": iface, "interfaces": ifaces, "physical": sorted(physical),
            "today": {"rx": days.get(today, [0, 0])[0], "tx": days.get(today, [0, 0])[1]},
            "month": {"rx": months.get(this_month, [0, 0])[0], "tx": months.get(this_month, [0, 0])[1]},
            "days": daily,
            "months": [{"month": m, "rx": v[0], "tx": v[1]} for m, v in sorted(months.items())][-24:],
            "since": min(days) if days else None}


# ---------------------------------------------------------------------------
# Sampler hook (called from history.background_sampler once a minute)
# ---------------------------------------------------------------------------
def _sample_containers(conn, now: int) -> None:
    import containers
    containers.sample(conn, now)


class Monitor:
    def __init__(self):
        self.throttle = _ThrottleWatch()
        self.ifaces = _IfaceWatch()
        self.procs = _ProcWatch()
        self.last_cleanup = 0.0

    def sample(self, temp: Optional[float]) -> None:
        now = int(time.time())
        with connect() as conn:
            for name, step in (("throttle", lambda: self.throttle.sample(conn, now, temp)),
                               ("services", lambda: _sample_services(conn, now)),
                               ("interfaces", lambda: self.ifaces.sample(conn, now)),
                               ("processes", lambda: self.procs.sample(conn, now)),
                               ("sensors", lambda: _sample_sensors(conn, now)),
                               ("containers", lambda: _sample_containers(conn, now))):
                try:
                    step()
                except Exception as e:
                    logger.warning(f"Monitor {name} sample failed: {e}")
            # Every sample: anomalies (checks itself every 10 min), DNS monitoring and IP owners (when due)
            try:
                import anomalies
                anomalies.check()
            except Exception as e:
                logger.warning(f"Anomaly check failed: {e}")
            if now % 300 < 60:
                try:
                    import netscan
                    netscan.watch_ip_owners()
                except Exception as e:
                    logger.warning(f"IP watch failed: {e}")
            try:
                import dbmonitor
                dbmonitor.check_if_due()
            except Exception as e:
                logger.warning(f"Database check failed: {e}")
            try:
                import dnswatch
                threading.Thread(target=dnswatch.check_if_due, daemon=True).start()
            except Exception as e:
                logger.warning(f"DNS watch failed: {e}")
            if now - self.last_cleanup > 3600:
                self.last_cleanup = now
                try:
                    import insights
                    insights.disk_snapshot()
                except Exception as e:
                    logger.warning(f"Disk snapshot failed: {e}")
                try:
                    import power
                    power.rollup()
                except Exception as e:
                    logger.warning(f"Power roll-up failed: {e}")
                try:
                    import imagecheck
                    imagecheck.check_if_due()
                except Exception as e:
                    logger.warning(f"Image check failed: {e}")
                try:
                    import geoip
                    geoip.refresh_if_old()
                except Exception as e:
                    logger.warning(f"GeoIP refresh failed: {e}")
                try:
                    import capacity
                    capacity.rollup()
                except Exception as e:
                    logger.warning(f"Capacity roll-up failed: {e}")
                try:
                    import backups
                    backups.check_if_due()
                except Exception as e:
                    logger.warning(f"Backup check failed: {e}")
                try:
                    import domains
                    threading.Thread(target=domains.check_if_due, daemon=True).start()
                except Exception as e:
                    logger.warning(f"Domain check failed: {e}")
                for mod in ("vulns", "eol", "selftest", "hardening", "stackcheck"):
                    try:
                        __import__(mod).check_if_due()
                    except Exception as e:
                        logger.warning(f"{mod} check failed: {e}")
                for mod, fn in (("audit", "checkpoint_if_due"), ("versions", "cleanup")):
                    try:
                        getattr(__import__(mod), fn)()
                    except Exception as e:
                        logger.warning(f"{mod}.{fn} failed: {e}")
                try:
                    import terminal
                    terminal.cleanup()
                    import recordings
                    recordings.cleanup()
                except Exception as e:
                    logger.warning(f"Terminal clean-up failed: {e}")
                try:
                    import files
                    files.trash_purge()
                except Exception as e:
                    logger.warning(f"Trash purge failed: {e}")
                cleanup(conn)

def cleanup(conn=None) -> None:
    """Drop everything older than the limits under Einstellungen -> Datenaufbewahrung."""
    import retention
    if conn is None:
        with connect() as c:
            return cleanup(c)
    now = int(time.time())
    metrics = now - retention.seconds("metrics")
    cutoff = now - retention.seconds("uptime")
    conn.execute("DELETE FROM throttle_events WHERE end < ?", (cutoff,))
    conn.execute("DELETE FROM service_periods WHERE end < ?", (cutoff,))
    conn.execute("DELETE FROM samples WHERE ts < ?", (metrics,))
    conn.execute("DELETE FROM iface_samples WHERE ts < ?", (metrics,))
    conn.execute("DELETE FROM proc_samples WHERE ts < ?", (metrics,))
    conn.execute("DELETE FROM sensor_samples WHERE ts < ?", (metrics,))
    conn.execute("DELETE FROM traffic_daily WHERE day < ?",
                 (time.strftime("%Y-%m-%d", time.localtime(now - retention.seconds("traffic"))),))
    try:
        conn.execute("DELETE FROM container_samples WHERE ts < ?", (metrics,))
    except Exception:
        pass  # table created on the first container sample
    try:
        day = time.strftime("%Y-%m-%d", time.localtime(now - retention.seconds("disk")))
        conn.execute("DELETE FROM disk_daily WHERE day < ?", (day,))
        conn.execute("DELETE FROM folder_sizes WHERE day < ?", (day,))
    except Exception:
        pass


def clear() -> None:
    with connect() as conn:
        for table in ("throttle_events", "service_periods", "iface_samples"):
            conn.execute(f"DELETE FROM {table}")
