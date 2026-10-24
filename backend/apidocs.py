"""
API documentation (page /api-doku): every endpoint of the dashboard with method, path,
description, parameters, required permission and whether it is forwarded to a selected remote
Pi - built from FastAPI's own OpenAPI description plus access.py and remotes.py, so it can't
get out of date.
"""

import re
from typing import Any, Dict, List

import access
import remotes

HIDDEN = re.compile(r"^/(api/terminal/ws|api/ws|ws|hook/|api/webhook/github|api/x/|mcp)")
GROUPS = [
    (r"^/api/(stats|system|status-summary|history|replay|processes|capabilities|about|diagnostics|nvme|power|sensors)", "System & Messwerte"),
    (r"^/api/(services|workloads)", "Dienste"),
    (r"^/api/containers|^/api/appstore|^/api/stack-templates|^/api/gitops", "Container"),
    (r"^/api/(updates|packages|fleet|remotes|agent)", "Updates & Flotte"),
    (r"^/api/tools/(lan|dns|traceroute|port-test|network|connections)|^/api/(wol|netbox|latency|dns-watch)", "Netzwerk"),
    (r"^/api/tools/(firewall|fail2ban|ssh|geoip|geoblock|activity)|^/api/secret-scan", "Sicherheit"),
    (r"^/api/tools|^/api/(events|report|disk-forecast|monitor|anomalies|capacity|backups|logs|alerts|tasks)", "Werkzeuge & Auswertung"),
    (r"^/api/files|^/api/exchange", "Dateien"),
    (r"^/api/(docs|wiki|notes|kanban|reminders|costs|search|maintenance|incidents|changes|problems)", "Doku & Planung"),
    (r"^/api/(boards|links|calendar|weather|health)", "Dashboard"),
    (r"^/api/(settings|users|groups|auth|me|mcp|action|access-review)", "Einstellungen & Konto"),
    (r"^/api/(compliance|patch-sla)", "Compliance"),
]
PERM_LABELS = {p[0]: p[1] for p in __import__("auth").PERMISSIONS}


def _group(path: str) -> str:
    return next((label for rx, label in GROUPS if re.match(rx, path)), "Weitere")


def _example_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def build(openapi: Dict[str, Any]) -> Dict[str, Any]:
    components = (openapi.get("components") or {}).get("schemas") or {}
    endpoints: List[Dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not path.startswith("/api/") or HIDDEN.match(path):
            continue
        for method, op in methods.items():
            method = method.upper()
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue
            perm = access.required_permission(method, _example_path(path))
            params = [{"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required")),
                       "type": (p.get("schema") or {}).get("type") or "string",
                       "default": (p.get("schema") or {}).get("default")} for p in op.get("parameters") or []]
            body = None
            content = ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
            ref = (content.get("schema") or {}).get("$ref", "")
            if ref:
                schema = components.get(ref.rsplit("/", 1)[-1]) or {}
                body = [{"name": k, "type": v.get("type") or "object", "required": k in (schema.get("required") or []),
                         "default": v.get("default")} for k, v in (schema.get("properties") or {}).items()]
            elif content:
                body = []   # free JSON object
            doc = (op.get("description") or "").strip()
            summary = op.get("summary") or ""
            endpoints.append({
                "method": method, "path": path, "group": _group(path),
                "summary": doc.split("\n\n")[0].replace("\n", " ") if doc else "",
                "name": summary, "params": params, "body": body,
                "permission": perm, "permission_label": PERM_LABELS.get(perm, "") if perm not in (None, "login") else
                ("Angemeldet" if perm == "login" else "Ohne eigenes Recht (auch „Ansehen ohne Login“)"),
                "local_only": bool(remotes.LOCAL_ONLY.match(path)),
            })
    order = [g for _, g in GROUPS] + ["Weitere"]
    endpoints.sort(key=lambda e: (order.index(e["group"]), e["path"], e["method"]))
    return {"endpoints": endpoints, "groups": [g for g in order if any(e["group"] == g for e in endpoints)],
            "count": len(endpoints)}
