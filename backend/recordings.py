"""
Terminal recordings (Einstellungen -> Terminal-Mitschnitte): every persistent web terminal
session is recorded as an asciicast v2 file and can be played back later - who did what,
when, on the server or in which container. Only admins (users.manage) see and play them.

tmux pipes the session's output into castrec.py (pipe-pane), so it is recorded while no
browser is connected as well, and across dashboard restarts. Passwords typed at a sudo prompt
are not echoed by the terminal and so are not in the recording. Files live in recordings/
(<session>.cast + <session>.json with who / where / when), removed after the set number of
days. Settings in settings.json "terminal_recording".
"""

import json
import os
import re
import shlex
import sys
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "terminal_recording"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(BASE_DIR, "recordings")
RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "castrec.py")
ID_RE = re.compile(r"^picore-[0-9a-f]{12}$")
MAX_SEND = 60 * 1024 * 1024


class RecordingError(Exception):
    pass


def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return {"enabled": bool(c.get("enabled", True)), "days": int(c.get("days", 30))}


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    try:
        days = int(fields.get("days", 30))
    except (TypeError, ValueError):
        raise RecordingError("Aufbewahrung: keine Zahl.")
    if not 1 <= days <= 365:
        raise RecordingError("Aufbewahrung: 1 bis 365 Tage.")
    settings_store.set(KEY, {"enabled": bool(fields.get("enabled")), "days": days})
    return overview()


def _paths(sid: str) -> tuple:
    if not ID_RE.match(sid or ""):
        raise RecordingError("Mitschnitt nicht gefunden.")
    return os.path.join(REC_DIR, f"{sid}.cast"), os.path.join(REC_DIR, f"{sid}.json")


def start(sid: str, user: str, container: str, client: str, cols: int, rows: int, tmux) -> bool:
    """Called right after a session was created; tmux(args) runs a tmux command for it."""
    if not config()["enabled"]:
        return False
    os.makedirs(REC_DIR, mode=0o700, exist_ok=True)
    cast, meta = _paths(sid)
    with open(meta, "w") as f:
        json.dump({"id": sid, "user": user, "container": container, "client": client, "started": int(time.time())}, f)
    os.chmod(meta, 0o600)
    cmd = " ".join(shlex.quote(a) for a in (sys.executable, RECORDER, cast, str(cols), str(rows)))
    res = tmux(["pipe-pane", "-o", "-t", sid, f"exec {cmd}"])
    return res.returncode == 0


def _meta(sid: str) -> Optional[Dict[str, Any]]:
    cast, meta = _paths(sid)
    try:
        with open(meta) as f:
            m = json.load(f)
        st = os.stat(cast)
    except (OSError, ValueError):
        return None
    m.update(size=st.st_size, last=int(st.st_mtime), duration=max(0, int(st.st_mtime) - m.get("started", int(st.st_mtime))))
    return m


def overview() -> Dict[str, Any]:
    import terminal
    running = {s["id"] for s in terminal.sessions()}
    items = []
    try:
        names = os.listdir(REC_DIR)
    except FileNotFoundError:
        names = []
    for name in names:
        if name.endswith(".json") and ID_RE.match(name[:-5]):
            m = _meta(name[:-5])
            if m:
                m["running"] = m["id"] in running
                items.append(m)
    items.sort(key=lambda m: -m["started"])
    return {"config": config(), "recordings": items, "available": terminal.persistent(),
            "total": sum(m["size"] for m in items)}


def read(sid: str) -> str:
    cast, _ = _paths(sid)
    try:
        if os.path.getsize(cast) > MAX_SEND:
            raise RecordingError("Mitschnitt zu groß zum Abspielen im Browser.")
        with open(cast, errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        raise RecordingError("Mitschnitt nicht gefunden.")


ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[=>78DEHMNOPZc\\])|[\x00-\x08\x0b-\x1f\x7f]")
MAX_HITS = 5


def _text(cast: str):
    """The recording's visible output without escape codes, with (offset, seconds) marks
    so a hit can be mapped back to the time it appeared."""
    parts: List[str] = []
    marks: List[tuple] = []
    size = 0
    with open(cast, errors="replace") as f:
        f.readline()  # header
        for line in f:
            try:
                at, kind, data = json.loads(line)
            except (ValueError, TypeError):
                continue
            if kind != "o":
                continue
            chunk = ANSI_RE.sub("", data.replace("\r\n", "\n"))
            if chunk:
                marks.append((size, float(at)))
                parts.append(chunk)
                size += len(chunk)
    return "".join(parts), marks


def search(query: str) -> Dict[str, Any]:
    """Full-text search through what the recorded sessions showed (commands typed and their
    output): for each recording with a hit the time of the first hit and a few snippets."""
    import bisect
    q = (query or "").strip()
    if not 2 <= len(q) <= 200:
        raise RecordingError("Suchbegriff: 2 bis 200 Zeichen.")
    needle = q.lower()
    hits = []
    for m in overview()["recordings"]:
        cast, _ = _paths(m["id"])
        try:
            if os.path.getsize(cast) > MAX_SEND:
                continue
            text, marks = _text(cast)
        except OSError:
            continue
        low = text.lower()
        starts = [o for o, _ in marks]
        found, pos = [], low.find(needle)
        while pos >= 0 and len(found) < MAX_HITS:
            i = max(0, bisect.bisect_right(starts, pos) - 1)
            a, b = max(0, pos - 60), min(len(text), pos + len(q) + 60)
            snip = " ".join(text[a:b].split())
            found.append({"at": marks[i][1] if marks else 0, "text": ("…" if a else "") + snip + ("…" if b < len(text) else "")})
            pos = low.find(needle, pos + len(needle))
        if found:
            hits.append({"id": m["id"], "count": low.count(needle), "hits": found})
    return {"q": q, "results": hits}


def delete(sid: str) -> Dict[str, Any]:
    import terminal
    if terminal.session(sid):
        raise RecordingError("Die Sitzung läuft noch – erst beenden, dann löschen.")
    for p in _paths(sid):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return overview()


def cleanup() -> None:
    import terminal
    cutoff = time.time() - config()["days"] * 86400
    running = {s["id"] for s in terminal.sessions()}
    for m in overview()["recordings"]:
        if m["last"] < cutoff and m["id"] not in running:
            for p in _paths(m["id"]):
                try:
                    os.remove(p)
                except OSError:
                    pass
