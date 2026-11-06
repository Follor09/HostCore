"""
Service catalog (Dokumentation -> Betrieb -> Service-Katalog): standard requests as forms.

- Catalog items define a form and what happens after approval: manual work (a card on the task
  board), a change request, a new VM from a template, or a quick action.
- Workflow: Eingereicht -> In Umsetzung -> Erledigt / Fehlgeschlagen, or Abgelehnt / Zurückgezogen.
  Every step lands in the request's timeline.
- Four eyes as for change requests: the requester cannot approve their own request unless nobody
  else may approve (recorded as Selbstfreigabe). Automatic items always need an approval, and the
  approver must hold the permission of what runs (vms.manage, actions.run): the catalog never
  lets someone do through a form what they couldn't do directly.
"""

import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store
import tasks

KEY = "service_catalog"
KINDS = {"manual": "Manuell (Aufgabe)", "change": "Änderungsantrag", "vm": "VM aus Vorlage", "action": "Schnellaktion"}
STATUSES = {"eingereicht": "Eingereicht", "umsetzung": "In Umsetzung", "erledigt": "Erledigt", "fehlgeschlagen": "Fehlgeschlagen",
            "abgelehnt": "Abgelehnt", "zurueckgezogen": "Zurückgezogen"}
OPEN = ("eingereicht", "umsetzung")
FIELD_TYPES = ("text", "textarea", "number", "select")
FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
APPROVE_PERM = "changes.approve"
RUN_PERM = {"vm": "vms.manage", "action": "actions.run"}
VM_FIELDS = [{"key": "vm_name", "label": "Name der VM", "type": "text", "required": True, "options": []},
             {"key": "ssh_key", "label": "SSH-Schlüssel", "type": "textarea", "required": False, "options": []}]
MAX_ITEMS = 100
MAX_REQUESTS = 2000
_lock = threading.Lock()


class CatalogError(Exception):
    pass


def _data() -> Dict[str, Any]:
    return {"items": [], "requests": [], **(settings_store.get(KEY, {}) or {})}


def _save(data: Dict[str, Any]) -> None:
    data["requests"] = data["requests"][-MAX_REQUESTS:]
    settings_store.set(KEY, data)


def _find(rows: List[Dict[str, Any]], rid: str, label: str) -> Dict[str, Any]:
    row = next((x for x in rows if x["id"] == rid), None)
    if not row:
        raise CatalogError(f"{label} nicht gefunden.")
    return row


def _approvers(exclude: str = "") -> List[str]:
    import auth
    return [u["username"] for u in auth.list_users()
            if not u["disabled"] and not u["expired"] and u["username"] != exclude and auth.can(u["username"], APPROVE_PERM)]


