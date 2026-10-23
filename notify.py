"""
E-mail notifications via SMTP (Einstellungen -> E-Mail-Benachrichtigungen):

- Events: new warnings / critical events from the event feed (outages, failed web checks,
  throttling, overdue backups, webhooks ...), collected into one mail every few minutes.
- Daily overview: due reminders and upcoming deadlines (contracts to cancel, warranties,
  domains, overdue backups) - only sent when there is something to say.
- Weekly report: the most important figures of /report.
- MCP approvals: a mail as soon as a MCP action waits for confirmation.

Settings in settings.json "email" (the password never leaves the Pi again), progress in
"email_state" so a restart neither repeats nor loses mails.
"""

import asyncio
import datetime
import html
import logging
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Dict, List, Optional, Tuple

import settings_store

logger = logging.getLogger("pi_dashboard.notify")

KEY = "email"
STATE_KEY = "email_state"
EVENT_INTERVAL = 300
LEVELS = {"good": 0, "info": 1, "warning": 2, "critical": 3}
LEVEL_LABEL = {"good": "OK", "info": "Hinweis", "warning": "Warnung", "critical": "Kritisch"}
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_send_lock = threading.Lock()


class NotifyError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return c if isinstance(c, dict) else {}


def _state() -> Dict[str, Any]:
    s = settings_store.get(STATE_KEY) or {}
    return s if isinstance(s, dict) else {}


def _set_state(**kw) -> None:
    s = _state()
    s.update(kw)
    settings_store.set(STATE_KEY, s)


def public() -> Dict[str, Any]:
    c, s = config(), _state()
    return {"enabled": bool(c.get("enabled")), "host": c.get("host", ""), "port": c.get("port", 587),
            "security": c.get("security", "starttls"), "username": c.get("username", ""),
            "has_password": bool(c.get("password")), "sender": c.get("sender", ""), "sender_name": c.get("sender_name", "PiCore"),
            "recipients": c.get("recipients", []), "dashboard_url": c.get("dashboard_url", ""),
            "events_level": c.get("events_level", "critical"), "daily": c.get("daily", True), "daily_hour": c.get("daily_hour", 7),
            "weekly": c.get("weekly", True), "weekly_day": c.get("weekly_day", 0), "weekly_hour": c.get("weekly_hour", 8),
            "approvals": c.get("approvals", True),
            "last_sent": s.get("last_sent", 0), "last_subject": s.get("last_subject", ""), "last_error": s.get("last_error", ""),
            "last_error_ts": s.get("last_error_ts", 0), "sent_total": s.get("sent_total", 0)}


def _int(v: Any, lo: int, hi: int, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise NotifyError(f"{what}: keine Zahl.")
    if not lo <= n <= hi:
        raise NotifyError(f"{what}: {lo} bis {hi}.")
    return n


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(config())
    host = str(fields.get("host") or "").strip()
    if host and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}|\[?[0-9a-fA-F:]+\]?", host):
        raise NotifyError("SMTP-Server: ungültiger Name.")
    security = str(fields.get("security") or "starttls")
    if security not in ("starttls", "ssl", "none"):
        raise NotifyError("Unbekannte Verschlüsselung.")
    sender = str(fields.get("sender") or "").strip()
    if sender and not EMAIL_RE.match(sender):
        raise NotifyError("Absender: keine gültige E-Mail-Adresse.")
    raw = fields.get("recipients") or []
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    recipients = []
    for r in raw:
        r = str(r).strip()
        if not r:
            continue
        if not EMAIL_RE.match(r):
            raise NotifyError(f"Empfänger „{r}“: keine gültige E-Mail-Adresse.")
        if r not in recipients:
            recipients.append(r)
    if len(recipients) > 10:
        raise NotifyError("Höchstens 10 Empfänger.")
    url = str(fields.get("dashboard_url") or "").strip().rstrip("/")
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise NotifyError("Dashboard-Adresse: mit http:// oder https:// angeben.")
    level = str(fields.get("events_level") or "critical")
    if level not in ("off", "warning", "critical"):
        raise NotifyError("Unbekannte Stufe.")
    c.update(enabled=bool(fields.get("enabled")), host=host, port=_int(fields.get("port") or 587, 1, 65535, "Port"),
             security=security, username=str(fields.get("username") or "").strip()[:200], sender=sender,
             sender_name=re.sub(r"[\r\n<>\"]", "", str(fields.get("sender_name") or "PiCore"))[:60] or "PiCore",
             recipients=recipients, dashboard_url=url, events_level=level,
             daily=bool(fields.get("daily")), daily_hour=_int(fields.get("daily_hour", 7), 0, 23, "Uhrzeit"),
             weekly=bool(fields.get("weekly")), weekly_day=_int(fields.get("weekly_day", 0), 0, 6, "Wochentag"),
             weekly_hour=_int(fields.get("weekly_hour", 8), 0, 23, "Uhrzeit"), approvals=bool(fields.get("approvals")))
    password = fields.get("password")
    if password:
        c["password"] = str(password)
    if fields.get("clear_password"):
        c.pop("password", None)
    if c["enabled"]:
        missing = [n for n, v in (("SMTP-Server", host), ("Absender", sender), ("Empfänger", recipients)) if not v]
        if missing:
            raise NotifyError(f"Zum Einschalten fehlt: {', '.join(missing)}.")
    settings_store.set(KEY, c)
    if c["enabled"] and not _state().get("last_event_ts"):
        _set_state(last_event_ts=int(time.time()))  # no mails for what happened before
    return public()


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def _link(path: str) -> str:
    base = config().get("dashboard_url", "")
    return f"{base}{path}" if base and path.startswith("/") else ""


