"""
Off-site backup: one restic run as root through pi-helper.sh offsite (from the root-owned copy).

    offsitetool.py backup      < spec      back up the chosen sources, then apply retention
    offsitetool.py snapshots   < spec      JSON list of this server's backup states
    offsitetool.py restore <snapshot> [<path>] < spec    into a root-only folder below RESTORE_ROOT
    offsitetool.py check       < spec      read part of the data back and verify it

The spec (JSON on stdin) comes from the dashboard. Reading every file as root is what makes a
backup useful, and also what makes this script powerful, so the dashboard is not trusted to
choose freely: it names source *categories*, the folders are fixed here; own folders, stack
folders and local targets must lie below a short list of data roots; rclone remotes are built
here from a few fields per provider (no free rclone config: options like `ssh` or
`bearer_token_command` would run commands as root). Shadow files and SSH host keys are never
backed up. Only the standard library; restic and rclone must be installed.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

STATE_DIR = "/var/lib/pi-dashboard"
RESTORE_ROOT = os.path.join(STATE_DIR, "restore")
PROGRESS_DIR = os.path.join(STATE_DIR, "offsite-progress")   # root-owned, readable: <job>.json while a backup runs
JOB_RE = re.compile(r"^[0-9a-f]{10}$")
DATA_ROOTS = ("/home", "/srv", "/opt", "/mnt", "/media", "/data", "/var/www", "/var/lib/docker/volumes")
SOURCES = {
    "docker": ["/var/lib/docker/volumes"],
    "k3s": ["/var/lib/rancher/k3s/storage", "/var/lib/rancher/k3s/server/db", "/var/lib/rancher/k3s/server/token",
            "/var/lib/rancher/k3s/server/manifests", "/etc/rancher/k3s"],
    "system": ["/etc", "/var/spool/cron", "/usr/local/bin", "/usr/local/sbin"],
    "logs": ["/var/log"],
    "home": ["/home"],
}
NEVER = ["/etc/shadow", "/etc/shadow-", "/etc/gshadow", "/etc/gshadow-", "/etc/ssh/ssh_host_*_key", "/etc/.pwd.lock"]
DASHBOARD_SKIP = ["venv", ".git", "__pycache__", "icon_cache", "appstore_logos", "geoip.db", "tasks", "node_modules"]
PROVIDERS = ("drive", "dropbox", "webdav", "s3", "local")
WEBDAV_VENDORS = ("nextcloud", "owncloud", "sharepoint", "other")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+ -]{1,250}$")
REMOTE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ +-]{1,200}$")
SNAP_RE = re.compile(r"^[0-9a-f]{8,64}$")
TAG = "hostcore"


def die(text: str) -> None:
    print(f"❌ {text}", file=sys.stderr, flush=True)
    sys.exit(2)


def say(text: str) -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# Checking what the dashboard asks for
# ---------------------------------------------------------------------------
def below(path: str, roots) -> bool:
    real = os.path.normpath(path)
    return any(real == r or real.startswith(r + "/") for r in roots)


def clean_path(p, roots, what: str) -> str:
    p = str(p or "").strip().rstrip("/") or "/"
    if not PATH_RE.match(p) or ".." in p.split("/") or not below(p, roots):
        die(f"{what} {p!r}: erlaubt sind Ordner unter {', '.join(roots)}.")
    real = os.path.realpath(p)
    if not below(real, roots):
        die(f"{what} {p!r} zeigt per Link nach {real} – nicht erlaubt.")
    return real


def dashboard_dir() -> str:
    try:
        with open(os.path.join(STATE_DIR, "dir")) as f:
            d = f.read().strip()
    except OSError:
        d = ""
    if not d or not os.path.isdir(d):
        die("Dashboard-Ordner unbekannt – install.sh erneut ausführen.")
    return d


def text(spec, key: str, limit: int = 4000) -> str:
    v = spec.get(key, "")
    if not isinstance(v, str) or len(v) > limit or "\n" in v or "\r" in v or "\x00" in v:
        die(f"Ungültiger Wert für {key}.")
    return v.strip()


def target(spec, tmpdir: str):
    """(repository, environment) for restic; rclone remotes get their own config file in tmpdir."""
    t = spec.get("target")
    if not isinstance(t, dict) or t.get("kind") not in PROVIDERS:
        die("Unbekanntes Ziel.")
    kind = t["kind"]
    env = {}
    if kind == "local":
        repo = clean_path(t.get("path"), ("/mnt", "/media", "/srv", "/data"), "Zielordner")
        return repo, env
    if kind == "s3":
        endpoint = text(t, "endpoint", 300).rstrip("/")
        bucket = text(t, "bucket", 100)
        if not re.match(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?$", endpoint):
            die("S3-Endpunkt: https://… ohne Pfad, z. B. https://s3.eu-central-1.amazonaws.com")
        if not re.match(r"^[a-z0-9][a-z0-9.-]{1,62}$", bucket):
            die("Ungültiger Bucket-Name.")
        sub = text(t, "folder", 200).strip("/")
        if sub and not REMOTE_PATH_RE.match(sub):
            die("Ungültiger Ordner im Bucket.")
        env = {"AWS_ACCESS_KEY_ID": text(t, "key_id", 200), "AWS_SECRET_ACCESS_KEY": text(t, "secret", 300)}
        region = text(t, "region", 60)
        if region:
            env["AWS_DEFAULT_REGION"] = region
        return f"s3:{endpoint}/{bucket}" + (f"/{sub}" if sub else ""), env
    if not shutil.which("rclone"):
        die("rclone ist nicht installiert.")
    folder = text(t, "folder", 200).strip("/") or "HostCore"
    if not REMOTE_PATH_RE.match(folder):
        die("Ungültiger Ordner im Ziel.")
    lines = ["[hc]"]
    if kind in ("drive", "dropbox"):
        token = t.get("token", "")
        try:
            tok = json.loads(token)
            assert isinstance(tok, dict) and (tok.get("refresh_token") or tok.get("access_token"))
        except (ValueError, TypeError, AssertionError):
            die("Die Anmeldung fehlt oder ist ungültig – bitte im Dashboard neu verbinden.")
        lines += [f"type = {kind}", f"token = {json.dumps(tok, separators=(',', ':'))}"]
        if kind == "drive":
            lines.append("scope = drive")
            # Own Google client: its own quota instead of the one all rclone users share (heavily throttled)
            cid, csec = text(t, "client_id", 200), text(t, "client_secret", 200)
            if cid:
                if not re.match(r"^[0-9]+-[A-Za-z0-9_]+\.apps\.googleusercontent\.com$", cid) or not re.match(r"^[A-Za-z0-9_-]{10,100}$", csec):
                    die("Ungültige Google-Client-ID oder ungültiges Client-Secret.")
                lines += [f"client_id = {cid}", f"client_secret = {csec}"]
            root = text(t, "root_folder_id", 100)
            if root:
                if not re.match(r"^[A-Za-z0-9_-]{5,100}$", root):
                    die("Ungültige Ordner-ID.")
                lines.append(f"root_folder_id = {root}")
    elif kind == "webdav":
        url = text(t, "url", 400)
        if not re.match(r"^https://[^\s]+$", url):
            die("WebDAV-Adresse: https://…")
        vendor = t.get("vendor") if t.get("vendor") in WEBDAV_VENDORS else "other"
        user = text(t, "user", 200)
        res = subprocess.run(["rclone", "obscure", "-"], input=t.get("password", "") or "", capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            die("Passwort ließ sich nicht für rclone aufbereiten.")
        lines += ["type = webdav", f"url = {url}", f"vendor = {vendor}", f"user = {user}", f"pass = {res.stdout.strip()}"]
    conf = os.path.join(tmpdir, "rclone.conf")
    fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    env["RCLONE_CONFIG"] = conf
    return f"rclone:hc:{folder}", env


def sources(spec):
    paths, excludes = [], list(NEVER)
    for cat in spec.get("sources") or []:
        if cat == "dashboard":
            d = dashboard_dir()
            paths.append(d)
            excludes += [os.path.join(d, x) for x in DASHBOARD_SKIP]
        elif cat in SOURCES:
            paths += SOURCES[cat]
        else:
            die(f"Unbekannte Quelle {cat!r}.")
    if "docker" in (spec.get("sources") or []):
        for p in (spec.get("stacks") or [])[:100]:
            if PATH_RE.match(str(p)) and below(os.path.realpath(str(p)), DATA_ROOTS):
                paths.append(os.path.realpath(str(p)))
    for p in (spec.get("custom") or [])[:50]:
        paths.append(clean_path(p, DATA_ROOTS, "Ordner"))
    for p in (spec.get("exclude") or [])[:100]:
        p = str(p)
        if not p or len(p) > 300 or "\n" in p:
            die("Ungültige Ausnahme.")
        excludes.append(p)
    there = sorted({p for p in paths if os.path.exists(p)})
    return there, sorted(set(paths) - set(there)), excludes


# ---------------------------------------------------------------------------
# Progress for the dashboard: one small JSON file per job, written by root, never anything secret
# ---------------------------------------------------------------------------
class Progress:
    def __init__(self, job: str):
        self.path = os.path.join(PROGRESS_DIR, f"{job}.json") if JOB_RE.match(job or "") else ""
        self.state = {"started": int(time.time()), "phase": "connect"}
        self.last = 0.0
        if self.path:
            os.makedirs(PROGRESS_DIR, mode=0o755, exist_ok=True)
            os.chmod(PROGRESS_DIR, 0o755)
        self.write()

    def write(self, force: bool = True, **fields) -> None:
        self.state.update(fields)
        if not self.path or (not force and time.time() - self.last < 3):
            return
        self.last = time.time()
        self.state["updated"] = int(self.last)
        tmp = f"{self.path}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# restic
# ---------------------------------------------------------------------------
RCLONE_HINT = re.compile(r"ERROR|Failed|failed to|googleapi|rateLimit|quota|invalid_grant|token|unauthorized|forbidden|\b40[0-9]\b|\b50[0-9]\b", re.I)


def tidy(line: str) -> str:
    """A log line short enough to read: restic's JSON message, no timestamps, addresses without their long
    query, pack names cut - so the actual reason at the end of rclone's lines isn't lost to truncation."""
    line = line.strip()
    if line.startswith("{"):
        try:
            line = str(json.loads(line).get("message") or line)
        except (ValueError, AttributeError):
            pass
    line = re.sub(r"^(rclone:\s*)+", "", line)
    line = re.sub(r"\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s*", "", line)
    line = re.sub(r"(https?://[^/\s\"]+)[^\s\"]*", r"\1/…", line)
    line = re.sub(r"\b([0-9a-f]{8})[0-9a-f]{24,}\b", r"\1…", line)
    return re.sub(r"\s{2,}", " ", line).strip()


def explain(stderr: str) -> str:
    """The useful part of a failure: restic's last line, plus rclone's own message (the real cause) if any."""
    lines = [tidy(ln) for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return "unbekannter Fehler"
    last = lines[-1]
    cause = next((ln for ln in reversed(lines[:-1]) if RCLONE_HINT.search(ln) and ln != last), "")
    low = (cause + " " + last).lower()
    if "ratelimit" in low or "quota" in low or "429" in low:
        return throttled(stderr)
    if "invalid_grant" in low or "token has been expired or revoked" in low:
        return "Die Anmeldung beim Anbieter ist abgelaufen oder wurde widerrufen – in der Sicherung neu verbinden."
    if "error talking http to rclone" in low or ("context deadline exceeded" in low and "rclone" in low):
        return ("rclone hat nicht rechtzeitig geantwortet – meist wartet es auf den Anbieter, der drosselt (bei Google Drive: "
                "eigene Google-Client-ID eintragen und neu verbinden) oder nicht erreichbar ist." + (f" rclone: {cause[:250]}" if cause else ""))
    return (f"{last[:300]} – rclone: {cause[-400:]}" if cause else last[:400])


RCLONE_PROJECT = "202264815644"     # Google project of rclone's built-in client, shared by every rclone user


def throttled(stderr: str) -> str:
    """Google's 403/429 'quota exceeded' in plain words - and whose quota: rclone's shared one or the own project."""
    text = stderr or ""
    project = (re.search(r"project_number:(\d+)", text) or [None, ""])[1]
    metric = re.search(r"quota metric '([^']+)' and limit '([^']+)'", text)
    what = f" ({metric.group(2)})" if metric else ""
    if project == RCLONE_PROJECT or not project:
        return ("Google Drive drosselt die Anfragen: Die Sicherung nutzt noch die Kennung von rclone, die sich alle rclone-Nutzer teilen und "
                f"deren Kontingent{what} meist erschöpft ist. Abhilfe: eine eigene Google-Client-ID anlegen, in der Sicherung eintragen und "
                "neu mit Google Drive verbinden.")
    return (f"Google Drive drosselt die Anfragen deines eigenen Google-Projekts{what} – das legt sich meist nach ein paar Minuten. "
            "Später noch einmal starten.")


class Restic:
    def __init__(self, spec, tmpdir: str):
        if not shutil.which("restic"):
            die("restic ist nicht installiert.")
        password = spec.get("password")
        if not isinstance(password, str) or len(password) < 12:
            die("Das Passwort der Sicherung fehlt.")
        repo, env = target(spec, tmpdir)
        pw = os.path.join(tmpdir, "password")
        fd = os.open(pw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)
        self.env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C",
                    "RESTIC_REPOSITORY": repo, "RESTIC_PASSWORD_FILE": pw, "RESTIC_CACHE_DIR": os.path.join(STATE_DIR, "restic-cache"), **env}
        self.repo = repo
        # rclone: more time to answer the first request (restic's default is 1 minute, too short while
        # Drive backs off), and on Google Drive deleted packs bypass the trash, which counts against the quota
        self.opts = []
        if repo.startswith("rclone:"):
            # Drive: deleted packs bypass the trash (it counts against the quota); every pack from 1 MB up goes as a
            # resumable upload in 8 MB pieces, so a failed piece is sent again instead of failing the whole pack
            # (a plain upload can't be repeated once its data is streamed - rclone then answers restic with 500)
            extra = " --drive-use-trash=false --drive-upload-cutoff 1M --drive-chunk-size 8M" if (spec.get("target") or {}).get("kind") == "drive" else ""
            extra += " --low-level-retries 20"
            # rclone reports what it really uploaded and how fast, every 30 s (read by backup() for the log and the progress)
            self.opts = ["-o", "rclone.timeout=5m", "-o", f"rclone.args=serve restic --stdio --b2-hard-delete{extra} "
                         "--stats 30s --stats-one-line --stats-log-level NOTICE"]
        host = socket.gethostname()
        self.host = host if HOST_RE.match(host) else "server"

    def run(self, *args, check=True, timeout=None, capture=True):
        res = subprocess.run(["restic", *self.opts, *args], env=self.env, capture_output=capture, text=True, timeout=timeout, errors="replace")
        if check and res.returncode != 0:
            die(f"restic {args[0]}: {explain(res.stderr) if res.stderr else 'fehlgeschlagen'}")
        return res

    def preflight(self, attempts: int = 3) -> None:
        """rclone targets: one quick listing first, so a throttled or expired sign-in says so in plain words
        instead of restic's 'context deadline exceeded' after a minute. A short throttling of an own Google
        project is waited out (a minute, a few times); rclone's shared client is reported right away."""
        if not self.repo.startswith("rclone:"):
            return
        say("▶ Verbindung prüfen")
        for attempt in range(1, attempts + 1):
            try:
                res = subprocess.run(["rclone", "lsjson", "--dirs-only", "--max-depth", "1", "--retries", "1", "--low-level-retries", "3",
                                      "--contimeout", "30s", "--timeout", "90s", "hc:"], env=self.env, capture_output=True, text=True,
                                     timeout=180, errors="replace")
            except subprocess.TimeoutExpired:
                die("Das Ziel antwortet nicht innerhalb von 3 Minuten. Bei Google Drive ist das meist die Drosselung der gemeinsamen "
                    "rclone-Kennung – eine eigene Google-Client-ID anlegen, in der Sicherung eintragen und neu verbinden.")
            if res.returncode == 0:
                return
            err = res.stderr or ""
            own = re.search(r"project_number:(\d+)", err)
            limited = re.search(r"ratelimit|quota|\b429\b", err, re.I)
            if limited and own and own.group(1) != RCLONE_PROJECT and attempt < attempts:
                say(f"   Google drosselt gerade – neuer Versuch in 60 s ({attempt}/{attempts - 1})")
                time.sleep(60)
                continue
            die(f"Ziel nicht erreichbar: {explain(err)}")

    def ensure_repo(self) -> None:
        self.preflight()
        res = self.run("cat", "config", check=False, timeout=600)
        if res.returncode == 0:
            return
        err = res.stderr or ""
        if res.returncode == 10 or "does not exist" in err or "unable to open config file" in err.lower() or "Is there a repository" in err:
            say(f"▶ Neues Repository anlegen: {self.repo}")
            self.run("init", timeout=600)
            return
        if "wrong password" in err.lower() or res.returncode == 12:
            die("Das Repository gibt es schon, aber das Passwort passt nicht.")
        die(f"Ziel nicht erreichbar: {explain(err)}")

    def snapshots(self):
        self.preflight(attempts=1)          # the page waits for this one: no waiting out
        res = self.run("snapshots", "--json", "--host", self.host, "--tag", TAG, check=False, timeout=300)
        if res.returncode != 0:
            err = res.stderr or ""
            if res.returncode == 10 or "does not exist" in err or "unable to open config file" in err.lower():
                return {"exists": False, "snapshots": []}
            die(explain(err))
        items = json.loads(res.stdout or "[]") or []
        return {"exists": True, "snapshots": [{"id": s.get("short_id") or s["id"][:8], "time": s.get("time", ""), "paths": s.get("paths", []),
                                              "summary": {k: (s.get("summary") or {}).get(k) for k in ("total_files_processed", "total_bytes_processed", "data_added")}}
                                             for s in items][-200:]}


UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}
STATS_RE = re.compile(r"([\d.]+)\s*([KMGT]?i?B)\s*/\s*[\d.]+\s*[KMGT]?i?B,\s*(?:-|\d+%),\s*([\d.]+)\s*([KMGT]?i?B)/s")


def rclone_stats(line: str):
    """(bytes uploaded so far, bytes per second) from rclone's one-line stats, or None."""
    m = STATS_RE.search(line)
    if not m or m.group(2) not in UNITS or m.group(4) not in UNITS:
        return None
    return int(float(m.group(1)) * UNITS[m.group(2)]), int(float(m.group(3)) * UNITS[m.group(4)])


class StderrWatch:
    """restic's stderr, which also carries rclone's: kept for the error message at the end, while rclone's
    upload statistics go to the progress and its warnings (throttling, retries) to the log right away."""

    def __init__(self, prog, stream):
        self.prog, self.lines, self.last_seen = prog, [], {}
        self.last_stats = 0.0
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        for line in stream:
            self.lines.append(line)
            if len(self.lines) > 2000:
                del self.lines[:1000]
            st = rclone_stats(line)
            if st:
                self.prog.write(False, uploaded=st[0], upload_speed=st[1])
                if time.time() - self.last_stats > 120:
                    self.last_stats = time.time()
                    say(f"   rclone: {human(st[0])} hochgeladen · {human(st[1])}/s")
                continue
            if RCLONE_HINT.search(line) or "retry" in line.lower() or "pacer" in line.lower():
                limited = re.search(r"ratelimit|quota|\b429\b", line, re.I)
                # the same message again (or any throttling): at most once a minute
                key = "throttled" if limited else re.sub(r"^[\d/: ]+|\b[0-9a-f]{6,}\b", "", line)[:120]
                if time.time() - self.last_seen.get(key, 0) > 60:
                    self.last_seen[key] = time.time()
                    text = throttled(line) if limited else tidy(line)
                    say(f"   ⚠ {text[:500]}")

    def text(self) -> str:
        self.thread.join(timeout=10)
        return "".join(self.lines)


