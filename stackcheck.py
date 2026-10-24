"""
Compose security check (Container -> Sicherheitscheck): looks at every compose stack for
risky settings - privileged containers, the Docker socket mounted, host network / PID,
dangerous capabilities, unconfined seccomp / AppArmor, host folders like / or /etc mounted,
ports open on all interfaces, "latest" tags, passwords written into the compose file, no
restart policy - with a score per stack and the fix for each finding.

The effective configuration comes from `docker compose config --format json` (all files
and overrides merged, variables filled in); passwords are only looked for in the files
themselves, so values from .env / env_file count as fine. Checked once a day and on demand.
Findings can be accepted per stack ("ignore"), e.g. the docker.sock of Portainer.
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import settings_store

logger = logging.getLogger("pi_dashboard.stackcheck")

IGNORE_KEY = "stack_check_ignore"
CHECK_INTERVAL = 24 * 3600
WEIGHT = {"critical": 30, "warning": 10, "info": 3}
DANGEROUS_CAPS = {"ALL", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_RAWIO", "NET_ADMIN", "DAC_READ_SEARCH", "SYS_TIME"}
HOST_PATHS = {"/": "critical", "/etc": "critical", "/root": "critical", "/boot": "critical", "/var/lib/docker": "critical",
              "/proc": "warning", "/sys": "warning", "/dev": "warning", "/home": "warning", "/var/run": "warning", "/run": "warning"}
SECRET_KEY = re.compile(r"(PASS(WORD|WD)?|SECRET(_?KEY)?|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIALS?)$", re.I)
# KEY: value / KEY=value / - KEY=value in a compose file (environment section)
SECRET_LINE = re.compile(r"^\s*(?:-\s*)?[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*[:=]\s*[\"']?([^\"'#\s][^\"'#]*?)[\"']?\s*(?:#.*)?$")
_lock = threading.Lock()
_result: Dict[str, Any] = {"stacks": [], "checked": 0}
_running = False


class StackCheckError(Exception):
    pass


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _f(check: str, level: str, service: str, text: str, fix: str) -> Dict[str, Any]:
    return {"id": f"{check}:{service}", "check": check, "level": level, "service": service, "text": text, "fix": fix}


def _volumes(svc: Dict[str, Any]) -> List[Tuple[str, str, bool]]:
    """[(type, source, read_only)] from the long volume syntax compose config produces."""
    out = []
    for v in svc.get("volumes") or []:
        if isinstance(v, dict):
            out.append((v.get("type", ""), v.get("source") or "", bool(v.get("read_only"))))
        elif isinstance(v, str):
            src = v.split(":")[0]
            out.append(("bind" if src.startswith(("/", ".", "~")) else "volume", src, v.endswith(":ro")))
    return out


def check_service(name: str, svc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    if svc.get("privileged"):
        out.append(_f("privileged", "critical", name, "Läuft privilegiert – voller Zugriff auf den Pi",
                      "privileged: true entfernen und nur die nötigen Rechte mit cap_add oder devices geben."))
    for kind, src, ro in _volumes(svc):
        if kind != "bind":
            continue
        if src.rstrip("/").endswith("docker.sock"):
            out.append(_f("docker_sock", "critical", name, "Docker-Socket eingebunden" + (" (nur lesend – trotzdem voller Zugriff)" if ro else ""),
                          "Nur einbinden, wenn die App Docker steuern muss (z. B. Portainer, Watchtower) – sonst entfernen; "
                          "besser über einen Socket-Proxy mit eingeschränkten Rechten."))
            continue
        path = os.path.normpath(src) if src else ""
        if path in HOST_PATHS:
            level = HOST_PATHS[path]
            if ro and level == "critical":
                level = "warning"
            out.append(_f("host_path", level, name, f"Host-Ordner {path} eingebunden" + (" (nur lesend)" if ro else ""),
                          "Nur den Unterordner einbinden, den die App wirklich braucht, und wenn möglich mit :ro."))
    if svc.get("network_mode") == "host":
        out.append(_f("host_network", "warning", name, "Teilt das Netzwerk des Pi (network_mode: host)",
                      "Ohne network_mode: host und mit ports: nur die nötigen Ports freigeben – außer die App braucht Broadcasts (z. B. Home Assistant, Pi-hole DHCP)."))
    for key, label in (("pid", "Prozesse"), ("ipc", "IPC")):
        if svc.get(key) == "host":
            out.append(_f(f"host_{key}", "warning", name, f"Sieht die {label} des Pi ({key}: host)", f"{key}: host entfernen, wenn es nicht nötig ist."))
    caps = {str(c).upper().replace("CAP_", "") for c in svc.get("cap_add") or []}
    bad = sorted(caps & DANGEROUS_CAPS)
    if bad:
        out.append(_f("caps", "critical" if {"ALL", "SYS_ADMIN"} & set(bad) else "warning", name, f"Weitreichende Rechte: {', '.join(bad)}",
                      "Nur die wirklich benötigten Capabilities hinzufügen."))
    for opt in svc.get("security_opt") or []:
        opt = str(opt).replace("=", ":")
        if opt in ("seccomp:unconfined", "apparmor:unconfined"):
            out.append(_f("unconfined", "warning", name, f"Schutz ausgeschaltet ({opt})", f"security_opt {opt} entfernen."))
    if svc.get("devices"):
        out.append(_f("devices", "info", name, f"{len(svc['devices'])} Gerät(e) des Pi durchgereicht", "In Ordnung, wenn die App das Gerät braucht (z. B. Zigbee-Stick, GPU)."))
    if svc.get("network_mode") != "host":
        open_ports = [p for p in svc.get("ports") or [] if isinstance(p, dict) and p.get("published") and p.get("host_ip") in (None, "", "0.0.0.0", "::")]
        if open_ports:
            nums = ", ".join(f"{p['published']}" for p in open_ports[:6]) + (" …" if len(open_ports) > 6 else "")
            out.append(_f("open_ports", "info", name, f"Port {nums} auf allen Schnittstellen erreichbar",
                          "Läuft die App hinter einem Reverse-Proxy, nur lokal veröffentlichen: \"127.0.0.1:PORT:PORT\"; sonst per Firewall aufs Heimnetz beschränken."))
    image = str(svc.get("image") or "")
    if image and "@sha256:" not in image and not svc.get("build"):   # own builds have no version to pin
        tag = image.rsplit("/", 1)[-1].partition(":")[2]
        if not tag or tag == "latest":
            out.append(_f("latest", "info", name, f"Image ohne feste Version ({image.rsplit('/', 1)[-1]})",
                          "Eine feste Version angeben (z. B. :1.2.3), damit ein Update nur kommt, wenn du es willst."))
    if not svc.get("restart") and not (svc.get("deploy") or {}).get("restart_policy"):
        out.append(_f("restart", "info", name, "Keine Restart-Policy – startet nach einem Neustart des Pi nicht von selbst",
                      "restart: unless-stopped ergänzen."))
    return out


def check_secrets(files: List[str]) -> List[Dict[str, Any]]:
    """Passwords written directly into the compose files (not ${VAR} / .env / env_file)."""
    out, seen = [], set()
    for path in files:
        try:
            with open(path, errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            m = SECRET_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if not SECRET_KEY.search(key) or not value or "${" in value or value.startswith("$") or value.lower() in ("true", "false", "null", "changeme", "~"):
                continue
            if key.upper().endswith(("_FILE", "_PATH")) or value.startswith("/run/secrets"):
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(_f("secret", "warning", key, f"{key} steht im Klartext in {os.path.basename(path)} (Zeile {n})",
                          f"In die .env-Datei neben der Compose-Datei verschieben und in der Compose-Datei ${{{key}}} schreiben."))
    return out


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def _config(stack: Dict[str, Any]) -> Dict[str, Any]:
    args = ["docker", "compose", "-p", stack["name"]]
    for f in stack["files"]:
        args += ["-f", f]
    res = subprocess.run(args + ["config", "--format", "json"], cwd=stack["dir"] or None, capture_output=True, text=True,
                         timeout=30, errors="replace")
    if res.returncode != 0:
        raise StackCheckError((res.stderr.strip().splitlines() or ["docker compose config fehlgeschlagen"])[-1][:300])
    return json.loads(res.stdout)


def _ignored() -> Dict[str, List[str]]:
    v = settings_store.get(IGNORE_KEY) or {}
    return v if isinstance(v, dict) else {}


def check_stack(stack: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"name": stack["name"], "dir": stack["dir"], "files": stack["files"], "findings": [], "error": ""}
    if not stack.get("file_exists"):
        entry["error"] = "Compose-Datei nicht gefunden – der Stack lässt sich nicht prüfen."
        return entry
    try:
        cfg = _config(stack)
        findings = []
        for name, svc in sorted((cfg.get("services") or {}).items()):
            findings += check_service(name, svc or {})
        findings += check_secrets(stack["files"])
        entry["findings"] = findings
    except (StackCheckError, subprocess.SubprocessError, OSError, ValueError) as e:
        entry["error"] = str(e)[:300]
    return entry


def _score(findings: List[Dict[str, Any]]) -> int:
    return max(0, 100 - sum(WEIGHT[f["level"]] for f in findings))


def _with_ignored(entry: Dict[str, Any], ignored: Dict[str, List[str]]) -> Dict[str, Any]:
    ign = set(ignored.get(entry["name"]) or [])
    findings = [{**f, "ignored": f["id"] in ign} for f in entry["findings"]]
    active = [f for f in findings if not f["ignored"]]
    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (f["ignored"], order[f["level"]], f["service"]))
    counts = {lvl: sum(1 for f in active if f["level"] == lvl) for lvl in order}
    return {**entry, "findings": findings, "counts": counts, "score": None if entry["error"] else _score(active)}


def run() -> Dict[str, Any]:
    global _running
    import containers
    if _running:
        return overview()
    _running = True
    try:
        stacks = [check_stack(s) for s in containers.stacks()]
        with _lock:
            _result.update(stacks=stacks, checked=int(time.time()))
    finally:
        _running = False
    return overview()


def check_if_due() -> None:
    if time.time() - _result["checked"] >= CHECK_INTERVAL and not _running:
        threading.Thread(target=run, daemon=True).start()


def overview() -> Dict[str, Any]:
    ignored = _ignored()
    with _lock:
        stacks = [_with_ignored(e, ignored) for e in _result["stacks"]]
        checked = _result["checked"]
    stacks.sort(key=lambda s: (s["score"] is None, s["score"] if s["score"] is not None else 0, s["name"]))
    scores = [s["score"] for s in stacks if s["score"] is not None]
    return {"stacks": stacks, "checked": checked, "running": _running,
            "score": round(sum(scores) / len(scores)) if scores else None,
            "critical": sum(s["counts"]["critical"] for s in stacks if "counts" in s)}


def set_ignored(stack: str, finding: str, ignore: bool) -> Dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", stack or "") or not re.match(r"^[a-z_]{2,20}:[A-Za-z0-9_.-]{1,128}$", finding or ""):
        raise StackCheckError("Ungültiger Befund.")
    with _lock:
        ignored = _ignored()
        items = set(ignored.get(stack) or [])
        (items.add if ignore else items.discard)(finding)
        if items:
            ignored[stack] = sorted(items)
        else:
            ignored.pop(stack, None)
        settings_store.set(IGNORE_KEY, ignored)
    return overview()


def alerts() -> List[Dict[str, Any]]:
    out = []
    for s in overview()["stacks"]:
        n = s.get("counts", {}).get("critical", 0)
        if n:
            out.append({"level": "warning", "text": f"Stack {s['name']}: {n} {'kritischer Sicherheitsbefund' if n == 1 else 'kritische Sicherheitsbefunde'}"})
    return out
