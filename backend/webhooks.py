"""
Webhook inbox: own addresses (/hook/<token>) that other services post messages to - Uptime Kuma,
Watchtower / Shoutrrr, GitHub, scripts (curl). Each message becomes an event in the dashboard's
event feed (and the weekly report), with a level guessed from the payload.

Hooks in settings.json "webhooks", the last messages in "webhook_events" (ring of 500 by default, see retention.py).
"""

import json
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

HOOKS_KEY = "webhooks"
EVENTS_KEY = "webhook_events"
MAX_HOOKS = 30

MAX_BODY = 64 * 1024
RATE_PER_MINUTE = 60
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
LEVELS = ("good", "info", "warning", "critical")
_lock = threading.Lock()
_rate: Dict[str, List[float]] = {}


class WebhookError(Exception):
    pass


def _max_events() -> int:
    import retention
    return retention.get("webhook_events")


def trim() -> None:
    with _lock:
        events = settings_store.get(EVENTS_KEY, []) or []
        if len(events) > _max_events():
            settings_store.set(EVENTS_KEY, events[-_max_events():])


def hooks() -> List[Dict[str, Any]]:
    return list(settings_store.get(HOOKS_KEY, []) or [])


def overview() -> Dict[str, Any]:
    events = list(reversed(settings_store.get(EVENTS_KEY, []) or []))
    return {"hooks": [{**h, "recent": [e for e in events if e["hook"] == h["id"]][:5]} for h in hooks()]}


def create(name: str, level: str, user: str) -> Dict[str, Any]:
    name = re.sub(r"[\x00-\x1f]", "", str(name or "")).strip()[:60]
    if not name:
        raise WebhookError("Name angeben, z.B. „Uptime Kuma“.")
    if level not in ("auto",) + LEVELS:
        raise WebhookError("Unbekannte Stufe.")
    with _lock:
        items = hooks()
        if len(items) >= MAX_HOOKS:
            raise WebhookError(f"Höchstens {MAX_HOOKS} Webhooks.")
        items.append({"id": secrets.token_hex(4), "name": name, "token": secrets.token_urlsafe(24), "level": level,
                      "created": int(time.time()), "user": user or "", "count": 0, "last": 0})
        settings_store.set(HOOKS_KEY, items)
    return overview()


def delete(hook_id: str) -> Dict[str, Any]:
    with _lock:
        settings_store.set(HOOKS_KEY, [h for h in hooks() if h["id"] != hook_id])
        settings_store.set(EVENTS_KEY, [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["hook"] != hook_id])
    return overview()


def _text(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _parse(body: bytes, content_type: str, headers: Dict[str, str]) -> Dict[str, str]:
    """title, detail and level from common payload shapes; plain text as it is."""
    data: Any = None
    if body:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            data = None
    gh_event = headers.get("x-github-event", "")
    if isinstance(data, dict):
        # Uptime Kuma
        if isinstance(data.get("heartbeat"), dict) or isinstance(data.get("monitor"), dict):
            hb, mon = data.get("heartbeat") or {}, data.get("monitor") or {}
            status = hb.get("status")
            level = "critical" if status == 0 else "good" if status == 1 else "warning" if status in (2, 3) else "info"
            return {"title": _text(data.get("msg") or f"{mon.get('name', 'Monitor')}: {'down' if status == 0 else 'up'}", 200),
                    "detail": _text(hb.get("msg") or mon.get("url") or "", 300), "level": level}
        # GitHub
        if gh_event:
            repo = (data.get("repository") or {}).get("full_name", "")
            action = data.get("action", "")
            subject = (data.get("pull_request") or data.get("issue") or data.get("release") or {}).get("title") \
                or (data.get("head_commit") or {}).get("message", "")
            return {"title": _text(f"GitHub {gh_event}{f' ({action})' if action else ''}{f' in {repo}' if repo else ''}", 200),
                    "detail": _text(subject, 300), "level": "info"}
        title = data.get("title") or data.get("subject") or data.get("name") or ""
        message = data.get("message") or data.get("msg") or data.get("text") or data.get("content") or data.get("body") or ""
        if not title and message:
            title, message = message, ""
        raw_level = str(data.get("level") or data.get("severity") or data.get("status") or data.get("priority") or "").lower()
        level = ("critical" if raw_level in ("critical", "error", "err", "fatal", "down", "failed", "failure", "alert", "high", "5", "4")
                 else "warning" if raw_level in ("warning", "warn", "degraded", "medium", "3")
                 else "good" if raw_level in ("ok", "good", "success", "up", "resolved", "healthy")
                 else "info")
        if not title:
            title = _text(json.dumps(data, ensure_ascii=False), 200)
        return {"title": _text(title, 200), "detail": _text(message, 300), "level": level}
    text = body.decode("utf-8", "replace").strip()
    lines = [l for l in text.splitlines() if l.strip()]
    low = text.lower()
    level = ("critical" if re.search(r"\b(error|failed|failure|down|critical)\b", low)
             else "warning" if re.search(r"\b(warn|warning)\b", low) else "info")
    return {"title": _text(lines[0] if lines else "(leere Meldung)", 200), "detail": _text(" ".join(lines[1:]), 300), "level": level}


def receive(token: str, body: bytes, content_type: str, headers: Dict[str, str], ip: str) -> Dict[str, Any]:
    if not TOKEN_RE.match(token or ""):
        raise WebhookError("Unbekannter Webhook.")
    hook = next((h for h in hooks() if secrets.compare_digest(h["token"], token)), None)
    if not hook:
        raise WebhookError("Unbekannter Webhook.")
    now = time.time()
    recent = [t for t in _rate.get(hook["id"], []) if now - t < 60]
    if len(recent) >= RATE_PER_MINUTE:
        raise WebhookError("Zu viele Meldungen – höchstens 60 pro Minute.")
    recent.append(now)
    _rate[hook["id"]] = recent
    msg = _parse(body[:MAX_BODY], content_type, headers)
    if hook.get("level", "auto") != "auto":
        msg["level"] = hook["level"]
    event = {"ts": int(now), "hook": hook["id"], "source": hook["name"], "ip": ip, **msg}
    with _lock:
        events = (settings_store.get(EVENTS_KEY, []) or [])[-(_max_events() - 1):]
        events.append(event)
        settings_store.set(EVENTS_KEY, events)
        items = hooks()
        for h in items:
            if h["id"] == hook["id"]:
                h["count"] = h.get("count", 0) + 1
                h["last"] = int(now)
        settings_store.set(HOOKS_KEY, items)
    return {"ok": True, "level": event["level"]}


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]
