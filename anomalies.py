"""
Anomaly detection (System -> Zustand -> Auffälligkeiten): every 10 minutes the last 15 minutes
of CPU, temperature, RAM and network traffic are compared with what is usual for this time of
day (same hour on the previous days, from the metrics history). A value far above the usual
range - by a statistical margin and by a minimum absolute amount - opens an anomaly; it closes
again when the value is back in the usual range. Opening and closing become events (and so
reach the e-mail notifications); open anomalies show up in the status summary.
"""

import logging
import math
import time
from typing import Any, Dict, List, Optional

import history
import settings_store

logger = logging.getLogger("pi_dashboard.anomalies")

KEY = "anomaly"
WINDOW = 15 * 60             # the recent values that are judged
CHECK_INTERVAL = 600
MIN_BASELINE = 120           # samples needed for a usable baseline (about 2 days at this hour)
# metric: (label, unit, minimum absolute deviation, floor for the spread)
METRICS = {
    "cpu": ("CPU-Last", "%", 20.0, 4.0),
    "temp": ("Temperatur", "°C", 6.0, 1.5),
    "ram": ("Arbeitsspeicher", "%", 12.0, 3.0),
    "net_down": ("Download", "KB/s", 2048.0, 100.0),
    "net_up": ("Upload", "KB/s", 2048.0, 100.0),
}
LEVELS = {"niedrig": 4.0, "mittel": 3.0, "hoch": 2.5}   # sensitivity -> how many standard deviations
_last_check = {"ts": 0.0}


def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return {"enabled": c.get("enabled", True), "sensitivity": c.get("sensitivity", "mittel") if c.get("sensitivity") in LEVELS else "mittel",
            "metrics": [m for m in c.get("metrics", list(METRICS)) if m in METRICS]}


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    sens = str(fields.get("sensitivity") or "mittel")
    if sens not in LEVELS:
        raise ValueError("Unbekannte Empfindlichkeit.")
    metrics = [m for m in (fields.get("metrics") or []) if m in METRICS]
    settings_store.set(KEY, {"enabled": bool(fields.get("enabled")), "sensitivity": sens, "metrics": metrics})
    return overview()


