"""
Access review (Einstellungen -> Rechte-Review): who can do what, and is that still right?

- Every account with its group, critical permissions, second factor, last activity, password
  age and guest expiry; groups with their members; other ways in: share and exchange links,
  MCP identities, SSO links.
- Findings: inactive or never used accounts, administrators and holders of critical rights
  without a second factor, expired guests that still exist, old passwords, long disabled accounts.
- Review cycle (default every 90 days): an administrator signs the review off with a note; the
  state at that moment is kept as a snapshot with a SHA-256 fingerprint and can be exported
  (Markdown, CSV) as evidence. An overdue review shows in the status overview.
"""

import csv
import hashlib
import io
import json
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "access_review"
CRITICAL_PERMS = ["users.manage", "terminal", "firewall.manage", "system.power", "updates.install", "apps.install",
                  "changes.approve", "files.share"]
DEFAULTS = {"interval_days": 90, "inactive_days": 90, "password_days": 365}
MAX_REVIEWS = 40
_lock = threading.Lock()


class ReviewError(Exception):
    pass


def _cfg() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    c = c if isinstance(c, dict) else {}
    return {**DEFAULTS, **c, "reviews": list(c.get("reviews") or [])}


def save_settings(fields: Dict[str, Any]) -> Dict[str, Any]:
    c = _cfg()
    for key, lo, hi in (("interval_days", 7, 365), ("inactive_days", 14, 730), ("password_days", 30, 1825)):
        try:
            v = int(fields.get(key) or c[key])
        except (TypeError, ValueError):
            raise ReviewError("Bitte Zahlen eintragen.")
        if not lo <= v <= hi:
            raise ReviewError(f"Wert außerhalb von {lo}–{hi} Tagen.")
        c[key] = v
    settings_store.set(KEY, c)
    return overview()


