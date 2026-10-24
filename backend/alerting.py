"""
Alarms with acknowledgement and escalation (Einstellungen -> Alarmierung, System -> Alarme).

Every new critical event of the event feed (or warning, if chosen) becomes an alarm. An alarm
is mailed to the first step of the escalation chain right away; while nobody acknowledges it,
the next steps follow after their delay (e.g. after 15 minutes the second person, after an
hour everyone), the last step can be repeated. Acknowledged in the dashboard or with the link
in the mail (no login needed, one link per recipient). When the matching all-clear event
arrives ("... wieder aktiv", "... wieder erreichbar"), the alarm closes by itself.

While alarms are switched on, the normal event mail leaves out what the alarms already send.
Settings in settings.json "alerting", the alarms in "alerts".
"""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import settings_store

logger = logging.getLogger("pi_dashboard.alerting")

KEY = "alerting"
ALERTS_KEY = "alerts"
LEVELS = {"good": 0, "info": 1, "warning": 2, "critical": 3}
MAX_STEPS = 4
MAX_TARGETS = 10
MAX_ALERTS = 300
KEEP_DAYS = 30
SKIP_CATEGORIES = ("journal", "maintenance")
LOOKBACK = 900   # events can show up later than their time stamp (an outage is dated to its start)
# "<subject> <problem>" / "<subject> <all-clear>": the subject ties an all-clear to its alarm
PROBLEM = re.compile(r"^(.+?) (ausgefallen|nicht erreichbar|läuft nicht|nicht abfragbar.*|überfällig.*|fehlgeschlagen.*)$")
CLEAR = re.compile(r"^(.+?) (wieder aktiv|wieder erreichbar|läuft wieder|wieder abfragbar|wieder aktuell|wieder erfolgreich|wieder in Ordnung)$")
_lock = threading.Lock()


class AlertError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return c if isinstance(c, dict) else {}


def _alerts() -> List[Dict[str, Any]]:
    return list(settings_store.get(ALERTS_KEY, []) or [])


def _store(alerts: List[Dict[str, Any]]) -> None:
    settings_store.set(ALERTS_KEY, alerts[-MAX_ALERTS:])


def enabled() -> bool:
    return bool(config().get("enabled"))


def threshold() -> int:
    return LEVELS[config().get("level", "critical")]


def _email_ready() -> bool:
    import notify
    c = notify.config()
    return bool(c.get("enabled") and c.get("host") and c.get("sender"))


def _users() -> Dict[str, Dict[str, Any]]:
    import auth
    return {u["username"].casefold(): u for u in auth.list_users()}


