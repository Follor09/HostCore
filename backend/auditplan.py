"""
Internal audit programme (Compliance -> Interne Audits): plan audits, carry them out against a
checklist, record findings and follow corrective actions to their end.

- An audit has a scope, an auditor, the person responsible for the audited area and a date. The
  auditor shouldn't audit their own area; that is pointed out and appears in the report.
- Checklist: free points or the requirements of the baseline check (Grundschutz-Check).
- A deviation opens a finding by itself. Findings need an owner, a corrective action and a date;
  they are closed by someone other than the owner, who confirms the action worked.
- An audit is finished when every point is rated. The report goes to Markdown and into the
  evidence package.
"""

import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "internal_audits"
STATUSES = {"geplant": "Geplant", "durchfuehrung": "In Durchführung", "abgeschlossen": "Abgeschlossen"}
RESULTS = {"offen": "Offen", "konform": "Konform", "beobachtung": "Beobachtung", "gering": "Geringe Abweichung", "schwer": "Schwere Abweichung", "na": "Nicht anwendbar"}
FINDING_STATES = {"offen": "Offen", "massnahme": "Maßnahme läuft", "wirksam": "Wirksam geschlossen"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_AUDITS = 500
MAX_CHECKS = 300
_lock = threading.Lock()


class AuditPlanError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _get(items: List[Dict[str, Any]], aid: str) -> Dict[str, Any]:
    a = next((x for x in items if x["id"] == aid), None)
    if not a:
        raise AuditPlanError("Audit nicht gefunden.")
    return a


def _finding(a: Dict[str, Any], fid: str) -> Dict[str, Any]:
    f = next((x for x in a.get("findings", []) if x["id"] == fid), None)
    if not f:
        raise AuditPlanError("Feststellung nicht gefunden.")
    return f


def _date(value: Any, label: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise AuditPlanError(f"{label} fehlt.")
    if text and not DATE_RE.match(text):
        raise AuditPlanError(f"{label}: ungültiges Datum.")
    return text


def _view(a: Dict[str, Any]) -> Dict[str, Any]:
    today = time.strftime("%Y-%m-%d")
    checks = a.get("checks", [])
    rated = sum(1 for c in checks if c["result"] != "offen")
    finds = [{**f, "state_label": FINDING_STATES[f["state"]], "severity_label": RESULTS[f["severity"]],
              "overdue": bool(f["state"] != "wirksam" and f.get("due") and f["due"] < today)} for f in a.get("findings", [])]
    return {**a, "findings": finds, "status_label": STATUSES[a["status"]], "rated": rated, "total": len(checks),
            "open_findings": sum(1 for f in finds if f["state"] != "wirksam"), "overdue": bool(a["status"] == "geplant" and a["planned"] < today),
            "conflict": bool(a.get("auditor") and a["auditor"].strip().lower() == (a.get("responsible") or "").strip().lower())}


def overview() -> Dict[str, Any]:
    views = sorted((_view(a) for a in _items()), key=lambda a: (a["status"] == "abgeschlossen", a["planned"]))
    return {"audits": views, "statuses": STATUSES, "results": RESULTS, "finding_states": FINDING_STATES,
            "open_findings": sum(a["open_findings"] for a in views), "planned": sum(1 for a in views if a["status"] != "abgeschlossen")}


def save(aid: Optional[str], f: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    title = str(f.get("title") or "").strip()
    if not 1 <= len(title) <= 140:
        raise AuditPlanError("Titel fehlt (höchstens 140 Zeichen).")
    clean = {"title": title, "standard": str(f.get("standard") or "").strip()[:80], "scope": str(f.get("scope") or "").strip()[:2000],
             "auditor": str(f.get("auditor") or "").strip()[:80], "responsible": str(f.get("responsible") or "").strip()[:80],
             "planned": _date(f.get("planned"), "Termin", True)}
    if not clean["auditor"]:
        raise AuditPlanError("Auditor fehlt.")
    with _lock:
        items = _items()
        if aid:
            a = _get(items, aid)
            if a["status"] == "abgeschlossen":
                raise AuditPlanError("Abgeschlossene Audits bleiben unverändert.")
            a.update(clean)
        else:
            if len(items) >= MAX_AUDITS:
                raise AuditPlanError(f"Höchstens {MAX_AUDITS} Audits.")
            aid = secrets.token_hex(5)
            items.append({"id": aid, **clean, "status": "geplant", "checks": [], "findings": [], "created": int(time.time()), "user": user, "summary": ""})
        settings_store.set(KEY, items)
    return {**overview(), "saved": aid}


def _open(items: List[Dict[str, Any]], aid: str) -> Dict[str, Any]:
    a = _get(items, aid)
    if a["status"] == "abgeschlossen":
        raise AuditPlanError("Dieses Audit ist abgeschlossen.")
    return a


def add_checks(aid: str, texts: List[str], from_baseline: bool = False) -> Dict[str, Any]:
    rows = [{"text": str(t).strip()[:500], "ref": ""} for t in texts if str(t).strip()]
    if from_baseline:
        import baseline
        rows += [{"text": f"{r[3]}: {r[4]}"[:500], "ref": f"{r[0]} ({r[2]})"} for r in baseline.CONTROLS]
    if not rows:
        raise AuditPlanError("Keine Prüfpunkte angegeben.")
    with _lock:
        items = _items()
        a = _open(items, aid)
        known = {c["text"] for c in a["checks"]}
        new = [{"id": secrets.token_hex(4), **r, "result": "offen", "note": ""} for r in rows if r["text"] not in known]
        if len(a["checks"]) + len(new) > MAX_CHECKS:
            raise AuditPlanError(f"Höchstens {MAX_CHECKS} Prüfpunkte.")
        a["checks"] += new
        if a["status"] == "geplant" and new:
            a["status"] = "durchfuehrung"
            a["started"] = int(time.time())
        settings_store.set(KEY, items)
    return overview()


def rate(aid: str, cid: str, result: str, note: str, user: str = "") -> Dict[str, Any]:
    if result not in RESULTS:
        raise AuditPlanError("Unbekannte Bewertung.")
    note = str(note or "").strip()[:2000]
    if result in ("gering", "schwer") and not note:
        raise AuditPlanError("Eine Abweichung braucht eine Beschreibung.")
    with _lock:
        items = _items()
        a = _open(items, aid)
        c = next((x for x in a["checks"] if x["id"] == cid), None)
        if not c:
            raise AuditPlanError("Prüfpunkt nicht gefunden.")
        c.update(result=result, note=note, user=user, ts=int(time.time()))
        known = next((f for f in a["findings"] if f.get("check") == cid), None)
        if result in ("gering", "schwer"):
            if known:
                known.update(severity=result, description=note)
            else:
                a["findings"].append({"id": secrets.token_hex(4), "check": cid, "title": c["text"][:140], "description": note, "severity": result, "owner": "",
                                      "action": "", "due": "", "state": "offen", "created": int(time.time())})
        elif known and known["state"] == "offen" and not known["action"]:
            a["findings"].remove(known)
        settings_store.set(KEY, items)
    return overview()


def update_finding(aid: str, fid: str, fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        a = _get(items, aid)
        f = _finding(a, fid)
        if f["state"] == "wirksam":
            raise AuditPlanError("Diese Feststellung ist geschlossen.")
        f.update(owner=str(fields.get("owner") or "").strip()[:80], action=str(fields.get("action") or "").strip()[:2000], due=_date(fields.get("due"), "Frist"))
        f["state"] = "massnahme" if f["action"] else "offen"
        settings_store.set(KEY, items)
    return overview()


def close_finding(aid: str, fid: str, note: str, user: str = "") -> Dict[str, Any]:
    note = str(note or "").strip()[:1000]
    if not note:
        raise AuditPlanError("Bitte festhalten, woran die Wirksamkeit erkennbar ist.")
    with _lock:
        items = _items()
        a = _get(items, aid)
        f = _finding(a, fid)
        if not f.get("action") or not f.get("owner"):
            raise AuditPlanError("Erst Verantwortliche(n) und Maßnahme eintragen.")
        if f["owner"].strip().lower() == (user or "").strip().lower():
            raise AuditPlanError("Die Wirksamkeit bestätigt jemand anderes als die verantwortliche Person.")
        f.update(state="wirksam", verified_by=user, verified_at=int(time.time()), verified_note=note)
        settings_store.set(KEY, items)
    return overview()


def finish(aid: str, summary: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        a = _open(items, aid)
        if not a["checks"]:
            raise AuditPlanError("Das Audit hat noch keine Prüfpunkte.")
        left = sum(1 for c in a["checks"] if c["result"] == "offen")
        if left:
            raise AuditPlanError(f"{left} Prüfpunkte sind noch nicht bewertet.")
        a.update(status="abgeschlossen", finished=int(time.time()), finished_by=user, summary=str(summary or "").strip()[:4000])
        settings_store.set(KEY, items)
    return overview()


def delete(aid: str) -> Dict[str, Any]:
    with _lock:
        items = _items()
        if _get(items, aid)["status"] == "abgeschlossen":
            raise AuditPlanError("Abgeschlossene Audits bleiben als Nachweis erhalten.")
        settings_store.set(KEY, [x for x in items if x["id"] != aid])
    return overview()


def markdown(aid: str = "") -> str:
    fmt = lambda ts: time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"
    o = overview()
    rows = [a for a in o["audits"] if not aid or a["id"] == aid]
    if aid and not rows:
        raise AuditPlanError("Audit nicht gefunden.")
    out = ["# Interne Audits", ""]
    if not aid:
        out += ["| Audit | Termin | Status | Auditor | Prüfpunkte | Offene Feststellungen |", "|---|---|---|---|---|---|"]
        out += [f"| {a['title']} | {a['planned']} | {a['status_label']} | {a['auditor']} | {a['rated']} von {a['total']} | {a['open_findings']} |" for a in rows]
        out.append("")
    for a in rows:
        out += [f"## {a['title']}", "", f"- Status: {a['status_label']} · Termin: {a['planned']} · abgeschlossen: {fmt(a.get('finished'))}", f"- Grundlage: {a['standard'] or '–'}",
                f"- Auditor: {a['auditor']} · Verantwortlich: {a['responsible'] or '–'}"]
        if a["conflict"]:
            out.append("- Hinweis: Auditor prüft den eigenen Bereich.")
        if a["scope"]:
            out += ["", a["scope"]]
        if a["checks"]:
            out += ["", "| Prüfpunkt | Ergebnis | Notiz |", "|---|---|---|"]
            out += [f"| {c['text'].replace('|', '/')} | {RESULTS[c['result']]} | {(c['note'] or '').replace('|', '/')} |" for c in a["checks"]]
        if a["findings"]:
            out += ["", "| Feststellung | Schwere | Verantwortlich | Maßnahme | Frist | Status |", "|---|---|---|---|---|---|"]
            out += [f"| {f['title'].replace('|', '/')} | {f['severity_label']} | {f['owner'] or '–'} | {(f['action'] or '–').replace('|', '/')} | {f['due'] or '–'} | {f['state_label']} |" for f in a["findings"]]
        if a.get("summary"):
            out += ["", "**Fazit:** " + a["summary"]]
        out.append("")
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    out = []
    for a in overview()["audits"]:
        if a["overdue"]:
            out.append({"level": "warning", "text": f"Audit „{a['title']}“ war für den {a['planned']} geplant"})
        late = sum(1 for f in a["findings"] if f["overdue"])
        if late:
            out.append({"level": "warning", "text": f"Audit „{a['title']}“: {late} Korrekturmaßnahmen überfällig"})
    return out
