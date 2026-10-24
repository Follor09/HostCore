"""
Emergency handbook (Dokumentation -> Notfallhandbuch): how to bring every service back, put
together from what the dashboard already knows - so it is current without being written by hand.

- Per service (compose stack or service profile): purpose, address, owner, where its data lives,
  the backups that cover it, database dumps, the Git source (GitOps), volumes and host folders,
  and generated restore steps (folder, variables, data, database restore commands, start,
  check). Own steps, dependencies, a check and a runbook can be added per service.
- General part: this server, emergency contacts, where credentials are kept (a hint only - the
  handbook never contains passwords), contracts with providers, domains, hardware.
- Gaps (no backup, no current dump, failing backup, no profile ...) give a readiness value.
- Export as Markdown or a print view (save as PDF in the browser), meant to be kept offline.
"""

import os
import platform
import re
import shlex
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "drhandbook"
PRIORITIES = {1: "Kritisch", 2: "Wichtig", 3: "Normal"}
DUMP_MAX_AGE = 7 * 86400
EXPORT_MAX_AGE = 90 * 86400
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_RE = re.compile(r"^(service|health|stack|custom):[\w.@-]{1,80}$")
CONTRACT_CATEGORIES = ("Internet & Telefon", "Server & Hosting", "Domain", "Lizenz")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"ts": 0.0, "doc": None}


class HandbookError(Exception):
    pass


def _cfg() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    c = c if isinstance(c, dict) else {}
    c.setdefault("general", {})
    c.setdefault("services", {})
    return c


