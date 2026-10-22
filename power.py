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


# ---------------------------------------------------------------------------
# Power budget: a monthly budget in kWh, finished months are evaluated once
# ---------------------------------------------------------------------------
BUDGET_KEY = "power_budget"


def save_budget(kwh: float) -> Dict[str, Any]:
    kwh = round(float(kwh), 2)
    if kwh and not 0.1 <= kwh <= 1000:
        raise ValueError("Budget: 0,1–1000 kWh (0 = aus).")
    cfg = settings_store.get(BUDGET_KEY, {}) or {}
    if kwh and not cfg.get("since"):
        cfg["since"] = time.strftime("%Y-%m")
    cfg["budget_kwh"] = kwh
    settings_store.set(BUDGET_KEY, cfg)
    return budget_status()


def _score_months(cfg: Dict[str, Any], days: list) -> Dict[str, Any]:
    """Finished months since the budget was set get evaluated with the budget of that time."""
    months = dict(cfg.get("months", {}))
    this_month = time.strftime("%Y-%m")
    totals: Dict[str, Dict[str, float]] = {}
    for d in days:
        m = totals.setdefault(d["day"][:7], {"kwh": 0.0, "days": 0})
        m["kwh"] += d["kwh"]
        m["days"] += 1
    changed = False
    for month, m in totals.items():
        if month >= this_month or month < cfg.get("since", this_month) or month in months:
            continue
        months[month] = {"kwh": round(m["kwh"], 3), "budget": cfg["budget_kwh"], "ok": m["kwh"] <= cfg["budget_kwh"],
                         "days": m["days"]}
        changed = True
    if changed:
        cfg["months"] = months
        settings_store.set(BUDGET_KEY, cfg)
    return months


def _tips(ov: Dict[str, Any]) -> list:
    import containers
    import systools
    tips = []
    try:
        mode = systools.cpu_mode()
        if mode.get("available") and mode.get("governor") in ("performance", "ondemand"):
            tips.append({"text": "CPU-Modus „Sparsam“ oder „Sanft“ spart im Leerlauf spürbar Strom.", "link": "/system#cpu-modus"})
    except Exception:
        pass
    try:
        busy = sorted((i for i in containers.stats_summary()["items"] if i["fresh"] and i["cpu"] >= 3),
                      key=lambda i: -i["cpu"])
        if busy:
            b = busy[0]
            tips.append({"text": f"{b['name']} braucht gerade {b['cpu']:.0f} % CPU – läuft der Container unnötig?",
                         "link": "/containers#statistik"})
    except Exception:
        pass
    try:
        import psutil
        stats = psutil.net_if_stats()
        eth_up = any(systools._iface_kind(n) == "ethernet" and s.isup for n, s in stats.items())
        wlan_up = any(systools._iface_kind(n) == "wlan" and s.isup for n, s in stats.items())
        if eth_up and wlan_up:
            tips.append({"text": "LAN und WLAN sind beide an – ohne WLAN (dtoverlay=disable-wifi) spart der Pi etwas Strom.", "link": ""})
    except Exception:
        pass
    tips.append({"text": "Ohne Monitor: HDMI bleibt aus, wenn beim Start kein Bildschirm steckt – Kabel ab spart ein paar Milliwatt.", "link": ""})
    return tips[:3]


def budget_status() -> Dict[str, Any]:
    ov = overview()
    cfg = settings_store.get(BUDGET_KEY, {}) or {}
    budget = float(cfg.get("budget_kwh") or 0)
    out: Dict[str, Any] = {"available": ov.get("available", False), "budget_kwh": budget}
    if not ov.get("available"):
        return out
    import calendar
    t = time.localtime()
    days_in_month = calendar.monthrange(t.tm_year, t.tm_mon)[1]
    elapsed = (t.tm_mday - 1) + (t.tm_hour * 60 + t.tm_min) / 1440
    month = ov["month"]
    out.update(month_kwh=month["kwh"], projected_kwh=month["projected_kwh"], avg_w=ov["avg_w"],
               days_left=round(days_in_month - elapsed, 1), days_in_month=days_in_month,
               suggested_kwh=round(max(0.5, month["projected_kwh"] * 0.9), 1), tips=_tips(ov),
               month_cost=month["cost"], currency=ov["settings"]["currency"])
    if not budget:
        return out
    months = _score_months(cfg, ov["days"])
    streak = 0
    for m in sorted(months, reverse=True):
        if not months[m]["ok"]:
            break
        streak += 1
    # Pace: the share of the budget that may be used by now
    allowed_now = budget * elapsed / days_in_month
    out.update(progress=round(month["kwh"] / budget, 3), pace=round(elapsed / days_in_month, 3),
               on_track=month["projected_kwh"] <= budget, ahead_kwh=round(allowed_now - month["kwh"], 3),
               per_day_left=round(max(0.0, budget - month["kwh"]) / max(0.5, days_in_month - elapsed), 3),
               streak=streak, won=sum(1 for m in months.values() if m["ok"]),
               history=[{"month": k, **v} for k, v in sorted(months.items())][-12:])
    return out
