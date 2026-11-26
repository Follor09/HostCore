"""
Off-site backup (System -> Speicher & Backups -> Externe Sicherung): this server's data to
Google Drive, Dropbox, Nextcloud/WebDAV, S3-compatible storage or a USB drive.

Each job picks what to back up - Docker volumes and stack folders, Kubernetes (k3s) volumes,
the dashboard's own data with its database dumps, system settings, logs, home folders, own
folders - where to, when, and how many states to keep. restic does the work: encrypted on this
server before anything leaves it, deduplicated, with old states pruned. Google Drive and
Dropbox go through rclone.

The backup itself runs as root through pi-helper.sh offsite (backend/offsitetool.py, which
checks every input and builds the folder list itself) as a task, so it keeps running when the
dashboard restarts. Restores go to a root-only folder; nothing is overwritten in place.

Signing in to Google or Dropbox: rclone authorize runs here and waits on 127.0.0.1:53682. The
dashboard fetches its start link and shows the provider's sign-in page; afterwards the browser
is sent to 127.0.0.1:53682, which on another computer doesn't load - the address is pasted
back here and handed to rclone, which then prints the token.
"""

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import settings_store
import tasks

logger = logging.getLogger("pi_dashboard.offsite")

KEY = "offsite"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_DIR = os.path.join(BASE_DIR, "offsite")
MAX_JOBS = 10
PROVIDERS = {"drive": "Google Drive", "dropbox": "Dropbox", "webdav": "Nextcloud / WebDAV", "s3": "S3-kompatibel", "local": "USB-Laufwerk / Ordner"}
SOURCES = {
    "docker": ("Docker-Volumes & Stacks", "/var/lib/docker/volumes und die Ordner der Compose-Stacks"),
    "k3s": ("Kubernetes (k3s)", "Volumes der Pods (local-path), Cluster-Datenbank, Token und Manifeste"),
    "dashboard": ("Dashboard-Daten", "Einstellungen, Benutzer, Verlauf, Doku, Wiki und die Datenbank-Dumps"),
    "system": ("Systemeinstellungen", "/etc, Cron-Jobs, /usr/local – ohne Passwort-Hashes und SSH-Host-Schlüssel"),
    "logs": ("Logs", "/var/log mit dem Journal"),
    "home": ("Benutzerordner", "/home"),
}
SECRETS = {"drive": ("token", "client_secret"), "dropbox": ("token",), "webdav": ("password",), "s3": ("secret",), "local": ()}
FIELDS = {
    "drive": ("token", "folder", "root_folder_id", "client_id", "client_secret"),
    "dropbox": ("token", "folder"),
    "webdav": ("url", "vendor", "user", "password", "folder"),
    "s3": ("endpoint", "region", "bucket", "folder", "key_id", "secret"),
    "local": ("path",),
}
DATA_ROOTS = ("/home", "/srv", "/opt", "/mnt", "/media", "/data", "/var/www", "/var/lib/docker/volumes")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+ -]{1,250}$")
SNAP_RE = re.compile(r"^([0-9a-f]{8,64}|latest)$")
AUTH_PORT = 53682
PROGRESS_DIR = "/var/lib/pi-dashboard/offsite-progress"   # written by offsitetool.py (root) while a backup runs
PHASES = {"connect": "Verbinde mit dem Ziel", "backup": "Sichern", "prune": "Alte Stände aufräumen", "done": "Abschließen", "error": "Fehler"}
_lock = threading.Lock()
_auth: Dict[str, Any] = {}


class OffsiteError(Exception):
    pass


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def _jobs() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _job(jid: str) -> Dict[str, Any]:
    j = next((x for x in _jobs() if x["id"] == jid), None)
    if not j:
        raise OffsiteError("Sicherung nicht gefunden.")
    return j


def _update(jid: str, **fields: Any) -> None:
    with _lock:
        items = _jobs()
        j = next((x for x in items if x["id"] == jid), None)
        if j:
            j.update(fields)
            settings_store.set(KEY, items)


def tools() -> Dict[str, bool]:
    return {"restic": bool(shutil.which("restic")), "rclone": bool(shutil.which("rclone"))}


