"""SQLite file for the security watchers (file integrity, sudo log, malware findings, Falco events)."""

import os
import sqlite3
import threading

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secwatch.db")
_lock = threading.RLock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS fim_files (path TEXT PRIMARY KEY, sha TEXT, mode INTEGER, uid INTEGER, gid INTEGER, size INTEGER, mtime INTEGER);
CREATE TABLE IF NOT EXISTS fim_findings (id INTEGER PRIMARY KEY, path TEXT, kind TEXT, level TEXT, cause TEXT, detail TEXT, ts INTEGER,
    state TEXT DEFAULT 'open', note TEXT DEFAULT '', closed_by TEXT DEFAULT '', closed_ts INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS fim_findings_state ON fim_findings(state, ts);
CREATE TABLE IF NOT EXISTS sudo_log (id INTEGER PRIMARY KEY, ts INTEGER, user TEXT, runas TEXT, tty TEXT, cwd TEXT, command TEXT, ok INTEGER,
    reason TEXT DEFAULT '', rule TEXT DEFAULT '', level TEXT DEFAULT '', own INTEGER DEFAULT 0, UNIQUE(ts, user, command, tty));
CREATE INDEX IF NOT EXISTS sudo_log_ts ON sudo_log(ts);
CREATE TABLE IF NOT EXISTS malware_findings (id INTEGER PRIMARY KEY, scanner TEXT, path TEXT, name TEXT, first_seen INTEGER, last_seen INTEGER,
    state TEXT DEFAULT 'open', note TEXT DEFAULT '', closed_by TEXT DEFAULT '', UNIQUE(scanner, path, name));
CREATE TABLE IF NOT EXISTS falco_events (id INTEGER PRIMARY KEY, ts INTEGER, last_ts INTEGER, rule TEXT, priority TEXT, container TEXT, process TEXT,
    user TEXT, output TEXT, count INTEGER DEFAULT 1, state TEXT DEFAULT 'open', closed_by TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS falco_events_ts ON falco_events(ts);
"""


class Db:
    """with Db() as c: one connection under one lock; commits at the end."""

    def __enter__(self) -> sqlite3.Connection:
        global _ready
        _lock.acquire()
        try:
            self.c = sqlite3.connect(PATH, timeout=30)
            self.c.row_factory = sqlite3.Row
            if not _ready:
                self.c.executescript(SCHEMA)
                os.chmod(PATH, 0o600)
                _ready = True
            return self.c
        except Exception:
            _lock.release()
            raise

    def __exit__(self, kind, value, tb) -> None:
        try:
            if kind is None:
                self.c.commit()
            self.c.close()
        finally:
            _lock.release()
