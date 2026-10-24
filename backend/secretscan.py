"""
Secret scan (System-Werkzeuge -> Sicherheit -> Geheimnis-Scan): finds keys, tokens and passwords
that were left where they don't belong.

Where: the stack folders (~/picore-apps, every compose project, GitOps clones), the quick action
scripts, folders added in the settings, and the dashboard's own documentation (notes, wiki,
runbooks, journal, service profiles, command snippets, contracts). Not: .env files and the
dashboard's own credential stores - variables files are the right place; for them only the
file permissions are checked (readable by others = finding).

What: private keys, cloud and Git hosting tokens (AWS, GitHub, GitLab, Slack, Stripe, Google,
OpenAI, Anthropic, Telegram, Discord webhooks, NetBox), credentials inside URLs, JWTs, and
"password: …" assignments with a literal value (not ${VAR}).

Only a masked excerpt and a fingerprint are stored - never the secret itself. Findings can be
accepted (false alarm, test key); new findings become events. Runs daily and on demand.
"""

import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import settings_store

logger = logging.getLogger("pi_dashboard.secretscan")

KEY = "secret_scan"
RESULT_KEY = "secret_scan_result"
EVENTS_KEY = "secret_scan_events"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_INTERVAL = 24 * 3600
MAX_FILE = 512 * 1024
MAX_BYTES = 150 * 1024 * 1024     # read at most this much per scan (data folders of stacks can be huge)
MAX_FILES = 20000
MAX_FINDINGS = 500
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "vendor", ".cache", "dist", "build", "site-packages"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tgz", ".xz", ".bz2", ".7z", ".mp4", ".mkv",
            ".mp3", ".woff", ".woff2", ".ttf", ".otf", ".db", ".sqlite", ".sqlite3", ".so", ".bin", ".iso", ".img", ".jar", ".pyc", ".lock"}
CODE_EXT = {".py", ".js", ".ts", ".go", ".rb", ".php", ".java", ".rs", ".c", ".cpp", ".cs", ".lua", ".mjs", ".cjs", ".vue", ".tsx", ".jsx"}
ENV_NAMES = re.compile(r"(^|/)\.env(\.[\w.-]+)?$|\.env$")
KEY_FILES = re.compile(r"(^|/)(id_(rsa|ed25519|ecdsa|dsa)|[\w.-]+\.(pem|key|p12|pfx))$")

