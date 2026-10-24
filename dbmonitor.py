"""
Database monitor (Container -> Datenbanken): finds PostgreSQL, MySQL and MariaDB containers and
shows version, uptime, connections, size per database with growth, long-running queries,
the largest tables and the state of the dumps.

Queries run inside the container (`docker exec`), with the credentials the container already
has in its environment (POSTGRES_USER, MYSQL_ROOT_PASSWORD / MARIADB_ROOT_PASSWORD ...), so no
password is stored here and no port has to be published. Checked every five minutes; a
change of state (connections nearly used up, a query running for minutes, a database not
reachable, an overdue or failed dump) becomes an event, so it reaches the event feed, the
status overview, e-mail and alerting.

Dumps (pg_dumpall / mysqldump, gzip) go to db-dumps/<container>/ next to the dashboard - on
demand or daily, the newest ones are kept. They run as tasks (tasks.py), so a dump carries on
when the dashboard restarts. Settings in settings.json "db_monitor", sizes per
day in "db_sizes", events in "db_events".
"""

import datetime
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import settings_store

logger = logging.getLogger("pi_dashboard.dbmonitor")

KEY = "db_monitor"
SIZES_KEY = "db_sizes"
EVENTS_KEY = "db_events"
DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db-dumps")
CHECK_INTERVAL = 300
SIZE_DAYS = 90
LONG_QUERY = 300          # seconds until a running query is a warning
IDLE_TX = 600             # seconds "idle in transaction" until it is a warning
CONN_WARN, CONN_CRIT = 80, 95
DUMP_TIMEOUT = 3600
ENGINES = {"postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB"}
IMAGE_RE = [
    (re.compile(r"(^|/)(postgres|postgis|timescaledb[\w-]*|pgvector|pgvecto-rs)(:|@|$)"), "postgres"),
    (re.compile(r"(^|/)mariadb(:|@|$)"), "mariadb"),
    (re.compile(r"(^|/)(mysql|percona[\w-]*)(:|@|$)"), "mysql"),
]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SYSTEM_SCHEMAS = ("mysql", "information_schema", "performance_schema", "sys")
_lock = threading.Lock()
_status: Dict[str, Dict[str, Any]] = {}     # container -> last check
_last_check = 0.0
_checking = False


class DbError(Exception):
    pass


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def engine_of(image: str) -> str:
    image = (image or "").lower()
    for rx, engine in IMAGE_RE:
        if rx.search(image):
            return engine
    return ""


def discover() -> List[Dict[str, Any]]:
    import containers
    out = []
    for c in containers.docker_list().get("containers", []):
        engine = engine_of(c["image"])
        if engine:
            out.append({"name": c["name"], "image": c["image"], "engine": engine, "state": c["state"],
                        "status": c["status"], "stack": c["stack"], "level": c["level"]})
    return out


def _config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return c if isinstance(c, dict) else {}


def _settings(name: str) -> Dict[str, Any]:
    s = (_config().get("containers") or {}).get(name) or {}
    return {"daily": bool(s.get("daily")), "hour": int(s.get("hour", 3)), "keep": int(s.get("keep", 7)),
            "ignore": bool(s.get("ignore"))}


