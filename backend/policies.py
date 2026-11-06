"""
Policy management (Compliance -> Richtlinien): internal policies with versions, approval and proof
that people have read them.

- A policy has a draft and released versions. Every released version keeps its text, its SHA-256
  fingerprint, author and approver; older versions stay readable.
- Release follows the four-eyes rule of change requests (permission changes.approve); the author
  can only release alone if nobody else may (recorded as Selbstfreigabe).
- Acknowledgement per user and version: a new version asks again. The audience is everyone or
  chosen groups. Each user sees what is waiting for them.
- A review interval flags policies that are due; export as CSV for auditors.
"""

import csv
import hashlib
import io
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "policies"
CATEGORIES = ("Informationssicherheit", "Datenschutz", "Betrieb", "Personal", "Sonstiges")
APPROVE_PERM = "changes.approve"
MAX_POLICIES = 200
MAX_TEXT = 100000
_lock = threading.Lock()


class PolicyError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _get(items: List[Dict[str, Any]], pid: str) -> Dict[str, Any]:
    p = next((x for x in items if x["id"] == pid), None)
    if not p:
        raise PolicyError("Richtlinie nicht gefunden.")
    return p


def _users() -> List[Dict[str, Any]]:
    import auth
    return [u for u in auth.list_users() if not u["disabled"] and not u["expired"]]


def _audience(p: Dict[str, Any], users: List[Dict[str, Any]]) -> List[str]:
    groups = p.get("groups") or []
    return [u["username"] for u in users if not groups or u.get("group") in groups]


def _approvers(exclude: str = "") -> List[str]:
    import auth
    return [u["username"] for u in _users() if u["username"] != exclude and auth.can(u["username"], APPROVE_PERM)]


