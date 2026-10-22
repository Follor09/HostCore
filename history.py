"""
Metrics history for the dashboard.
Samples CPU, temperature, RAM, network and fan level once a minute into SQLite
(history.db, untracked) and keeps 7 days. Served downsampled for 24h / 7d charts.

NVMe health gets one row per day (wear, data written, temperature range) kept for
3 years, so slow trends like wear become visible.
"""

import asyncio
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List

import psutil

import homelab
import system_info

logger = logging.getLogger("pi_dashboard.history")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "history.db")

SAMPLE_INTERVAL = 60                 # seconds between samples
RETENTION_SECONDS = 7 * 24 * 3600

# range -> (window seconds, bucket seconds): ~288 points for 24h, 336 for 7d
RANGES = {
    "24h": (24 * 3600, 5 * 60),
    "7d": (7 * 24 * 3600, 30 * 60),
}

METRICS = ["cpu", "temp", "ram", "net_down", "net_up", "fan", "power", "nvme_temp", "disk_read", "disk_write"]

NVME_SNAPSHOT_INTERVAL = 3600
NVME_RETENTION_DAYS = 3 * 365
NVME_COLUMNS = ["used", "spare", "tb_written", "tb_read", "hours", "media_errors", "unsafe_shutdowns",
                "temp_avg", "temp_max", "temp_min"]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts INTEGER PRIMARY KEY,
            cpu REAL, temp REAL, ram REAL,
            net_down REAL, net_up REAL, fan REAL
        )
    """)
    # Columns added in later versions: extend older databases in place
    existing = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    for column in METRICS:
        if column not in existing:
            conn.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS nvme_daily (
            day TEXT PRIMARY KEY, ts INTEGER, {', '.join(f'{c} REAL' for c in NVME_COLUMNS)}
        )
    """)
    return conn


class _Sampler:
    """Averages CPU and network over the whole interval, not just the last instant."""

    def __init__(self):
        self.cpu_times = psutil.cpu_times()
        self.net = psutil.net_io_counters()
        self.disk = system_info.disk_io_counters()
        self.t = time.time()

    def sample(self) -> Dict[str, Any]:
        now = time.time()
        cpu_times, net = psutil.cpu_times(), psutil.net_io_counters()
        dt = max(now - self.t, 1e-6)

        total = sum(cpu_times) - sum(self.cpu_times)
        idle = (cpu_times.idle + getattr(cpu_times, "iowait", 0)) - \
               (self.cpu_times.idle + getattr(self.cpu_times, "iowait", 0))
        cpu = max(0.0, min(100.0, 100.0 * (1 - idle / total))) if total > 0 else 0.0

        recv = net.bytes_recv - self.net.bytes_recv
        sent = net.bytes_sent - self.net.bytes_sent

        # Disk throughput summed over the physical disks present in both readings
        disk = system_info.disk_io_counters()
        common = disk.keys() & self.disk.keys()
        d_read = sum(disk[n].read_bytes - self.disk[n].read_bytes for n in common)
        d_write = sum(disk[n].write_bytes - self.disk[n].write_bytes for n in common)

        self.cpu_times, self.net, self.disk, self.t = cpu_times, net, disk, now

        temp = system_info.get_cpu_temp()
        fan = system_info.get_fan_status()
        power = system_info.get_power_status()
        return {
            "ts": int(now),
            "cpu": round(cpu, 1),
            "temp": None if temp.get("is_simulated") else temp.get("celsius"),
            "ram": psutil.virtual_memory().percent,
            # negative deltas mean the counters were reset
            "net_down": round(recv / dt / 1024, 1) if recv >= 0 else None,
            "net_up": round(sent / dt / 1024, 1) if sent >= 0 else None,
            "fan": fan.get("level") if fan.get("available") else None,
            "power": power.get("board_watt") if power.get("available") else None,
            "nvme_temp": homelab._nvme_temp(),
            "disk_read": round(d_read / dt / 1024, 1) if common and d_read >= 0 else None,
            "disk_write": round(d_write / dt / 1024, 1) if common and d_write >= 0 else None,
        }


def _store(row: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO samples (ts, {', '.join(METRICS)}) VALUES (?{', ?' * len(METRICS)})",
            [row["ts"]] + [row[m] for m in METRICS],
        )
        conn.execute("DELETE FROM samples WHERE ts < ?", (row["ts"] - RETENTION_SECONDS,))


