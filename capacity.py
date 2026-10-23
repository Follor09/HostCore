"""
Capacity planning (System -> Zustand -> Kapazitätsplanung, Flotte -> Kapazität): daily figures
of CPU and RAM (average and 95th percentile / maximum) are kept far longer than the minute
values (as long as the storage history), and a linear trend over the last 30 days estimates
when RAM, CPU or a drive gets tight.
"""

import time
from typing import Any, Dict, List, Optional

import history

RAM_LIMIT = 90.0     # % average RAM
CPU_LIMIT = 80.0     # % average CPU
TREND_DAYS = 30


def _conn():
    conn = history._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS capacity_daily (
        day TEXT PRIMARY KEY, cpu_avg REAL, cpu_p95 REAL, ram_avg REAL, ram_max REAL, temp_max REAL, samples INTEGER)""")
    return conn


def _p95(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]


def rollup() -> None:
    """Aggregate the minute samples per day (called hourly; today is updated as it goes)."""
    import retention
    with _conn() as conn:
        rows = conn.execute("SELECT ts, cpu, ram, temp FROM samples ORDER BY ts").fetchall()
        days: Dict[str, Dict[str, List[float]]] = {}
        for ts, cpu, ram, temp in rows:
            d = days.setdefault(time.strftime("%Y-%m-%d", time.localtime(ts)), {"cpu": [], "ram": [], "temp": []})
            if cpu is not None:
                d["cpu"].append(cpu)
            if ram is not None:
                d["ram"].append(ram)
            if temp is not None:
                d["temp"].append(temp)
        for day, d in days.items():
            if len(d["cpu"]) < 30:
                continue   # too little for a day figure (dashboard was off)
            conn.execute("""INSERT OR REPLACE INTO capacity_daily (day, cpu_avg, cpu_p95, ram_avg, ram_max, temp_max, samples)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (day, sum(d["cpu"]) / len(d["cpu"]), _p95(d["cpu"]),
                          sum(d["ram"]) / len(d["ram"]) if d["ram"] else None, max(d["ram"]) if d["ram"] else None,
                          max(d["temp"]) if d["temp"] else None, len(d["cpu"])))
        conn.execute("DELETE FROM capacity_daily WHERE day < ?",
                     (time.strftime("%Y-%m-%d", time.localtime(time.time() - retention.seconds("disk"))),))


def _trend(points: List[tuple]) -> Optional[float]:
    """Slope per day of (day index, value) by least squares."""
    pts = [(x, y) for x, y in points if y is not None]
    if len(pts) < 7:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / den if den else None


def _forecast(series: List[Dict[str, Any]], key: str, limit: float) -> Dict[str, Any]:
    recent = series[-TREND_DAYS:]
    pts = [(i, d[key]) for i, d in enumerate(recent)]
    slope = _trend(pts)
    current = next((d[key] for d in reversed(recent) if d[key] is not None), None)
    out = {"current": None if current is None else round(current, 1), "slope": None if slope is None else round(slope, 3),
           "days_left": None, "limit": limit, "enough_data": slope is not None}
    if current is not None and slope is not None and slope > 0.01 and current < limit:
        out["days_left"] = int((limit - current) / slope)
    if current is not None and current >= limit:
        out["days_left"] = 0
    return out


def overview() -> Dict[str, Any]:
    import insights
    import psutil
    rollup()
    with _conn() as conn:
        rows = conn.execute("SELECT day, cpu_avg, cpu_p95, ram_avg, ram_max, temp_max FROM capacity_daily ORDER BY day").fetchall()
    series = [{"day": d, "cpu_avg": round(c, 1) if c is not None else None, "cpu_p95": round(p, 1) if p is not None else None,
               "ram_avg": round(r, 1) if r is not None else None, "ram_max": round(rm, 1) if rm is not None else None,
               "temp_max": round(t, 1) if t is not None else None} for d, c, p, r, rm, t in rows]
    disks = []
    try:
        for m in insights.disk_forecast().get("mounts", []):
            disks.append({"mount": m.get("mount"), "percent": m.get("percent"), "days_left": m.get("days_left"),
                          "growth_per_day": m.get("per_day") or m.get("growth_per_day")})
    except Exception:
        pass
    mem = psutil.virtual_memory()
    return {"days": len(series), "series": series[-180:],
            "ram": {**_forecast(series, "ram_avg", RAM_LIMIT), "total_mb": round(mem.total / 1048576)},
            "cpu": {**_forecast(series, "cpu_avg", CPU_LIMIT), "cores": psutil.cpu_count()},
            "disks": disks, "trend_days": TREND_DAYS}


def summary() -> Dict[str, Any]:
    """Short form for the fleet table."""
    o = overview()
    disk = min((d for d in o["disks"] if d.get("days_left") is not None), key=lambda d: d["days_left"], default=None)
    return {"days": o["days"], "ram": {k: o["ram"][k] for k in ("current", "slope", "days_left")},
            "cpu": {k: o["cpu"][k] for k in ("current", "slope", "days_left")},
            "disk": disk}
