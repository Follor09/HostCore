"""
Stack templates (Container -> Vorlagen): own Compose files saved on the main Pi, created as a
new stack on any Pi of the fleet (~/picore-apps/<name>/docker-compose.yml, then pull + up -d).
A template can be taken from an existing stack. Secrets don't belong into a template: an .env
is entered when creating the stack and only written on the target Pi.
"""

import asyncio
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "stack_templates"
MAX_TEMPLATES = 100
_lock = threading.Lock()


class TemplateError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _services(compose: str) -> List[str]:
    """Service names from the compose text (top level "services:" block, two-space indent)."""
    out, inside = [], False
    for line in compose.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside:
            if re.match(r"^\S", line):
                break
            m = re.match(r"^ {2}([A-Za-z0-9_.-]+):\s*$", line) or re.match(r"^\t([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                out.append(m.group(1))
    return out


def overview() -> Dict[str, Any]:
    items = sorted(_items(), key=lambda t: t["name"].lower())
    return {"templates": [{**t, "services": _services(t["compose"])} for t in items]}


def save(template_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    name = str(fields.get("name") or "").strip()[:60]
    if not name:
        raise TemplateError("Name fehlt.")
    compose = str(fields.get("compose") or "").replace("\r\n", "\n")
    if not compose.strip():
        raise TemplateError("Compose-Datei fehlt.")
    if len(compose) > 200_000:
        raise TemplateError("Compose-Datei zu groß (höchstens 200 KB).")
    if not re.search(r"^services:", compose, re.M):
        raise TemplateError("Keine Compose-Datei: der Abschnitt „services:“ fehlt.")
    stack = re.sub(r"[^a-z0-9_-]+", "-", str(fields.get("stack_name") or name).lower()).strip("-")[:40] or "stack"
    clean = {"name": name, "description": str(fields.get("description") or "").strip()[:300], "compose": compose,
             "stack_name": stack, "env_hint": str(fields.get("env_hint") or "").strip()[:2000]}
    with _lock:
        items = _items()
        if template_id:
            t = next((x for x in items if x["id"] == template_id), None)
            if not t:
                raise TemplateError("Vorlage nicht gefunden.")
            t.update(clean, updated=int(time.time()), updated_by=user)
        else:
            if len(items) >= MAX_TEMPLATES:
                raise TemplateError(f"Höchstens {MAX_TEMPLATES} Vorlagen.")
            items.append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user})
        settings_store.set(KEY, items)
    return overview()


def delete(template_id: str) -> Dict[str, Any]:
    with _lock:
        settings_store.set(KEY, [t for t in _items() if t["id"] != template_id])
    return overview()


async def deploy(template_id: str, targets: List[str], stack_name: str, env: str, user: str = "") -> Dict[str, Any]:
    """Create the stack on the given Pis ("" = this Pi), each in its own folder."""
    import containers
    import fleet
    t = next((x for x in _items() if x["id"] == template_id), None)
    if not t:
        raise TemplateError("Vorlage nicht gefunden.")
    name = (stack_name or t["stack_name"]).strip().lower()

    async def one(pid: str) -> Dict[str, Any]:
        try:
            if pid == "":
                job = await asyncio.to_thread(containers.create_stack, name, t["compose"], env, user)
            else:
                job = await fleet._call(pid, "POST", "/api/containers/stacks/create",
                                        {"name": name, "compose": t["compose"], "env": env}, user)
            return {"id": pid, "ok": True, "message": f"Stack {name} wird angelegt", "job": job.get("id"), "dir": job.get("dir", "")}
        except (containers.ContainerError, ValueError, OSError) as e:
            return {"id": pid, "ok": False, "message": str(e)}
        except Exception as e:   # remote not reachable
            return {"id": pid, "ok": False, "message": f"{type(e).__name__}: {e}"[:200]}

    return {"results": await asyncio.gather(*(one(p) for p in dict.fromkeys(targets))), "stack": name}