def nvme_snapshot() -> None:
    """Upsert today's NVMe row: SMART values now, temperature range from today's samples."""
    health = homelab.get_nvme_health()
    if not health.get("present"):
        return
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    midnight = int(time.mktime(time.strptime(day, "%Y-%m-%d")))
    with _connect() as conn:
        t_avg, t_max, t_min = conn.execute(
            "SELECT AVG(nvme_temp), MAX(nvme_temp), MIN(nvme_temp) FROM samples WHERE ts >= ?", (midnight,)).fetchone()
        smart = health if health.get("smart") else {}
        row = {
            "used": smart.get("percentage_used"), "spare": smart.get("available_spare"),
            "tb_written": smart.get("tb_written"), "tb_read": smart.get("tb_read"),
            "hours": smart.get("power_on_hours"), "media_errors": smart.get("media_errors"),
            "unsafe_shutdowns": smart.get("unsafe_shutdowns"),
            "temp_avg": round(t_avg, 1) if t_avg is not None else health.get("temp"),
            "temp_max": t_max if t_max is not None else health.get("temp"),
            "temp_min": t_min if t_min is not None else health.get("temp"),
        }
        conn.execute(
            f"INSERT OR REPLACE INTO nvme_daily (day, ts, {', '.join(NVME_COLUMNS)}) VALUES (?, ?{', ?' * len(NVME_COLUMNS)})",
            [day, int(now)] + [row[c] for c in NVME_COLUMNS])
        cutoff = time.strftime("%Y-%m-%d", time.localtime(now - NVME_RETENTION_DAYS * 86400))
        conn.execute("DELETE FROM nvme_daily WHERE day < ?", (cutoff,))


async def background_sampler() -> None:
    import monitor  # imports this module too
    logger.info(f"History sampler started (every {SAMPLE_INTERVAL}s, db: {DB_PATH})")
    sampler = _Sampler()
    watcher = monitor.Monitor()
    last_nvme = 0.0
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL)
        row = {}
        try:
            row = sampler.sample()
            await asyncio.to_thread(_store, row)
        except Exception as e:
            logger.warning(f"History sample failed: {e}")
        try:
            await asyncio.to_thread(watcher.sample, row.get("temp"))
        except Exception as e:
            logger.warning(f"Monitor sample failed: {e}")
        if time.time() - last_nvme >= NVME_SNAPSHOT_INTERVAL:
            last_nvme = time.time()
            try:
                await asyncio.to_thread(nvme_snapshot)
            except Exception as e:
                logger.warning(f"NVMe snapshot failed: {e}")