# (id, label, level, regex, value group)
RULES: List[Tuple[str, str, str, "re.Pattern", int]] = [
    ("private_key", "Privater Schlüssel", "critical", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"), 0),
    ("aws_key", "AWS-Zugangsschlüssel", "critical", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), 1),
    ("github_token", "GitHub-Token", "critical", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"), 1),
    ("gitlab_token", "GitLab-Token", "critical", re.compile(r"\b(glpat-[A-Za-z0-9_-]{20,})\b"), 1),
    ("slack_token", "Slack-Token", "critical", re.compile(r"\b(xox[abposr]-[A-Za-z0-9-]{10,})\b"), 1),
    ("stripe_key", "Stripe-Schlüssel", "critical", re.compile(r"\b((?:sk|rk)_live_[A-Za-z0-9]{20,})\b"), 1),
    ("google_key", "Google-API-Schlüssel", "warning", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"), 1),
    ("anthropic_key", "Anthropic-API-Schlüssel", "critical", re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{20,})\b"), 1),
    ("openai_key", "OpenAI-API-Schlüssel", "critical", re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{40,})\b"), 1),
    ("telegram_token", "Telegram-Bot-Token", "warning", re.compile(r"\b(\d{8,10}:AA[A-Za-z0-9_-]{33})\b"), 1),
    ("discord_webhook", "Discord-Webhook", "warning", re.compile(r"(https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{30,})"), 1),
    ("netbox_token", "NetBox-Token", "warning", re.compile(r"\b(nbt_[A-Za-z0-9]{20,})\b"), 1),
    ("url_credentials", "Zugangsdaten in einer Adresse", "warning",
     re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://([^\s:/@'\"${}]{1,64}:[^\s/@'\"${}]{3,128})@[\w.-]+"), 1),
    ("jwt", "JSON Web Token", "info", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"), 1),
    ("password_assignment", "Passwort im Klartext", "warning",
     re.compile(r"(?i)\b[\w.-]*(?:passw(?:or)?d|passwort|secret|api[_-]?key|token|pwd)\b\s*[:=]\s*[\"']?([^\s\"'#,;{}]{6,200})"), 1),
]
PLACEHOLDER = re.compile(r"(?i)^(\$\{?[\w:?\-]+\}?|<.*>|x{4,}|\*{3,}|changeme|change_me|your[_-].*|example.*|placeholder|password\d*|secret|"
                         r"none|null|true|false|redacted|dummy|test(ing)?|\.\.\.|•+|xxx.*|todo|required|file:.*|/run/secrets/.*|[\w./-]*\.(txt|pem|key|json|yaml|yml))$")


class SecretScanError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    c = c if isinstance(c, dict) else {}
    return {"folders": list(c.get("folders") or []), "ignored": list(c.get("ignored") or []), "docs": c.get("docs", True)}


def save_settings(fields: Dict[str, Any]) -> Dict[str, Any]:
    folders = []
    for f in (fields.get("folders") or [])[:20]:
        f = os.path.realpath(os.path.expanduser(str(f).strip()))
        if not f or f in ("/", "/proc", "/sys", "/dev"):
            continue
        if not os.path.isdir(f):
            raise SecretScanError(f"Ordner nicht gefunden: {f}")
        folders.append(f)
    c = config()
    c.update(folders=sorted(set(folders)), docs=bool(fields.get("docs", True)))
    settings_store.set(KEY, c)
    return overview()


def set_ignored(fingerprint: str, ignore: bool) -> Dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{20}", fingerprint or ""):
        raise SecretScanError("Ungültiger Befund.")
    c = config()
    items = set(c["ignored"])
    (items.add if ignore else items.discard)(fingerprint)
    c["ignored"] = sorted(items)[-2000:]
    settings_store.set(KEY, c)
    return overview()


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return value[:1] + "•" * (len(value) - 1)
    return value[:4] + "•" * min(12, len(value) - 6) + value[-2:]


def _fp(location: str, rule: str, value: str) -> str:
    return hashlib.sha256(f"{location}\0{rule}\0{value}".encode()).hexdigest()[:20]


def _code_value(value: str) -> bool:
    """Looks like an expression, not a literal: calls, indexes, attribute paths."""
    return bool(re.search(r"[()\[\]<>]", value) or re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", value))


def scan_text(text: str, location: str, line_offset: int = 0, code: bool = False) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            line = line[:4000]
        for rid, label, level, rx, grp in RULES:
            for m in rx.finditer(line):
                value = m.group(grp) if grp else m.group(0)
                if rid == "password_assignment":
                    if PLACEHOLDER.match(value) or "$" in value or _code_value(value):
                        continue
                    if code and not re.search(r"[:=]\s*[\"']", m.group(0)):
                        continue          # in source code only quoted literals count
                    if any(r[0] != rid and r[3].search(line) for r in RULES):
                        continue          # a more specific rule already names it
                if rid == "url_credentials" and PLACEHOLDER.match(value.split(":", 1)[1]):
                    continue
                key = (rid, value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"rule": rid, "label": label, "level": level, "location": location, "line": no + line_offset,
                            "excerpt": _mask(value) if rid != "private_key" else "-----BEGIN … PRIVATE KEY-----",
                            "fingerprint": _fp(location, rid, value)})
    return out


def _roots() -> List[Tuple[str, str]]:
    """(folder, kind) to scan."""
    roots: List[Tuple[str, str]] = []
    apps = os.path.expanduser("~/picore-apps")
    if os.path.isdir(apps):
        roots.append((apps, "Stacks"))
    try:
        import containers
        for s in containers.stacks():
            if s.get("dir") and os.path.isdir(s["dir"]) and not s["dir"].startswith(apps + "/"):
                roots.append((s["dir"], "Stacks"))
    except Exception:
        pass
    actions = os.path.expanduser("~/pi-dashboard-actions")
    if os.path.isdir(actions):
        roots.append((actions, "Schnellaktionen"))
    for f in config()["folders"]:
        roots.append((f, "Eigene Ordner"))
    # Nested roots only once
    out: List[Tuple[str, str]] = []
    for path, kind in sorted(set(roots), key=lambda r: len(r[0])):
        if not any(path == o or path.startswith(o.rstrip("/") + "/") for o, _ in out):
            out.append((path, kind))
    return out


def _walk(root: str, budget: List[int]):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".cache")]
        for name in filenames:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            yield os.path.join(dirpath, name)


def _perm_finding(path: str, st: os.stat_result, what: str) -> Optional[Dict[str, Any]]:
    if st.st_mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWOTH):
        return {"rule": "permissions", "label": f"{what} für andere lesbar", "level": "warning", "location": path, "line": 0,
                "excerpt": oct(st.st_mode & 0o777), "fingerprint": _fp(path, "permissions", oct(st.st_mode & 0o777)),
                "fix": f"chmod 600 {path}"}
    return None


def _scan_files(roots: List[Tuple[str, str]], stats: Dict[str, int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    budget = [MAX_FILES]
    read_left = MAX_BYTES
    for root, kind in roots:
        for path in _walk(root, budget):
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if ENV_NAMES.search(path):
                f = _perm_finding(path, st, "Variablen-Datei")
                if f:
                    out.append({**f, "kind": kind})
                stats["env"] += 1
                continue
            if KEY_FILES.search(path):
                f = _perm_finding(path, st, "Schlüsseldatei")
                if f:
                    out.append({**f, "kind": kind})
            if os.path.splitext(path)[1].lower() in SKIP_EXT or st.st_size > MAX_FILE or st.st_size == 0:
                continue
            if read_left <= 0:
                stats["truncated"] = 1
                break
            try:
                with open(path, "rb") as fh:
                    data = fh.read(MAX_FILE)
            except OSError:
                continue
            read_left -= len(data)
            if b"\0" in data[:8192]:
                continue
            stats["files"] += 1
            for f in scan_text(data.decode("utf-8", "replace"), path, code=os.path.splitext(path)[1].lower() in CODE_EXT):
                out.append({**f, "kind": kind})
    stats["truncated"] = int(stats.get("truncated") or budget[0] <= 0)
    return out


def _doc_texts() -> List[Tuple[str, str]]:
    """(location, text) of the dashboard's own documentation."""
    texts: List[Tuple[str, str]] = []
    get = settings_store.get
    try:
        notes = get("notes") or {}
        texts.append(("Notizen", notes.get("text", "") if isinstance(notes, dict) else str(notes)))
    except Exception:
        pass
    for key, label, fields in (("runbooks", "Runbook", ("title", "description")), ("journal", "Betriebstagebuch", ("title", "text")),
                               ("snippets", "Befehl", ("name", "command", "description")), ("contracts", "Vertrag", ("name", "notes")),
                               ("incidents", "Störung", ("title", "summary"))):
        for item in get(key, []) or []:
            if not isinstance(item, dict):
                continue
            body = "\n".join(str(item.get(f) or "") for f in fields)
            if key == "runbooks":
                body += "\n" + "\n".join(s.get("text", "") for s in item.get("steps") or [])
            texts.append((f"{label}: {item.get('title') or item.get('name') or item.get('id')}", body))
    for target, d in (get("service_docs", {}) or {}).items():
        texts.append((f"Steckbrief: {d.get('name', target)}", "\n".join(str(v) for v in d.values() if isinstance(v, str))))
    try:
        with open(os.path.join(BASE_DIR, "wiki.json")) as f:
            wiki = json.load(f)
        for p in (wiki.get("pages") if isinstance(wiki, dict) else wiki) or []:
            if isinstance(p, dict):
                texts.append((f"Wiki: {p.get('title', p.get('id'))}", str(p.get("content") or p.get("text") or "")))
    except (OSError, ValueError):
        pass
    return texts


_running = False
_lock = threading.Lock()


def run() -> Dict[str, Any]:
    global _running
    with _lock:
        if _running:
            raise SecretScanError("Der Scan läuft schon.")
        _running = True
    started = time.time()
    try:
        stats = {"files": 0, "env": 0, "docs": 0, "truncated": 0}
        roots = _roots()
        findings = _scan_files(roots, stats)
        if config()["docs"]:
            for loc, text in _doc_texts():
                stats["docs"] += 1
                findings += [{**f, "kind": "Dokumentation"} for f in scan_text(text, loc)]
        # One entry per fingerprint, the most severe first
        uniq: Dict[str, Dict[str, Any]] = {}
        for f in findings:
            uniq.setdefault(f["fingerprint"], f)
        order = {"critical": 0, "warning": 1, "info": 2}
        items = sorted(uniq.values(), key=lambda f: (order[f["level"]], f["location"], f["line"]))[:MAX_FINDINGS]
        old = settings_store.get(RESULT_KEY) or {}
        known = {f["fingerprint"] for f in old.get("findings", [])}
        ignored = set(config()["ignored"])
        new = [f for f in items if f["fingerprint"] not in known and f["fingerprint"] not in ignored and f["level"] != "info"]
        result = {"checked": int(time.time()), "duration": round(time.time() - started, 1), "roots": [{"path": p, "kind": k} for p, k in roots],
                  "stats": stats, "findings": items}
        settings_store.set(RESULT_KEY, result)
        if new and old.get("checked"):
            events = (settings_store.get(EVENTS_KEY, []) or [])[-99:]
            worst = "critical" if any(f["level"] == "critical" for f in new) else "warning"
            events.append({"ts": int(time.time()), "level": worst, "title": f"{len(new)} neue {'Fundstelle' if len(new) == 1 else 'Fundstellen'} im Geheimnis-Scan",
                           "detail": ", ".join(sorted({f['label'] for f in new}))[:200]})
            settings_store.set(EVENTS_KEY, events)
    finally:
        _running = False
    return overview()


def check_if_due() -> None:
    last = (settings_store.get(RESULT_KEY) or {}).get("checked", 0)
    if time.time() - last >= CHECK_INTERVAL and not _running:
        threading.Thread(target=_safe_run, daemon=True).start()


def _safe_run() -> None:
    try:
        run()
    except Exception as e:
        logger.warning(f"Secret scan failed: {e}")


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def overview() -> Dict[str, Any]:
    r = settings_store.get(RESULT_KEY) or {}
    ignored = set(config()["ignored"])
    items = [{**f, "ignored": f["fingerprint"] in ignored} for f in r.get("findings", [])]
    active = [f for f in items if not f["ignored"]]
    return {"checked": r.get("checked", 0), "duration": r.get("duration"), "running": _running, "roots": r.get("roots", []),
            "stats": r.get("stats", {}), "findings": items, "settings": {k: v for k, v in config().items() if k != "ignored"},
            "counts": {lvl: sum(1 for f in active if f["level"] == lvl) for lvl in ("critical", "warning", "info")},
            "ignored": len(items) - len(active)}


def alerts() -> List[Dict[str, Any]]:
    c = overview()["counts"]
    if c["critical"]:
        return [{"level": "critical", "text": f"Geheimnis-Scan: {c['critical']} {'kritische Fundstelle' if c['critical'] == 1 else 'kritische Fundstellen'}"}]
    if c["warning"]:
        return [{"level": "warning", "text": f"Geheimnis-Scan: {c['warning']} {'Fundstelle' if c['warning'] == 1 else 'Fundstellen'}"}]
    return []
