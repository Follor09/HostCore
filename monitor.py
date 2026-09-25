"""
Long-running observations, sampled together with the metrics history (once a minute):

- Throttling log: when the Pi hit under-voltage, frequency capping, thermal throttling
  or the soft temperature limit - including short events between two samples (from the
  "has occurred" bits vcgencmd keeps until reboot).
- Service uptime: periods per service (active / inactive / error), for uptime % and a
  timeline. Gaps where the dashboard itself was off count as "unknown".
- Traffic per network interface (LAN, WLAN, VPN ...), 7 days like the other metrics.

Everything lives in history.db next to the metrics.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

import psutil

import history
import services_manager
import system_info

logger = logging.getLogger("pi_dashboard.monitor")

RETENTION_DAYS = 90            # throttling events + service periods
IFACE_RETENTION = history.RETENTION_SECONDS
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
        measured = sum(svc["seconds"].values())
        up = svc["seconds"].get("active", 0)
        # Outages: every non-active period, newest first
        outages = [p for p in svc["periods"] if p["status"] != "active"]
        result.append({
            "id": sid, "name": name, "measured": measured,
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
        self.prev, self.t = cur, time.time()


def interfaces() -> List[str]:
    """Interfaces with traffic in the stored history (for the chart's selector)."""
    with connect() as conn:
        rows = conn.execute("""SELECT iface, SUM(down + up) AS total FROM iface_samples
                               WHERE ts >= ? GROUP BY iface HAVING total > 0 ORDER BY total DESC""",
                            (int(time.time()) - IFACE_RETENTION,)).fetchall()
    return [r[0] for r in rows]


def iface_history(iface: str, since: int, bucket: int) -> Dict[int, Dict[str, float]]:
    with connect() as conn:
        rows = conn.execute(f"""SELECT (ts / {bucket}) * {bucket} AS b, AVG(down), AVG(up) FROM iface_samples
                                WHERE iface = ? AND ts >= ? GROUP BY b""", (iface, since)).fetchall()
    return {b: {"net_down": round(d, 1), "net_up": round(u, 1)} for b, d, u in rows}


# ---------------------------------------------------------------------------
# Sampler hook (called from history.background_sampler once a minute)
# ---------------------------------------------------------------------------
class Monitor:
    def __init__(self):
        self.throttle = _ThrottleWatch()
        self.ifaces = _IfaceWatch()
        self.last_cleanup = 0.0

    def sample(self, temp: Optional[float]) -> None:
        now = int(time.time())
        with connect() as conn:
            for name, step in (("throttle", lambda: self.throttle.sample(conn, now, temp)),
                               ("services", lambda: _sample_services(conn, now)),
                               ("interfaces", lambda: self.ifaces.sample(conn, now))):
                try:
                    step()
                except Exception as e:
                    logger.warning(f"Monitor {name} sample failed: {e}")
            if now - self.last_cleanup > 3600:
                self.last_cleanup = now
                cutoff = now - RETENTION_DAYS * 86400
                conn.execute("DELETE FROM throttle_events WHERE end < ?", (cutoff,))
                conn.execute("DELETE FROM service_periods WHERE end < ?", (cutoff,))
                conn.execute("DELETE FROM iface_samples WHERE ts < ?", (now - IFACE_RETENTION,))


def clear() -> None:
    with connect() as conn:
        for table in ("throttle_events", "service_periods", "iface_samples"):
            conn.execute(f"DELETE FROM {table}")