# ---------------------------------------------------------------------------
# Current state
# ---------------------------------------------------------------------------
def _days(ts: Optional[float]) -> Optional[int]:
    return int((time.time() - ts) // 86400) if ts else None


def _activity() -> Dict[str, int]:
    """Newest sign-in or session use per user (sessions with "stay logged in" count as use)."""
    import audit
    import auth
    out: Dict[str, int] = {}
    for s in auth.list_sessions():
        out[s["user"]] = max(out.get(s["user"], 0), int(s.get("last_seen") or 0))
    try:
        with audit._conn() as conn:
            for user, ts in conn.execute("SELECT user, MAX(ts) FROM audit WHERE category = 'login' AND status BETWEEN 200 AND 299 "
                                         "AND action LIKE 'Anmeldung%' GROUP BY user"):
                if user:
                    key = auth.canonical_username(user)
                    if key:
                        out[key] = max(out.get(key, 0), int(ts))
    except Exception:
        pass
    return out


def _f(level: str, text: str, fix: str = "", action: str = "") -> Dict[str, str]:
    return {"level": level, "text": text, "fix": fix, "action": action}


def state() -> Dict[str, Any]:
    import auth
    import oidc
    import websec
    c = _cfg()
    groups = {g["id"]: g for g in auth.list_groups()}
    activity = _activity()
    now = time.time()
    sso_2fa = oidc.counts_as_2fa()
    users = []
    for u in auth.list_users():
        name = u["username"]
        g = groups.get(u["group"], {})
        perms = set(g.get("perms") or [])
        critical = [p for p in CRITICAL_PERMS if p in perms]
        last = max(activity.get(name, 0), u.get("last_login") or 0) or None
        factor = "TOTP" if u["totp"] else "Passkey" if u["passkeys"] else "SSO" if (u["sso"] and sso_2fa) else ""
        findings: List[Dict[str, str]] = []
        if not u["disabled"] and not u["expired"]:
            idle = _days(last)
            if last is None and _days(u.get("created")) is not None and _days(u["created"]) > 30:
                findings.append(_f("warning", "Nie angemeldet (seit über 30 Tagen angelegt)", "Deaktivieren, wenn das Konto nicht gebraucht wird.", "disable"))
            elif idle is not None and idle > c["inactive_days"]:
                findings.append(_f("warning", f"Seit {idle} Tagen nicht angemeldet", "Deaktivieren, wenn das Konto nicht mehr gebraucht wird.", "disable"))
            if not factor and u["group"] == "admin":
                findings.append(_f("critical", "Administrator ohne zweiten Faktor", "Zwei-Faktor-Anmeldung oder Passkey einrichten lassen, oder unter Sicherheit Pflicht machen."))
            elif not factor and critical:
                findings.append(_f("warning", "Kritische Rechte ohne zweiten Faktor", "Zwei-Faktor-Anmeldung einrichten oder die Rechte reduzieren."))
            pw = _days(u.get("changed"))
            if not u["sso_only"] and pw is not None and pw > c["password_days"]:
                findings.append(_f("info", f"Passwort seit {pw} Tagen unverändert", "Neues Passwort setzen lassen."))
        if u["expired"]:
            findings.append(_f("info", "Gastzugang abgelaufen, Konto existiert noch", "Konto löschen.", "delete"))
        elif u["disabled"] and (_days(u.get("changed")) or 0) > 180:
            findings.append(_f("info", "Seit langem deaktiviert", "Konto löschen, wenn es nicht wiederkommt.", "delete"))
        users.append({"username": name, "group": u["group"], "group_name": g.get("name", u["group"]), "critical": critical,
                      "perm_count": len(perms), "factor": factor, "sso": u["sso"], "sso_only": u["sso_only"], "last_activity": last,
                      "created": u.get("created"), "password_changed": u.get("changed"), "expires": u.get("expires"),
                      "disabled": u["disabled"], "expired": u["expired"], "email": bool(u.get("email")), "findings": findings})
    users.sort(key=lambda x: (-max([{"critical": 3, "warning": 2, "info": 1}[f["level"]] for f in x["findings"]] or [0]), x["username"].casefold()))

    group_list = [{"id": gid, "name": g["name"], "members": g["members"], "critical": [p for p in CRITICAL_PERMS if p in g["perms"]],
                   "perms": len(g["perms"])} for gid, g in groups.items()]
    general: List[Dict[str, str]] = []
    admins = [u for u in users if u["group"] == "admin" and not u["disabled"] and not u["expired"]]
    if len(admins) > 3:
        general.append(_f("info", f"{len(admins)} Administratoren", "Nur so viele Administratoren wie nötig – andere in eine Gruppe mit passenden Rechten."))
    if websec.config()["twofa"] == "none" and admins:
        general.append(_f("warning", "Zwei-Faktor-Anmeldung ist für niemanden Pflicht", "Einstellungen → Sicherheit → Zwei-Faktor: „Pflicht für Administratoren“."))

    links = []
    try:
        import files
        links += [{"kind": "Freigabe-Link", "id": s["token"], "what": s["name"], "user": s.get("user", ""), "created": s["created"],
                   "expires": s["expires"], "delete": f"/api/files/shares/{s['token']}/delete"} for s in files.list_shares()]
    except Exception:
        pass
    try:
        import exchange
        links += [{"kind": "Austausch-Link", "id": x["token"], "what": f"{x.get('folder', '')} ({x.get('mode_label', '')})", "user": x.get("user", ""),
                   "created": x["created"], "expires": x["expires"], "delete": f"/api/exchange/{x['token']}/delete"} for x in exchange.list_links()]
    except Exception:
        pass
    mcp = []
    try:
        import mcp_server
        mc = mcp_server.config()
        if mc.get("enabled"):
            mcp = [{"identity": m["match"], "user": m["user"]} for m in mc.get("users", [])]
    except Exception:
        pass
    findings = sum(len(u["findings"]) for u in users) + len(general)
    return {"users": users, "groups": group_list, "general": general, "links": links, "mcp": mcp,
            "counts": {"users": len(users), "active": sum(1 for u in users if not u["disabled"] and not u["expired"]),
                       "findings": findings, "critical": sum(1 for u in users for f in u["findings"] if f["level"] == "critical"),
                       "links": len(links)},
            "generated": int(now)}


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------
def _snapshot(st: Dict[str, Any]) -> Dict[str, Any]:
    return {"users": [{k: u[k] for k in ("username", "group_name", "critical", "factor", "last_activity", "disabled", "expired")}
                      | {"findings": [f["text"] for f in u["findings"]]} for u in st["users"]],
            "groups": [{k: g[k] for k in ("name", "members", "critical")} for g in st["groups"]],
            "links": [{k: l[k] for k in ("kind", "what", "user", "expires")} for l in st["links"]],
            "mcp": st["mcp"], "counts": st["counts"]}


def _fingerprint(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def overview() -> Dict[str, Any]:
    c = _cfg()
    st = state()
    last = c["reviews"][-1] if c["reviews"] else None
    due = (last["ts"] + c["interval_days"] * 86400) if last else None
    return {**st, "settings": {k: c[k] for k in DEFAULTS},
            "reviews": [{k: r[k] for k in ("id", "ts", "user", "note", "fingerprint", "open_findings")} for r in reversed(c["reviews"])],
            "last_review": last and {k: last[k] for k in ("id", "ts", "user")}, "due": due,
            "overdue": bool(due is None or due < time.time())}


def sign_off(note: str, user: str) -> Dict[str, Any]:
    note = (note or "").strip()
    if len(note) > 2000:
        raise ReviewError("Notiz: höchstens 2000 Zeichen.")
    st = state()
    if st["counts"]["critical"] and not note:
        raise ReviewError("Es gibt kritische Befunde – bitte in der Notiz festhalten, warum trotzdem abgezeichnet wird.")
    snap = _snapshot(st)
    with _lock:
        c = _cfg()
        c["reviews"].append({"id": secrets.token_hex(5), "ts": int(time.time()), "user": user, "note": note, "snapshot": snap,
                             "fingerprint": _fingerprint(snap), "open_findings": st["counts"]["findings"]})
        c["reviews"] = c["reviews"][-MAX_REVIEWS:]
        settings_store.set(KEY, c)
    return overview()


def _fmt(ts: Optional[float]) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"


def review_markdown(review_id: str) -> str:
    r = next((x for x in _cfg()["reviews"] if x["id"] == review_id), None)
    if not r:
        raise ReviewError("Review nicht gefunden.")
    s = r["snapshot"]
    out = [f"# Rechte-Review vom {time.strftime('%d.%m.%Y %H:%M', time.localtime(r['ts']))}", "",
           f"- Abgezeichnet von: {r['user']}", f"- Offene Befunde zu diesem Zeitpunkt: {r['open_findings']}",
           f"- Fingerabdruck (SHA-256): `{r['fingerprint']}`", ""]
    if r.get("note"):
        out += ["## Notiz", "", r["note"], ""]
    out += ["## Konten", "", "| Benutzer | Gruppe | Kritische Rechte | 2. Faktor | Zuletzt aktiv | Status | Befunde |", "|---|---|---|---|---|---|---|"]
    for u in s["users"]:
        status = "abgelaufen" if u["expired"] else "deaktiviert" if u["disabled"] else "aktiv"
        out.append(f"| {u['username']} | {u['group_name']} | {', '.join(u['critical']) or '–'} | {u['factor'] or '–'} | {_fmt(u['last_activity'])} | "
                   f"{status} | {'; '.join(u['findings']) or '–'} |")
    out += ["", "## Gruppen", "", "| Gruppe | Mitglieder | Kritische Rechte |", "|---|---|---|"]
    out += [f"| {g['name']} | {g['members']} | {', '.join(g['critical']) or '–'} |" for g in s["groups"]]
    if s["links"]:
        out += ["", "## Links ohne Anmeldung", "", "| Art | Ziel | Erstellt von | Gültig bis |", "|---|---|---|---|"]
        out += [f"| {l['kind']} | {l['what']} | {l['user']} | {_fmt(l['expires'])} |" for l in s["links"]]
    if s["mcp"]:
        out += ["", "## MCP-Identitäten", ""] + [f"- {m['identity']} → {m['user']}" for m in s["mcp"]]
    return "\n".join(out) + "\n"


def csv_export() -> str:
    st = state()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Benutzer", "Gruppe", "Kritische Rechte", "Zweiter Faktor", "SSO", "Zuletzt aktiv", "Angelegt", "Passwort geändert",
                "Gast bis", "Deaktiviert", "Befunde"])
    for u in st["users"]:
        w.writerow([u["username"], u["group_name"], ", ".join(u["critical"]), u["factor"], "ja" if u["sso"] else "", _fmt(u["last_activity"]),
                    _fmt(u["created"]), _fmt(u["password_changed"]), _fmt(u["expires"]) if u["expires"] else "", "ja" if u["disabled"] else "",
                    "; ".join(f["text"] for f in u["findings"])])
    return buf.getvalue()


_alert_cache: Dict[str, Any] = {"ts": 0.0, "items": []}


def alerts() -> List[Dict[str, Any]]:
    """For the status overview - cached, the state walks users, sessions and the activity log."""
    if time.time() - _alert_cache["ts"] < 300:
        return _alert_cache["items"]
    out: List[Dict[str, Any]] = []
    _alert_cache.update(ts=time.time(), items=out)
    try:
        o = overview()
    except Exception:
        return out
    if o["counts"]["critical"]:
        out.append({"level": "warning", "text": f"Rechte-Review: {o['counts']['critical']} {'Administrator' if o['counts']['critical'] == 1 else 'Administratoren'} ohne zweiten Faktor"})
    if o["last_review"] and o["overdue"]:
        out.append({"level": "info", "text": "Rechte-Review ist fällig"})
    return out
