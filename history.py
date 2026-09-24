"""
Metrics history for the dashboard.
Samples CPU, temperature, RAM, network and fan level once a minute into SQLite
(history.db, untracked) and keeps 7 days. Served downsampled for 24h / 7d charts.
"""

import asyncio
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List

import psutil

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

METRICS = ["cpu", "temp", "ram", "net_down", "net_up", "fan"]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts INTEGER PRIMARY KEY,
            cpu REAL, temp REAL, ram REAL,
            net_down REAL, net_up REAL, fan REAL
        )
    """)
    return conn


class _Sampler:
    """Averages CPU and network over the whole interval, not just the last instant."""

    def __init__(self):
        self.cpu_times = psutil.cpu_times()
        self.net = psutil.net_io_counters()
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

        self.cpu_times, self.net, self.t = cpu_times, net, now

        temp = system_info.get_cpu_temp()
        fan = system_info.get_fan_status()
        return {
            "ts": int(now),
            "cpu": round(cpu, 1),
            "temp": None if temp.get("is_simulated") else temp.get("celsius"),
            "ram": psutil.virtual_memory().percent,
            # negative deltas mean the counters were reset
            "net_down": round(recv / dt / 1024, 1) if recv >= 0 else None,
            "net_up": round(sent / dt / 1024, 1) if sent >= 0 else None,
            "fan": fan.get("level") if fan.get("available") else None,
        }


def _store(row: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO samples (ts, {', '.join(METRICS)}) VALUES (?{', ?' * len(METRICS)})",
            [row["ts"]] + [row[m] for m in METRICS],
        )
        conn.execute("DELETE FROM samples WHERE ts < ?", (row["ts"] - RETENTION_SECONDS,))


async def background_sampler() -> None:
    logger.info(f"History sampler started (every {SAMPLE_INTERVAL}s, db: {DB_PATH})")
    sampler = _Sampler()
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL)
        try:
            row = sampler.sample()
            await asyncio.to_thread(_store, row)
        except Exception as e:
            logger.warning(f"History sample failed: {e}")


def get_history(range_key: str) -> Dict[str, Any]:
    """Bucketed averages (and CPU/temp maxima) for the requested range."""
    window, bucket = RANGES.get(range_key, RANGES["24h"])
    since = int(time.time()) - window

    if not os.path.exists(DB_PATH):
        return {"range": range_key, "bucket_seconds": bucket, "points": []}

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT (ts / {bucket}) * {bucket} AS b,
                   AVG(cpu), MAX(cpu), AVG(temp), MAX(temp), AVG(ram),
                   AVG(net_down), AVG(net_up), AVG(fan), MAX(fan)
            FROM samples WHERE ts >= ?
            GROUP BY b ORDER BY b
        """, (since,)).fetchall()

    def r(v, nd=1):
        return None if v is None else round(v, nd)

    points: List[Dict[str, Any]] = [
        {
            "ts": b, "cpu": r(cpu), "cpu_max": r(cpu_max), "temp": r(temp), "temp_max": r(temp_max),
            "ram": r(ram), "net_down": r(down), "net_up": r(up), "fan": r(fan, 2), "fan_max": fan_max,
        }
        for b, cpu, cpu_max, temp, temp_max, ram, down, up, fan, fan_max in rows
    ]
    return {"range": range_key, "bucket_seconds": bucket, "points": points}


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
    conn = sqlite3.connect(DB_PATH)
    conn.execute("VACUUM")  # actually give the space back
    conn.close()