def _conn():
    conn = history._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS anomaly_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, metric TEXT, start INTEGER, end INTEGER, open INTEGER,
        value REAL, peak REAL, baseline REAL, spread REAL)""")
    return conn


def _baselines(conn, now: int) -> Dict[str, Dict[str, Optional[float]]]:
    """Mean and standard deviation of each metric at this time of day (±1 h) on earlier days."""
    lt = time.localtime(now)
    minute_of_day = lt.tm_hour * 60 + lt.tm_min
    out: Dict[str, Dict[str, Optional[float]]] = {}
    rows = conn.execute(f"SELECT ts, {', '.join(METRICS)} FROM samples WHERE ts < ? AND ts >= ?",
                        (now - 3600, now - history.retention_seconds())).fetchall()
    picked: Dict[str, List[float]] = {m: [] for m in METRICS}
    for row in rows:
        t = time.localtime(row[0])
        diff = abs(t.tm_hour * 60 + t.tm_min - minute_of_day)
        if min(diff, 1440 - diff) > 60:
            continue
        for i, m in enumerate(METRICS, start=1):
            if row[i] is not None:
                picked[m].append(row[i])
    for m, vals in picked.items():
        if len(vals) < MIN_BASELINE:
            out[m] = {"mean": None, "spread": None, "samples": len(vals)}
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        out[m] = {"mean": mean, "spread": math.sqrt(var), "samples": len(vals)}
    return out


def _recent(conn, now: int) -> Dict[str, Optional[float]]:
    row = conn.execute(f"SELECT {', '.join(f'AVG({m})' for m in METRICS)} FROM samples WHERE ts >= ?", (now - WINDOW,)).fetchone()
    return {m: row[i] for i, m in enumerate(METRICS)}


def _limit(m: str, base: Dict[str, Optional[float]], z: float) -> Optional[float]:
    if base["mean"] is None:
        return None
    _, _, min_dev, floor = METRICS[m]
    return base["mean"] + max(z * max(base["spread"], floor), min_dev)


def check(force: bool = False) -> None:
    cfg = config()
    now = int(time.time())
    if not force and now - _last_check["ts"] < CHECK_INTERVAL - 30:
        return
    _last_check["ts"] = now
    if not cfg["enabled"]:
        return
    z = LEVELS[cfg["sensitivity"]]
    with _conn() as conn:
        base = _baselines(conn, now)
        recent = _recent(conn, now)
        for m in METRICS:
            value = recent.get(m)
            limit = _limit(m, base[m], z)
            open_row = conn.execute("SELECT id, peak FROM anomaly_events WHERE metric = ? AND open = 1", (m,)).fetchone()
            if m not in cfg["metrics"] or value is None or limit is None:
                if open_row:
                    conn.execute("UPDATE anomaly_events SET open = 0, end = ? WHERE id = ?", (now, open_row[0]))
                continue
            if value > limit:
                if open_row:
                    conn.execute("UPDATE anomaly_events SET end = ?, peak = MAX(peak, ?) WHERE id = ?", (now, value, open_row[0]))
                else:
                    conn.execute("""INSERT INTO anomaly_events (metric, start, end, open, value, peak, baseline, spread)
                                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)""", (m, now - WINDOW, now, value, value, base[m]["mean"], base[m]["spread"]))
            elif open_row:
                # Back to normal once clearly below the limit (hysteresis: no flapping around the line)
                back = base[m]["mean"] + (limit - base[m]["mean"]) * 0.7
                if value <= back:
                    conn.execute("UPDATE anomaly_events SET open = 0, end = ? WHERE id = ?", (now, open_row[0]))
        conn.execute("DELETE FROM anomaly_events WHERE end < ?", (now - 90 * 86400,))


def _fmt(m: str, v: Optional[float]) -> str:
    if v is None:
        return "–"
    unit = METRICS[m][1]
    if unit == "KB/s":
        return f"{v / 1024:.1f} MB/s" if v >= 1024 else f"{v:.0f} KB/s"
    return f"{v:.0f} {unit}" if unit == "%" else f"{v:.1f} {unit}"


def _title(m: str, value: float, baseline: float) -> str:
    return f"Ungewöhnlich hoch: {METRICS[m][0]} {_fmt(m, value)} (üblich {_fmt(m, baseline)})"


def events_since(since: float) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT metric, start, end, open, value, peak, baseline FROM anomaly_events WHERE end >= ? ORDER BY start",
                            (int(since),)).fetchall()
    out = []
    for m, start, end, is_open, value, peak, baseline in rows:
        if m not in METRICS:
            continue
        if start >= since:
            out.append({"ts": start, "level": "warning", "title": _title(m, value, baseline), "detail": ""})
        if not is_open:
            out.append({"ts": end, "level": "good", "title": f"{METRICS[m][0]} wieder im üblichen Bereich",
                        "detail": f"Spitze {_fmt(m, peak)}"})
    return out


def active() -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT metric, start, peak, baseline FROM anomaly_events WHERE open = 1").fetchall()
    return [{"metric": m, "label": METRICS[m][0], "since": s, "text": f"{METRICS[m][0]} ungewöhnlich hoch ({_fmt(m, p)}, üblich {_fmt(m, b)})"}
            for m, s, p, b in rows if m in METRICS]


def overview() -> Dict[str, Any]:
    cfg = config()
    now = int(time.time())
    z = LEVELS[cfg["sensitivity"]]
    with _conn() as conn:
        base = _baselines(conn, now)
        recent = _recent(conn, now)
        rows = conn.execute("""SELECT metric, start, end, open, value, peak, baseline FROM anomaly_events
                               WHERE end >= ? ORDER BY start DESC LIMIT 100""", (now - 30 * 86400,)).fetchall()
    metrics = []
    for m, (label, unit, _, _) in METRICS.items():
        limit = _limit(m, base[m], z)
        value = recent.get(m)
        metrics.append({"metric": m, "label": label, "now": _fmt(m, value), "usual": _fmt(m, base[m]["mean"]),
                        "limit": _fmt(m, limit), "learning": base[m]["mean"] is None, "samples": base[m]["samples"],
                        "watched": m in cfg["metrics"],
                        "state": "learning" if limit is None else "high" if value is not None and value > limit else "ok"})
    events = [{"metric": m, "label": METRICS[m][0], "start": s, "end": e, "open": bool(o), "value": _fmt(m, v), "peak": _fmt(m, p),
               "usual": _fmt(m, b), "duration": e - s} for m, s, e, o, v, p, b in rows if m in METRICS]
    return {"config": cfg, "metrics": metrics, "events": events, "levels": list(LEVELS),
            "available": {m: METRICS[m][0] for m in METRICS}, "min_baseline": MIN_BASELINE}