def _text(value: Any, label: str, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        raise HandbookError(f"{label}: höchstens {max_len} Zeichen.")
    return text


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def save_general(fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    contacts = []
    for c in (fields.get("contacts") or [])[:20]:
        name = _text((c or {}).get("name"), "Name", 80)
        if not name:
            continue
        contacts.append({"name": name, "role": _text(c.get("role"), "Rolle", 80), "phone": _text(c.get("phone"), "Telefon", 40),
                         "email": _text(c.get("email"), "E-Mail", 120)})
    general = {"contacts": contacts, "credentials": _text(fields.get("credentials"), "Zugangsdaten", 1000),
               "location": _text(fields.get("location"), "Ablage", 300), "notes": _text(fields.get("notes"), "Hinweise", 3000),
               "updated": int(time.time()), "updated_by": user}
    with _lock:
        c = _cfg()
        c["general"] = general
        settings_store.set(KEY, c)
    _cache["doc"] = None
    return build()


def save_service(target: str, fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    import backups
    import docs
    if not TARGET_RE.match(target or ""):
        raise HandbookError("Ungültiger Dienst.")
    try:
        priority = int(fields.get("priority") or 3)
    except (TypeError, ValueError):
        priority = 3
    if priority not in PRIORITIES:
        raise HandbookError("Unbekannte Priorität.")
    known_jobs = {j["id"] for j in backups.overview()["jobs"]}
    job_ids = [str(j) for j in (fields.get("backups") or []) if str(j) in known_jobs][:10]
    runbook = str(fields.get("runbook") or "")
    if runbook and runbook not in {r["id"] for r in docs.runbooks()["runbooks"]}:
        raise HandbookError("Runbook nicht gefunden.")
    entry = {"priority": priority, "backups": job_ids, "runbook": runbook,
             "depends": _text(fields.get("depends"), "Abhängig von", 300), "steps": _text(fields.get("steps"), "Eigene Schritte", 4000),
             "verify": _text(fields.get("verify"), "Prüfung", 1000), "no_backup_needed": bool(fields.get("no_backup_needed")),
             "updated": int(time.time()), "updated_by": user}
    with _lock:
        c = _cfg()
        if target not in c["services"] and len(c["services"]) >= 300:
            raise HandbookError("Höchstens 300 Dienste.")
        c["services"][target] = entry
        settings_store.set(KEY, c)
    _cache["doc"] = None
    return build()


def mark_exported(user: str = "") -> Dict[str, Any]:
    with _lock:
        c = _cfg()
        c["exported"] = {"ts": int(time.time()), "user": user}
        settings_store.set(KEY, c)
    _cache["doc"] = None
    return build()


# ---------------------------------------------------------------------------
# Building the handbook
# ---------------------------------------------------------------------------
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _host() -> Dict[str, Any]:
    import system_info
    import updater
    ips = _safe(system_info.get_ip_addresses, {})
    url = re.sub(r"^https?://[^/@]*@", "https://", updater.update_state.get("remote_url") or "")
    return {"hostname": platform.node(), "model": _safe(system_info.get_pi_model, ""), "os": _safe(_os_name, ""),
            "kernel": platform.release(), "ip": ips.get("primary_ip", "") if isinstance(ips, dict) else "",
            "interfaces": {k: v for k, v in (ips.get("interfaces", {}) if isinstance(ips, dict) else {}).items() if k != "lo"},
            "dashboard_dir": BASE_DIR, "repo": url, "branch": updater.update_state.get("branch", ""),
            "commit": updater.update_state.get("current_commit", "")}


def _os_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _mounts(stack: Dict[str, Any]) -> Dict[str, List[str]]:
    """Named volumes and host folders from the merged compose config."""
    import stackcheck
    out: Dict[str, List[str]] = {"volumes": [], "folders": []}
    cfg = _safe(lambda: stackcheck._config(stack), None)
    if not cfg:
        return out
    project = cfg.get("name") or stack["name"]
    for svc in (cfg.get("services") or {}).values():
        for v in svc.get("volumes") or []:
            if not isinstance(v, dict):
                continue
            if v.get("type") == "volume" and v.get("source"):
                vol = (cfg.get("volumes") or {}).get(v["source"], {}) or {}
                out["volumes"].append(vol.get("name") or f"{project}_{v['source']}")
            elif v.get("type") == "bind" and v.get("source") and not v["source"].startswith(("/var/run/docker.sock", "/etc/localtime", "/etc/timezone")):
                out["folders"].append(v["source"])
    out["volumes"] = sorted(set(out["volumes"]))
    out["folders"] = sorted(set(out["folders"]))
    return out


def _covers(job: Dict[str, Any], paths: List[str]) -> bool:
    """A folder backup whose folder holds one of the paths (or the other way round)."""
    target = (job.get("target") or "").rstrip("/")
    if job.get("kind") != "folder" or not target:
        return False
    return any(p == target or p.startswith(target + "/") or target.startswith(p.rstrip("/") + "/") for p in paths if p)


def _db_restore(db: Dict[str, Any], dump: str) -> str:
    q = shlex.quote
    if db["engine"] == "postgres":
        inner = 'psql -q -U "${POSTGRES_USER:-postgres}" -d postgres'
    else:
        inner = ('C=$(command -v mariadb || command -v mysql); '
                 '"$C" -uroot -p"${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}"')
    return f"gunzip -c {q(dump)} | docker exec -i {q(db['name'])} sh -c {q(inner)}"


def _service(target: str, name: str, sdoc: Dict[str, Any], entry: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    kind = target.split(":", 1)[0]
    stack = ctx["stacks"].get(target.split(":", 1)[1]) if kind == "stack" else None
    gitops = ctx["gitops"].get(stack["name"]) if stack else None
    svc: Dict[str, Any] = {"target": target, "name": name, "kind": kind, "priority": entry.get("priority", 3),
                           "profile": {k: sdoc.get(k, "") for k in ("purpose", "url", "ports", "data", "backup", "docs_url", "owner", "notes")},
                           "has_profile": bool(sdoc), "depends": entry.get("depends", ""), "verify": entry.get("verify", ""),
                           "own_steps": entry.get("steps", ""), "settings": entry, "stack": None, "gitops": None,
                           "databases": [], "backups": [], "runbook": None, "restore": [], "gaps": []}
    paths: List[str] = []
    if stack:
        mounts = _mounts(stack)
        env_file = os.path.join(stack["dir"], ".env") if stack["dir"] else ""
        svc["stack"] = {"dir": stack["dir"], "files": stack["files"], "images": stack["images"], "services": stack["services"],
                        "running": stack["running"], "total": stack["total"], "volumes": mounts["volumes"], "folders": mounts["folders"],
                        "env_file": env_file if env_file and os.path.exists(env_file) else ""}
        paths = [stack["dir"], *mounts["folders"]]
        if gitops:
            svc["gitops"] = {k: gitops.get(k) for k in ("url", "branch", "path", "file", "deployed", "has_env")}
            if gitops.get("has_env"):
                svc["stack"]["env_file"] = os.path.join(BASE_DIR, "gitops-env", f"{stack['name']}.env")
        for db in ctx["databases"]:
            if db.get("stack") == stack["name"]:
                files = (db.get("dumps") or {}).get("files") or []
                last = files[0] if files else None
                folder = (db.get("dumps") or {}).get("folder", "")
                svc["databases"].append({"name": db["name"], "engine": db.get("engine_label", db["engine"]), "engine_id": db["engine"],
                                         "folder": folder, "last": last and {"name": last["name"], "ts": last["ts"], "size": last["size"]}})
                paths.append(folder)
    # Backups: chosen ones, plus folder backups that hold the stack's folders or dumps
    chosen = set(entry.get("backups") or [])
    for job in ctx["backups"]:
        auto = _covers(job, paths)
        if job["id"] in chosen or auto:
            svc["backups"].append({"id": job["id"], "name": job["name"], "kind": job["kind"], "target": job.get("target", ""),
                                   "state": job["state"], "last": job.get("last_backup"), "auto": auto and job["id"] not in chosen})
    if entry.get("runbook"):
        rb = next((r for r in ctx["runbooks"] if r["id"] == entry["runbook"]), None)
        if rb:
            svc["runbook"] = {"id": rb["id"], "title": rb["title"], "steps": [s["text"] for s in rb["steps"]]}

    # Generated restore steps
    steps: List[Dict[str, str]] = []
    if stack:
        d = stack["dir"]
        if gitops:
            clone = f"git clone -b {shlex.quote(gitops['branch'])} {shlex.quote(gitops['url'])} {shlex.quote(os.path.join('~/picore-apps', stack['name']))}"
            steps.append({"text": "Compose-Dateien aus Git holen (oder im Dashboard unter Container → GitOps neu anlegen):", "cmd": clone})
        elif d:
            steps.append({"text": f"Ordner {d} aus der Sicherung zurückholen – er enthält {', '.join(os.path.basename(f) for f in stack['files']) or 'die Compose-Datei'}."})
        if svc["stack"]["env_file"]:
            steps.append({"text": f"Variablen-Datei {svc['stack']['env_file']} zurückholen – sie enthält Zugangsdaten und liegt nur auf dem Server."})
        if mounts["folders"]:
            steps.append({"text": "Daten in diesen Host-Ordnern zurückholen: " + ", ".join(mounts["folders"])})
        if mounts["volumes"]:
            steps.append({"text": "Docker-Volumes wiederherstellen (sonst startet der Dienst leer): " + ", ".join(mounts["volumes"])})
        for db in svc["databases"]:
            svc_name = next((c["service"] for c in stack["containers"] if c["name"] == db["name"]), "")
            up = f"cd {shlex.quote(d)} && docker compose -p {shlex.quote(stack['name'])} up -d {shlex.quote(svc_name)}" if svc_name else ""
            dump = os.path.join(db["folder"], db["last"]["name"]) if db["last"] else os.path.join(db["folder"], "<dump>.sql.gz")
            steps.append({"text": f"Datenbank {db['name']} ({db['engine']}) zuerst allein starten und den letzten Dump einspielen:",
                          "cmd": "\n".join(x for x in (up, _db_restore({"name": db["name"], "engine": db["engine_id"]}, dump)) if x)})
        env_arg = f" --env-file {shlex.quote(svc['stack']['env_file'])}" if gitops and gitops.get("has_env") else ""
        steps.append({"text": "Stack starten:", "cmd": f"cd {shlex.quote(d)} && docker compose -p {shlex.quote(stack['name'])}{env_arg} up -d"})
    for line in [l.strip() for l in (entry.get("steps") or "").splitlines() if l.strip()]:
        steps.append({"text": line, "own": "1"})
    check = entry.get("verify") or (f"{sdoc['url']} im Browser öffnen und die Anmeldung prüfen." if sdoc.get("url") else "")
    if check:
        steps.append({"text": f"Prüfen: {check}"})
    svc["restore"] = steps

    # Gaps
    gaps = svc["gaps"]
    if not sdoc:
        gaps.append("Kein Steckbrief (Zweck, Adresse, Zuständig)")
    elif not sdoc.get("owner"):
        gaps.append("Niemand als zuständig eingetragen")
    if not svc["backups"] and not entry.get("no_backup_needed") and not (gitops and not svc["databases"] and not (stack and (_mounts_have_data(svc)))):
        gaps.append("Keine Sicherung zugeordnet")
    for b in svc["backups"]:
        if b["state"] in ("error", "overdue"):
            gaps.append(f"Sicherung {b['name']} {'fehlerhaft' if b['state'] == 'error' else 'überfällig'}")
    for db in svc["databases"]:
        if not db["last"]:
            gaps.append(f"Kein Dump der Datenbank {db['name']}")
        elif time.time() - db["last"]["ts"] > DUMP_MAX_AGE:
            gaps.append(f"Letzter Dump von {db['name']} älter als 7 Tage")
    if kind != "stack" and not entry.get("steps") and not svc["runbook"]:
        gaps.append("Keine Wiederherstellungsschritte")
    return svc


def _mounts_have_data(svc: Dict[str, Any]) -> bool:
    st = svc.get("stack") or {}
    return bool(st.get("volumes") or st.get("folders") or st.get("env_file"))


def build(force: bool = False) -> Dict[str, Any]:
    if not force and _cache["doc"] and time.time() - _cache["ts"] < 60:
        return _cache["doc"]
    import backups
    import containers
    import dbmonitor
    import docs
    import domains
    import gitops
    c = _cfg()
    sdocs = (settings_store.get(docs.SERVICEDOCS_KEY, {}) or {})
    ctx = {"stacks": {s["name"]: s for s in _safe(containers.stacks, [])},
           "gitops": {g["name"]: g for g in _safe(lambda: gitops.overview()["stacks"], [])},
           "databases": _safe(lambda: dbmonitor.overview()["databases"], []),
           "backups": _safe(lambda: backups.overview()["jobs"], []),
           "runbooks": _safe(lambda: docs.runbooks()["runbooks"], [])}
    targets: Dict[str, str] = {f"stack:{n}": n for n in ctx["stacks"]}
    for t, d in sdocs.items():
        targets[t] = d.get("name") or t.split(":", 1)[1]
    for t in c["services"]:
        targets.setdefault(t, t.split(":", 1)[1])
    services = [_service(t, n, sdocs.get(t) or {}, c["services"].get(t) or {}, ctx) for t, n in targets.items()]
    services.sort(key=lambda s: (s["priority"], s["name"].lower()))

    general = c["general"]
    exported = c.get("exported") or {}
    general_gaps = []
    if not general.get("contacts"):
        general_gaps.append("Keine Notfallkontakte eingetragen")
    if not general.get("credentials"):
        general_gaps.append("Nicht festgehalten, wo die Zugangsdaten liegen")
    if not exported.get("ts"):
        general_gaps.append("Noch nie exportiert – eine Kopie gehört offline (Ausdruck oder USB-Stick)")
    elif time.time() - exported["ts"] > EXPORT_MAX_AGE:
        general_gaps.append("Letzter Export älter als 90 Tage")
    complete = sum(1 for s in services if not s["gaps"])
    contracts = [x for x in _safe(lambda: docs.contracts()["items"], []) if x.get("category") in CONTRACT_CATEGORIES and x.get("state") != "ended"]
    doc = {
        "generated": int(time.time()), "host": _host(), "general": general, "general_gaps": general_gaps, "exported": exported,
        "services": services, "priorities": PRIORITIES,
        "summary": {"services": len(services), "complete": complete, "gaps": sum(len(s["gaps"]) for s in services) + len(general_gaps),
                    "score": round(100 * complete / len(services)) if services else None},
        "backups": [{k: b.get(k) for k in ("id", "name", "kind", "target", "state", "last_backup")} for b in ctx["backups"]],
        "runbooks": [{"id": r["id"], "title": r["title"]} for r in ctx["runbooks"]],
        "contracts": [{k: x.get(k) for k in ("name", "provider", "category", "link", "notes", "end")} for x in contracts],
        "domains": [{k: d.get(k) for k in ("unicode", "registrar", "expiry", "nameservers")} for d in _safe(lambda: domains.overview()["domains"], [])],
        "inventory": [{k: i.get(k) for k in ("name", "category", "manufacturer", "model", "serial", "location", "warranty_until", "mac")}
                      for i in _safe(lambda: docs.inventory()["items"], [])],
    }
    _cache.update(ts=time.time(), doc=doc)
    return doc


def facts() -> Dict[str, Any]:
    d = build()
    return {**d["summary"], "exported": d["exported"].get("ts"), "general_gaps": len(d["general_gaps"])}


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------
def _date(ts: Optional[float], with_time: bool = True) -> str:
    return time.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y", time.localtime(ts)) if ts else "–"


def markdown() -> str:
    d = build(force=True)
    h = d["host"]
    g = d["general"]
    out: List[str] = [f"# Notfallhandbuch – {h['hostname']}", "",
                      f"Stand: {_date(d['generated'])} · erzeugt vom HostCore Dashboard · {d['summary']['services']} Dienste, "
                      f"{d['summary']['complete']} ohne Lücken", "",
                      "> Dieses Handbuch enthält keine Passwörter. Es beschreibt, wo Daten, Sicherungen und Zugangsdaten liegen "
                      "und in welcher Reihenfolge die Dienste zurückkommen.", ""]
    out += ["## Notfallkontakte", ""]
    if g.get("contacts"):
        out += ["| Name | Rolle | Telefon | E-Mail |", "|---|---|---|---|"]
        out += [f"| {c['name']} | {c['role']} | {c['phone']} | {c['email']} |" for c in g["contacts"]]
    else:
        out.append("_Keine eingetragen._")
    out += ["", "## Zugangsdaten", "", g.get("credentials") or "_Nicht festgehalten._", ""]
    if g.get("location"):
        out += ["**Ablage dieses Handbuchs:** " + g["location"], ""]
    if g.get("notes"):
        out += ["## Hinweise", "", g["notes"], ""]
    out += ["## Dieser Server", "", f"- Hostname: `{h['hostname']}`", f"- Modell: {h['model']}", f"- System: {h['os']} · Kernel {h['kernel']}",
            f"- Adresse: `{h['ip']}`"]
    for name, ip in (h.get("interfaces") or {}).items():
        out.append(f"  - {name}: `{ip if isinstance(ip, str) else ip}`")
    out += ["", "### Dashboard neu installieren", "",
            "```", f"git clone {h['repo'] or '<Repository>'} ~/pi-dashboard", "~/pi-dashboard/install.sh", "```", "",
            f"Danach `settings.json`, `auth.json`, `history.db` und die Ordner `gitops-env/`, `db-dumps/` aus der Sicherung nach `{h['dashboard_dir']}` zurückholen "
            "und den Dienst neu starten: `sudo systemctl restart raspberry-dashboard`.", ""]

    out += ["## Wiederanlauf-Reihenfolge", ""]
    for p, label in PRIORITIES.items():
        names = [s["name"] for s in d["services"] if s["priority"] == p]
        if names:
            out.append(f"{p}. **{label}:** " + ", ".join(names))
    out.append("")

    for s in d["services"]:
        out += [f"## {s['name']}", "", f"Priorität: **{PRIORITIES[s['priority']]}**" + (f" · Abhängig von: {s['depends']}" if s["depends"] else ""), ""]
        pr = s["profile"]
        for key, label in (("purpose", "Zweck"), ("url", "Adresse"), ("ports", "Ports"), ("owner", "Zuständig"), ("data", "Daten"),
                           ("backup", "Sicherung (Steckbrief)"), ("docs_url", "Dokumentation"), ("notes", "Notizen")):
            if pr.get(key):
                out.append(f"- **{label}:** {pr[key]}")
        st = s["stack"]
        if st:
            out.append(f"- **Ordner:** `{st['dir']}`")
            out.append(f"- **Images:** {', '.join(f'`{i}`' for i in st['images']) or '–'}")
            if st["volumes"]:
                out.append(f"- **Volumes:** {', '.join(f'`{v}`' for v in st['volumes'])}")
            if st["folders"]:
                out.append(f"- **Host-Ordner:** {', '.join(f'`{v}`' for v in st['folders'])}")
        if s["gitops"]:
            gi = s["gitops"]
            out.append(f"- **Git:** {gi['url']} · Branch `{gi['branch']}`" + (f" · Ordner `{gi['path']}`" if gi["path"] else "")
                       + (f" · ausgerollt `{gi['deployed'][:8]}`" if gi.get("deployed") else ""))
        for db in s["databases"]:
            last = f"{db['last']['name']} ({_date(db['last']['ts'])})" if db["last"] else "keiner"
            out.append(f"- **Datenbank {db['name']}** ({db['engine']}): letzter Dump {last}, Ordner `{db['folder']}`")
        for b in s["backups"]:
            out.append(f"- **Sicherung {b['name']}** ({b['kind']}): `{b['target']}` · letzte {_date(b['last'])} · {b['state']}")
        out += ["", "### Wiederherstellen", ""]
        for i, step in enumerate(s["restore"], 1):
            out.append(f"{i}. {step['text']}")
            if step.get("cmd"):
                out += ["", "   ```", *[f"   {l}" for l in step["cmd"].splitlines()], "   ```", ""]
        if s["runbook"]:
            out += ["", f"**Runbook „{s['runbook']['title']}“:**", ""] + [f"- [ ] {x}" for x in s["runbook"]["steps"]]
        if s["gaps"]:
            out += ["", "**Lücken:** " + "; ".join(s["gaps"])]
        out.append("")

    if d["backups"]:
        out += ["## Sicherungen", "", "| Name | Art | Ziel | Letzte | Zustand |", "|---|---|---|---|---|"]
        out += [f"| {b['name']} | {b['kind']} | `{b['target']}` | {_date(b['last_backup'])} | {b['state']} |" for b in d["backups"]]
        out.append("")
    if d["contracts"]:
        out += ["## Anbieter & Verträge", "", "| Name | Anbieter | Art | Kundenkonto | Notizen |", "|---|---|---|---|---|"]
        out += [f"| {x['name']} | {x['provider']} | {x['category']} | {x['link'] or ''} | {(x['notes'] or '').replace(chr(10), ' ')} |" for x in d["contracts"]]
        out.append("")
    if d["domains"]:
        out += ["## Domains", "", "| Domain | Registrar | Läuft ab | Nameserver |", "|---|---|---|---|"]
        out += [f"| {x['unicode']} | {x['registrar'] or ''} | {x['expiry'] or ''} | {', '.join(x['nameservers'] or [])} |" for x in d["domains"]]
        out.append("")
    if d["inventory"]:
        out += ["## Hardware", "", "| Gerät | Kategorie | Modell | Seriennummer | Standort | Garantie bis |", "|---|---|---|---|---|---|"]
        out += [f"| {x['name']} | {x['category']} | {' '.join(v for v in (x['manufacturer'], x['model']) if v)} | {x['serial'] or ''} | "
                f"{x['location'] or ''} | {x['warranty_until'] or ''} |" for x in d["inventory"]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
