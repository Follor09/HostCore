"""
Homelab costs (Dokumentation -> Kosten): electricity (measured by the Pi 5 PMIC, see power.py),
contracts & subscriptions (docs.contracts) and hardware purchases (inventory with price and date).
Hardware is also spread over three years ("pro Monat abgeschrieben") so a running monthly total
can be compared with the subscriptions.
"""

import datetime
from typing import Any, Dict, List

import docs
import history
import power
import settings_store

DEPRECIATION_MONTHS = 36


def _months_back(n: int) -> List[str]:
    today = datetime.date.today().replace(day=1)
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _power_by_month() -> Dict[str, float]:
    cfg = power.settings()
    price, extra = cfg["price_ct"] / 100, cfg["extra_w"]
    interval_h = history.SAMPLE_INTERVAL / 3600
    out: Dict[str, float] = {}
    try:
        power.rollup()
        with power._conn() as conn:
            for day, wh, minutes in conn.execute("SELECT day, wh, minutes FROM power_daily"):
                kwh = (wh + extra * minutes * interval_h) / 1000
                out[day[:7]] = out.get(day[:7], 0) + kwh * price
    except Exception:
        pass
    return out


def _month_diff(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def overview() -> Dict[str, Any]:
    months = _months_back(12)
    this_month = months[-1]

    # Electricity
    try:
        p = power.overview()
    except Exception:
        p = {"available": False}
    by_month = _power_by_month()
    power_month = (p.get("month") or {}).get("projected_cost") if p.get("available") else None
    power_year = (p.get("year") or {}).get("cost") if p.get("available") else None

    # Contracts & subscriptions
    ct = docs.contracts()
    by_cat: Dict[str, float] = {}
    for c in ct["items"]:
        if c["state"] != "ended" and c.get("monthly"):
            by_cat[c["category"]] = round(by_cat.get(c["category"], 0) + c["monthly"], 2)

    # Hardware
    items = [i for i in settings_store.get("inventory", []) or [] if i.get("price") is not None and i.get("purchased")]
    total_hw = round(sum(float(i["price"]) for i in items), 2)
    by_year: Dict[str, float] = {}
    for i in items:
        y = i["purchased"][:4]
        by_year[y] = round(by_year.get(y, 0) + float(i["price"]), 2)
    hw_monthly = round(sum(float(i["price"]) / DEPRECIATION_MONTHS for i in items
                           if 0 <= _month_diff(i["purchased"][:7], this_month) < DEPRECIATION_MONTHS), 2)
    this_year = str(datetime.date.today().year)

    # Last 12 months: electricity, subscriptions active then, hardware bought then
    hist = []
    for m in months:
        subs = 0.0
        for c in ct["items"]:
            if not c.get("monthly"):
                continue
            start = (c.get("start") or "0000-00")[:7]
            end = (c.get("next_end") or "9999-12")[:7] if not c.get("auto_renew") else "9999-12"
            if start <= m <= end:
                subs += c["monthly"]
        bought = sum(float(i["price"]) for i in items if i["purchased"][:7] == m)
        hist.append({"month": m, "power": round(by_month.get(m, 0), 2), "contracts": round(subs, 2), "hardware": round(bought, 2)})

    monthly_total = round((power_month or 0) + ct["monthly_total"] + hw_monthly, 2)
    return {
        "power": {"available": bool(p.get("available")), "month": power_month, "year": power_year,
                  "avg_w": p.get("avg_w"), "price_ct": power.settings()["price_ct"]},
        "contracts": {"monthly": ct["monthly_total"], "yearly": ct["yearly_total"], "by_category": by_cat,
                      "count": sum(1 for c in ct["items"] if c["state"] != "ended")},
        "hardware": {"total": total_hw, "this_year": by_year.get(this_year, 0), "by_year": dict(sorted(by_year.items())),
                     "monthly_depreciation": hw_monthly, "items": len(items), "depreciation_months": DEPRECIATION_MONTHS},
        "total": {"monthly": monthly_total, "yearly": round(monthly_total * 12, 2)},
        "history": hist,
    }
