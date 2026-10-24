"""
Compose stacks from Git (GitOps): a stack lives in a clone of a Git repository under
~/picore-apps/<name>; the repository is the source of truth.

- Each stack names repository, branch, subfolder and compose file. HTTPS with an optional
  access token (passed to git through GIT_CONFIG_* variables, never on the command line or in
  .git/config) or SSH with the dashboard user's keys.
- The clone's HEAD is the deployed commit. Checking fetches the branch and shows the commits and
  the diff that touch the stack's folder; "Ausrollen" checks the new commit out and runs
  `docker compose up -d --remove-orphans` as a task (databases dumped first, health check last -
  like the safe stack update). Automatic mode deploys new commits by itself.
- Drift: files changed on the Pi, services that do not run, and containers on other images than
  the compose file asks for are reported (status overview, events).
- Local variables (.env) stay on the Pi, outside the repository (--env-file).
- A deploy webhook per stack (/hook/gitops/<token>) lets GitHub/Gitea/GitLab trigger a check at once.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

logger = logging.getLogger("pi_dashboard.gitops")

KEY = "gitops"
EVENTS_KEY = "gitops_events"
APPS_DIR = os.path.expanduser("~/picore-apps")
ENV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gitops-env")
HEALTH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stackhealth.py")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
INTERVALS = [1, 5, 15, 60, 360]
MAX_STACKS = 40
MAX_DIFF = 200_000
LIMITS_FILE = "docker-compose.picore.yml"      # resource limits set in the dashboard (containers.py)
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")

_lock = threading.Lock()
_checking: Dict[str, bool] = {}
_last_check: Dict[str, float] = {}


class GitOpsError(Exception):
    pass


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def _all() -> List[Dict[str, Any]]:
    items = settings_store.get(KEY) or []
    return items if isinstance(items, list) else []


def _save_all(items: List[Dict[str, Any]]) -> None:
    settings_store.set(KEY, items)


def _get(name: str) -> Dict[str, Any]:
    s = next((x for x in _all() if x["name"] == name), None)
    if not s:
        raise GitOpsError(f"GitOps-Stack {name} nicht gefunden.")
    return s


def _update(name: str, **fields) -> Dict[str, Any]:
    with _lock:
        items = _all()
        for s in items:
            if s["name"] == name:
                s.update(fields)
                _save_all(items)
                return s
    raise GitOpsError(f"GitOps-Stack {name} nicht gefunden.")


def _event(level: str, title: str, detail: str = "", key: str = "") -> None:
    with _lock:
        events = (settings_store.get(EVENTS_KEY, []) or [])[-199:]
        events.append({"ts": int(time.time()), "level": level, "title": title, "detail": detail[:300], "key": key})
        settings_store.set(EVENTS_KEY, events)


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------
def _repo_dir(s: Dict[str, Any]) -> str:
    return os.path.join(APPS_DIR, s["name"])


def _stack_dir(s: Dict[str, Any]) -> str:
    return os.path.join(_repo_dir(s), s["path"]) if s["path"] else _repo_dir(s)


def _env_file(s: Dict[str, Any]) -> str:
    return os.path.join(ENV_DIR, f"{s['name']}.env")


def _git_env(s: Dict[str, Any]) -> Dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"}
    token = s.get("token")
    if token and s["url"].startswith("https://"):
        # Basic auth works for GitHub, Gitea/Forgejo and GitLab tokens alike
        cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {cred}")
    return env


def _git(s: Dict[str, Any], args: List[str], timeout: float = 60, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd or _repo_dir(s), capture_output=True, text=True, timeout=timeout,
                              env=_git_env(s))
    except subprocess.TimeoutExpired:
        raise GitOpsError("Git antwortet nicht (Zeitüberschreitung).")
    except OSError:
        raise GitOpsError("git ist nicht installiert.")


def _clean_err(text: str, s: Dict[str, Any]) -> str:
    text = (text or "").strip().splitlines()
    msg = next((l for l in reversed(text) if l.strip()), "unbekannter Fehler")
    if s.get("token"):
        msg = msg.replace(s["token"], "•••")
    return msg[:300]


def _ok(res: subprocess.CompletedProcess, s: Dict[str, Any], what: str) -> str:
    if res.returncode != 0:
        raise GitOpsError(f"{what}: {_clean_err(res.stderr or res.stdout, s)}")
    return res.stdout


def _remote_ref(s: Dict[str, Any]) -> str:
    return f"refs/remotes/origin/{s['branch']}"


def _rev(s: Dict[str, Any], ref: str) -> str:
    res = _git(s, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], timeout=10)
    return res.stdout.strip() if res.returncode == 0 else ""


def _fetch(s: Dict[str, Any]) -> None:
    _ok(_git(s, ["fetch", "--quiet", "--prune", "origin", f"+refs/heads/{s['branch']}:{_remote_ref(s)}"], timeout=120), s, "Abrufen fehlgeschlagen")


def _paths(s: Dict[str, Any]) -> List[str]:
    return ["--", s["path"] or "."]


def _commits(s: Dict[str, Any], a: str, b: str, limit: int = 30) -> List[Dict[str, Any]]:
    if not a or not b or a == b:
        return []
    res = _git(s, ["log", f"--max-count={limit}", "--format=%H%x1f%an%x1f%at%x1f%s", f"{a}..{b}", *_paths(s)], timeout=20)
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append({"sha": parts[0], "author": parts[1][:80], "ts": int(parts[2] or 0), "subject": parts[3][:200]})
    return out


def _local_changes(s: Dict[str, Any]) -> List[str]:
    res = _git(s, ["status", "--porcelain", "--untracked-files=normal", *_paths(s)], timeout=20)
    return [l[3:] for l in res.stdout.splitlines() if l.strip()][:50]


def _exclude_local_files(s: Dict[str, Any]) -> None:
    """The dashboard's own override file next to the stack is no change to the repository."""
    path = os.path.join(_repo_dir(s), ".git", "info", "exclude")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = open(path).read() if os.path.exists(path) else ""
        if LIMITS_FILE not in current:
            with open(path, "a") as f:
                f.write(f"\n# PiCore Dashboard\n{LIMITS_FILE}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
def _compose_file(s: Dict[str, Any]) -> str:
    return os.path.join(_stack_dir(s), s["file"])


def compose_args(s: Dict[str, Any]) -> List[str]:
    args = ["-p", s["name"]]
    if os.path.isfile(_env_file(s)):
        args += ["--env-file", _env_file(s)]
    args += ["-f", _compose_file(s)]
    override = os.path.join(_stack_dir(s), LIMITS_FILE)
    if os.path.isfile(override):
        args += ["-f", override]
    return args


def _compose_config(s: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = subprocess.run(["docker", "compose", *compose_args(s), "config", "--format", "json"], cwd=_stack_dir(s),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise GitOpsError("docker compose antwortet nicht.")
    if res.returncode != 0:
        raise GitOpsError(f"Compose-Datei ungültig: {(res.stderr or res.stdout).strip()[:300]}")
    try:
        return json.loads(res.stdout)
    except ValueError:
        raise GitOpsError("docker compose config lieferte kein JSON.")


def _drift(s: Dict[str, Any]) -> Dict[str, Any]:
    """Running state against the compose file of the deployed commit."""
    import containers
    out: Dict[str, Any] = {"missing": [], "stopped": [], "image": [], "error": ""}
    try:
        cfg = _compose_config(s)
    except GitOpsError as e:
        out["error"] = str(e)
        return out
    running = [c for c in containers.docker_list().get("containers", []) if c.get("stack") == s["name"]]
    for svc, spec in (cfg.get("services") or {}).items():
        if not isinstance(spec, dict) or (spec.get("deploy") or {}).get("replicas") == 0 or spec.get("profiles"):
            continue
        members = [c for c in running if c.get("service") == svc]
        if not members:
            out["missing"].append(svc)
            continue
        if not any(c.get("state") == "running" for c in members) and spec.get("restart", "no") not in ("no", "on-failure"):
            out["stopped"].append(svc)
        want = str(spec.get("image") or "")
        if want and not spec.get("build"):
            norm = lambda i: re.sub(r"^(docker\.io/)?(library/)?", "", i if ":" in i.split("/")[-1] or "@" in i else f"{i}:latest")
            have = {norm(c.get("image", "")) for c in members}
            if any(re.fullmatch(r"(sha256:)?[0-9a-f]{12,64}(:latest)?", h) for h in have):
                continue     # shown by image ID: the tag moved on, nothing to compare
            if norm(want) not in have:
                out["image"].append({"service": svc, "want": want, "have": sorted(have)[0] if have else ""})
    return out


# ---------------------------------------------------------------------------
# Public view
# ---------------------------------------------------------------------------
def _public(s: Dict[str, Any], deep: bool = False) -> Dict[str, Any]:
    out = {k: s.get(k) for k in ("name", "url", "branch", "path", "file", "auto", "interval", "created", "user",
                                 "deployed", "deployed_at", "deployed_by", "remote", "checked", "error", "history",
                                 "pending", "local_changes", "drift", "last_task")}
    out["has_token"] = bool(s.get("token"))
    out["has_env"] = os.path.isfile(_env_file(s))
    out["dir"] = _stack_dir(s)
    out["repo_dir"] = _repo_dir(s)
    out["hook_path"] = f"/hook/gitops/{s['hook']}" if s.get("hook") else ""
    out["behind"] = len(s.get("pending") or [])
    drift = s.get("drift") or {}
    out["drift_count"] = len(drift.get("missing", [])) + len(drift.get("stopped", [])) + len(drift.get("image", [])) + len(s.get("local_changes") or [])
    out["state"] = ("error" if s.get("error") else "deploying" if _task_running(s) else
                    "drift" if out["drift_count"] else "behind" if out["behind"] else "synced" if s.get("deployed") else "new")
    return out


def _task_running(s: Dict[str, Any]) -> bool:
    import tasks
    tid = s.get("last_task")
    if not tid:
        return False
    try:
        return tasks.get(tid)["state"] == "running"
    except tasks.TaskError:
        return False


def overview() -> Dict[str, Any]:
    items = [_public(s) for s in _all()]
    return {"stacks": items, "intervals": INTERVALS, "git": shutil.which("git") is not None,
            "counts": {k: sum(1 for i in items if i["state"] == k) for k in ("synced", "behind", "drift", "error", "deploying", "new")}}


def alerts() -> List[Dict[str, Any]]:
    out = []
    for s in overview()["stacks"]:
        if s["state"] == "error":
            out.append({"level": "warning", "text": f"GitOps {s['name']}: {s['error']}"})
        elif s["state"] == "drift":
            out.append({"level": "warning", "text": f"GitOps {s['name']}: weicht vom Repository ab"})
        elif s["state"] == "behind" and not s["auto"]:
            out.append({"level": "info", "text": f"GitOps {s['name']}: {s['behind']} neue {'Änderung' if s['behind'] == 1 else 'Änderungen'} zum Ausrollen"})
    return out


# ---------------------------------------------------------------------------
# Create / edit / remove
# ---------------------------------------------------------------------------
def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if re.match(r"^https?://[^\s@/]+/[^\s]+$", url):
        return url
    if re.match(r"^(ssh://)?[A-Za-z0-9._-]+@[A-Za-z0-9.-]+(:\d+)?[:/][^\s]+$", url):
        return url
    if re.match(r"^https://[^\s/]*@", url):
        raise GitOpsError("Zugangsdaten nicht in die Adresse schreiben – dafür gibt es das Feld Token.")
    raise GitOpsError("Repository: https://… oder git@host:pfad angeben.")


def _clean_path(path: str) -> str:
    path = (path or "").strip().strip("/")
    if path in ("", "."):
        return ""
    if len(path) > 200 or any(p in ("", ".", "..") for p in path.split("/")) or not re.fullmatch(r"[\w./ -]+", path):
        raise GitOpsError("Unterordner: relativer Pfad ohne „..“.")
    return path


def _write_env(s: Dict[str, Any], env: Optional[str]) -> None:
    if env is None:
        return
    path = _env_file(s)
    if not env.strip():
        if os.path.exists(path):
            os.remove(path)
        return
    if len(env) > 50_000:
        raise GitOpsError("Variablen: höchstens 50 KB.")
    os.makedirs(ENV_DIR, mode=0o700, exist_ok=True)
    with os.fdopen(os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(env if env.endswith("\n") else env + "\n")
    os.replace(path + ".tmp", path)


def _settings(fields: Dict[str, Any], s: Dict[str, Any]) -> None:
    interval = int(fields.get("interval") or s.get("interval") or 5)
    if interval not in INTERVALS:
        raise GitOpsError("Ungültiges Prüfintervall.")
    s.update(auto=bool(fields.get("auto")), interval=interval)


def create(fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    import containers
    if not shutil.which("git"):
        raise GitOpsError("git ist nicht installiert (sudo apt install git).")
    name = str(fields.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise GitOpsError("Name: Kleinbuchstaben, Ziffern, - und _ (höchstens 41 Zeichen).")
    if any(x["name"] == name for x in _all()):
        raise GitOpsError(f"„{name}“ wird schon aus Git verwaltet.")
    if len(_all()) >= MAX_STACKS:
        raise GitOpsError(f"Höchstens {MAX_STACKS} GitOps-Stacks.")
    existing = next((x for x in containers.stacks() if x["name"] == name), None)
    if existing and not fields.get("adopt"):
        raise GitOpsError(f"Es gibt schon einen Stack „{name}“. Zum Übernehmen „Vorhandenen Stack übernehmen“ ankreuzen – "
                          "beim ersten Ausrollen werden seine Container aus dem Repository neu erstellt.")
    branch = str(fields.get("branch") or "main").strip()
    if not BRANCH_RE.match(branch) or ".." in branch:
        raise GitOpsError("Ungültiger Branch.")
    s: Dict[str, Any] = {"name": name, "url": _clean_url(fields.get("url", "")), "branch": branch,
                         "path": _clean_path(fields.get("path", "")), "file": "", "token": str(fields.get("token") or "").strip()[:500],
                         "created": int(time.time()), "user": user, "hook": secrets.token_urlsafe(24), "history": [],
                         "deployed": "", "remote": "", "checked": 0, "error": ""}
    _settings(fields, s)
    if s["token"] and not s["url"].startswith("https://"):
        raise GitOpsError("Ein Token nur über https – sonst ginge er unverschlüsselt durchs Netz.")
    folder = _repo_dir(s)
    if os.path.exists(folder) and os.listdir(folder):
        raise GitOpsError(f"Den Ordner {folder} gibt es schon und er ist nicht leer.")
    os.makedirs(APPS_DIR, exist_ok=True)
    res = _git(s, ["clone", "--quiet", "--branch", branch, "--single-branch", "--no-tags", s["url"], folder], timeout=300, cwd=APPS_DIR)
    if res.returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise GitOpsError(f"Klonen fehlgeschlagen: {_clean_err(res.stderr, s)}")
    try:
        if not os.path.isdir(_stack_dir(s)):
            raise GitOpsError(f"Den Unterordner „{s['path']}“ gibt es im Branch {branch} nicht.")
        want = str(fields.get("file") or "").strip()
        if want:
            if "/" in want or want.startswith(".") or not os.path.isfile(os.path.join(_stack_dir(s), want)):
                raise GitOpsError(f"Compose-Datei „{want}“ nicht gefunden.")
            s["file"] = want
        else:
            s["file"] = next((n for n in COMPOSE_NAMES if os.path.isfile(os.path.join(_stack_dir(s), n))), "")
            if not s["file"]:
                raise GitOpsError("Keine Compose-Datei (compose.yaml / docker-compose.yml) im Ordner gefunden.")
        _write_env(s, fields.get("env"))
        _exclude_local_files(s)
        _compose_config(s)
    except GitOpsError:
        shutil.rmtree(folder, ignore_errors=True)
        if os.path.exists(_env_file(s)):
            os.remove(_env_file(s))
        raise
    # Not deployed yet: HEAD is the newest commit, but "deployed" stays empty until the first deploy
    s["remote"] = _rev(s, "HEAD")
    s["checked"] = int(time.time())
    with _lock:
        items = _all()
        items.append(s)
        _save_all(items)
    _event("info", f"GitOps-Stack {name} angelegt", f"{s['url']} ({branch}{'/' + s['path'] if s['path'] else ''})", key=f"gitops:{name}")
    return _public(s)


def edit(name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(_get(name))
    _settings(fields, s)
    if "token" in fields and str(fields.get("token") or "").strip():
        if not s["url"].startswith("https://"):
            raise GitOpsError("Ein Token nur über https – sonst ginge er unverschlüsselt durchs Netz.")
        s["token"] = str(fields["token"]).strip()[:500]
    if fields.get("remove_token"):
        s["token"] = ""
    branch = str(fields.get("branch") or s["branch"]).strip()
    if branch != s["branch"]:
        if not BRANCH_RE.match(branch) or ".." in branch:
            raise GitOpsError("Ungültiger Branch.")
        s["branch"] = branch
        s["pending"] = []
    _write_env(s, fields.get("env"))
    return _public(_update(name, **{k: s[k] for k in ("auto", "interval", "token", "branch", "pending") if k in s}))


def env_text(name: str) -> str:
    s = _get(name)
    try:
        with open(_env_file(s)) as f:
            return f.read()
    except OSError:
        return ""


def remove(name: str, delete_files: bool = False, stop: bool = False, user: str = "") -> Dict[str, Any]:
    s = _get(name)
    if _task_running(s):
        raise GitOpsError("Gerade läuft ein Ausrollen – erst danach entfernen.")
    if stop:
        subprocess.run(["docker", "compose", *compose_args(s), "down"], cwd=_stack_dir(s), capture_output=True, timeout=300)
    with _lock:
        _save_all([x for x in _all() if x["name"] != name])
    if os.path.exists(_env_file(s)):
        os.remove(_env_file(s))
    if delete_files:
        shutil.rmtree(_repo_dir(s), ignore_errors=True)
    _event("info", f"GitOps für {name} beendet", ("Stack gestoppt, " if stop else "") + ("Ordner gelöscht" if delete_files else "Ordner bleibt"),
           key=f"gitops:{name}")
    return overview()


# ---------------------------------------------------------------------------
# Check (fetch, pending commits, drift) and deploy
# ---------------------------------------------------------------------------
def check(name: str, deploy_if_auto: bool = True) -> Dict[str, Any]:
    s = _get(name)
    if _checking.get(name):
        return _public(s)
    _checking[name] = True
    _last_check[name] = time.time()
    try:
        error = ""
        try:
            _fetch(s)
        except GitOpsError as e:
            error = str(e)
        remote = _rev(s, _remote_ref(s)) or s.get("remote", "")
        deployed = s.get("deployed") or ""
        pending = _commits(s, deployed, remote) if remote and deployed else []
        fields = {"remote": remote, "pending": pending, "checked": int(time.time()), "error": error,
                  "local_changes": _local_changes(s) if deployed else [],
                  "drift": _drift(s) if deployed and not _task_running(s) else {}}
        before = s.get("error", "")
        s = _update(name, **fields)
        if error and error != before:
            _event("warning", f"GitOps {name}: Abruf fehlgeschlagen", error, key=f"gitops:{name}")
        new_commit = bool(deployed and remote and remote != deployed and pending)
        auto = bool(s.get("auto") and deploy_if_auto)
        if new_commit and remote != s.get("announced"):
            _update(name, announced=remote)
            if not auto:
                _event("info", f"GitOps {name}: neue Version im Repository", f"{remote[:8]} · {pending[0]['subject']}", key=f"gitops:{name}")
        if auto and new_commit and not _task_running(s) and s.get("failed") != remote:
            deploy(name, user="GitOps (automatisch)")
        elif deployed and remote and remote != deployed and not pending and not fields["local_changes"]:
            # New commits, but none touch the stack's folder: follow without redeploying
            _ok(_git(s, ["checkout", "--quiet", "--force", remote]), s, "Auschecken fehlgeschlagen")
            _update(name, deployed=remote)
        return _public(_get(name))
    finally:
        _checking.pop(name, None)


def diff(name: str) -> Dict[str, Any]:
    s = _get(name)
    remote = s.get("remote") or _rev(s, _remote_ref(s))
    base = s.get("deployed") or ""
    if not base:
        return {"base": "", "target": remote, "diff": "", "stat": "", "first": True, "local": ""}
    stat = _git(s, ["diff", "--stat", base, remote, *_paths(s)], timeout=20).stdout if remote and remote != base else ""
    text = _git(s, ["diff", "--no-color", base, remote, *_paths(s)], timeout=20).stdout if remote and remote != base else ""
    local = _git(s, ["diff", "--no-color", "HEAD", *_paths(s)], timeout=20).stdout
    return {"base": base, "target": remote, "stat": stat[-5000:], "diff": text[:MAX_DIFF], "truncated": len(text) > MAX_DIFF,
            "local": local[:MAX_DIFF], "first": False}


def _done(task: Dict[str, Any]) -> None:
    d = task.get("data") or {}
    name, commit = d.get("gitops"), d.get("commit", "")
    if not name:
        return
    try:
        s = _get(name)
    except GitOpsError:
        return
    entry = {"ts": int(task.get("ended") or time.time()), "commit": commit, "previous": d.get("previous", ""),
             "subject": d.get("subject", ""), "user": task.get("user", ""), "state": task["state"], "task": task["id"]}
    history = ([entry] + (s.get("history") or []))[:20]
    if task["state"] == "ok":
        _update(name, deployed=commit, deployed_at=entry["ts"], deployed_by=entry["user"], history=history, failed="",
                pending=[], local_changes=[], drift={})
        _event("good", f"GitOps {name}: {commit[:8]} ausgerollt", d.get("subject", ""), key=f"gitops:{name}")
    else:
        # The files are at the new commit, but the stack failed: remember it, so automatic mode does not loop
        _update(name, history=history, failed=commit)
        _event("critical", f"GitOps {name}: Ausrollen von {commit[:8]} fehlgeschlagen", "Protokoll in den Aufgaben", key=f"gitops:{name}")
    import containers
    containers.invalidate()


def _register_hooks() -> None:
    import tasks
    tasks.on_done("gitops", _done)


_register_hooks()


def deploy(name: str, user: str = "", commit: str = "") -> Dict[str, Any]:
    import containers
    import dbmonitor
    import tasks
    s = _get(name)
    target = commit or s.get("remote") or _rev(s, _remote_ref(s))
    if not SHA_RE.match(target or ""):
        raise GitOpsError("Kein Commit zum Ausrollen – erst „Prüfen“.")
    if _task_running(s):
        raise GitOpsError("Für diesen Stack läuft schon ein Ausrollen.")
    subject = _git(s, ["log", "-1", "--format=%s", target], timeout=10).stdout.strip()[:200]
    base = compose_args(s)
    steps: List[Dict[str, Any]] = [
        {"argv": ["git", "-C", _repo_dir(s), "checkout", "--quiet", "--force", target], "show": f"git checkout {target[:8]}"},
        {"argv": ["docker", "compose", *base, "config", "-q"], "show": "docker compose config (Prüfung)"},
        {"argv": ["docker", "compose", *base, "pull"], "show": "docker compose pull"},
        {"argv": ["docker", "compose", *base, "up", "-d", "--remove-orphans", "--build"], "show": "docker compose up -d --remove-orphans"},
        {"argv": [sys.executable, HEALTH_SCRIPT, name, "120"], "show": "Gesundheitsprüfung"},
    ]
    import imagepolicy
    gate = imagepolicy.gate_step(name, base)
    if gate:
        steps.insert(1, gate)      # after the checkout: the new commit's compose file is checked
    dumps = dbmonitor.pre_update_dumps(name) if any(x["name"] == name for x in containers.stacks()) else []
    steps = steps[:1] + [d.pop("step") for d in dumps] + steps[1:]
    data = {"gitops": name, "commit": target, "previous": s.get("deployed", ""), "subject": subject, "dumps": dumps, "critical": True,
            "stack": name, "action": "update", "label": "Ausrollen"}
    try:
        task = tasks.start("gitops", f"Ausrollen: {name} @ {target[:8]}", steps, user=user, key=f"stack:{name}",
                           cwd=_stack_dir(s), timeout=1800 * len(steps), data=data)
    except tasks.TaskError:
        raise GitOpsError("Für diesen Stack läuft schon eine Aktion.")
    _update(name, last_task=task["id"])
    containers.invalidate()
    return {**_public(_get(name)), "task": task["id"]}


def check_if_due() -> None:
    """Called every minute by the monitor."""
    now = time.time()
    for s in _all():
        if _checking.get(s["name"]) or now - _last_check.get(s["name"], 0) < s.get("interval", 5) * 60 - 5:
            continue
        threading.Thread(target=_safe_check, args=(s["name"],), daemon=True).start()


def _safe_check(name: str) -> None:
    try:
        check(name)
    except Exception as e:
        logger.warning(f"GitOps check {name} failed: {e}")


def hook(token: str) -> bool:
    """Deploy webhook: true if the token belongs to a stack (the check then runs in the background)."""
    for s in _all():
        if s.get("hook") and secrets.compare_digest(hashlib.sha256(s["hook"].encode()).digest(), hashlib.sha256(token.encode()).digest()):
            threading.Thread(target=_safe_check, args=(s["name"],), daemon=True).start()
            return True
    return False


def new_hook(name: str) -> Dict[str, Any]:
    return _public(_update(name, hook=secrets.token_urlsafe(24)))