def _where(t: Dict[str, Any]) -> str:
    k = t.get("kind")
    if k == "local":
        return t.get("path", "")
    if k == "s3":
        return f"{t.get('endpoint', '')}/{t.get('bucket', '')}" + (f"/{t['folder']}" if t.get("folder") else "")
    if k == "webdav":
        return f"{t.get('url', '')} · {t.get('folder') or 'HostCore'}"
    return t.get("folder") or "HostCore"


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def progress(j: Dict[str, Any], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """How far a running backup is and how long it still takes - from restic's own estimate while it
    knows one, else from the last run's duration."""
    if not j.get("running"):
        return None
    now = now or time.time()
    p: Dict[str, Any] = {}
    try:
        with open(os.path.join(PROGRESS_DIR, f"{j['id']}.json")) as f:
            p = json.load(f)
    except (OSError, ValueError):
        pass
    started = _num(p.get("started"))
    if not started or started < (j.get("last_run") or 0) - 120:      # file of an earlier run: not started yet
        p, started = {}, float(j.get("last_run") or now)
    phase = p.get("phase") if p.get("phase") in PHASES else "connect"
    elapsed = max(0, int(now - started))
    last = j.get("summary") or {}
    previous = _num(last.get("total_seconds")) or _num(last.get("seconds"))
    remaining, estimate = None, False
    if phase == "backup":
        r = _num(p.get("remaining"))
        if r and r > 0:
            remaining = max(0, int(r - (now - (_num(p.get("updated")) or now))))
    if remaining is None and previous and phase in ("connect", "backup", "prune"):
        remaining, estimate = max(0, int(previous - elapsed)), True
    percent = _num(p.get("percent"))
    if phase in ("prune", "done"):
        percent = 100.0
    return {"phase": phase, "label": PHASES[phase], "percent": percent, "elapsed": elapsed, "remaining": remaining, "estimate": estimate,
            "previous": int(previous) if previous else None,
            **{k: p.get(k) for k in ("files_done", "total_files", "bytes_done", "total_bytes", "errors", "uploaded", "upload_speed")}}


def _public(j: Dict[str, Any]) -> Dict[str, Any]:
    t = j.get("target") or {}
    hidden = SECRETS.get(t.get("kind"), ())
    target = {k: v for k, v in t.items() if k not in hidden}
    target.update({f"has_{k}": bool(t.get(k)) for k in hidden})
    out = {k: v for k, v in j.items() if k not in ("password", "target")}
    out.update(target=target, where=_where(t), provider=PROVIDERS.get(t.get("kind"), t.get("kind")), state=_state(j), progress=progress(j))
    return out


def _gap_days(days: List[int]) -> int:
    """Longest stretch between two planned weekdays (1 for daily)."""
    d = sorted(set(days))
    if not d:
        return 7
    gaps = [(d[(i + 1) % len(d)] - d[i]) % 7 or 7 for i in range(len(d))]
    return max(gaps)


def _state(j: Dict[str, Any], now: Optional[float] = None) -> str:
    now = now or time.time()
    if j.get("running"):
        return "running"
    if j.get("last_state") == "error":
        return "error"
    sched = j.get("schedule") or {}
    if sched.get("enabled"):
        since = j.get("last_ok") or j.get("created") or now
        if now - since > _gap_days(sched.get("days") or []) * 86400 + 6 * 3600:
            return "overdue"
    if not j.get("last_ok"):
        return "never"
    return "ok"


def overview() -> Dict[str, Any]:
    _refresh_running()
    jobs = [_public(j) for j in _jobs()]
    return {"jobs": jobs, "tools": tools(), "providers": PROVIDERS, "sources": {k: {"label": v[0], "desc": v[1]} for k, v in SOURCES.items()},
            "fields": FIELDS, "problems": sum(1 for j in jobs if j["state"] in ("error", "overdue")), "restore_dir": "/var/lib/pi-dashboard/restore"}


def _clean_target(kind: str, f: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    if kind not in PROVIDERS:
        raise OffsiteError("Bitte ein Ziel wählen.")
    out: Dict[str, Any] = {"kind": kind}
    for k in FIELDS[kind]:
        v = f.get(k, "")
        v = v.strip() if isinstance(v, str) else ""
        if k in SECRETS[kind] and not v and old.get("kind") == kind:
            v = old.get(k, "")                       # left empty: keep the saved secret
        if "\n" in v and k != "token":
            raise OffsiteError("Zeilenumbrüche sind nur in der Anmeldung erlaubt.")
        out[k] = v[:4000]
    if kind in ("drive", "dropbox"):
        try:
            tok = json.loads(out["token"]) if out["token"] else None
        except ValueError:
            tok = None
        if not isinstance(tok, dict) or not (tok.get("refresh_token") or tok.get("access_token")):
            raise OffsiteError(f"Bitte zuerst mit {PROVIDERS[kind]} verbinden.")
        out["token"] = json.dumps(tok, separators=(",", ":"))
        if kind == "drive" and out["root_folder_id"] and not re.match(r"^[A-Za-z0-9_-]{5,100}$", out["root_folder_id"]):
            raise OffsiteError("Die Ordner-ID ist der letzte Teil der Drive-Adresse des Ordners (Buchstaben, Ziffern, - und _).")
        if kind == "drive":
            _check_client(out["client_id"], out["client_secret"])
            if not out["client_id"]:
                out["client_secret"] = ""
            # A sign-in belongs to the client it was made with: a new client needs a new sign-in
            if old.get("kind") == "drive" and out["client_id"] != old.get("client_id", "") and out["token"] == old.get("token"):
                raise OffsiteError("Die Google-Client-ID wurde geändert – bitte danach neu mit Google Drive verbinden.")
    if kind in ("drive", "dropbox", "webdav"):
        out["folder"] = out["folder"].strip("/") or f"HostCore/{_hostname()}"
        if not re.match(r"^[A-Za-z0-9._/ +-]{1,200}$", out["folder"]):
            raise OffsiteError("Ordner im Ziel: Buchstaben, Ziffern, Leerzeichen und . _ - / +")
    if kind == "webdav":
        if not re.match(r"^https://\S+$", out["url"]):
            raise OffsiteError("WebDAV-Adresse mit https:// angeben, bei Nextcloud z. B. https://cloud.example.de/remote.php/dav/files/<benutzer>")
        if out["vendor"] not in ("nextcloud", "owncloud", "sharepoint", "other"):
            out["vendor"] = "other"
        if not out["user"] or not out["password"]:
            raise OffsiteError("Benutzer und Passwort (bei Nextcloud ein App-Passwort) angeben.")
    if kind == "s3":
        if not re.match(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?$", out["endpoint"].rstrip("/")):
            raise OffsiteError("S3-Endpunkt: https://… ohne Pfad, z. B. https://s3.eu-central-003.backblazeb2.com")
        out["endpoint"] = out["endpoint"].rstrip("/")
        if not re.match(r"^[a-z0-9][a-z0-9.-]{1,62}$", out["bucket"]):
            raise OffsiteError("Ungültiger Bucket-Name.")
        out["folder"] = out["folder"].strip("/") or _hostname()
        if not out["key_id"] or not out["secret"]:
            raise OffsiteError("Zugangsschlüssel (Key-ID und Secret) angeben.")
    if kind == "local":
        p = out["path"].rstrip("/")
        if not PATH_RE.match(p) or ".." in p.split("/") or not any(p.startswith(r + "/") for r in ("/mnt", "/media", "/srv", "/data")):
            raise OffsiteError("Zielordner unter /mnt, /media, /srv oder /data angeben, z. B. /mnt/usb/hostcore.")
        out["path"] = p
    return out


def _check_client(cid: str, secret: str) -> None:
    if not cid and not secret:
        return
    if not re.match(r"^[0-9]+-[A-Za-z0-9_]+\.apps\.googleusercontent\.com$", cid or ""):
        raise OffsiteError("Die Client-ID sieht aus wie 123456789-abc….apps.googleusercontent.com.")
    if not re.match(r"^[A-Za-z0-9_-]{10,100}$", secret or ""):
        raise OffsiteError("Zur Client-ID gehört das Client-Secret (beginnt meist mit GOCSPX-).")


def _hostname() -> str:
    import socket
    h = socket.gethostname()
    return h if re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$", h) else "server"


def _clean_paths(lines: Any, what: str) -> List[str]:
    if isinstance(lines, str):
        lines = lines.splitlines()
    out = []
    for p in lines or []:
        p = str(p).strip().rstrip("/")
        if not p:
            continue
        if not PATH_RE.match(p) or ".." in p.split("/") or not any(p == r or p.startswith(r + "/") for r in DATA_ROOTS):
            raise OffsiteError(f"{what} {p}: erlaubt sind Ordner unter {', '.join(DATA_ROOTS)}.")
        out.append(p)
    if len(out) > 50:
        raise OffsiteError("Höchstens 50 eigene Ordner.")
    return out


def save(jid: Optional[str], f: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    name = str(f.get("name") or "").strip()[:60]
    if not name:
        raise OffsiteError("Bitte einen Namen angeben.")
    old = _job(jid) if jid else {}
    target = _clean_target(str((f.get("target") or {}).get("kind") or ""), f.get("target") or {}, old.get("target") or {})
    sources = [s for s in SOURCES if s in (f.get("sources") or [])]
    custom = _clean_paths(f.get("custom"), "Ordner")
    if not sources and not custom:
        raise OffsiteError("Bitte auswählen, was gesichert werden soll.")
    exclude = [str(x).strip() for x in (f.get("exclude").splitlines() if isinstance(f.get("exclude"), str) else f.get("exclude") or []) if str(x).strip()]
    if len(exclude) > 100 or any(len(x) > 300 for x in exclude):
        raise OffsiteError("Höchstens 100 Ausnahmen mit je 300 Zeichen.")
    sched = f.get("schedule") or {}
    try:
        hour = int(sched.get("hour", 3))
        days = sorted({int(d) for d in sched.get("days") or []})
        keep = {k: int((f.get("keep") or {}).get(k) or 0) for k in ("daily", "weekly", "monthly")}
    except (TypeError, ValueError):
        raise OffsiteError("Ungültige Zeit, Wochentage oder Aufbewahrung.")
    if not 0 <= hour <= 23 or any(d < 0 or d > 6 for d in days):
        raise OffsiteError("Stunde 0 bis 23, Wochentage Montag bis Sonntag.")
    if bool(sched.get("enabled")) and not days:
        raise OffsiteError("Für den Zeitplan mindestens einen Wochentag wählen.")
    if any(not 0 <= v <= 1000 for v in keep.values()) or not any(keep.values()):
        raise OffsiteError("Aufbewahrung: mindestens eine Zahl zwischen 1 und 1000 (täglich, wöchentlich oder monatlich).")
    password = str(f.get("password") or "")
    if password and len(password) < 12:
        raise OffsiteError("Das Passwort der Sicherung braucht mindestens 12 Zeichen.")
    if old and password and password != old.get("password") and old.get("last_ok"):
        raise OffsiteError("Das Passwort einer Sicherung mit gespeicherten Ständen lässt sich nicht ändern – dafür eine neue Sicherung anlegen.")
    clean = {"name": name, "target": target, "sources": sources, "custom": custom, "exclude": exclude,
             "schedule": {"enabled": bool(sched.get("enabled")), "hour": hour, "days": days}, "keep": keep}
    with _lock:
        items = _jobs()
        if jid:
            j = next((x for x in items if x["id"] == jid), None)
            if not j:
                raise OffsiteError("Sicherung nicht gefunden.")
            if (j["target"].get("kind"), _where(j["target"])) != (target["kind"], _where(target)):
                j.update(last_ok=0, snapshots=None)      # another place: its states start over
            j.update(clean, password=password or j["password"])
        else:
            if len(items) >= MAX_JOBS:
                raise OffsiteError(f"Höchstens {MAX_JOBS} externe Sicherungen.")
            items.append({"id": secrets.token_hex(5), **clean, "password": password or secrets.token_urlsafe(24), "user": user,
                          "created": int(time.time()), "last_run": 0, "last_ok": 0, "last_state": "", "last_error": "", "last_day": ""})
        settings_store.set(KEY, items)
    return overview()


def delete(jid: str) -> Dict[str, Any]:
    with _lock:
        items = _jobs()
        if not any(x["id"] == jid for x in items):
            raise OffsiteError("Sicherung nicht gefunden.")
        settings_store.set(KEY, [x for x in items if x["id"] != jid])
    return overview()


def password(jid: str) -> Dict[str, Any]:
    return {"password": _job(jid)["password"]}


# ---------------------------------------------------------------------------
# Running (as root, through pi-helper.sh offsite)
# ---------------------------------------------------------------------------
def _spec(j: Dict[str, Any]) -> Dict[str, Any]:
    stacks: List[str] = []
    if "docker" in j["sources"]:
        try:
            import containers
            stacks = sorted({s["dir"] for s in containers.stacks() if s.get("dir")})
        except Exception as e:
            logger.warning(f"Off-site backup: stack folders: {e}")
    return {"job": j["id"], "target": j["target"], "password": j["password"], "sources": j["sources"], "custom": j.get("custom") or [],
            "stacks": stacks, "exclude": j.get("exclude") or [], "keep": j.get("keep") or {}}


def _helper() -> str:
    import security
    if not os.path.exists(security.HELPER):
        raise OffsiteError("Helfer fehlt – install.sh auf dem Server erneut ausführen.")
    return security.HELPER


def _need_tools(j: Dict[str, Any]) -> None:
    t = tools()
    if not t["restic"]:
        raise OffsiteError("restic ist nicht installiert – oben „restic und rclone installieren“.")
    if j["target"]["kind"] in ("drive", "dropbox", "webdav") and not t["rclone"]:
        raise OffsiteError("rclone ist nicht installiert – oben „restic und rclone installieren“.")


def _quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _start(j: Dict[str, Any], cmd: str, title: str, user: str, args: List[str] = (), timeout: int = 24 * 3600) -> Dict[str, Any]:
    import sys
    if not sys.platform.startswith("linux"):
        raise OffsiteError("Nur unter Linux möglich.")
    _need_tools(j)
    helper = _helper()
    os.makedirs(SPEC_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(SPEC_DIR, f"{j['id']}-{secrets.token_hex(4)}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_spec(j), f)
    shell = (f"sudo -n {_quote(helper)} offsite {cmd} {' '.join(_quote(a) for a in args)} < {_quote(path)}; "
             f"rc=$?; rm -f {_quote(path)}; exit $rc")
    try:
        task = tasks.start("offsite", title, [{"shell": shell, "show": title}], user=user, key=f"offsite:{j['id']}", timeout=timeout,
                           data={"job": j["id"], "cmd": cmd, "spec": path})
    except tasks.TaskError as e:
        os.unlink(path)
        raise OffsiteError(str(e))
    if cmd == "backup":
        _update(j["id"], running=task["id"], last_run=int(time.time()))
    return task


def run(jid: str, user: str = "") -> Dict[str, Any]:
    j = _job(jid)
    return _start(j, "backup", f"Externe Sicherung: {j['name']}", user)


def check(jid: str, user: str = "") -> Dict[str, Any]:
    j = _job(jid)
    return _start(j, "check", f"Externe Sicherung prüfen: {j['name']}", user)


def restore(jid: str, snapshot: str, path: str, user: str = "") -> Dict[str, Any]:
    j = _job(jid)
    snapshot = str(snapshot or "").strip()
    path = str(path or "").strip().rstrip("/")
    if not SNAP_RE.match(snapshot):
        raise OffsiteError("Ungültiger Stand.")
    if path and (not PATH_RE.match(path) or ".." in path.split("/")):
        raise OffsiteError("Pfad mit / beginnen, z. B. /var/lib/docker/volumes/nextcloud_data.")
    return _start(j, "restore", f"Externe Sicherung wiederherstellen: {j['name']} ({snapshot})", user, [snapshot] + ([path] if path else []))


def snapshots(jid: str) -> Dict[str, Any]:
    import security
    j = _job(jid)
    _need_tools(j)
    _helper()
    try:
        out = security._helper_input(["offsite", "snapshots"], json.dumps(_spec(j)), timeout=300)
        data = json.loads(out.strip().splitlines()[-1] if out.strip() else "{}")
    except subprocess.TimeoutExpired:
        raise OffsiteError("Das Ziel antwortet nicht (5 Minuten).")
    except (PermissionError, RuntimeError) as e:
        raise OffsiteError(str(e).removeprefix("❌ "))
    except ValueError:
        raise OffsiteError("Unerwartete Antwort von restic.")
    _update(jid, snapshots=len(data.get("snapshots") or []))
    return {**data, "job": jid}


def _log_tail(task_id: str, size: int = 16384) -> str:
    try:
        path = tasks._path(task_id, "log")
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - size))
            return f.read().decode(errors="replace")
    except (OSError, tasks.TaskError):
        return ""


def _task_done(task: Dict[str, Any]) -> None:
    data = task.get("data") or {}
    spec = data.get("spec")
    if spec and os.path.exists(spec):
        os.unlink(spec)
    jid = data.get("job")
    if not jid or data.get("cmd") != "backup":
        return
    tail = _log_tail(task["id"])
    summary: Dict[str, Any] = {}
    for line in tail.splitlines():
        if line.startswith("HC-SUMMARY "):
            try:
                summary = json.loads(line[11:])
            except ValueError:
                pass
    if task.get("state") == "ok":
        _update(jid, running="", last_state="ok", last_error="", last_ok=int(time.time()), summary=summary)
    else:
        err = next((ln[2:].strip() for ln in reversed(tail.splitlines()) if ln.startswith("❌")), "")
        _update(jid, running="", last_state="error",
                last_error=(err or f"Aufgabe: {tasks.STATES.get(task.get('state'), task.get('state'))}")[:400])


tasks.on_done("offsite", _task_done)


def _refresh_running() -> None:
    """A task that ended while nobody looked (or before a restart) - the hook catches up via tasks.poll."""
    for j in _jobs():
        if j.get("running"):
            try:
                if tasks.get(j["running"])["state"] != "running":
                    tasks.poll()
            except tasks.TaskError:
                _update(j["id"], running="")


def tick(now: Optional[float] = None) -> None:
    """Once a minute: each job once on its planned days, at its hour."""
    lt = time.localtime(now or time.time())
    today = time.strftime("%Y-%m-%d", lt)
    for j in _jobs():
        s = j.get("schedule") or {}
        if not s.get("enabled") or lt.tm_wday not in (s.get("days") or []) or lt.tm_hour != s.get("hour") or j.get("last_day") == today:
            continue
        _update(j["id"], last_day=today)
        try:
            run(j["id"], "Zeitplan")
        except OffsiteError as e:
            _update(j["id"], last_state="error", last_error=str(e)[:400], last_run=int(time.time()))


def alerts() -> List[Dict[str, Any]]:
    out = []
    for j in _jobs():
        st = _state(j)
        if st == "error":
            out.append({"level": "warning", "text": f"Externe Sicherung {j['name']} fehlgeschlagen: {j.get('last_error') or 'siehe Aufgaben'}"})
        elif st == "overdue":
            out.append({"level": "warning", "text": f"Externe Sicherung {j['name']} ist überfällig"})
    return out


def install() -> Dict[str, Any]:
    import security
    missing = [p for p, ok in tools().items() if not ok]
    if not missing:
        return {"ok": True, "message": "restic und rclone sind schon installiert."}
    try:
        security._helper("apt-pkg", "install", *missing, timeout=30)
    except (PermissionError, RuntimeError) as e:
        raise OffsiteError(str(e))
    return {"ok": True, "message": f"{' und '.join(missing)} wird installiert – Fortschritt unter Pakete."}


# ---------------------------------------------------------------------------
# Signing in to Google Drive / Dropbox with rclone authorize
# ---------------------------------------------------------------------------
def _auth_reader(proc: subprocess.Popen, state: Dict[str, Any]) -> None:
    for line in proc.stdout:
        state["lines"].append(line.rstrip("\n")[:4000])
        if len(state["lines"]) > 200:
            del state["lines"][:50]
    proc.wait()


def _auth_stop() -> None:
    p = _auth.get("proc")
    if p and p.poll() is None:
        p.kill()
    if _auth.get("timer"):
        _auth["timer"].cancel()
    _auth.clear()


def auth_start(kind: str, client_id: str = "", client_secret: str = "", job: str = "") -> Dict[str, Any]:
    """Start rclone authorize and return the provider's sign-in address. With an own Google client, the
    sign-in is made for it (its secret may be the one already saved with the backup)."""
    import httpx
    if kind not in ("drive", "dropbox"):
        raise OffsiteError("Anmelden gibt es nur für Google Drive und Dropbox.")
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if kind == "drive" and client_id and not client_secret and job:
        saved = (_job(job).get("target") or {})
        if saved.get("client_id") == client_id:
            client_secret = saved.get("client_secret", "")
    if kind != "drive":
        client_id = client_secret = ""
    _check_client(client_id, client_secret)
    if not shutil.which("rclone"):
        raise OffsiteError("rclone ist nicht installiert – oben „restic und rclone installieren“.")
    with _lock:
        _auth_stop()
        try:
            proc = subprocess.Popen(["rclone", "authorize", kind, *([client_id, client_secret] if client_id else []), "--auth-no-open-browser"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, errors="replace")
        except OSError as e:
            raise OffsiteError(f"rclone ließ sich nicht starten: {e}")
        state = {"proc": proc, "kind": kind, "lines": [], "started": time.time()}
        threading.Thread(target=_auth_reader, args=(proc, state), daemon=True).start()
        _auth.update(state)
    local = ""
    for _ in range(100):
        local = next((m.group(0) for ln in state["lines"] for m in [re.search(rf"http://127\.0\.0\.1:{AUTH_PORT}/auth\?state=[\w-]+", ln)] if m), "")
        if local or proc.poll() is not None:
            break
        time.sleep(0.1)
    if not local:
        _auth_stop()
        raise OffsiteError("rclone hat keine Anmeldeadresse geliefert" + (f": {state['lines'][-1]}" if state["lines"] else "."))
    try:
        r = httpx.get(local, follow_redirects=False, timeout=10)
        url = r.headers.get("location", "")
    except httpx.HTTPError as e:
        _auth_stop()
        raise OffsiteError(f"rclone antwortet nicht: {e}")
    if not url.startswith("https://"):
        _auth_stop()
        raise OffsiteError("rclone hat keine Anmeldeadresse geliefert.")
    timer = threading.Timer(900, lambda p=proc: p.poll() is None and p.kill())     # give up after 15 minutes
    timer.daemon = True                                                             # never holds up a shutdown
    timer.start()
    _auth["timer"] = timer
    return {"url": url, "kind": kind}


def _token_from(lines: List[str]) -> str:
    text = "\n".join(lines)
    m = re.search(r"--->\s*(\{.*?\})\s*<---", text, re.S)
    return m.group(1) if m else ""


def auth_finish(address: str) -> Dict[str, Any]:
    """Hand the address the browser ended on (http://127.0.0.1:53682/?state=…&code=…) to rclone; return the token."""
    import httpx
    proc = _auth.get("proc")
    if not proc:
        raise OffsiteError("Keine Anmeldung offen – bitte noch einmal „Verbinden“.")
    token = _token_from(_auth["lines"])
    if not token:
        address = str(address or "").strip()
        u = urlsplit(address)
        if not (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost") and u.port == AUTH_PORT and "code=" in (u.query or "")):
            raise OffsiteError(f"Bitte die ganze Adresse aus der Adresszeile einfügen – sie beginnt mit http://127.0.0.1:{AUTH_PORT}/ und enthält code=.")
        if proc.poll() is not None:
            raise OffsiteError("Die Anmeldung ist abgelaufen – bitte noch einmal „Verbinden“.")
        try:
            httpx.get(f"http://127.0.0.1:{AUTH_PORT}{u.path or '/'}?{u.query}", timeout=30)
        except httpx.HTTPError as e:
            raise OffsiteError(f"rclone antwortet nicht: {e}")
        for _ in range(100):
            token = _token_from(_auth["lines"])
            if token or proc.poll() is not None:
                break
            time.sleep(0.1)
    if not token:
        last = next((ln for ln in reversed(_auth["lines"]) if ln.strip()), "")
        raise OffsiteError("Die Anmeldung hat nicht geklappt" + (f": {last[:300]}" if last else "."))
    kind = _auth.get("kind")
    with _lock:
        _auth_stop()
    return {"token": token, "kind": kind}