def resolve_targets(targets: List[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """["niko", "a@b.de"] -> [(label, address)], [problems]"""
    import notify
    users = _users()
    out, problems = [], []
    for t in targets:
        if "@" in t:
            out.append((t, t))
            continue
        u = users.get(t.casefold())
        if not u:
            problems.append(f"Benutzer „{t}“ gibt es nicht.")
        elif not u.get("email") or not notify.EMAIL_RE.match(u["email"]):
            problems.append(f"{u['username']} hat keine E-Mail-Adresse hinterlegt.")
        else:
            out.append((u["username"], u["email"]))
    return out, problems


def public() -> Dict[str, Any]:
    c = config()
    steps = c.get("steps") or [{"after": 0, "targets": []}]
    problems = []
    for s in steps:
        problems += resolve_targets(s["targets"])[1]
    return {"enabled": bool(c.get("enabled")), "level": c.get("level", "critical"), "steps": steps,
            "repeat": int(c.get("repeat", 0)), "email_ready": _email_ready(), "problems": sorted(set(problems)),
            "users": [{"username": u["username"], "email": u.get("email", "")} for u in _users().values()]}


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    import notify
    level = str(fields.get("level") or "critical")
    if level not in ("warning", "critical"):
        raise AlertError("Unbekannte Stufe.")
    try:
        repeat = int(fields.get("repeat") or 0)
    except (TypeError, ValueError):
        raise AlertError("Wiederholen: keine Zahl.")
    if repeat and not 5 <= repeat <= 1440:
        raise AlertError("Wiederholen: 5 bis 1440 Minuten.")
    raw = fields.get("steps") or []
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        raise AlertError(f"1 bis {MAX_STEPS} Stufen.")
    steps, last = [], -1
    for n, s in enumerate(raw, 1):
        try:
            after = int((s or {}).get("after") or 0)
        except (TypeError, ValueError):
            raise AlertError(f"Stufe {n}: Wartezeit ist keine Zahl.")
        if n == 1:
            after = 0
        elif not 1 <= after <= 1440:
            raise AlertError(f"Stufe {n}: Wartezeit 1 bis 1440 Minuten.")
        if after <= last:
            raise AlertError(f"Stufe {n}: muss später als die vorige kommen.")
        targets = s.get("targets") or []
        if isinstance(targets, str):
            targets = re.split(r"[,;\s]+", targets)
        clean = []
        for t in targets:
            t = str(t).strip()
            if not t:
                continue
            if "@" in t and not notify.EMAIL_RE.match(t):
                raise AlertError(f"Stufe {n}: „{t}“ ist keine gültige E-Mail-Adresse.")
            if "@" not in t and not re.match(r"^[\w.-]{1,64}$", t):
                raise AlertError(f"Stufe {n}: „{t}“ ist weder Benutzer noch E-Mail-Adresse.")
            if t not in clean:
                clean.append(t)
        if not clean:
            raise AlertError(f"Stufe {n}: mindestens einen Empfänger eintragen.")
        if len(clean) > MAX_TARGETS:
            raise AlertError(f"Stufe {n}: höchstens {MAX_TARGETS} Empfänger.")
        steps.append({"after": after, "targets": clean})
        last = after
    on = bool(fields.get("enabled"))
    with _lock:
        c = dict(config())
        if on and not c.get("enabled"):
            c["since"] = int(time.time())   # nothing from before switching on
        c.update(enabled=on, level=level, steps=steps, repeat=repeat)
        settings_store.set(KEY, c)
    return public()


# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------
def _subject(e: Dict[str, Any]) -> str:
    if e.get("key"):
        return e["key"]
    title = re.sub(r"^Backup-Prüfung ", "Backup ", re.sub(r"^Störung( behoben)?: ", "Störung ", e["title"]))
    m = PROBLEM.match(title) or CLEAR.match(title)
    return (m.group(1) if m else title).strip().casefold()


def _alert_id(e: Dict[str, Any]) -> str:
    return hashlib.sha1(f"{e['ts']}|{e['category']}|{e['title']}".encode()).hexdigest()[:12]


def handles(event: Dict[str, Any]) -> bool:
    """True when an alarm is (or will be) sent for this event - the event mail leaves it out then."""
    return (enabled() and LEVELS.get(event.get("level"), 0) >= threshold()
            and event.get("category") not in SKIP_CATEGORIES)


def _note(a: Dict[str, Any], text: str, ts: Optional[int] = None) -> None:
    a.setdefault("history", []).append({"ts": ts or int(time.time()), "text": text})
    a["history"] = a["history"][-30:]


def _public_alert(a: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in a.items() if k != "tokens"}


def overview(limit: int = 100) -> Dict[str, Any]:
    alerts = _alerts()
    order = {"open": 0, "acked": 1, "resolved": 2}
    alerts.sort(key=lambda a: (order.get(a["status"], 3), -a["created"]))
    counts = {s: sum(1 for a in alerts if a["status"] == s) for s in order}
    return {"alerts": [_public_alert(a) for a in alerts[:limit]], "counts": counts, "config": public()}


def open_count() -> int:
    return sum(1 for a in _alerts() if a["status"] == "open")


def _change(alert_id: str, fn) -> Dict[str, Any]:
    with _lock:
        alerts = _alerts()
        a = next((x for x in alerts if x["id"] == alert_id), None)
        if not a:
            raise AlertError("Alarm nicht gefunden.")
        fn(a)
        _store(alerts)
    return _public_alert(a)


def acknowledge(alert_id: str, by: str, note: str = "") -> Dict[str, Any]:
    note = (note or "").strip()[:300]

    def fn(a):
        if a["status"] != "open":
            raise AlertError("Der Alarm ist schon quittiert." if a["status"] == "acked" else "Der Alarm ist schon erledigt.")
        a.update(status="acked", acked_by=by, acked_at=int(time.time()), ack_note=note)
        _note(a, f"Quittiert von {by}" + (f": {note}" if note else ""))
    a = _change(alert_id, fn)
    threading.Thread(target=_tell_acked, args=(a,), daemon=True).start()
    return a


def resolve(alert_id: str, by: str) -> Dict[str, Any]:
    def fn(a):
        if a["status"] == "resolved":
            raise AlertError("Der Alarm ist schon erledigt.")
        a.update(status="resolved", resolved_at=int(time.time()), resolved_by=by)
        a.pop("tokens", None)
        _note(a, f"Als erledigt markiert von {by}")
    return _change(alert_id, fn)


def by_token(token: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """Alarm and recipient of an acknowledgement link."""
    if not token or not re.match(r"^[A-Za-z0-9_-]{20,64}$", token):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    for a in _alerts():
        for h, label in (a.get("tokens") or {}).items():
            if hmac.compare_digest(h, digest):
                return a, label
    return None


def token_info(token: str) -> Dict[str, Any]:
    hit = by_token(token)
    if not hit:
        return {"valid": False}
    a, label = hit
    return {"valid": True, "recipient": label, "alert": {k: a.get(k) for k in ("title", "detail", "level", "created", "status",
                                                                                "acked_by", "acked_at")}}


def acknowledge_token(token: str, note: str = "") -> Dict[str, Any]:
    hit = by_token(token)
    if not hit:
        raise AlertError("Der Link ist ungültig oder der Alarm ist schon erledigt.")
    a, label = hit
    return acknowledge(a["id"], f"{label} (E-Mail-Link)", note)


# ---------------------------------------------------------------------------
# Mails
# ---------------------------------------------------------------------------
def _send(a: Dict[str, Any], step: int, targets: List[Tuple[str, str]], again: bool = False) -> List[str]:
    """One mail per recipient (each with its own acknowledgement link). Returns who got it."""
    import notify
    sent = []
    base = notify.config().get("dashboard_url", "")
    for label, address in targets:
        token = secrets.token_urlsafe(24)
        a.setdefault("tokens", {})[hashlib.sha256(token.encode()).hexdigest()] = label
        lines = [(a["level"], a["title"], a.get("link", ""), time.strftime("%d.%m. %H:%M", time.localtime(a["ts"])))]
        if a.get("detail"):
            lines.append(("info", a["detail"], ""))
        intro = (f"Erinnerung – noch nicht quittiert. " if again else "") + \
                (f"Eskalationsstufe {step + 1}: seit {_mins(time.time() - a['created'])} nicht quittiert."
                 if step else "Neuer Alarm – bitte quittieren, sobald sich jemand kümmert.")
        sections = [("Alarm", lines)]
        if base:
            sections.append(("Quittieren", [("info", "Ich kümmere mich – Alarm quittieren", f"/alarm?token={token}")]))
        else:
            sections.append(("Quittieren", [("info", "Im Dashboard unter System → Alarme quittieren "
                                             "(für einen Link in der Mail die Dashboard-Adresse in den E-Mail-Einstellungen eintragen)", "")]))
        text, body = notify._render("Alarm" if not step else f"Alarm – Stufe {step + 1}", intro, sections, "/system#alarme",
                                    overall=a["level"])
        prefix = "[HostCore] " + ("Erinnerung: " if again else "Alarm: " if not step else f"Eskalation {step + 1}: ")
        try:
            notify.send(prefix + a["title"], text, body, to=[address])
            sent.append(label)
        except notify.NotifyError as e:
            _note(a, f"Versand an {label} fehlgeschlagen – {e}")
    return sent


def _mins(sec: float) -> str:
    m = int(sec // 60)
    return f"{m} Min." if m < 120 else f"{m // 60} Std."


def _tell_acked(a: Dict[str, Any]) -> None:
    """Everyone who already got the alarm learns that someone is on it."""
    import notify
    if not _email_ready():
        return
    steps = config().get("steps") or []
    notified = set(a.get("notified_steps") or [])
    targets = {}
    for i, s in enumerate(steps):
        if i in notified:
            for label, address in resolve_targets(s["targets"])[0]:
                targets[address] = label
    lines = [("good", f"Quittiert von {a['acked_by']}", "", time.strftime("%d.%m. %H:%M", time.localtime(a["acked_at"])))]
    if a.get("ack_note"):
        lines.append(("info", a["ack_note"], ""))
    text, body = notify._render("Alarm quittiert", a["title"], [("Status", lines)], "/system#alarme", overall="good")
    for address in targets:
        try:
            notify.send(f"[HostCore] Quittiert: {a['title']}", text, body, to=[address])
        except notify.NotifyError as e:
            logger.warning(f"Ack notice to {address}: {e}")


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------
def _new_events(c: Dict[str, Any], now: int) -> List[Dict[str, Any]]:
    """Events not handled yet: a look back of LOOKBACK seconds, minus the ones already seen."""
    import insights
    since = max((c.get("last_ts") or now) - LOOKBACK, c.get("since") or 0)
    days = max(1, min(7, int((now - since) / 86400) + 1))
    seen = c.get("seen") or {}
    return sorted((e for e in insights.events(days, 1000)["events"]
                   if e["ts"] > since and e["category"] not in SKIP_CATEGORIES and _alert_id(e) not in seen),
                  key=lambda e: e["ts"])


def _due_step(a: Dict[str, Any], steps: List[Dict[str, Any]], now: int) -> int:
    age = (now - a["created"]) / 60
    return max(i for i, s in enumerate(steps) if s["after"] <= age)


def tick() -> None:
    c = config()
    if not c.get("enabled"):
        return
    now = int(time.time())
    steps = c.get("steps") or []
    if not steps:
        return
    events = _new_events(c, now)
    with _lock:
        alerts = _alerts()
        known = {a["id"] for a in alerts}
        for e in events:
            subject = _subject(e)
            if e["level"] == "good" or e["title"].startswith("Störung behoben: "):
                for a in alerts:
                    if a["status"] != "resolved" and a["subject"] == subject and a["ts"] <= e["ts"]:
                        a.update(status="resolved", resolved_at=e["ts"], resolved_by="")
                        a.pop("tokens", None)
                        _note(a, f"Von selbst behoben: {e['title']}", e["ts"])
                continue
            if LEVELS.get(e["level"], 0) < LEVELS[c.get("level", "critical")]:
                continue
            aid = _alert_id(e)
            if aid in known:
                continue
            same = next((a for a in alerts if a["status"] != "resolved" and a["subject"] == subject), None)
            if same:
                same["repeats"] = same.get("repeats", 0) + 1
                if LEVELS[e["level"]] > LEVELS[same["level"]]:
                    same["level"] = e["level"]
                _note(same, f"Erneut: {e['title']}" + (f" – {e['detail']}" if e.get("detail") else ""), e["ts"])
                continue
            alerts.append({"id": aid, "ts": e["ts"], "created": now, "level": e["level"], "category": e["category"],
                           "title": e["title"], "detail": e.get("detail", ""), "link": e.get("link", ""), "subject": subject,
                           "status": "open", "step": -1, "notified_at": 0, "notified_steps": [],
                           "history": [{"ts": now, "text": "Alarm ausgelöst"}]})
            known.add(aid)
        # Escalation
        ready = _email_ready()
        repeat = int(c.get("repeat") or 0) * 60
        for a in alerts:
            if a["status"] != "open":
                continue
            due = _due_step(a, steps, now)
            again = False
            if due <= a["step"]:
                if not (repeat and due == len(steps) - 1 and now - a["notified_at"] >= repeat):
                    continue
                again = True
            if not ready:
                if not a.get("no_mail_noted"):
                    _note(a, "Keine E-Mail verschickt – E-Mail-Versand ist nicht eingerichtet")
                    a["no_mail_noted"] = True
                a["step"] = due
                continue
            targets, problems = resolve_targets(steps[due]["targets"])
            sent = _send(a, due, targets, again)
            for p in problems:
                _note(a, p)
            if sent:
                _note(a, ("Erinnerung an " if again else f"Stufe {due + 1} benachrichtigt: " if due else "Benachrichtigt: ") + ", ".join(sent))
            a["step"], a["notified_at"] = due, now
            a["notified_steps"] = sorted(set(a.get("notified_steps", [])) | {due})
        # Clean-up: closed alarms after 30 days
        cutoff = now - KEEP_DAYS * 86400
        alerts = [a for a in alerts if a["status"] == "open" or max(a.get("resolved_at", 0), a.get("acked_at", 0), a["created"]) >= cutoff]
        _store(alerts)
        cfg = dict(config())
        cfg["last_ts"] = now
        seen = {k: v for k, v in (cfg.get("seen") or {}).items() if v > now - 2 * LOOKBACK}
        seen.update({_alert_id(e): e["ts"] for e in events})
        cfg["seen"] = seen
        settings_store.set(KEY, cfg)


def send_test() -> Dict[str, Any]:
    import notify
    if not _email_ready():
        raise AlertError("Zuerst den E-Mail-Versand einrichten (Einstellungen → E-Mail).")
    steps = config().get("steps") or []
    if not steps:
        raise AlertError("Zuerst die Alarmierung speichern.")
    targets, problems = resolve_targets(steps[0]["targets"])
    if not targets:
        raise AlertError(problems[0] if problems else "Stufe 1 hat keinen Empfänger.")
    text, body = notify._render("Test-Alarm", "So sieht ein Alarm aus. Dieser hier muss nicht quittiert werden.",
                                [("Alarm", [("critical", "Beispiel: Dienst ausgefallen", "")])], "/system#alarme", overall="critical")
    for label, address in targets:
        notify.send("[HostCore] Test-Alarm", text, body, to=[address])
    return {"sent": [label for label, _ in targets], "problems": problems}


async def background_runner() -> None:
    logger.info("Alerting started")
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(tick)
        except Exception as e:
            logger.warning(f"Alerting: {e}")