def human(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def backup(spec, r: Restic) -> None:
    prog = Progress(str(spec.get("job") or ""))
    paths, missing, excludes = sources(spec)
    if not paths:
        prog.write(phase="error")
        die("Keine der gewählten Quellen ist auf diesem Server vorhanden.")
    say(f"▶ Ziel: {r.repo}")
    for p in missing:
        say(f"   (übersprungen, gibt es hier nicht: {p})")
    try:
        r.ensure_repo()
    except SystemExit:
        prog.write(phase="error")
        raise
    r.run("unlock", check=False, timeout=300)          # stale locks of an interrupted run
    say("▶ Sichern: " + ", ".join(paths))
    prog.write(phase="backup", percent=0)
    args = ["backup", "--json", "--host", r.host, "--tag", TAG, "--exclude-caches", "--one-file-system"]
    for e in excludes:
        args += ["--exclude", e]
    start = time.time()
    # A status line every 2 s (restic's default without a terminal is one a minute)
    proc = subprocess.Popen(["nice", "-n", "15", "ionice", "-c3", "restic", *r.opts, *args, *paths], env={**r.env, "RESTIC_PROGRESS_FPS": "0.5"},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    # stderr is read alongside: a run with many unreadable files would otherwise fill the pipe and stall
    watch = StderrWatch(prog, proc.stderr)
    summary, last_print = {}, 0.0
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind = msg.get("message_type")
        if kind == "status":
            prog.write(False, percent=round((msg.get("percent_done") or 0) * 100, 1), files_done=msg.get("files_done", 0),
                       total_files=msg.get("total_files", 0), bytes_done=msg.get("bytes_done", 0), total_bytes=msg.get("total_bytes", 0),
                       remaining=msg.get("seconds_remaining"), errors=msg.get("error_count", 0))
        if kind == "status" and time.time() - last_print > 30:
            last_print = time.time()
            pct = msg.get("percent_done") or 0
            say(f"   {pct * 100:.0f} % · {msg.get('files_done', 0)} Dateien · {human(msg.get('bytes_done'))}")
        elif kind == "error":
            item = msg.get("item") or ""
            say(f"   ⚠ {item}: {(msg.get('error') or {}).get('message', '')}"[:300])
        elif kind == "summary":
            summary = msg
    rc = proc.wait()
    err_text = watch.text()
    if rc not in (0, 3):              # 3: some files could not be read, the snapshot exists anyway
        prog.write(phase="error")
        die(f"restic backup: {explain(err_text) if err_text.strip() else f'Fehler {rc}'}")
    prog.write(percent=100, remaining=0, backup_seconds=int(time.time() - start))
    if rc == 3:
        say("⚠ Einige Dateien ließen sich nicht lesen – der Stand ist trotzdem gespeichert.")
    keep = spec.get("keep") or {}
    flags = []
    for k in ("daily", "weekly", "monthly", "yearly"):           # sons, fathers, grandfathers, great-grandfathers
        try:
            n = int(keep.get(k) or 0)
        except (TypeError, ValueError):
            n = 0
        if 0 < n <= 1000:
            flags += [f"--keep-{k}", str(n)]
    if flags:
        say("▶ Alte Stände aufräumen")
        prog.write(phase="prune", prune_started=int(time.time()))
        r.run("forget", "--host", r.host, "--tag", TAG, "--prune", *flags, timeout=6 * 3600, capture=True)
    prog.write(phase="done")
    out = {"files": summary.get("total_files_processed"), "bytes": summary.get("total_bytes_processed"),
           "added": summary.get("data_added"), "snapshot": (summary.get("snapshot_id") or "")[:8],
           "seconds": int(time.time() - start), "partial": rc == 3, "total_seconds": int(time.time() - prog.state["started"]),
           "backup_seconds": prog.state.get("backup_seconds"), "prune_seconds": int(time.time() - prog.state.get("prune_started", time.time()))}
    say(f"✅ Gesichert: {out['files']} Dateien, {human(out['bytes'])} – neu übertragen {human(out['added'])} in {out['seconds']} s")
    say("HC-SUMMARY " + json.dumps(out))


def restore(spec, r: Restic, snap: str, path: str) -> None:
    if not SNAP_RE.match(snap) and snap != "latest":
        die("Ungültiger Stand.")
    if path and (not PATH_RE.match(path) or ".." in path.split("/")):
        die("Ungültiger Pfad.")
    os.makedirs(RESTORE_ROOT, mode=0o700, exist_ok=True)
    os.chmod(RESTORE_ROOT, 0o700)
    dest = os.path.join(RESTORE_ROOT, f"{time.strftime('%Y%m%d-%H%M%S')}-{snap}")
    say(f"▶ Stand {snap} wiederherstellen nach {dest}" + (f" (nur {path})" if path else ""))
    args = ["restore", snap, "--host", r.host, "--target", dest]
    if path:
        args += ["--include", path]
    r.run(*args, timeout=12 * 3600, capture=False)
    say(f"✅ Wiederhergestellt nach {dest}")
    say("   Der Ordner gehört root. Dateien von dort an ihren Platz kopieren, z. B. im Terminal mit sudo cp -a.")
    say("HC-RESTORED " + json.dumps({"dir": dest}))


def main() -> None:
    if os.geteuid() != 0:
        die("Muss als root laufen (pi-helper.sh offsite).")
    if len(sys.argv) < 2:
        die("Befehl fehlt.")
    cmd = sys.argv[1]
    try:
        spec = json.loads(sys.stdin.read(1024 * 1024) or "{}")
    except ValueError:
        die("Ungültige Angaben.")
    if not isinstance(spec, dict):
        die("Ungültige Angaben.")
    tmpdir = tempfile.mkdtemp(prefix="hc-offsite-")
    try:
        r = Restic(spec, tmpdir)
        if cmd == "backup":
            backup(spec, r)
        elif cmd == "snapshots":
            print(json.dumps(r.snapshots()))
        elif cmd == "restore":
            restore(spec, r, sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "")
        elif cmd == "check":
            subset = str(spec.get("subset") or "")
            m = re.match(r"^(\d{1,3})/(\d{1,3})$", subset)
            if not (m and 1 <= int(m.group(1)) <= int(m.group(2)) <= 100):
                subset = "2%"
            r.ensure_repo()
            say(f"▶ Repository prüfen: Struktur und Datenteil {subset}" if "/" in subset else "▶ Repository prüfen: Struktur und 2 % der Daten")
            r.run("check", f"--read-data-subset={subset}", timeout=12 * 3600, capture=False)
            say("✅ Repository in Ordnung")
        else:
            die("Unbekannter Befehl.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