def _fields(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (VM_FIELDS if item["kind"] == "vm" else []) + item.get("fields", [])


def _log(r: Dict[str, Any], text: str, user: str) -> None:
    r.setdefault("timeline", []).append({"ts": int(time.time()), "status": r["status"], "text": text[:1000], "user": user})


def _view(r: Dict[str, Any]) -> Dict[str, Any]:
    return {**r, "status_label": STATUSES[r["status"]], "open": r["status"] in OPEN, "kind_label": KINDS.get(r["kind"], r["kind"])}


def overview(user: str = "") -> Dict[str, Any]:
    import actions
    import auth
    import vmops
    data = _data()
    approver = bool(user and auth.can(user, APPROVE_PERM))
    manage = bool(user and auth.can(user, "settings.manage"))
    rows = [r for r in data["requests"] if approver or manage or r["user"] == user]
    rows = sorted(rows, key=lambda r: (r["status"] not in OPEN, -r["created"]))[:300]
    out = {"items": [{**i, "form": _fields(i), "kind_label": KINDS[i["kind"]]} for i in data["items"] if i.get("active", True) or manage],
           "requests": [_view(r) for r in rows], "kinds": KINDS, "statuses": STATUSES, "can_approve": approver, "can_manage": manage,
           "waiting": sum(1 for r in data["requests"] if r["status"] == "eingereicht")}
    if manage:
        try:
            out["templates"] = [{"id": t["id"], "name": t["name"]} for t in vmops.templates()]
            out["actions"] = [{"id": a["id"], "name": a.get("name") or a["id"]} for a in actions.list_actions().get("actions", [])]
        except Exception:
            out.setdefault("templates", [])
            out.setdefault("actions", [])
    return out


def _clean_item(f: Dict[str, Any]) -> Dict[str, Any]:
    title = str(f.get("title") or "").strip()
    if not 1 <= len(title) <= 100:
        raise CatalogError("Titel fehlt (höchstens 100 Zeichen).")
    kind = str(f.get("kind") or "manual")
    if kind not in KINDS:
        raise CatalogError("Unbekannte Art der Umsetzung.")
    template, action = str(f.get("template") or ""), str(f.get("action") or "")
    if kind == "vm" and not template:
        raise CatalogError("VM-Vorlage wählen.")
    if kind == "action" and not action:
        raise CatalogError("Schnellaktion wählen.")
    fields, seen = [], set()
    for x in (f.get("fields") or [])[:20]:
        key, label, ftype = str(x.get("key") or "").strip(), str(x.get("label") or "").strip()[:80], str(x.get("type") or "text")
        if not FIELD_KEY.match(key) or key in seen or key in ("vm_name", "ssh_key"):
            raise CatalogError(f"Ungültiger oder doppelter Feldschlüssel: {key or '–'}")
        if not label or ftype not in FIELD_TYPES:
            raise CatalogError(f"Feld {key}: Bezeichnung oder Typ fehlt.")
        options = [str(o).strip()[:80] for o in x.get("options") or [] if str(o).strip()][:30]
        if ftype == "select" and not options:
            raise CatalogError(f"Feld {key}: Auswahl braucht Optionen.")
        seen.add(key)
        fields.append({"key": key, "label": label, "type": ftype, "required": bool(x.get("required")), "options": options})
    # Automatic items always need a second person
    return {"title": title, "description": str(f.get("description") or "").strip()[:1000], "category": str(f.get("category") or "Allgemein").strip()[:40],
            "kind": kind, "template": template if kind == "vm" else "", "action": action if kind == "action" else "",
            "approval": True if kind in RUN_PERM else bool(f.get("approval", True)), "fields": fields, "active": bool(f.get("active", True))}


def save_item(iid: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean_item(fields)
    with _lock:
        data = _data()
        if iid:
            _find(data["items"], iid, "Eintrag").update(clean, edited=int(time.time()), edited_by=user)
        else:
            if len(data["items"]) >= MAX_ITEMS:
                raise CatalogError(f"Höchstens {MAX_ITEMS} Einträge.")
            data["items"].append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user})
        _save(data)
    return overview(user)


