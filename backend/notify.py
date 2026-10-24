"""
E-mail notifications via SMTP (Einstellungen -> E-Mail-Benachrichtigungen):

- Events: new warnings / critical events from the event feed (outages, failed web checks,
  throttling, overdue backups, webhooks ...), collected into one mail every few minutes.
- Daily overview: due reminders and upcoming deadlines (contracts to cancel, warranties,
  domains, overdue backups, end of support, open vulnerabilities) - only sent when there is something
  to say.
- Weekly report: the most important figures of /report.
- MCP approvals: a mail as soon as a MCP action waits for confirmation.
- Alarms (alerting.py) use the same SMTP settings; while they are on, the event mail leaves
  out what the alarms already send.

Settings in settings.json "email" (the password never leaves the server again), progress in
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
            "security": c.get("security", "starttls"), "insecure": bool(c.get("insecure")), "username": c.get("username", ""),
            "has_password": bool(c.get("password")), "sender": c.get("sender", ""), "sender_name": c.get("sender_name", "HostCore"),
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
             security=security, insecure=bool(fields.get("insecure")),
             username=str(fields.get("username") or "").strip()[:200], sender=sender,
             sender_name=re.sub(r"[\r\n<>\"]", "", str(fields.get("sender_name") or "HostCore"))[:60] or "HostCore",
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


COLORS = {"critical": "#dc2626", "warning": "#d97706", "good": "#059669", "info": "#64748b"}
OVERALL = {"critical": ("Kritisch", "#dc2626"), "warning": ("Warnungen", "#d97706"), "info": ("Hinweise", "#0891b2"),
           "good": ("Alles in Ordnung", "#059669")}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _render(title: str, intro: str, sections: List[Tuple[str, List[tuple]]], footer_link: str = "",
            tiles: Optional[List[Tuple[str, str, str, str]]] = None, overall: str = "") -> Tuple[str, str]:
    """Plain text + HTML mail (tables and inline styles, so Outlook, Gmail and Apple Mail show the same).
    sections: (heading, [(level, text, link path[, value on the right])]); tiles: (label, value, sub, level)."""
    e = html.escape
    host = _hostname()
    # ---- plain text
    text = [title, "=" * len(title)]
    if intro:
        text.append(intro)
    text.append("")
    if overall in OVERALL:
        text += [f"Zustand: {OVERALL[overall][0]}", ""]
    for label, value, sub, _ in tiles or []:
        text.append(f"{label}: {value}{f' ({sub})' if sub else ''}")
    if tiles:
        text.append("")
    for heading, lines in sections:
        if not lines:
            continue
        text += [heading, "-" * len(heading)]
        for line in lines:
            level, msg, path = line[0], line[1], line[2]
            value = line[3] if len(line) > 3 else ""
            url = _link(path) if path else ""
            label = LEVEL_LABEL.get(level, "")
            text.append(f"- {f'[{label}] ' if label and level not in ('info', 'good') else ''}{msg}{f'  {value}' if value else ''}"
                        f"{f'  <{url}>' if url else ''}")
        text.append("")
    link = _link(footer_link or "/")
    if link:
        text.append(f"Im Dashboard: {link}")
    text.append(f"-- HostCore auf {host}")

    # ---- HTML
    parts = []
    pill = ""
    if overall in OVERALL:
        name, color = OVERALL[overall]
        pill = (f'<td align="right" style="vertical-align:top"><span style="display:inline-block;padding:5px 12px;border-radius:999px;'
                f'background:{color};color:#ffffff;font-size:12px;font-weight:600;white-space:nowrap">{e(name)}</span></td>')
    parts.append(
        '<tr><td class="pad" style="background:#0f172a;padding:22px 28px;border-radius:14px 14px 0 0">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td>'
        '<div style="font-size:11px;letter-spacing:2px;font-weight:700;color:#22d3ee">PICORE</div>'
        f'<div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:6px">{e(title)}</div>'
        f'<div style="font-size:13px;color:#94a3b8;margin-top:4px">{e(intro or host)}</div>'
        f'</td>{pill}</tr></table></td></tr>')
    body_rows = []
    if tiles:
        cells = []
        for label, value, sub, level in tiles:
            color = COLORS.get(level, "#cbd5e1") if level else "#cbd5e1"
            sub_html = f'<div style="font-size:12px;color:#64748b;margin-top:2px">{e(sub)}</div>' if sub else ""
            cells.append(
                f'<td class="tile" width="33%" style="padding:6px;vertical-align:top">'
                f'<div style="border:1px solid #e2e8f0;border-top:3px solid {color};border-radius:10px;padding:12px 14px;background:#ffffff">'
                f'<div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px">{e(label)}</div>'
                f'<div style="font-size:20px;font-weight:700;color:#0f172a;margin-top:4px">{e(value)}</div>'
                f'{sub_html}'
                '</div></td>')
        rows = ["".join(cells[i:i + 3]) for i in range(0, len(cells), 3)]
        body_rows.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed">'
                         + "".join(f"<tr>{r}</tr>" for r in rows) + "</table>")
    for heading, lines in sections:
        if not lines:
            continue
        body_rows.append(f'<div style="font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#64748b;'
                         f'margin:22px 0 8px">{e(heading)}</div>')
        trs = []
        for n, line in enumerate(lines):
            level, msg, path = line[0], line[1], line[2]
            value = line[3] if len(line) > 3 else ""
            url = _link(path) if path else ""
            dot = COLORS.get(level, "#94a3b8")
            label = LEVEL_LABEL.get(level, "") if level in ("warning", "critical") else ""
            cell = e(msg)
            if url:
                cell = f'<a href="{e(url)}" style="color:#0f172a;text-decoration:none">{cell}</a>'
            if label:
                cell += f' <span style="font-size:11px;font-weight:600;color:{dot}">{e(label)}</span>'
            border = "" if n == len(lines) - 1 else "border-bottom:1px solid #f1f5f9;"
            trs.append(f'<tr><td width="18" style="padding:9px 0;{border}vertical-align:top">'
                       f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot};margin-top:5px"></span></td>'
                       f'<td style="padding:9px 8px 9px 0;{border}font-size:14px;color:#0f172a;line-height:1.45">{cell}</td>'
                       f'<td align="right" style="padding:9px 0;{border}font-size:13px;color:#475569;white-space:nowrap;vertical-align:top;'
                       f'font-variant-numeric:tabular-nums">{e(value)}</td></tr>')
        body_rows.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                         'style="border:1px solid #e2e8f0;border-radius:10px;padding:2px 14px;background:#ffffff">' + "".join(trs) + "</table>")
    if link:
        body_rows.append(f'<div style="margin:26px 0 4px;text-align:center"><a href="{e(link)}" style="display:inline-block;background:#0891b2;'
                         'color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;padding:11px 22px;border-radius:9px">'
                         'Im Dashboard öffnen</a></div>')
    parts.append('<tr><td class="pad" style="background:#f8fafc;padding:22px 28px 26px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 14px 14px">'
                 + "".join(body_rows) + "</td></tr>")
    settings = _link("/settings#email")
    footer = f"HostCore auf {e(host)}"
    if settings:
        footer += f' · <a href="{e(settings)}" style="color:#64748b">Benachrichtigungen einstellen</a>'
    parts.append(f'<tr><td style="padding:16px 8px;text-align:center;font-size:12px;color:#94a3b8">{footer}</td></tr>')
    body = ('<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">'
            '<style>@media (max-width:540px){.tile{display:block!important;width:100%!important;box-sizing:border-box;padding:4px 0!important}'
            '.pad{padding-left:16px!important;padding-right:16px!important}.outer{padding:12px 6px!important}}</style></head>'
            f'<body style="margin:0;padding:0;background:#e2e8f0;font-family:{FONT}">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e2e8f0"><tr><td class="outer" align="center" style="padding:24px 12px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;font-family:{FONT}">'
            + "".join(parts) + "</table></td></tr></table></body></html>")
    return "\n".join(text), body


def _hostname() -> str:
    import socket
    return socket.gethostname()


def send(subject: str, text: str, html_body: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None,
         to: Optional[List[str]] = None) -> None:
    """to: own recipients (e.g. the user of a password reset) instead of the configured ones."""
    c = cfg or config()
    if not c.get("host") or not c.get("sender") or not (to or c.get("recipients")):
        raise NotifyError("SMTP-Server, Absender und Empfänger eintragen.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((c.get("sender_name") or "HostCore", c["sender"]))
    msg["To"] = ", ".join(to or c["recipients"])
    msg["Message-ID"] = make_msgid(domain=c["sender"].split("@")[-1])
    msg["Date"] = datetime.datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    port = int(c.get("port") or 587)
    ctx = ssl.create_default_context()
    if c.get("insecure"):
        # Own mail server with a self-signed certificate: still encrypted, but the certificate is not checked
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
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
            if isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
                err = "Zertifikat des Mailservers ungültig (selbst signiert?) – „Zertifikat nicht prüfen“ einschalten."
            _set_state(last_error=err, last_error_ts=int(time.time()))
            raise NotifyError(f"Versand fehlgeschlagen – {err}")
    s = _state()
    _set_state(last_sent=int(time.time()), last_subject=subject, last_error="", sent_total=s.get("sent_total", 0) + 1)


def send_test(fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(config())
    text, body = _render("Test-Nachricht", "Der E-Mail-Versand des Dashboards funktioniert.", [])
    send("[HostCore] Test-Nachricht", text, body, c)
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
    import alerting
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
           if e["ts"] > since and LEVELS.get(e["level"], 0) >= threshold and e["category"] != "journal"
           and not alerting.handles(e)]   # alarms mail those themselves
    if new:
        new.sort(key=lambda e: e["ts"])
        worst = max(new, key=lambda e: LEVELS[e["level"]])
        lines = [(e["level"], f"{e['title']}{f' – ' + e['detail'] if e.get('detail') else ''}", e.get("link", ""), _fmt_ts(e["ts"]))
                 for e in new]
        subject = f"[HostCore] {worst['title']}" if len(new) == 1 else f"[HostCore] {len(new)} neue Ereignisse ({LEVEL_LABEL[worst['level']]})"
        text, body = _render("Neue Ereignisse", f"{_hostname()} · seit {_fmt_ts(since)}", [("Ereignisse", lines)], "/system#ereignisse",
                             overall=worst["level"])
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
    for mod, fn, link in (("eol", "alerts", "/system#lebenszyklus"), ("vulns", "alerts", "/system#schwachstellen"),
                          ("incidents", "overdue_actions", "/doku#stoerungen")):
        try:
            for a in getattr(__import__(mod), fn)():
                out.append((a["level"], a["text"], link))
        except Exception as e:
            logger.warning(f"Deadlines ({mod}): {e}")
    return out


def _daily_mail() -> bool:
    items = deadlines()
    if not items:
        return True
    worst = max((LEVELS.get(i[0], 0) for i in items), default=0)
    sections = [("Fällig und bald fällig", items)]
    try:
        # Outside witness for the tamper-evident activity log: the newest hash, kept in your mailbox
        import audit
        cp = audit.checkpoint()
        if cp:
            sections.append(("Prüfsumme Aktivitätsprotokoll", [("info", f"Eintrag {cp['id']}: {cp['hash'][:16]}…{cp['hash'][-8:]}", "/system#aktivitaet")]))
    except Exception as e:
        logger.warning(f"Audit checkpoint: {e}")
    text, body = _render("Tagesübersicht", f"{_hostname()} · {time.strftime('%d.%m.%Y')}",
                         sections, "/", overall=["good", "info", "warning", "critical"][worst])
    n = len(items)
    return _send_quiet(f"[HostCore] Tagesübersicht: {n} {'Punkt' if n == 1 else 'Punkte'}", text, body)


def weekly_content() -> Tuple[str, str, str]:
    import insights
    rep = insights.weekly_report()
    m = rep["metrics"]
    num = lambda v, unit="", d=1: "–" if v is None else f"{v:.{d}f}".replace(".", ",") + unit

    # Availability: services and web checks
    avail, values = [], []
    for s in rep.get("services", []):
        if s["uptime"] is not None and not s["never_up"]:
            values.append(s["uptime"])
            avail.append(("good" if s["uptime"] >= 99.9 else "warning" if s["uptime"] >= 99 else "critical", s["name"],
                          "/system#verfuegbarkeit", f"{num(s['uptime'], ' %', 2)}" + (f" · {s['outages']} Ausf." if s["outages"] else "")))
    for w in rep.get("web_services", []):
        if w["uptime"] is not None:
            values.append(w["uptime"])
            avail.append(("good" if w["uptime"] >= 99.9 else "warning" if w["uptime"] >= 99 else "critical", w["name"],
                          "/settings#web-dienste", num(w["uptime"], " %", 2)))
    avail.sort(key=lambda a: -LEVELS.get(a[0], 0))
    worst_up = min(values) if values else None
    ev = rep.get("events", {})
    logins = rep.get("logins", {})
    temp_max = m.get("temp_max")
    tiles = [
        ("Verfügbarkeit", num(worst_up, " %", 2) if worst_up is not None else "–", "schlechtester Dienst" if values else "keine Dienste",
         "" if worst_up is None else "good" if worst_up >= 99.9 else "warning" if worst_up >= 99 else "critical"),
        ("Ereignisse", f"{ev.get('critical', 0)} / {ev.get('warning', 0)}", "kritisch / Warnungen",
         "critical" if ev.get("critical") else "warning" if ev.get("warning") else "good"),
        ("Anmeldungen", f"{logins.get('failed', 0)}", f"fehlgeschlagen · {logins.get('ok', 0)} erfolgreich",
         "warning" if logins.get("failed") else "good"),
        ("CPU", num(m.get("cpu_avg"), " %"), f"Spitze {num(m.get('cpu_max'), ' %')}", ""),
        ("Temperatur", num(temp_max, " °C"), f"höchstens · Ø {num(m.get('temp_avg'), ' °C')}",
         "" if temp_max is None else "critical" if temp_max >= 80 else "warning" if temp_max >= 70 else "good"),
        ("Arbeitsspeicher", num(m.get("ram_avg"), " %"), f"Ø · Spitze {num(m.get('ram_max'), ' %')}", ""),
    ]
    if m.get("power_avg") is not None:
        tiles.append(("Leistung", num(m["power_avg"], " W", 2), "im Schnitt", ""))
    traffic = rep.get("traffic") or {}
    if traffic.get("rx") or traffic.get("tx"):
        gb = lambda b: f"{b / 1e9:.1f}".replace(".", ",") + " GB"
        tiles.append(("Datenverbrauch", gb((traffic.get("rx") or 0) + (traffic.get("tx") or 0)),
                      f"↓ {gb(traffic.get('rx') or 0)} · ↑ {gb(traffic.get('tx') or 0)}", ""))

    status_items = rep.get("status", {}).get("items", [])
    status = [(i["level"], i["text"], i.get("link", "") if i.get("link", "").startswith("/") else "")
              for i in status_items if i["level"] != "good"]
    overall = rep.get("status", {}).get("level", "good")
    security = []
    if logins.get("ssh_failed"):
        security.append(("warning" if logins["ssh_failed"] >= 20 else "info", "Fehlgeschlagene SSH-Anmeldungen", "/system#ssh", str(logins["ssh_failed"])))
    for d in rep.get("new_devices", [])[:10]:
        security.append(("info", f"Neues Gerät: {d}", "/system#geraete"))
    dated = [(j["ts"], ("info", f"{j['title']} ({j['category']})", "/doku#tagebuch", _fmt_ts(j["ts"]))) for j in rep.get("journal", [])[:15]]
    dated += [(c["ts"], ("info", f"{c['action']}{' – ' + c['detail'] if c.get('detail') else ''}", "/system#aktivitaet", _fmt_ts(c["ts"])))
              for c in rep.get("changes", [])[:15]]
    changes = [c for _, c in sorted(dated, key=lambda x: -x[0])]
    top = [("info", h["label"], "/system#ressourcen", f"{num(h['cpu_avg'], ' %')} CPU") for h in rep.get("top_cpu", [])[:5]]
    period = f"{time.strftime('%d.%m.', time.localtime(rep['from']))} – {time.strftime('%d.%m.%Y', time.localtime(rep['to']))}"
    text, body = _render("Wochenbericht", f"{_hostname()} · {period}",
                         [("Was gerade Aufmerksamkeit braucht", status), ("Verfügbarkeit", avail), ("Sicherheit", security),
                          ("Änderungen", changes[:20]), ("Größte CPU-Verbraucher", top)], "/report", tiles=tiles, overall=overall)
    return f"[HostCore] Wochenbericht {period}", text, body


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
        _send_quiet(f"[HostCore] Freigabe: {item['tool']}", text, body)
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
