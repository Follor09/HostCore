"""
Self-test (Einstellungen -> Selbstdiagnose): checks the dashboard's own installation and says
how to fix what is wrong.

- Service & updates: systemd service, update timer and its last run, root copy for the
  updater, sudo rules, local changes that the next update would discard, GitHub access
- Files: owner-only rights of files with passwords and tokens, files owned by someone else,
  free space, size and integrity of history.db, whether the monitor still records
- Environment: Python packages from requirements.txt, time sync, Docker / Kubernetes access
- Connections: remote Pis, e-mail delivery, MCP server, GeoIP database age

Runs on request and once a day; problems show up in the status summary. Two repairs need no
root and are offered as buttons: tighten file rights and compact the history database.
Everything else comes with the command to run.
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

logger = logging.getLogger("pi_dashboard.selftest")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_KEY = "selftest"
CHECK_INTERVAL = 24 * 3600
ROOT_SRC = "/var/lib/pi-dashboard/src"
SUDOERS = "/etc/sudoers.d/pi-dashboard-restart"
SENSITIVE = ["auth.json", "settings.json", "agent.json", "vulns.json", "mcp_calls.json", ".env", ".git/github-credentials"]
JSON_FILES = ["settings.json", "auth.json", "board_history.json", "wiki.json"]
GROUPS = ["Dienst & Updates", "Rechte & Dateien", "Umgebung", "Verbindungen"]
_lock = threading.Lock()
_running = {"active": False}


def _r(cid: str, group: str, title: str, state: str, detail: str = "", fix: str = "", repair: str = "") -> Dict[str, Any]:
    return {"id": cid, "group": group, "title": title, "state": state, "detail": detail, "fix": fix, "repair": repair}


def _run(cmd: List[str], timeout: float = 10, cwd: Optional[str] = None, env: Optional[dict] = None) -> tuple:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        # rstrip only: `git status --porcelain` starts lines with a space
        return res.returncode, (res.stdout or "").rstrip(), (res.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "nicht gefunden"
    except subprocess.TimeoutExpired:
        return 124, "", "Zeitüberschreitung"


def _systemd() -> bool:
    return bool(shutil.which("systemctl")) and os.path.isdir("/run/systemd/system")


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# ---------------------------------------------------------------------------
# Service & updates
# ---------------------------------------------------------------------------
def _service_checks() -> List[Dict[str, Any]]:
    g = "Dienst & Updates"
    out = []
    if not _systemd():
        return [_r("systemd", g, "systemd", "skip", "Kein systemd – Dienst und Update-Timer werden nicht geprüft.")]
    _, state, _ = _run(["systemctl", "is-active", "raspberry-dashboard"])
    out.append(_r("service", g, "Dashboard-Dienst", "ok" if state == "active" else "warning",
                  "läuft als systemd-Dienst" if state == "active" else f"Dienst raspberry-dashboard ist „{state or 'unbekannt'}“ – das Dashboard läuft wohl von Hand gestartet und startet nach einem Neustart nicht von selbst.",
                  "" if state == "active" else f"{BASE_DIR}/install.sh"))
    _, timer, _ = _run(["systemctl", "is-active", "pi-dashboard-update.timer"])
    if timer != "active":
        out.append(_r("timer", g, "Auto-Update-Timer", "warning", "Der Timer ist aus – Commits kommen nicht mehr automatisch auf den Pi.",
                      "sudo systemctl enable --now pi-dashboard-update.timer"))
    else:
        _, props, _ = _run(["systemctl", "show", "pi-dashboard-update.service", "-p", "Result", "-p", "ExecMainStatus", "-p", "ExecMainExitTimestamp"])
        p = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
        ok = p.get("Result", "success") == "success" and p.get("ExecMainStatus", "0") in ("0", "")
        last = p.get("ExecMainExitTimestamp", "")
        out.append(_r("timer", g, "Auto-Update-Timer", "ok" if ok else "warning",
                      ("aktiv" + (f", letzter Lauf {last}" if last else "")) if ok else f"Letzter Lauf fehlgeschlagen (Status {p.get('ExecMainStatus', '?')}, {p.get('Result', '?')}).",
                      "" if ok else "journalctl -u pi-dashboard-update -n 50 --no-pager"))
    # Root copy that update.sh, apt-run.sh and the helper run from
    try:
        st = os.stat(ROOT_SRC)
        if st.st_uid != 0:
            out.append(_r("rootcopy", g, "Root-Kopie für Updates", "critical", f"{ROOT_SRC} gehört nicht root – damit könnte der Dashboard-Benutzer root werden.",
                          f"{BASE_DIR}/install.sh"))
        elif st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            out.append(_r("rootcopy", g, "Root-Kopie für Updates", "critical", f"{ROOT_SRC} ist für andere beschreibbar.", f"sudo chmod -R go-w {ROOT_SRC}"))
        else:
            out.append(_r("rootcopy", g, "Root-Kopie für Updates", "ok", f"{ROOT_SRC} gehört root und ist geschützt"))
    except FileNotFoundError:
        out.append(_r("rootcopy", g, "Root-Kopie für Updates", "warning", f"{ROOT_SRC} fehlt – Updates, Firewall und Paketverwaltung funktionieren nicht.",
                      f"{BASE_DIR}/install.sh"))
    out.append(_r("sudo", g, "sudo-Regeln", "ok" if os.path.exists(SUDOERS) else "warning",
                  "eng begrenzte Regeln vorhanden" if os.path.exists(SUDOERS) else "Die sudo-Regeln fehlen – Dienste steuern, apt, Aufräumen und CPU-Modus gehen nicht.",
                  "" if os.path.exists(SUDOERS) else f"{BASE_DIR}/install.sh"))
    return out


def _git_checks() -> List[Dict[str, Any]]:
    g = "Dienst & Updates"
    if not os.path.isdir(os.path.join(BASE_DIR, ".git")) or not shutil.which("git"):
        return [_r("git", g, "Git-Arbeitskopie", "warning", "Kein Git-Repository – Auto-Update ist nicht möglich.")]
    out = []
    rc, changed, _ = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=BASE_DIR)
    files = [ln[3:] for ln in changed.splitlines() if ln.strip()]
    if rc == 0:
        out.append(_r("git-local", g, "Lokale Änderungen", "info" if files else "ok",
                      f"{len(files)} geänderte Datei(en), die das nächste Auto-Update verwirft: {', '.join(files[:5])}{' …' if len(files) > 5 else ''}" if files
                      else "keine – nichts geht beim nächsten Update verloren",
                      f"cd {BASE_DIR} && git diff" if files else ""))
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    rc, _, err = _run(["git", "ls-remote", "--heads", "origin"], timeout=20, cwd=BASE_DIR, env=env)
    private = os.path.exists(os.path.join(BASE_DIR, ".git", "github-credentials"))
    if rc == 0:
        out.append(_r("git-remote", g, "GitHub-Zugriff", "ok", "Repository erreichbar" + (" (mit Token)" if private else "")))
    else:
        auth = re.search(r"Authentication|could not read Username|403|401", err or "", re.I)
        out.append(_r("git-remote", g, "GitHub-Zugriff", "critical" if auth else "warning",
                      ("Token abgelehnt oder abgelaufen – Updates kommen nicht mehr an." if auth else f"GitHub nicht erreichbar: {(err or '').splitlines()[-1][:160] if err else 'unbekannter Fehler'}"),
                      f"{BASE_DIR}/set-token.sh" if auth else ""))
    return out


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def _too_open() -> List[str]:
    out = []
    for name in SENSITIVE:
        path = os.path.join(BASE_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mode & 0o077:
            out.append(name)
    return out


def _file_checks() -> List[Dict[str, Any]]:
    g = "Rechte & Dateien"
    out = []
    loose = _too_open()
    out.append(_r("perms", g, "Dateien mit Zugangsdaten", "warning" if loose else "ok",
                  f"Für andere Benutzer lesbar: {', '.join(loose)} – darin stehen Passwörter, Tokens oder Sitzungsschlüssel." if loose
                  else "nur für den Dashboard-Benutzer lesbar",
                  f"chmod 600 {' '.join(os.path.join(BASE_DIR, n) for n in loose)}" if loose else "", "perms" if loose else ""))
    me = os.getuid() if hasattr(os, "getuid") else None
    foreign = []
    if me is not None:
        for entry in os.scandir(BASE_DIR):
            if entry.name in ("venv", ".git") or entry.is_symlink():
                continue
            try:
                if entry.stat(follow_symlinks=False).st_uid != me:
                    foreign.append(entry.name)
            except OSError:
                continue
    if foreign:
        out.append(_r("owner", g, "Dateibesitzer", "warning",
                      f"Gehören einem anderen Benutzer (z.B. nach einem Start mit sudo): {', '.join(sorted(foreign)[:6])}{' …' if len(foreign) > 6 else ''} – Updates oder Speichern können scheitern.",
                      f"sudo chown -R $(stat -c %U {BASE_DIR}): {BASE_DIR}"))
    else:
        out.append(_r("owner", g, "Dateibesitzer", "ok", "alle Dateien gehören dem Dashboard-Benutzer"))
    bad_json = []
    for name in JSON_FILES:
        path = os.path.join(BASE_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                json.load(f)
        except (OSError, ValueError):
            bad_json.append(name)
    if bad_json:
        out.append(_r("json", g, "Einstellungsdateien", "critical", f"Nicht lesbar oder beschädigt: {', '.join(bad_json)} – Einstellungen gehen verloren.",
                      f"ls -l {BASE_DIR}/*.json*"))
    else:
        size = os.path.getsize(os.path.join(BASE_DIR, "settings.json")) if os.path.exists(os.path.join(BASE_DIR, "settings.json")) else 0
        out.append(_r("json", g, "Einstellungsdateien", "warning" if size > 8 * 1024 * 1024 else "ok",
                      f"settings.json ist {_fmt_bytes(size)} groß – Datenaufbewahrung prüfen." if size > 8 * 1024 * 1024
                      else f"lesbar, settings.json {_fmt_bytes(size)}"))
    du = shutil.disk_usage(BASE_DIR)
    free_state = "critical" if du.free < 500 * 1024 ** 2 else "warning" if du.free < 2 * 1024 ** 3 else "ok"
    out.append(_r("space", g, "Freier Speicher", free_state, f"{_fmt_bytes(du.free)} frei auf dem Laufwerk des Dashboards",
                  "" if free_state == "ok" else "System-Werkzeuge → Aufräumen"))
    out.extend(_db_checks())
    return out


def _db_checks() -> List[Dict[str, Any]]:
    g = "Rechte & Dateien"
    import history
    path = history.DB_PATH
    if not os.path.exists(path):
        return [_r("db", g, "Verlauf-Datenbank", "info", "history.db existiert noch nicht.")]
    out = []
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
            pages, free = conn.execute("PRAGMA page_count").fetchone()[0], conn.execute("PRAGMA freelist_count").fetchone()[0]
            last = conn.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return [_r("db", g, "Verlauf-Datenbank", "critical", f"Nicht lesbar: {e}", f"ls -l {path}")]
    size = os.path.getsize(path)
    waste = free / pages if pages else 0
    if check != "ok":
        out.append(_r("db", g, "Verlauf-Datenbank", "critical", f"Integritätsprüfung meldet: {str(check)[:160]}",
                      "Einstellungen → Verlauf → „Verlauf löschen“"))
    else:
        compact = waste > 0.25 and size > 20 * 1024 ** 2
        out.append(_r("db", g, "Verlauf-Datenbank", "info" if compact else "ok",
                      f"{_fmt_bytes(size)}, {waste:.0%} ungenutzt – Verdichten gibt Platz frei." if compact else f"in Ordnung, {_fmt_bytes(size)}",
                      "", "vacuum" if compact else ""))
    age = time.time() - (last or 0)
    out.append(_r("sampling", g, "Messwerte", "ok" if age < 300 else "warning",
                  "werden jede Minute gespeichert" if age < 300 else
                  ("Noch keine Messwerte gespeichert." if not last else f"Letzter Messwert vor {int(age // 60)} Min. – die Aufzeichnung hängt."),
                  "" if age < 300 else "sudo systemctl restart raspberry-dashboard"))
    return out


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def _requirements() -> List[tuple]:
    out = []
    try:
        with open(os.path.join(BASE_DIR, "requirements.txt")) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(==|>=)?\s*([\w.]+)?", line)
                if m:
                    out.append((m.group(1), m.group(2) or "", m.group(3) or ""))
    except OSError:
        pass
    return out


def _env_checks() -> List[Dict[str, Any]]:
    g = "Umgebung"
    out = []
    from importlib import metadata
    missing, wrong = [], []
    for name, op, ver in _requirements():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if op == "==" and ver and have != ver:
            wrong.append(f"{name} {have} (erwartet {ver})")
    py = ".".join(map(str, sys.version_info[:3]))
    if missing or wrong:
        out.append(_r("python", g, "Python-Pakete", "critical" if missing else "warning",
                      "; ".join(filter(None, [f"fehlen: {', '.join(missing)}" if missing else "", f"abweichend: {', '.join(wrong)}" if wrong else ""])),
                      f"{BASE_DIR}/venv/bin/pip install -r {BASE_DIR}/requirements.txt"))
    else:
        out.append(_r("python", g, "Python-Pakete", "ok", f"alle aus requirements.txt installiert (Python {py})"))
    if shutil.which("timedatectl"):
        _, synced, _ = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        out.append(_r("time", g, "Uhrzeit", "ok" if synced == "yes" else "warning",
                      "per NTP synchronisiert" if synced == "yes" else "Nicht synchronisiert – Zeitstempel, Anmeldungen und Zertifikatsprüfungen können falsch sein.",
                      "" if synced == "yes" else "sudo timedatectl set-ntp true"))
    if shutil.which("docker"):
        rc, _, err = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
        denied = "permission denied" in (err or "").lower()
        out.append(_r("docker", g, "Docker-Zugriff", "ok" if rc == 0 else "warning",
                      "Docker erreichbar" if rc == 0 else ("Kein Zugriff auf den Docker-Socket – Container-Bereiche bleiben leer." if denied else f"Docker antwortet nicht: {(err or '').splitlines()[-1][:140] if err else rc}"),
                      "sudo usermod -aG docker $USER && sudo systemctl restart raspberry-dashboard" if denied else ("sudo systemctl status docker" if rc else "")))
    if shutil.which("k3s") or shutil.which("kubectl"):
        import homelab
        env = homelab._kube_env()
        out.append(_r("kube", g, "Kubernetes-Zugriff", "ok" if env else "warning",
                      f"kubeconfig: {env['KUBECONFIG']}" if env else "Keine lesbare kubeconfig – Pods und Workloads fehlen.",
                      "" if env else "mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config"))
    return out


# ---------------------------------------------------------------------------
# Connections & accounts
# ---------------------------------------------------------------------------
def _connection_checks(agent_mode: bool) -> List[Dict[str, Any]]:
    g = "Verbindungen"
    out = []
    if not agent_mode:
        import auth
        cfg = auth.load_config()
        admins = auth._active_admins(cfg) if cfg else []
        out.append(_r("admins", g, "Administratoren", "ok" if admins else "critical",
                      f"{len(admins)} aktive(r) Administrator(en)" if admins else "Kein aktiver Administrator – niemand kann Benutzer und Rechte verwalten.",
                      "" if admins else f"{BASE_DIR}/set-password.sh"))
        if cfg and cfg.get("public_view"):
            out.append(_r("public", g, "Ansehen ohne Login", "info", "eingeschaltet – jeder im Netz sieht das Dashboard (Aktionen bleiben geschützt).",
                          f"{BASE_DIR}/set-password.sh public-view off"))
        out.extend(_remote_checks())
    try:
        import notify
        mail = notify.public()
        if mail["enabled"]:
            out.append(_r("mail", g, "E-Mail-Versand", "warning" if mail["last_error"] else "ok",
                          f"Letzter Versuch gescheitert: {mail['last_error'][:160]}" if mail["last_error"] else "funktioniert",
                          "Einstellungen → E-Mail → „Test senden“" if mail["last_error"] else ""))
    except Exception as e:
        logger.warning(f"Self-test (mail): {e}")
    try:
        import mcp_server
        m = mcp_server.public()
        if m["enabled"]:
            out.append(_r("mcp", g, "MCP-Server", "ok" if m["running"] else "warning",
                          "läuft" if m["running"] else f"eingeschaltet, läuft aber nicht{': ' + m['error'][:140] if m.get('error') else ''}",
                          "" if m["running"] else "Einstellungen → MCP-Server"))
    except Exception as e:
        logger.warning(f"Self-test (mcp): {e}")
    try:
        import geoip
        info = geoip.info()
        if info["installed"]:
            days = (time.time() - info["updated"]) / 86400 if info["updated"] else 0
            out.append(_r("geoip", g, "GeoIP-Datenbank", "info" if days > 60 else "ok",
                          f"Stand {info['month']}" + (f" – seit {int(days)} Tagen nicht aktualisiert" if days > 60 else ""),
                          "System-Werkzeuge → Firewall → Länder" if days > 60 else ""))
    except Exception as e:
        logger.warning(f"Self-test (geoip): {e}")
    return out


def _remote_checks() -> List[Dict[str, Any]]:
    import httpx
    import remotes
    out = []
    for r in remotes._all():
        try:
            res = httpx.get(f"{r['url']}/api/agent/info", headers={"Authorization": f"Bearer {r['token']}"}, timeout=5)
            state, detail = ("ok", f"erreichbar ({r['url']})") if res.status_code == 200 else \
                ("critical", "Token abgelehnt – im Agent neu erzeugen und hier eintragen.") if res.status_code in (401, 403) else \
                ("warning", f"Antwort {res.status_code}")
        except httpx.HTTPError as e:
            state, detail = "warning", f"Nicht erreichbar ({type(e).__name__}) – {r['url']}"
        out.append(_r(f"remote:{r['id']}", "Verbindungen", f"Remote-Pi {r['name']}", state, detail,
                      "" if state == "ok" else "Einstellungen → Remote-Pis"))
    return out


# ---------------------------------------------------------------------------
# Run, repair
# ---------------------------------------------------------------------------
def run() -> Dict[str, Any]:
    import agent
    agent_mode = agent.enabled()   # an agent has no users of its own
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for fn, args in ((_service_checks, ()), (_git_checks, ()), (_file_checks, ()), (_env_checks, ()), (_connection_checks, (agent_mode,))):
        try:
            results.extend(fn(*args))
        except Exception as e:
            logger.warning(f"Self-test {fn.__name__}: {e}")
            results.append(_r(fn.__name__, "Umgebung", f"Prüfung {fn.__name__.strip('_').replace('_checks', '')}", "warning",
                              f"Prüfung selbst fehlgeschlagen: {type(e).__name__}: {e}"[:200]))
    settings_store.set(RESULT_KEY, {"checked": int(time.time()), "duration": round(time.time() - t0, 1), "results": results})
    return overview()


def overview() -> Dict[str, Any]:
    d = settings_store.get(RESULT_KEY) or {}
    results = d.get("results", [])
    counts = {s: sum(1 for r in results if r["state"] == s) for s in ("critical", "warning", "info", "ok", "skip")}
    return {"checked": d.get("checked", 0), "duration": d.get("duration"), "running": _running["active"],
            "groups": GROUPS, "results": results, "counts": counts}


def alerts() -> List[Dict[str, Any]]:
    return [{"level": r["state"], "text": f"Selbstdiagnose: {r['title']}"} for r in overview()["results"] if r["state"] in ("critical", "warning")]


def _thread() -> None:
    try:
        run()
    except Exception as e:
        logger.warning(f"Self-test failed: {e}")
    finally:
        _running["active"] = False


def start() -> Dict[str, Any]:
    with _lock:
        if not _running["active"]:
            _running["active"] = True
            threading.Thread(target=_thread, daemon=True).start()
    return overview()


def check_if_due() -> None:
    if time.time() - (settings_store.get(RESULT_KEY) or {}).get("checked", 0) >= CHECK_INTERVAL - 60:
        start()


class RepairError(Exception):
    pass


def repair(repair_id: str) -> Dict[str, Any]:
    if repair_id == "perms":
        for name in _too_open():
            os.chmod(os.path.join(BASE_DIR, name), 0o600)
    elif repair_id == "vacuum":
        import history
        conn = sqlite3.connect(history.DB_PATH, timeout=30)
        try:
            conn.execute("VACUUM")
        except sqlite3.Error as e:
            raise RepairError(f"Verdichten fehlgeschlagen: {e}")
        finally:
            conn.close()
    else:
        raise RepairError("Unbekannte Reparatur.")
    return run()
