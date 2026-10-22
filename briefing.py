"""
Night summary: what happened between 22:00 and 07:00 in one sentence with a status
level, plus the few things worth knowing (outages, failed logins, new devices, updates,
peaks). Before 07:00 it covers the night so far.
"""

import os
import time
from typing import Any, Dict, List

import history
import insights

NIGHT_START = 22
NIGHT_END = 7


def _window() -> Dict[str, Any]:
    now = time.time()
    t = time.localtime(now)
    at = lambda day_offset, hour: int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day_offset, hour, 0, 0, 0, 0, -1)))
    if t.tm_hour >= NIGHT_START:                       # late evening: tonight so far
        return {"start": at(0, NIGHT_START), "end": int(now), "running": True}
    if t.tm_hour < NIGHT_END:                          # still night
        return {"start": at(-1, NIGHT_START), "end": int(now), "running": True}
    return {"start": at(-1, NIGHT_START), "end": at(0, NIGHT_END), "running": False}


def _peaks(start: int, end: int) -> Dict[str, Any]:
    if not os.path.exists(history.DB_PATH):
        return {}
    with history._connect() as conn:
        n, t_max, c_max, c_avg, d_sum, u_sum = conn.execute(
            "SELECT COUNT(*), MAX(temp), MAX(cpu), AVG(cpu), SUM(net_down), SUM(net_up) FROM samples WHERE ts >= ? AND ts <= ?",
            (start, end)).fetchone()
    if not n:
        return {}
    kb = history.SAMPLE_INTERVAL        # KB/s averaged per minute -> KB
    return {"samples": n, "temp_max": t_max, "cpu_max": c_max, "cpu_avg": c_avg,
            "traffic_mb": round(((d_sum or 0) + (u_sum or 0)) * kb / 1024), "coverage": n * history.SAMPLE_INTERVAL / max(1, end - start)}


def _fmt_num(v: float) -> str:
    return f"{v:.0f}"


def briefing() -> Dict[str, Any]:
    w = _window()
    evs = [e for e in insights.events(days=2, limit=1000)["events"] if w["start"] <= e["ts"] <= w["end"]]
    peaks = _peaks(w["start"], w["end"])

    outages = [e for e in evs if e["category"] == "services" and e["level"] in ("warning", "critical")]
    recovered = [e for e in evs if e["category"] == "services" and e["level"] == "good"]
    throttles = [e for e in evs if e["category"] == "hardware"]
    logins = [e for e in evs if e["category"] == "security" and "Anmeldung" in e["title"]]
    devices = [e for e in evs if e["category"] == "network"]
    updates = [e for e in evs if e["title"] == "Dashboard aktualisiert"]
    boots = [e for e in evs if e["title"] == "Pi gestartet"]

    items: List[Dict[str, Any]] = []

    def add(text, level="info", link=""):
        items.append({"text": text, "level": level, "link": link})

    for e in outages[:3]:
        add(f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['title']}", e["level"], e["link"])
    if len(outages) > 3:
        add(f"… und {len(outages) - 3} weitere Ausfälle", "warning", "/system#ereignisse")
    for e in throttles[:2]:
        add(f"{time.strftime('%H:%M', time.localtime(e['ts']))} {e['title']}", e["level"], e["link"])
    if boots:
        add("Neustart in der Nacht", "warning", "/system#ereignisse")
    if logins:
        add(f"{len(logins)}× fehlgeschlagene Anmeldung", "warning", "/system#aktivitaet")
    for e in devices[:2]:
        add(f"Neues Gerät: {e['detail'].split(' · ')[0]}", "info", "/system#geraete")
    if updates:
        add("Dashboard-Update installiert", "good", "")
    if peaks.get("traffic_mb", 0) >= 1024:
        add(f"{peaks['traffic_mb'] / 1024:.1f} GB Datenverkehr".replace(".", ",", 1), "info", "/system#datenverbrauch")

    critical = any(e["level"] == "critical" for e in outages + throttles)
    t_max = peaks.get("temp_max")
    if critical:
        level, title = "critical", "Kritische Ereignisse"
    elif outages or throttles or logins or boots:
        level, title = "warning", "Hinweise"
    elif t_max is not None and t_max >= 70:
        level, title = "warning", "Erhöhte Temperatur"
    elif not peaks:
        level, title = "info", "Keine Messdaten"
    else:
        level, title = "good", "Unauffällig"

    parts = []
    if outages:
        parts.append(f"{len(outages)} {'Ausfall' if len(outages) == 1 else 'Ausfälle'}"
                     + (f", {len(recovered)} wieder da" if recovered else ""))
    elif peaks:
        parts.append("keine Ausfälle")
    if peaks.get("cpu_max") is not None:
        parts.append(f"CPU max. {_fmt_num(peaks['cpu_max'])} %")
    if t_max is not None:
        parts.append(f"Temperatur max. {_fmt_num(t_max)} °C")
    sentence = f"{title}: {', '.join(parts)}." if parts else f"{title}."

    return {"level": level, "title": title, "sentence": sentence, "items": items, "start": w["start"], "end": w["end"],
            "running": w["running"], "peaks": peaks, "events": len(evs)}