def _current(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return p["versions"][-1] if p.get("versions") else None


def _view(p: Dict[str, Any], users: List[Dict[str, Any]], user: str, full: bool = False) -> Dict[str, Any]:
    cur = _current(p)
    who = _audience(p, users)
    done = {a["user"]: a["ts"] for a in p.get("acks", []) if cur and a["version"] == cur["version"]}
    review_due = 0
    if cur and p.get("review_months"):
        review_due = int((p.get("reviewed") or cur["approved_at"]) + p["review_months"] * 30.44 * 86400)
    state = "entwurf" if not cur else "archiviert" if p.get("retired") else "freigegeben"
    v = {k: p.get(k) for k in ("id", "title", "category", "owner", "groups", "review_months", "draft", "draft_by", "draft_note", "submitted", "submitted_by", "created")}
    v.update(state=state, version=cur["version"] if cur else 0, text=cur["text"] if cur else "", sha256=cur["sha256"] if cur else "",
             approved_by=cur["approved_by"] if cur else "", approved_at=cur["approved_at"] if cur else 0, self_approval=bool(cur and cur.get("self_approval")),
             history=[{k: x.get(k) for k in ("version", "approved_by", "approved_at", "author", "note", "sha256")} for x in reversed(p.get("versions", []))],
             audience=len(who), acked=sum(1 for u in who if u in done), missing=[u for u in who if u not in done] if state == "freigegeben" else [],
             mine=bool(state == "freigegeben" and user in who and user not in done), review_due=review_due, review_overdue=bool(state == "freigegeben" and review_due and review_due < time.time()))
    if not full:
        v["text"] = v["text"] if v["mine"] else ""
    return v


def overview(user: str = "") -> Dict[str, Any]:
    import auth
    users = _users()
    items = sorted((_view(p, users, user) for p in _items()), key=lambda x: (x["state"] != "freigegeben", x["title"].lower()))
    return {"policies": items, "categories": CATEGORIES, "groups": [{"id": g["id"], "name": g["name"]} for g in auth.list_groups()],
            "mine": [p["id"] for p in items if p["mine"]], "can_approve": bool(user and auth.can(user, APPROVE_PERM))}


def detail(pid: str, user: str = "") -> Dict[str, Any]:
    p = _get(_items(), pid)
    return _view(p, _users(), user, full=True)


def version_text(pid: str, version: int) -> Dict[str, Any]:
    p = _get(_items(), pid)
    v = next((x for x in p.get("versions", []) if x["version"] == version), None)
    if not v:
        raise PolicyError("Version nicht gefunden.")
    return {k: v.get(k) for k in ("version", "text", "sha256", "approved_by", "approved_at", "author", "note")}


def save(pid: Optional[str], f: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    title = str(f.get("title") or "").strip()
    if not 1 <= len(title) <= 120:
        raise PolicyError("Titel fehlt (höchstens 120 Zeichen).")
    category = str(f.get("category") or "Sonstiges")
    if category not in CATEGORIES:
        raise PolicyError("Unbekannte Kategorie.")
    text = str(f.get("draft") or "")
    if len(text) > MAX_TEXT:
        raise PolicyError(f"Text: höchstens {MAX_TEXT} Zeichen.")
    try:
        months = int(f.get("review_months") or 0)
    except (TypeError, ValueError):
        raise PolicyError("Prüfintervall: bitte eine Zahl.")
    if not 0 <= months <= 120:
        raise PolicyError("Prüfintervall: 0 bis 120 Monate.")
    clean = {"title": title, "category": category, "owner": str(f.get("owner") or "").strip()[:80], "groups": [str(g)[:40] for g in f.get("groups") or []][:20], "review_months": months,
             "draft": text, "draft_note": str(f.get("draft_note") or "").strip()[:300], "draft_by": user, "submitted": 0, "submitted_by": ""}
    with _lock:
        items = _items()
        if pid:
            _get(items, pid).update(clean)
        else:
            if len(items) >= MAX_POLICIES:
                raise PolicyError(f"Höchstens {MAX_POLICIES} Richtlinien.")
            pid = secrets.token_hex(5)
            items.append({"id": pid, **clean, "versions": [], "acks": [], "created": int(time.time()), "user": user})
        settings_store.set(KEY, items)
    return {**overview(user), "saved": pid}


def submit(pid: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        p = _get(items, pid)
        if not (p.get("draft") or "").strip():
            raise PolicyError("Der Entwurf ist leer.")
        p["submitted"], p["submitted_by"] = int(time.time()), user
        settings_store.set(KEY, items)
    return overview(user)


def release(pid: str, user: str = "") -> Dict[str, Any]:
    import auth
    if not auth.can(user, APPROVE_PERM):
        raise PolicyError("Keine Berechtigung zum Freigeben.")
    with _lock:
        items = _items()
        p = _get(items, pid)
        text = p.get("draft") or ""
        if not p.get("submitted"):
            raise PolicyError("Der Entwurf wurde noch nicht zur Freigabe eingereicht.")
        author = p.get("draft_by", "")
        self_approval = user == author
        if self_approval and _approvers(exclude=user):
            raise PolicyError(f"Vier-Augen-Prinzip: eine andere Person muss freigeben ({', '.join(_approvers(exclude=user)[:3])}).")
        number = (_current(p) or {"version": 0})["version"] + 1
        p["versions"].append({"version": number, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(), "author": author, "note": p.get("draft_note", ""),
                              "approved_by": user, "approved_at": int(time.time()), "self_approval": self_approval})
        p.update(draft="", draft_note="", draft_by="", submitted=0, submitted_by="", retired=False, reviewed=int(time.time()))
        settings_store.set(KEY, items)
    return overview(user)


def confirm_review(pid: str, user: str = "") -> Dict[str, Any]:
    """Reviewed, no change needed: the interval starts again, the version stays."""
    with _lock:
        items = _items()
        p = _get(items, pid)
        p["reviewed"], p["reviewed_by"] = int(time.time()), user
        settings_store.set(KEY, items)
    return overview(user)


def retire(pid: str, on: bool, user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        _get(items, pid)["retired"] = bool(on)
        settings_store.set(KEY, items)
    return overview(user)


def acknowledge(pid: str, user: str) -> Dict[str, Any]:
    if not user:
        raise PolicyError("Nicht angemeldet.")
    with _lock:
        items = _items()
        p = _get(items, pid)
        cur = _current(p)
        if not cur or p.get("retired"):
            raise PolicyError("Diese Richtlinie ist nicht in Kraft.")
        if not any(a["user"] == user and a["version"] == cur["version"] for a in p.get("acks", [])):
            p.setdefault("acks", []).append({"user": user, "version": cur["version"], "ts": int(time.time()), "sha256": cur["sha256"]})
            settings_store.set(KEY, items)
    return overview(user)


def delete(pid: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        if _get(items, pid).get("versions"):
            raise PolicyError("Freigegebene Richtlinien bleiben als Nachweis erhalten – bitte archivieren.")
        settings_store.set(KEY, [x for x in items if x["id"] != pid])
    return overview(user)


def csv_export() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Richtlinie", "Version", "Benutzer", "Kenntnis genommen am", "SHA-256"])
    for p in _items():
        for a in p.get("acks", []):
            w.writerow([p["title"], a["version"], a["user"], time.strftime("%d.%m.%Y %H:%M", time.localtime(a["ts"])), a["sha256"]])
    return buf.getvalue()


def markdown() -> str:
    fmt = lambda ts: time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"
    out = ["# Richtlinien", "", "| Richtlinie | Version | Freigabe | Von | Kenntnisnahme | Nächste Prüfung |", "|---|---|---|---|---|---|"]
    for p in overview()["policies"]:
        out.append(f"| {p['title']} | {p['version'] or '–'} | {fmt(p['approved_at'])} | {p['approved_by'] or '–'} | {p['acked']} von {p['audience']} | {fmt(p['review_due'])} |")
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    try:
        o = overview()
    except Exception:
        return []
    out = []
    for p in o["policies"]:
        if p["review_overdue"]:
            out.append({"level": "warning", "text": f"Richtlinie „{p['title']}“: Prüfung überfällig"})
        if p["submitted"]:
            out.append({"level": "info", "text": f"Richtlinie „{p['title']}“ wartet auf Freigabe"})
    missing = sum(1 for p in o["policies"] if p["missing"])
    if missing:
        out.append({"level": "info", "text": f"{missing} {'Richtlinie' if missing == 1 else 'Richtlinien'} noch nicht von allen zur Kenntnis genommen"})
    return out