def delete_item(iid: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        data = _data()
        _find(data["items"], iid, "Eintrag")
        data["items"] = [x for x in data["items"] if x["id"] != iid]
        _save(data)
    return overview(user)


def _values(item: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for f in _fields(item):
        v = str(values.get(f["key"]) or "").strip()
        if f["required"] and not v:
            raise CatalogError(f"{f['label']} fehlt.")
        if len(v) > 4000:
            raise CatalogError(f"{f['label']}: höchstens 4000 Zeichen.")
        if v and f["type"] == "number" and not re.match(r"^-?\d+([.,]\d+)?$", v):
            raise CatalogError(f"{f['label']}: bitte eine Zahl angeben.")
        if v and f["type"] == "select" and v not in f["options"]:
            raise CatalogError(f"{f['label']}: ungültige Auswahl.")
        out[f["key"]] = v
    return out


def _summary(r: Dict[str, Any]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in r["values"].items() if v and k != "ssh_key")


def _fulfil(r: Dict[str, Any], item: Optional[Dict[str, Any]], user: str) -> None:
    """Start what the item says; the request stays open until that is done."""
    kind = r["kind"]
    if kind == "manual":
        import kanban
        kanban.create({"title": f"Anfrage: {r['item_title']}"[:120], "desc": f"Von {r['user']}\n{_summary(r)}"[:2000], "labels": ["Service-Katalog"], "column": "idea"}, user)
        r["status"] = "umsetzung"
        _log(r, "Aufgabe auf dem Board angelegt.", user)
    elif kind == "change":
        import changes
        res = changes.save(None, {"title": r["item_title"][:120], "risk": "mittel", "reason": f"Service-Anfrage von {r['user']}\n{_summary(r)}"[:2000],
                                  "description": _summary(r)[:2000]}, user)
        r["change_id"] = res["saved"]
        r["status"] = "umsetzung"
        _log(r, "Änderungsantrag angelegt.", user)
    elif kind == "vm":
        import vmops
        task = vmops.create_from_template(item["template"], r["values"]["vm_name"], r["values"].get("ssh_key", ""), r["user"])
        r["task_id"] = task["id"]
        r["status"] = "umsetzung"
        _log(r, f"VM {r['values']['vm_name']} wird erstellt.", user)
    elif kind == "action":
        import actions
        r["task_id"] = actions.run_action(item["action"], r["user"])["id"]
        r["status"] = "umsetzung"
        _log(r, "Schnellaktion gestartet.", user)


def submit(iid: str, values: Dict[str, Any], note: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        data = _data()
        item = _find(data["items"], iid, "Eintrag")
        if not item.get("active", True):
            raise CatalogError("Dieser Eintrag ist nicht mehr verfügbar.")
        r = {"id": secrets.token_hex(5), "item_id": iid, "item_title": item["title"], "kind": item["kind"], "values": _values(item, values or {}), "labels": {f["key"]: f["label"] for f in _fields(item)},
             "note": str(note or "").strip()[:1000], "user": user, "created": int(time.time()), "status": "eingereicht", "timeline": []}
        _log(r, "Eingereicht.", user)
        data["requests"].append(r)
        if not item.get("approval", True) and item["kind"] not in RUN_PERM:
            _fulfil(r, item, user)
        _save(data)
    return {**overview(user), "saved": r["id"]}


def decide(rid: str, act: str, note: str, user: str = "") -> Dict[str, Any]:
    """approve | reject | withdraw | complete | fail"""
    import auth
    note = str(note or "").strip()[:1000]
    with _lock:
        data = _data()
        r = _find(data["requests"], rid, "Anfrage")
        approver = auth.can(user, APPROVE_PERM)
        if act == "withdraw":
            if r["user"] != user or r["status"] != "eingereicht":
                raise CatalogError("Nur eigene, noch nicht entschiedene Anfragen lassen sich zurückziehen.")
            r["status"] = "zurueckgezogen"
            _log(r, note or "Zurückgezogen.", user)
        elif act in ("approve", "reject"):
            if r["status"] != "eingereicht":
                raise CatalogError("Diese Anfrage wartet nicht auf eine Freigabe.")
            if not approver:
                raise CatalogError("Keine Berechtigung zum Freigeben.")
            if act == "reject":
                if not note:
                    raise CatalogError("Bitte die Ablehnung begründen.")
                r["status"] = "abgelehnt"
                _log(r, note, user)
            else:
                self_approval = user == r["user"]
                if self_approval and _approvers(exclude=user):
                    raise CatalogError(f"Vier-Augen-Prinzip: eine andere Person muss freigeben ({', '.join(_approvers(exclude=user)[:3])}).")
                need = RUN_PERM.get(r["kind"])
                if need and not auth.can(user, need):
                    raise CatalogError("Für die Umsetzung fehlt dir die Berechtigung – bitte jemanden mit dieser Berechtigung freigeben lassen.")
                r["approved_by"], r["approved_at"] = user, int(time.time())
                _log(r, ("Selbstfreigabe (niemand sonst darf freigeben). " if self_approval else "Freigegeben. ") + note, user)
                item = next((x for x in data["items"] if x["id"] == r["item_id"]), None)
                if r["kind"] in RUN_PERM and not item:
                    raise CatalogError("Der Katalogeintrag wurde gelöscht.")
                try:
                    _fulfil(r, item, user)
                except Exception as e:
                    r["status"] = "fehlgeschlagen"
                    _log(r, f"Umsetzung fehlgeschlagen: {e}", user)
        elif act in ("complete", "fail"):
            if r["status"] != "umsetzung":
                raise CatalogError("Diese Anfrage ist nicht in Umsetzung.")
            if not approver and not auth.can(user, "settings.manage"):
                raise CatalogError("Keine Berechtigung.")
            r["status"] = "erledigt" if act == "complete" else "fehlgeschlagen"
            r["closed"] = int(time.time())
            _log(r, note or STATUSES[r["status"]], user)
        else:
            raise CatalogError("Unbekannte Aktion.")
        _save(data)
    return overview(user)


def _task_done(task: Dict[str, Any]) -> None:
    with _lock:
        data = _data()
        r = next((x for x in data["requests"] if x.get("task_id") == task["id"] and x["status"] == "umsetzung"), None)
        if not r:
            return
        ok = task.get("state") == "ok"
        r["status"] = "erledigt" if ok else "fehlgeschlagen"
        r["closed"] = int(time.time())
        _log(r, "Umsetzung abgeschlossen." if ok else f"Aufgabe endete mit: {tasks.STATES.get(task.get('state'), task.get('state'))}", "System")
        _save(data)


tasks.on_done("vm", _task_done)
tasks.on_done("action", _task_done)


def alerts() -> List[Dict[str, Any]]:
    n = sum(1 for r in _data()["requests"] if r["status"] == "eingereicht")
    return [{"level": "info", "text": f"Service-Katalog: {n} {'Anfrage wartet' if n == 1 else 'Anfragen warten'} auf Freigabe"}] if n else []