def get_history(range_key: str, iface: str = "") -> Dict[str, Any]:
    """Bucketed averages (and CPU/temp maxima) for the requested range.
    With `iface`, the network values are that interface's instead of the total."""
    window, bucket = RANGES.get(range_key, RANGES["24h"])
    since = int(time.time()) - window

    if not os.path.exists(DB_PATH):
        return {"range": range_key, "bucket_seconds": bucket, "points": []}

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT (ts / {bucket}) * {bucket} AS b,
                   AVG(cpu), MAX(cpu), AVG(temp), MAX(temp), AVG(ram),
                   AVG(net_down), AVG(net_up), AVG(fan), MAX(fan), AVG(power), MAX(power),
                   AVG(nvme_temp), MAX(nvme_temp), AVG(disk_read), AVG(disk_write)
            FROM samples WHERE ts >= ?
            GROUP BY b ORDER BY b
        """, (since,)).fetchall()

    def r(v, nd=1):
        return None if v is None else round(v, nd)

    points: List[Dict[str, Any]] = [
        {
            "ts": b, "cpu": r(cpu), "cpu_max": r(cpu_max), "temp": r(temp), "temp_max": r(temp_max),
            "ram": r(ram), "net_down": r(down), "net_up": r(up), "fan": r(fan, 2), "fan_max": fan_max,
            "power": r(power, 2), "power_max": r(power_max, 2),
            "nvme_temp": r(nvme_temp), "nvme_temp_max": r(nvme_temp_max),
            "disk_read": r(d_read), "disk_write": r(d_write),
        }
        for b, cpu, cpu_max, temp, temp_max, ram, down, up, fan, fan_max, power, power_max, nvme_temp, nvme_temp_max, d_read, d_write in rows
    ]
    if iface:
        import monitor
        per_iface = monitor.iface_history(iface, since, bucket)
        for p in points:
            p.update(per_iface.get(p["ts"], {"net_down": None, "net_up": None}))
    return {"range": range_key, "bucket_seconds": bucket, "points": points, "iface": iface}


def get_raw(start: int, end: int) -> Dict[str, Any]:
    """Every stored sample (one per minute) between start and end - for the day replay."""
    end = min(int(end), int(time.time()))
    start = max(int(start), end - 2 * 86400)
    if not os.path.exists(DB_PATH):
        return {"start": start, "end": end, "samples": []}
    with _connect() as conn:
        rows = conn.execute(f"SELECT ts, {', '.join(METRICS)} FROM samples WHERE ts >= ? AND ts <= ? ORDER BY ts",
                            (start, end)).fetchall()
        oldest = conn.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
    return {"start": start, "end": end, "oldest": oldest, "metrics": METRICS,
            "samples": [list(r) for r in rows]}


def get_stats() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {"size_bytes": 0, "samples": 0, "oldest": None, "newest": None}
    with _connect() as conn:
        count, oldest, newest = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples").fetchone()
    return {"size_bytes": os.path.getsize(DB_PATH), "samples": count, "oldest": oldest, "newest": newest,
            "retention_days": RETENTION_SECONDS // 86400, "interval_seconds": SAMPLE_INTERVAL}


def clear() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM samples")
        # Traffic per interface belongs to the metrics; throttling log and uptime are kept
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'iface_samples'").fetchone():
            conn.execute("DELETE FROM iface_samples")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("VACUUM")  # actually give the space back
    conn.close()


def get_nvme_history(days: int = 90) -> Dict[str, Any]:
    """Daily NVMe rows for the last `days` days plus trend figures."""
    days = max(7, min(int(days), NVME_RETENTION_DAYS))
    if not os.path.exists(DB_PATH):
        return {"days": [], "summary": {}}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM nvme_daily WHERE day >= ? ORDER BY day", (since,))]
        # The day before the range, so the first day's writes can be computed too
        prev = conn.execute("SELECT * FROM nvme_daily WHERE day < ? ORDER BY day DESC LIMIT 1", (since,)).fetchone()
        first = conn.execute("SELECT day, used FROM nvme_daily WHERE used IS NOT NULL ORDER BY day LIMIT 1").fetchone()

    last_row = dict(prev) if prev else None
    for r in rows:
        r["written_gb"] = None
        if last_row and r["tb_written"] is not None and last_row["tb_written"] is not None:
            gap = max(1, round((time.mktime(time.strptime(r["day"], "%Y-%m-%d"))
                                - time.mktime(time.strptime(last_row["day"], "%Y-%m-%d"))) / 86400))
            # Several days without data: spread the difference evenly (average per day)
            r["written_gb"] = round(max(0.0, r["tb_written"] - last_row["tb_written"]) * 1000 / gap, 1)
        last_row = r

    summary: Dict[str, Any] = {}
    smart_rows = [r for r in rows if r["used"] is not None]
    if smart_rows:
        latest = smart_rows[-1]
        start = smart_rows[0]
        summary.update(used=latest["used"], used_change=latest["used"] - start["used"],
                       spare=latest["spare"], media_errors=latest["media_errors"],
                       unsafe_change=(latest["unsafe_shutdowns"] or 0) - (start["unsafe_shutdowns"] or 0))
        written = [r["written_gb"] for r in rows if r["written_gb"] is not None]
        summary["avg_written_gb"] = round(sum(written) / len(written), 1) if written else None

        # Remaining life from the wear trend since the first recorded day (needs a month of data)
        if first:
            span = (time.time() - time.mktime(time.strptime(first["day"], "%Y-%m-%d"))) / 86400
            summary["tracked_days"] = int(span)
            rise = latest["used"] - first["used"]
            if span < 30:
                summary["life_status"] = "collecting"
            elif rise <= 0:
                summary["life_status"] = "no_wear"
            else:
                per_year = rise / span * 365
                summary.update(life_status="estimate", wear_per_year=round(per_year, 2),
                               remaining_years=round(max(0.0, 100 - latest["used"]) / per_year, 1))
    temps = [r["temp_max"] for r in rows if r["temp_max"] is not None]
    if temps:
        summary["temp_max"] = max(temps)
    return {"days": rows, "summary": summary, "range_days": days}