def _render(title: str, intro: str, sections: List[Tuple[str, List[Tuple[str, str, str]]]], footer_link: str = "") -> Tuple[str, str]:
    """Plain text + simple HTML. sections: (heading, [(level, text, link path)])."""
    text = [title, "=" * len(title), ""]
    if intro:
        text += [intro, ""]
    parts = [f'<h1 style="font-size:18px;margin:0 0 6px">{html.escape(title)}</h1>']
    if intro:
        parts.append(f'<p style="margin:0 0 14px;color:#4a5568">{html.escape(intro)}</p>')
    colors = {"critical": "#c53030", "warning": "#b7791f", "good": "#2f855a", "info": "#4a5568"}
    for heading, lines in sections:
        if not lines:
            continue
        text += [heading, "-" * len(heading)]
        parts.append(f'<h2 style="font-size:14px;margin:16px 0 6px;border-bottom:1px solid #e2e8f0;padding-bottom:4px">{html.escape(heading)}</h2>'
                     '<table style="border-collapse:collapse;width:100%;font-size:13px">')
        for level, line, path in lines:
            url = _link(path) if path else ""
            label = LEVEL_LABEL.get(level, "")
            text.append(f"- {f'[{label}] ' if label and level != 'info' else ''}{line}{f'  {url}' if url else ''}")
            cell = html.escape(line)
            if url:
                cell = f'<a href="{html.escape(url)}" style="color:#2b6cb0;text-decoration:none">{cell}</a>'
            tag = (f'<span style="display:inline-block;min-width:62px;color:{colors.get(level, "#4a5568")};font-weight:600">'
                   f'{html.escape(label)}</span>') if label and level != "info" else ""
            parts.append(f'<tr><td style="padding:4px 0;border-bottom:1px solid #f1f5f9">{tag}{cell}</td></tr>')
        parts.append("</table>")
        text.append("")
    link = _link(footer_link or "/")
    if link:
        text.append(f"Dashboard: {link}")
        parts.append(f'<p style="margin-top:18px;font-size:12px;color:#718096"><a href="{html.escape(link)}" style="color:#2b6cb0">Im Dashboard öffnen</a></p>')
    host = _hostname()
    text.append(f"-- PiCore auf {host}")
    parts.append(f'<p style="font-size:11px;color:#a0aec0;margin-top:8px">PiCore auf {html.escape(host)}</p>')
    body = ('<!doctype html><html><body style="margin:0;background:#f7fafc">'
            '<div style="max-width:640px;margin:0 auto;padding:20px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
            'color:#1a202c;background:#ffffff">' + "".join(parts) + "</div></body></html>")
    return "\n".join(text), body


def _hostname() -> str:
    import socket
    return socket.gethostname()


