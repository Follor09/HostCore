"""
Full-text search across the dashboard's own content (command palette, "Inhalte"):
documentation (inventory, journal, runbooks, service docs), contracts, command snippets,
notes, tasks, reminders and the apps on the boards. Everything lives in settings.json, so a
plain scan is fast enough; results carry a short excerpt around the first hit.
"""

import re
from typing import Any, Dict, List

import settings_store

MAX_RESULTS = 30


def _excerpt(text: str, q: str, width: int = 70) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    i = text.lower().find(q)
    if i < 0:
        return text[:width * 2]
    start = max(0, i - width)
    return ("…" if start else "") + text[start:i + len(q) + width] + ("…" if i + len(q) + width < len(text) else "")


def search(query: str) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    if len(q) < 2:
        return {"results": []}
    words = q.split()
    results: List[Dict[str, Any]] = []

    def check(kind: str, title: str, fields: List[str], href: str) -> None:
        blob = " ".join(f for f in [title, *fields] if f).lower()
        if all(w in blob for w in words):
            text = " ".join(f for f in fields if f)
            results.append({"kind": kind, "title": title, "excerpt": _excerpt(text, words[0]) if text else "",
                            "href": href, "rank": 0 if q in (title or "").lower() else 1})

    for i in settings_store.get("inventory", []) or []:
        check("Inventar", i.get("name", ""), [i.get("manufacturer", ""), i.get("model", ""), i.get("serial", ""),
                                               i.get("location", ""), i.get("notes", "")], "/doku#inventar")
    for e in settings_store.get("journal", []) or []:
        check("Betriebstagebuch", e.get("title", ""), [e.get("text", ""), e.get("ref", "")], "/doku#tagebuch")
    for r in settings_store.get("runbooks", []) or []:
        check("Runbook", r.get("title", ""), [r.get("description", ""), *[s.get("text", "") for s in r.get("steps", [])]], "/doku#runbooks")
    for target, d in (settings_store.get("service_docs", {}) or {}).items():
        check("Steckbrief", d.get("name") or target.split(":", 1)[-1], [str(v) for k, v in d.items() if isinstance(v, str) and k != "name"],
              "/doku#steckbriefe")
    for c in settings_store.get("contracts", []) or []:
        check("Vertrag", c.get("name", ""), [c.get("provider", ""), c.get("category", ""), c.get("notes", "")], "/doku#vertraege")
    for s in settings_store.get("snippets", []) or []:
        check("Befehl", s.get("title", ""), [s.get("command", ""), s.get("description", ""), " ".join(s.get("tags", []))], "/doku#befehle")
    notes = (settings_store.get("notes", {}) or {})
    for line in (notes.get("text", "") if isinstance(notes, dict) else "").splitlines():
        if line.strip():
            check("Notiz", re.sub(r"^\s*([-*] (\[[ xX]\] )?|#+ )", "", line).strip()[:100], [], "/#notes-card")
    kanban = settings_store.get("kanban", {}) or {}
    for c in (kanban.get("cards", []) if isinstance(kanban, dict) else []):
        check("Aufgabe", c.get("title", ""), [c.get("desc", ""), " ".join(c.get("labels", []))], "/#do=kanban")
    rem = settings_store.get("reminders", {}) or {}
    for r in (rem.get("items", []) if isinstance(rem, dict) else []):
        check("Erinnerung", r.get("title", ""), [], "/#do=reminders")
    try:
        import wiki
        for p in wiki.all_pages():
            check("Wissen", p.get("title", ""), [p.get("folder", ""), p.get("content", "")], f"/wissen#{p['id']}")
    except Exception:
        pass
    boards = settings_store.get("boards", {}) or {}
    for b in (boards.get("boards", []) if isinstance(boards, dict) else []):
        for s in b.get("sections", []):
            for it in s.get("items", []):
                if it.get("kind") == "app":
                    check("App", it.get("name", ""), [it.get("description", ""), it.get("url", "")], it.get("url", ""))
                elif it.get("kind") == "text":
                    check("Textblock", (it.get("text", "").strip().splitlines() or [""])[0].lstrip("# ")[:80], [it.get("text", "")],
                          f"/#board-{b['id']}")
                elif it.get("kind") == "links":
                    for link in it.get("links", []):
                        check("Link", link.get("name", ""), [link.get("url", "")], link.get("url", ""))

    results.sort(key=lambda r: r["rank"])
    return {"results": [{k: v for k, v in r.items() if k != "rank"} for r in results[:MAX_RESULTS]]}