def save_settings(name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    if not any(d["name"] == name for d in discover()):
        raise DbError("Datenbank-Container nicht gefunden.")
    try:
        hour, keep = int(fields.get("hour", 3)), int(fields.get("keep", 7))
    except (TypeError, ValueError):
        raise DbError("Uhrzeit und Anzahl als Zahl angeben.")
    if not 0 <= hour <= 23:
        raise DbError("Uhrzeit: 0 bis 23.")
    if not 1 <= keep <= 60:
        raise DbError("Aufbewahren: 1 bis 60 Dumps.")
    daily = bool(fields.get("daily"))
    with _lock:
        c = dict(_config())
        all_ = dict(c.get("containers") or {})
        if daily and not (all_.get(name) or {}).get("daily"):
            # Grace period: a freshly switched-on schedule is not "overdue" before its first run
            c["since"] = {**(c.get("since") or {}), name: int(time.time())}
        all_[name] = {"daily": daily, "hour": hour, "keep": keep, "ignore": bool(fields.get("ignore"))}
        c["containers"] = all_
        settings_store.set(KEY, c)
    _prune(name)
    return overview()


# ---------------------------------------------------------------------------
# Running SQL inside the container
# ---------------------------------------------------------------------------
def _exec(name: str, cmd: List[str], sql: str = "", timeout: float = 20) -> str:
    if not NAME_RE.match(name):
        raise DbError("Ungültiger Container-Name.")
    try:
        res = subprocess.run(["docker", "exec", "-i", name, *cmd], input=sql, capture_output=True, text=True,
                             timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        raise DbError("Die Datenbank antwortet nicht (Zeitüberschreitung).")
    except FileNotFoundError:
        raise DbError("Docker ist nicht installiert.")
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip().splitlines()
        raise DbError(_explain(err[-1] if err else f"Fehler {res.returncode}"))
    return res.stdout


def _explain(err: str) -> str:
    low = err.lower()
    if "access denied" in low or "password authentication failed" in low:
        return "Anmeldung an der Datenbank abgelehnt – im Container ist kein passendes Passwort hinterlegt."
    if "is not running" in low or "no such container" in low:
        return "Container läuft nicht."
    if "executable file not found" in low or "not found" in low and ("psql" in low or "mysql" in low):
        return "Im Container fehlt der Datenbank-Client."
    return err[:300]


PG_ENV = 'export PGUSER="${POSTGRES_USER:-postgres}"; '
MY_ENV = ('if [ -n "${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}" ]; then export MYSQL_PWD="${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}" U=root; '
          'else export MYSQL_PWD="${MARIADB_PASSWORD:-$MYSQL_PASSWORD}" U="${MARIADB_USER:-${MYSQL_USER:-root}}"; fi; '
          'C=$(command -v mariadb || command -v mysql); ')


def _rows(out: str) -> List[List[str]]:
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def _pg_sql(name: str, sql: str, db: str = "postgres") -> List[List[str]]:
    if not re.match(r"^[A-Za-z0-9_.-]{1,63}$", db):
        raise DbError("Ungültiger Datenbankname.")
    return _rows(_exec(name, ["sh", "-c", PG_ENV + f'exec psql -d "{db}" -AtX -q -F "$(printf \'\\t\')" -v ON_ERROR_STOP=1 -f -'], sql))


def _my_sql(name: str, sql: str) -> List[List[str]]:
    return _rows(_exec(name, ["sh", "-c", MY_ENV + 'exec "$C" -u"$U" -N -B'], sql))


PG_QUERY = r"""
SELECT 'info', current_setting('server_version'), extract(epoch FROM pg_postmaster_start_time())::bigint,
       current_setting('max_connections'), (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend');
SELECT 'db', datname, pg_database_size(datname), numbackends, coalesce(blks_hit, 0), coalesce(blks_read, 0), deadlocks
  FROM pg_stat_database WHERE datname IS NOT NULL AND datname NOT IN ('template0', 'template1');
SELECT 'q', pid, coalesce(datname, ''), coalesce(usename, ''), state,
       extract(epoch FROM now() - coalesce(xact_start, query_start))::bigint,
       left(regexp_replace(coalesce(query, ''), '\s+', ' ', 'g'), 300)
  FROM pg_stat_activity
 WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(xact_start, query_start) > interval '30 seconds'
 ORDER BY 6 DESC LIMIT 10;
"""
PG_TABLES = """
SELECT 't', schemaname || '.' || relname, pg_total_relation_size(relid), n_live_tup, n_dead_tup
  FROM pg_stat_user_tables ORDER BY 3 DESC LIMIT 8;
"""
MY_QUERY = f"""
SELECT 'info', VERSION(), @@max_connections;
SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime', 'Threads_connected', 'Slow_queries', 'Max_used_connections');
SELECT 'db', s.schema_name, COALESCE(SUM(t.data_length + t.index_length), 0)
  FROM information_schema.SCHEMATA s LEFT JOIN information_schema.TABLES t ON t.table_schema = s.schema_name
 WHERE s.schema_name NOT IN {SYSTEM_SCHEMAS} GROUP BY s.schema_name;
SELECT 'q', ID, COALESCE(DB, ''), USER, COMMAND, TIME, LEFT(REPLACE(REPLACE(COALESCE(INFO, ''), '\\n', ' '), '\\t', ' '), 300)
  FROM information_schema.PROCESSLIST
 WHERE COMMAND NOT IN ('Sleep', 'Daemon', 'Binlog Dump') AND TIME >= 30 AND ID <> CONNECTION_ID()
 ORDER BY TIME DESC LIMIT 10;
SELECT 't', CONCAT(table_schema, '.', table_name), COALESCE(data_length + index_length, 0), COALESCE(table_rows, 0), 0
  FROM information_schema.TABLES WHERE table_schema NOT IN {SYSTEM_SCHEMAS} AND table_type = 'BASE TABLE'
 ORDER BY 3 DESC LIMIT 8;
"""


def _num(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _query_pg(name: str) -> Dict[str, Any]:
    r: Dict[str, Any] = {"databases": [], "queries": [], "tables": []}
    hit = read = 0
    for row in _pg_sql(name, PG_QUERY):
        tag = row[0]
        if tag == "info" and len(row) >= 5:
            r.update(version=row[1].split(" ")[0], started=_num(row[2]), max_connections=_num(row[3]), connections=_num(row[4]))
        elif tag == "db" and len(row) >= 7:
            r["databases"].append({"name": row[1], "size": _num(row[2]), "connections": _num(row[3]), "deadlocks": _num(row[6])})
            hit, read = hit + _num(row[4]), read + _num(row[5])
        elif tag == "q" and len(row) >= 7:
            r["queries"].append({"pid": _num(row[1]), "db": row[2], "user": row[3], "state": row[4],
                                 "seconds": _num(row[5]), "query": row[6]})
    r["cache_hit"] = round(hit / (hit + read) * 100, 1) if hit + read > 10000 else None
    # Largest tables of the biggest own database (not the maintenance db "postgres")
    own = sorted((d for d in r["databases"] if d["name"] != "postgres"), key=lambda d: -d["size"])
    if own:
        r["tables_db"] = own[0]["name"]
        r["tables"] = [{"name": t[1], "size": _num(t[2]), "rows": _num(t[3]), "dead": _num(t[4])}
                       for t in _pg_sql(name, PG_TABLES, own[0]["name"]) if t[0] == "t" and len(t) >= 5]
    return r


def _query_my(name: str) -> Dict[str, Any]:
    r: Dict[str, Any] = {"databases": [], "queries": [], "tables": [], "cache_hit": None}
    status = {}
    for row in _my_sql(name, MY_QUERY):
        tag = row[0]
        if tag == "info" and len(row) >= 3:
            r.update(version=row[1].split("-")[0], max_connections=_num(row[2]))
        elif tag in ("Uptime", "Threads_connected", "Slow_queries", "Max_used_connections") and len(row) >= 2:
            status[tag] = _num(row[1])
        elif tag == "db" and len(row) >= 3:
            r["databases"].append({"name": row[1], "size": _num(row[2]), "connections": None, "deadlocks": None})
        elif tag == "q" and len(row) >= 7:
            r["queries"].append({"pid": _num(row[1]), "db": row[2], "user": row[3], "state": row[4].lower(),
                                 "seconds": _num(row[5]), "query": row[6] if row[6] != "NULL" else ""})
        elif tag == "t" and len(row) >= 5:
            r["tables"].append({"name": row[1], "size": _num(row[2]), "rows": _num(row[3]), "dead": None})
    r["connections"] = status.get("Threads_connected", 0)
    r["started"] = int(time.time()) - status["Uptime"] if "Uptime" in status else 0
    r["slow_queries"] = status.get("Slow_queries")
    r["max_used_connections"] = status.get("Max_used_connections")
    return r


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _problems(db: Dict[str, Any], st: Dict[str, Any], dumps: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """[{key, level, text}] - the key decides whether a problem is new (event) or still the same one."""
    out = []
    name = db["name"]
    if db["state"] != "running":
        out.append({"key": "down", "level": "critical" if db["level"] == "error" else "warning", "text": f"Datenbank {name} läuft nicht"})
        return out
    if st.get("error"):
        out.append({"key": "error", "level": "warning", "text": f"Datenbank {name} nicht abfragbar – {st['error']}"})
    mx, cur = st.get("max_connections") or 0, st.get("connections") or 0
    if mx:
        pct = cur / mx * 100
        if pct >= CONN_WARN:
            out.append({"key": "connections", "level": "critical" if pct >= CONN_CRIT else "warning",
                        "text": f"Datenbank {name}: {cur} von {mx} Verbindungen belegt"})
    long_q = [q for q in st.get("queries", []) if q["seconds"] >= LONG_QUERY and q["state"] != "idle in transaction"]
    if long_q:
        out.append({"key": "long_query", "level": "warning",
                    "text": f"Datenbank {name}: Abfrage läuft seit {_fmt_secs(long_q[0]['seconds'])}"})
    idle = [q for q in st.get("queries", []) if q["state"].startswith("idle in transaction") and q["seconds"] >= IDLE_TX]
    if idle:
        out.append({"key": "idle_tx", "level": "warning",
                    "text": f"Datenbank {name}: Transaktion seit {_fmt_secs(idle[0]['seconds'])} offen ohne Aktivität"})
    if cfg["daily"]:
        last = dumps["files"][0]["ts"] if dumps["files"] else 0
        if dumps.get("error"):
            out.append({"key": "dump_failed", "level": "warning", "text": f"Dump von {name} fehlgeschlagen – {dumps['error']}"})
        elif time.time() - last > 26 * 3600 and time.time() - cfg.get("since", 0) > 26 * 3600:
            out.append({"key": "dump_overdue", "level": "warning",
                        "text": f"Dump von {name} überfällig" + (f" – letzter vor {_fmt_secs(time.time() - last)}" if last else "")})
    return out


def _fmt_secs(s: float) -> str:
    s = int(s)
    if s < 120:
        return f"{s} Sek."
    if s < 7200:
        return f"{s // 60} Min."
    if s < 172800:
        return f"{s // 3600} Std."
    return f"{s // 86400} Tagen"


def _record_sizes(name: str, databases: List[Dict[str, Any]]) -> None:
    today = time.strftime("%Y-%m-%d")
    sizes = settings_store.get(SIZES_KEY) or {}
    changed = False
    for d in databases:
        key = f"{name}/{d['name']}"
        series = sizes.get(key) or []
        if series and series[-1][0] == today:
            if series[-1][1] != d["size"]:
                series[-1][1] = d["size"]
                changed = True
        else:
            series.append([today, d["size"]])
            changed = True
        sizes[key] = series[-SIZE_DAYS:]
    if changed:
        settings_store.set(SIZES_KEY, sizes)


def _growth(name: str, db: str, size: int) -> Optional[int]:
    """Change over the last 7 days in bytes (None until there is a value that old)."""
    series = (settings_store.get(SIZES_KEY) or {}).get(f"{name}/{db}") or []
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    old = [v for d, v in series if d <= cutoff]
    return size - old[-1] if old else None


def _event(name: str, key: str, level: str, title: str, detail: str = "") -> None:
    with _lock:
        events = (settings_store.get(EVENTS_KEY, []) or [])[-199:]
        events.append({"ts": int(time.time()), "level": level, "title": title, "detail": detail, "container": name,
                       "key": f"db:{name}:{key}"})
        settings_store.set(EVENTS_KEY, events)


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def check_one(db: Dict[str, Any]) -> Dict[str, Any]:
    name = db["name"]
    st: Dict[str, Any] = {"checked": int(time.time())}
    if db["state"] == "running":
        try:
            st.update(_query_pg(name) if db["engine"] == "postgres" else _query_my(name))
            _record_sizes(name, st["databases"])
        except DbError as e:
            st["error"] = str(e)
    cfg = _settings(name)
    prev = _status.get(name, {})
    problems = [] if cfg["ignore"] else _problems(db, st, dump_files(name), {**cfg, "since": (_config().get("since") or {}).get(name, 0)})
    st["problems"] = problems
    # Events only for changes, and not on the first check after a restart (nothing to compare)
    if "problems" in prev:
        before = {p["key"]: p for p in prev["problems"]}
        now_keys = {p["key"] for p in problems}
        for p in problems:
            if p["key"] not in before or LEVELS[p["level"]] > LEVELS[before[p["key"]]["level"]]:
                _event(name, p["key"], p["level"], p["text"])
        for key, p in before.items():
            if key not in now_keys:
                _event(name, key, "good", RECOVERED.get(key, "Datenbank {name} wieder in Ordnung").format(name=name))
    _status[name] = st
    return st


LEVELS = {"good": 0, "info": 1, "warning": 2, "critical": 3}
RECOVERED = {"down": "Datenbank {name} läuft wieder", "error": "Datenbank {name} wieder abfragbar",
             "connections": "Datenbank {name}: Verbindungen wieder im Rahmen", "long_query": "Datenbank {name}: lange Abfrage beendet",
             "idle_tx": "Datenbank {name}: offene Transaktion beendet", "dump_failed": "Dump von {name} wieder erfolgreich",
             "dump_overdue": "Dump von {name} wieder aktuell"}


def check_all() -> None:
    global _last_check, _checking
    if _checking:
        return
    _checking = True
    try:
        found = discover()
        for db in found:
            try:
                check_one(db)
            except Exception as e:
                logger.warning(f"DB check {db['name']}: {e}")
            _dump_if_due(db)
        for gone in set(_status) - {d["name"] for d in found}:
            _status.pop(gone, None)
        _last_check = time.time()
    finally:
        _checking = False


def check_if_due() -> None:
    if time.time() - _last_check >= CHECK_INTERVAL and not _checking:
        threading.Thread(target=check_all, daemon=True).start()


# ---------------------------------------------------------------------------
# Dumps
# ---------------------------------------------------------------------------
def _dump_dir(name: str) -> str:
    if not NAME_RE.match(name):
        raise DbError("Ungültiger Container-Name.")
    return os.path.join(DUMP_DIR, name)


def dump_files(name: str) -> Dict[str, Any]:
    folder = _dump_dir(name)
    files = []
    try:
        for f in os.listdir(folder):
            if f.endswith(".sql.gz"):
                p = os.path.join(folder, f)
                st = os.stat(p)
                files.append({"name": f, "path": p, "size": st.st_size, "ts": int(st.st_mtime)})
    except FileNotFoundError:
        pass
    files.sort(key=lambda f: -f["ts"])
    last = (settings_store.get(KEY) or {}).get("last_dump", {}).get(name) or {}
    return {"folder": folder, "files": files, "total": sum(f["size"] for f in files),
            "error": last.get("error", "") if last.get("ts", 0) >= (files[0]["ts"] if files else 0) else "",
            "last_try": last.get("ts", 0)}


def _dump_command(db: Dict[str, Any], tmp: str, target: str) -> str:
    """Shell pipeline of a dump task: dump inside the container, gzip on the host, then rename."""
    if db["engine"] == "postgres":
        inner = PG_ENV + "exec pg_dumpall --clean --if-exists"
    else:
        inner = (MY_ENV + "D=$(command -v mariadb-dump || command -v mysqldump); "
                 'exec "$D" -u"$U" --all-databases --single-transaction --routines --events --triggers')
    q = shlex.quote
    return (f"umask 077; mkdir -p {q(os.path.dirname(target))}; "
            f"docker exec {q(db['name'])} sh -c {q(inner)} | gzip -6 > {q(tmp)} || {{ rm -f {q(tmp)}; exit 1; }}; "
            f"if [ \"$(gzip -dc {q(tmp)} | head -c 1 | wc -c)\" != 1 ]; then echo 'Der Dump ist leer.'; rm -f {q(tmp)}; exit 1; fi; "
            f"mv {q(tmp)} {q(target)} && echo \"Gespeichert: $(du -h {q(target)} | cut -f1)\"")


def _dump_done(task: Dict[str, Any]) -> None:
    d = task.get("data") or {}
    name = d.get("name", "")
    error = ""
    if task["state"] != "ok":
        try:
            os.remove(d.get("tmp", ""))
        except OSError:
            pass
        import tasks
        lines = [l for l in tasks.tail(task["id"], 4000).splitlines() if l.strip() and not l.startswith("$ ")]
        error = _explain(lines[-1]) if lines else tasks.STATES.get(task["state"], "Dump fehlgeschlagen.")
    elif name:
        _prune(name)
    with _lock:
        c = dict(_config())
        last = dict(c.get("last_dump") or {})
        last[name] = {"ts": int(task.get("ended") or time.time()), "error": error}
        c["last_dump"] = last
        settings_store.set(KEY, c)


def _prune(name: str) -> None:
    keep = _settings(name)["keep"]
    for f in dump_files(name)["files"][keep:]:
        try:
            os.remove(f["path"])
        except OSError as e:
            logger.warning(f"Could not remove old dump {f['path']}: {e}")


def _register_hooks() -> None:
    import tasks
    tasks.on_done("dbdump", _dump_done)


_register_hooks()


def start_dump(name: str, user: str = "") -> Dict[str, Any]:
    import tasks
    db = next((d for d in discover() if d["name"] == name), None)
    if not db:
        raise DbError("Datenbank-Container nicht gefunden.")
    if db["state"] != "running":
        raise DbError("Der Container läuft nicht.")
    target = os.path.join(_dump_dir(name), f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.sql.gz")
    tmp = target + ".part"
    try:
        task = tasks.start("dbdump", f"Datenbank-Dump: {name}", [{"shell": _dump_command(db, tmp, target),
                                                                   "show": f"{'pg_dumpall' if db['engine'] == 'postgres' else 'mysqldump'} | gzip > {os.path.basename(target)}"}],
                           user=user, key=f"dbdump:{name}", timeout=DUMP_TIMEOUT, data={"name": name, "tmp": tmp, "target": target})
    except tasks.TaskError:
        raise DbError("Für diese Datenbank läuft schon ein Dump.")
    return get_job(task["id"])


def get_job(job_id: str) -> Dict[str, Any]:
    import tasks
    try:
        task = tasks.get(job_id)
    except tasks.TaskError:
        raise DbError("Auftrag nicht gefunden.")
    d = task.get("data") or {}
    if task["kind"] != "dbdump":
        raise DbError("Auftrag nicht gefunden.")
    job = {"id": task["id"], "name": d.get("name", ""), "state": {"running": "running", "ok": "done"}.get(task["state"], "error"),
           "started": task["started"], "finished": task.get("ended"), "user": task.get("user", ""), "task": task["id"]}
    try:
        job["bytes"] = os.path.getsize(d["tmp"]) if task["state"] == "running" else 0
    except (OSError, KeyError):
        job["bytes"] = 0
    if job["state"] == "done":
        job["file"] = os.path.basename(d.get("target", ""))
        try:
            job["size"] = os.path.getsize(d["target"])
        except (OSError, KeyError):
            job["size"] = 0
    elif job["state"] == "error":
        import tasks as t
        lines = [l for l in t.tail(task["id"], 4000).splitlines() if l.strip() and not l.startswith("$ ")]
        job["error"] = _explain(lines[-1]) if lines else task["state_label"]
    return job


def _running_dump(name: str) -> Optional[Dict[str, Any]]:
    import tasks
    t = next((t for t in tasks.overview(limit=30, kind="dbdump", key=f"dbdump:{name}")["tasks"] if t["state"] == "running"), None)
    return get_job(t["id"]) if t else None


def _dump_if_due(db: Dict[str, Any]) -> None:
    cfg = _settings(db["name"])
    if not cfg["daily"] or cfg["ignore"] or db["state"] != "running":
        return
    lt = time.localtime()
    if lt.tm_hour < cfg["hour"]:
        return
    files = dump_files(db["name"])
    start_of_slot = time.mktime(lt[:3] + (cfg["hour"], 0, 0, 0, 0, -1))
    if (files["files"] and files["files"][0]["ts"] >= start_of_slot) or files["last_try"] >= start_of_slot:
        return
    try:
        start_dump(db["name"], "Zeitplan")
    except DbError as e:
        logger.warning(f"Scheduled dump {db['name']}: {e}")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview() -> Dict[str, Any]:
    items = []
    for db in discover():
        st = _status.get(db["name"], {})
        cfg = _settings(db["name"])
        dumps = dump_files(db["name"])
        for d in st.get("databases", []):
            d["growth_7d"] = _growth(db["name"], d["name"], d["size"])
        items.append({**db, "engine_label": ENGINES[db["engine"]], **{k: v for k, v in st.items()}, "settings": cfg,
                      "dumps": {**dumps, "files": [{k: v for k, v in f.items() if k != "path"} for f in dumps["files"][:10]]},
                      "dumping": _running_dump(db["name"])})
    order = {"critical": 0, "warning": 1}
    items.sort(key=lambda i: (min((order.get(p["level"], 2) for p in i.get("problems", [])), default=2), i["name"]))
    return {"databases": items, "checked": int(_last_check), "checking": _checking,
            "thresholds": {"long_query": LONG_QUERY, "idle_tx": IDLE_TX, "conn_warn": CONN_WARN}}


def refresh() -> Dict[str, Any]:
    check_all()
    return overview()


def alerts() -> List[Dict[str, Any]]:
    return [{"level": p["level"], "text": p["text"]} for st in _status.values() for p in st.get("problems", [])]

