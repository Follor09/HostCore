"""
Electricity use and cost: the Pi 5 PMIC reports the board's power every minute
(history samples, kept 7 days); an hourly roll-up keeps kWh per day for a year.
The PMIC doesn't see the power supply's own loss or USB/NVMe devices on other
rails well, so an optional fixed extra in watts can be added.
"""

import sqlite3
import time
from typing import Any, Dict

import history
import settings_store

DAYS_KEPT = 400
DEFAULTS = {"price_ct": 35.0, "extra_w": 0.0, "currency": "€"}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(history.DB_PATH, timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS power_daily (day TEXT PRIMARY KEY, wh REAL, minutes INTEGER, avg_w REAL, max_w REAL)")
    return conn


def settings() -> Dict[str, Any]:
    return {**DEFAULTS, **(settings_store.get("power_cost", {}) or {})}


def save_settings(price_ct: float, extra_w: float, currency: str = "€") -> Dict[str, Any]:
    if not 0 <= float(price_ct) <= 500:
        raise ValueError("Preis: 0–500 ct/kWh.")
    if not 0 <= float(extra_w) <= 100:
        raise ValueError("Zuschlag: 0–100 W.")
    currency = (currency or "€").strip()[:4] or "€"
    settings_store.set("power_cost", {"price_ct": round(float(price_ct), 2), "extra_w": round(float(extra_w), 1), "currency": currency})
    return overview()


def rollup() -> None:
    """Hourly: daily totals from the minute samples (kept 7 days, so each day gets
    recomputed until its samples are gone and only the roll-up remains)."""
    now = time.time()
    with _conn() as conn:
        for offset in range(0, history.RETENTION_SECONDS // 86400 + 1):
            t = time.localtime(now - offset * 86400)
            day = time.strftime("%Y-%m-%d", t)
            start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
            row = conn.execute("SELECT COUNT(power), AVG(power), MAX(power) FROM samples WHERE ts >= ? AND ts < ? AND power IS NOT NULL",
                               (start, start + 86400)).fetchone()
            count, avg, mx = row
            if count:
                conn.execute("INSERT OR REPLACE INTO power_daily VALUES (?,?,?,?,?)",
                             (day, avg * count * history.SAMPLE_INTERVAL / 3600, count, avg, mx))
        conn.execute("DELETE FROM power_daily WHERE day < ?", (time.strftime("%Y-%m-%d", time.localtime(now - DAYS_KEPT * 86400)),))


def overview() -> Dict[str, Any]:
    rollup()
    cfg = settings()
    price = cfg["price_ct"] / 100
    extra = cfg["extra_w"]
    with _conn() as conn:
        rows = conn.execute("SELECT day, wh, minutes, avg_w, max_w FROM power_daily ORDER BY day").fetchall()
        last = conn.execute("SELECT power FROM samples WHERE power IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    if not rows:
        return {"available": False, "settings": cfg}
    interval_h = history.SAMPLE_INTERVAL / 3600
    days = []
    for day, wh, minutes, avg_w, max_w in rows:
        # extra watts only for the time that was measured; missing time isn't guessed
        kwh = (wh + extra * minutes * interval_h) / 1000
        days.append({"day": day, "kwh": round(kwh, 4), "cost": round(kwh * price, 4), "avg_w": round(avg_w + extra, 2),
                     "max_w": round(max_w + extra, 2), "coverage": round(min(1, minutes * interval_h / 24), 3)})
    today = time.strftime("%Y-%m-%d")
    month = today[:7]
    full = [d for d in days if d["day"] != today and d["coverage"] > 0.5][-30:]
    avg_w = sum(d["avg_w"] for d in full) / len(full) if full else days[-1]["avg_w"]
    day_kwh = avg_w * 24 / 1000
    month_days = [d for d in days if d["day"].startswith(month)]
    t = time.localtime()
    import calendar
    days_in_month = calendar.monthrange(t.tm_year, t.tm_mon)[1]
    elapsed = (t.tm_mday - 1) + (t.tm_hour * 60 + t.tm_min) / 1440
    month_kwh = sum(d["kwh"] for d in month_days)
    return {
        "available": True, "settings": cfg,
        "now_w": round(last[0] + extra, 2) if last else None,
        "avg_w": round(avg_w, 2),
        "today": next((d for d in days if d["day"] == today), None),
        "month": {"kwh": round(month_kwh, 3), "cost": round(month_kwh * price, 2),
                  "projected_kwh": round(month_kwh + day_kwh * (days_in_month - elapsed), 2),
                  "projected_cost": round((month_kwh + day_kwh * (days_in_month - elapsed)) * price, 2)},
        "year": {"kwh": round(day_kwh * 365, 1), "cost": round(day_kwh * 365 * price, 2)},
        "days": days[-60:],
    }