def send(subject: str, text: str, html_body: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> None:
    c = cfg or config()
    if not c.get("host") or not c.get("sender") or not c.get("recipients"):
        raise NotifyError("SMTP-Server, Absender und Empfänger eintragen.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((c.get("sender_name") or "PiCore", c["sender"]))
    msg["To"] = ", ".join(c["recipients"])
    msg["Message-ID"] = make_msgid(domain=c["sender"].split("@")[-1])
    msg["Date"] = datetime.datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    port = int(c.get("port") or 587)
    ctx = ssl.create_default_context()
    with _send_lock:
        try:
            if c.get("security") == "ssl":
                server = smtplib.SMTP_SSL(c["host"], port, timeout=20, context=ctx)
            else:
                server = smtplib.SMTP(c["host"], port, timeout=20)
            with server:
                server.ehlo()
                if c.get("security", "starttls") == "starttls":
                    server.starttls(context=ctx)
                    server.ehlo()
                if c.get("username"):
                    server.login(c["username"], c.get("password", ""))
                server.send_message(msg)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            err = f"{type(e).__name__}: {e}"[:300]
            _set_state(last_error=err, last_error_ts=int(time.time()))
            raise NotifyError(f"Versand fehlgeschlagen – {err}")
    s = _state()
    _set_state(last_sent=int(time.time()), last_subject=subject, last_error="", sent_total=s.get("sent_total", 0) + 1)


def send_test(fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(config())
    text, body = _render("Test-Nachricht", "Der E-Mail-Versand des Dashboards funktioniert.", [])
    send("[PiCore] Test-Nachricht", text, body, c)
    return public()


def _send_quiet(subject: str, text: str, body: str) -> bool:
    try:
        send(subject, text, body)
        return True
    except NotifyError as e:
        logger.warning(str(e))
        return False


# ---------------------------------------------------------------------------
# Mails
# ---------------------------------------------------------------------------
def _fmt_ts(ts: int) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(ts))


def _event_mail(now: int) -> None:
    import insights
    c = config()
    level = c.get("events_level", "critical")
    since = _state().get("last_event_ts") or now
    if level == "off":
        _set_state(last_event_ts=now)
        return
    threshold = LEVELS[level]
    days = max(1, min(7, int((now - since) / 86400) + 1))
    new = [e for e in insights.events(days, 1000)["events"]
           if e["ts"] > since and LEVELS.get(e["level"], 0) >= threshold and e["category"] != "journal"]
    if new:
        new.sort(key=lambda e: e["ts"])
        worst = max(new, key=lambda e: LEVELS[e["level"]])
        lines = [(e["level"], f"{_fmt_ts(e['ts'])}  {e['title']}{f' – ' + e['detail'] if e.get('detail') else ''}", e.get("link", ""))
                 for e in new]
        subject = f"[PiCore] {worst['title']}" if len(new) == 1 else f"[PiCore] {len(new)} neue Ereignisse ({LEVEL_LABEL[worst['level']]})"
        text, body = _render("Neue Ereignisse", f"Seit {_fmt_ts(since)} auf {_hostname()}:", [("Ereignisse", lines)], "/system#ereignisse")
        if not _send_quiet(subject, text, body):
            return  # try again next round
    _set_state(last_event_ts=now)


def deadlines() -> List[Tuple[str, str, str]]:
    """Everything with a date that needs attention soon (daily mail)."""
    out: List[Tuple[str, str, str]] = []
    try:
        import reminders
        for r in reminders.overview()["items"]:
            if r["days"] <= 0:
                out.append(("warning" if r["days"] < 0 else "info",
                            f"Erinnerung: {r['title']} ({'seit ' + str(-r['days']) + ' Tagen überfällig' if r['days'] < 0 else 'heute fällig'})",
                            "/#do=reminders"))
    except Exception as e:
        logger.warning(f"Deadlines (reminders): {e}")
    try:
        import docs
        for ct in docs.contracts()["items"]:
            if ct["state"] == "notice":
                out.append(("warning", f"Vertrag {ct['name']}: kündbar bis {ct['cancel_by']} (noch {ct['days_to_cancel']} Tage)", "/doku#vertraege"))
        for it in docs.warranty_alerts(30):
            out.append(("info", f"Garantie von {it['name']} endet in {it['warranty_days']} Tagen", "/doku#inventar"))
    except Exception as e:
        logger.warning(f"Deadlines (docs): {e}")
    try:
        import domains
        for d in domains.alerts():
            out.append((d["level"], d["text"], "/doku#domains"))
    except Exception as e:
        logger.warning(f"Deadlines (domains): {e}")
    try:
        import backups
        for b in backups.alerts():
            out.append((b["level"], b["text"], "/system#backups"))
    except Exception as e:
        logger.warning(f"Deadlines (backups): {e}")
    return out


def _daily_mail() -> bool:
    items = deadlines()
    if not items:
        return True
    text, body = _render("Tagesübersicht", f"Fällige Erinnerungen und Fristen am {time.strftime('%d.%m.%Y')}.",
                         [("Fällig und bald fällig", items)], "/")
    n = len(items)
    return _send_quiet(f"[PiCore] Tagesübersicht: {n} {'Punkt' if n == 1 else 'Punkte'}", text, body)


def weekly_content() -> Tuple[str, str, str]:
    import insights
    rep = insights.weekly_report()
    m = rep["metrics"]
    fmt = lambda v, unit="": "–" if v is None else f"{v:g}{unit}"
    figures = [("info", f"CPU im Schnitt {fmt(m['cpu_avg'], ' %')}, höchstens {fmt(m['cpu_max'], ' %')}", ""),
               ("info", f"Temperatur im Schnitt {fmt(m['temp_avg'], ' °C')}, höchstens {fmt(m['temp_max'], ' °C')}", ""),
               ("info", f"Arbeitsspeicher im Schnitt {fmt(m['ram_avg'], ' %')}, höchstens {fmt(m['ram_max'], ' %')}", "")]
    if m.get("power_avg") is not None:
        figures.append(("info", f"Leistungsaufnahme im Schnitt {fmt(m['power_avg'], ' W')}", ""))
    ev = rep.get("events", {})
    figures.append(("warning" if ev.get("critical") else "info",
                    f"Ereignisse: {ev.get('critical', 0)} kritisch, {ev.get('warning', 0)} Warnungen", "/system#ereignisse"))
    avail = []
    for s in rep.get("services", []):
        if s["uptime"] is not None and not s["never_up"]:
            avail.append(("good" if s["uptime"] >= 99.9 else "warning" if s["uptime"] >= 99 else "critical",
                          f"{s['name']}: {s['uptime']:.2f} % ({s['outages']} Ausfälle)", "/system#verfuegbarkeit"))
    for w in rep.get("web_services", []):
        if w["uptime"] is not None:
            avail.append(("good" if w["uptime"] >= 99.9 else "warning" if w["uptime"] >= 99 else "critical",
                          f"{w['name']}: {w['uptime']:.2f} %", "/settings#web-dienste"))
    changes = [("info", f"{_fmt_ts(j['ts'])}  {j['title']} ({j['category']})", "/doku#tagebuch") for j in rep.get("journal", [])[:15]]
    changes += [("info", f"{_fmt_ts(c['ts'])}  {c['action']}{' – ' + c['detail'] if c.get('detail') else ''}", "/system#aktivitaet")
                for c in rep.get("changes", [])[:15]]
    status = [(i["level"], i["text"], i.get("link", "") if i.get("link", "").startswith("/") else "")
              for i in rep.get("status", {}).get("items", []) if i["level"] != "good"]
    logins = rep.get("logins", {})
    security = [("warning" if logins.get("failed") else "info",
                 f"Dashboard-Anmeldungen: {logins.get('ok', 0)} erfolgreich, {logins.get('failed', 0)} fehlgeschlagen", "/system#aktivitaet")]
    if logins.get("ssh_failed"):
        security.append(("warning" if logins["ssh_failed"] >= 20 else "info", f"Fehlgeschlagene SSH-Anmeldungen: {logins['ssh_failed']}", "/system#ssh"))
    for d in rep.get("new_devices", [])[:10]:
        security.append(("info", f"Neues Gerät: {d}", "/system#geraete"))
    period = f"{time.strftime('%d.%m.', time.localtime(rep['from']))} – {time.strftime('%d.%m.%Y', time.localtime(rep['to']))}"
    text, body = _render("Wochenbericht", f"{_hostname()}, {period}.",
                         [("Aktueller Zustand", status), ("Kennzahlen", figures), ("Verfügbarkeit", avail),
                          ("Sicherheit", security), ("Änderungen", changes)], "/report")
    return f"[PiCore] Wochenbericht {period}", text, body


def _weekly_mail() -> bool:
    subject, text, body = weekly_content()
    return _send_quiet(subject, text, body)


def approval_requested(item: Dict[str, Any]) -> None:
    """Called by the MCP server when an action waits for confirmation."""
    c = config()
    if not c.get("enabled") or not c.get("approvals", True):
        return

    def run():
        lines = [("warning", f"Werkzeug: {item['tool']}", ""), ("info", f"Als Benutzer: {item['user']}", ""),
                 ("info", f"Parameter: {item['summary']}", "")]
        text, body = _render("MCP-Aktion wartet auf Freigabe",
                             f"Claude möchte eine Aktion ausführen. Freigeben oder ablehnen bis {_fmt_ts(item['expires'])}.",
                             [("Aktion", lines)], "/settings#mcp-aktivitaet")
        _send_quiet(f"[PiCore] Freigabe: {item['tool']}", text, body)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------
def _tick() -> None:
    c = config()
    if not c.get("enabled"):
        return
    now = int(time.time())
    st = _state()
    if now - st.get("last_event_check", 0) >= EVENT_INTERVAL:
        _set_state(last_event_check=now)
        _event_mail(now)
    if st.get("retry_after", 0) > now:
        return  # the last scheduled mail failed: wait a while before the next attempt
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    if c.get("daily", True) and lt.tm_hour >= c.get("daily_hour", 7) and st.get("last_daily") != today:
        if _daily_mail():
            _set_state(last_daily=today)
        else:
            _set_state(retry_after=now + 1800)
            return
    if c.get("weekly", True) and lt.tm_wday == c.get("weekly_day", 0) and lt.tm_hour >= c.get("weekly_hour", 8) \
            and st.get("last_weekly") != today:
        if _weekly_mail():
            _set_state(last_weekly=today)
        else:
            _set_state(retry_after=now + 1800)


async def background_runner() -> None:
    logger.info("E-mail notifications started")
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(_tick)
        except Exception as e:
            logger.warning(f"E-mail notifications: {e}")
