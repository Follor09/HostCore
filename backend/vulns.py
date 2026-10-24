"""
Vulnerability scan (System -> Schwachstellen): known CVEs in what runs on this Pi.

- Installed packages: debsecan (Debian security tracker) - which CVEs affect the installed
  versions, whether a fixed version is available and how urgent the tracker rates them.
- Container images: Trivy, if installed - every image of a running container (Docker and k3s)
  with severity, installed and fixed version.

Both run once a day with low priority as the dashboard user (no root needed). Single CVEs can
be marked as accepted with a note; they no longer count for warnings. New serious findings with
a fix become an event; open ones show up in the status summary and the daily e-mail.
The results live in vulns.json (owner only, not in git).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List

import settings_store

logger = logging.getLogger("pi_dashboard.vulns")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(BASE_DIR, "vulns.json")
IGNORE_KEY = "vuln_ignored"
EVENTS_KEY = "vuln_events"
CHECK_INTERVAL = 24 * 3600
PKG_TIMEOUT = 600
IMAGE_TIMEOUT = 900
TOP_PER_IMAGE = 60
SEVERITIES = ["critical", "high", "medium", "low", "unknown"]
SERIOUS = ("critical", "high")
ID_RE = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA-[\w-]+|TEMP-[\w-]+|DSA-[\w-]+|[A-Z]+-\d{4}-[\w-]+)$")
SUMMARY_RE = re.compile(r"^(\S+)\s+(\S+)(?:\s+\((.*)\))?\s*$")
_lock = threading.Lock()
_running = {"active": False, "step": ""}


class VulnError(Exception):
    pass


def tools() -> Dict[str, bool]:
    return {"debsecan": bool(shutil.which("debsecan")), "trivy": bool(shutil.which("trivy"))}


def _load() -> Dict[str, Any]:
    try:
        with open(DATA_FILE) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(data: Dict[str, Any]) -> None:
    tmp = DATA_FILE + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def _ignored() -> Dict[str, Any]:
    d = settings_store.get(IGNORE_KEY) or {}
    return d if isinstance(d, dict) else {}


def _low_prio(cmd: List[str]) -> List[str]:
    prefix = ["nice", "-n", "19"] if shutil.which("nice") else []
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix + cmd


def _suite() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("VERSION_CODENAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Packages (debsecan)
# ---------------------------------------------------------------------------
def parse_debsecan(text: str) -> List[Dict[str, Any]]:
    """`debsecan --format summary`: CVE-2024-1234 openssl (fixed, remotely exploitable, high urgency)"""
    out = []
    for line in text.splitlines():
        m = SUMMARY_RE.match(line.strip())
        if not m or not ID_RE.match(m.group(1)):
            continue
        notes = [n.strip() for n in (m.group(3) or "").split(",") if n.strip()]
        urgency = next((n.split()[0] for n in notes if n.endswith("urgency")), "")
        sev = {"high": "high", "medium": "medium", "low": "low", "unimportant": "low"}.get(urgency, "unknown")
        out.append({"id": m.group(1), "package": m.group(2), "fixed": "fixed" in notes,
                    "remote": "remotely exploitable" in notes, "severity": sev, "obsolete": "obsolete" in notes})
    return out


def _scan_packages() -> Dict[str, Any]:
    if not shutil.which("debsecan"):
        return {"available": False}
    suite = _suite()
    cmd = ["debsecan", "--format", "summary"]
    res = None
    for args in ([*cmd, "--suite", suite] if suite else None, cmd):
        if not args:
            continue
        try:
            res = subprocess.run(_low_prio(args), capture_output=True, text=True, timeout=PKG_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise VulnError("debsecan hat zu lange gebraucht.")
        if res.returncode == 0:
            break
    if res is None or res.returncode != 0:
        raise VulnError(((res.stderr if res else "") or "debsecan fehlgeschlagen").strip().splitlines()[-1][:200])
    return {"available": True, "suite": suite, "checked": int(time.time()), "items": parse_debsecan(res.stdout)}


# ---------------------------------------------------------------------------
# Images (Trivy)
# ---------------------------------------------------------------------------
def _used_images() -> List[Dict[str, Any]]:
    import imagecheck
    images: Dict[str, Dict[str, Any]] = {}
    for u in imagecheck._docker_images() + imagecheck._pod_images():
        p = u["ref"]
        item = images.setdefault(p["ref"], {**p, "users": [], "docker": False})
        item["docker"] = item["docker"] or u["user"]["kind"] in ("docker", "stack")
        name = u["user"].get("name", "")
        if name and name not in item["users"]:
            item["users"].append(name)
    return list(images.values())


def _image_name(i: Dict[str, Any]) -> str:
    repo = i["repo"][8:] if i["registry"] == "docker.io" and i["repo"].startswith("library/") else i["repo"]
    name = f"{repo}:{i['tag']}" if i["registry"] == "docker.io" else f"{i['registry']}/{i['repo']}:{i['tag']}"
    return f"{name}@{i['pinned']}" if i.get("pinned") else name


def parse_trivy(data: Dict[str, Any]) -> Dict[str, Any]:
    counts = {s: 0 for s in SEVERITIES}
    fixable = {s: 0 for s in SEVERITIES}
    vulns: Dict[str, Dict[str, Any]] = {}
    for r in data.get("Results") or []:
        for v in r.get("Vulnerabilities") or []:
            sev = str(v.get("Severity") or "unknown").lower()
            sev = sev if sev in counts else "unknown"
            key = f"{v.get('VulnerabilityID')}|{v.get('PkgName')}"
            if key in vulns:
                continue
            fixed = str(v.get("FixedVersion") or "")
            counts[sev] += 1
            if fixed:
                fixable[sev] += 1
            vulns[key] = {"id": v.get("VulnerabilityID", ""), "package": v.get("PkgName", ""), "installed": v.get("InstalledVersion", ""),
                          "fixed_version": fixed, "fixed": bool(fixed), "severity": sev, "title": str(v.get("Title") or "")[:160],
                          "url": v.get("PrimaryURL", ""), "target": r.get("Class", "")}
    order = {s: n for n, s in enumerate(SEVERITIES)}
    top = sorted(vulns.values(), key=lambda v: (order[v["severity"]], not v["fixed"], v["id"]))
    return {"counts": counts, "fixable": fixable, "total": len(vulns), "vulns": top[:TOP_PER_IMAGE], "truncated": len(vulns) > TOP_PER_IMAGE}


def _scan_image(i: Dict[str, Any]) -> Dict[str, Any]:
    name = _image_name(i)
    cmd = ["trivy", "image", "--quiet", "--format", "json", "--scanners", "vuln", "--list-all-pkgs", "--skip-version-check",
           "--timeout", f"{IMAGE_TIMEOUT // 60}m", "--image-src", "docker,remote" if i["docker"] else "remote", name]
    base = {"ref": i["ref"], "image": name.split("@")[0], "users": i["users"], "checked": int(time.time())}
    try:
        res = subprocess.run(_low_prio(cmd), capture_output=True, text=True, timeout=IMAGE_TIMEOUT + 60)
    except subprocess.TimeoutExpired:
        return {**base, "error": "Trivy hat zu lange gebraucht."}
    if res.returncode != 0:
        lines = [ln for ln in (res.stderr or "").strip().splitlines() if ln.strip()]
        return {**base, "error": (lines[-1] if lines else f"Trivy beendet mit {res.returncode}")[:240]}
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        return {**base, "error": "Unerwartete Ausgabe von Trivy."}
    import sbom
    return {**base, **parse_trivy(data), "error": "", "packages": sbom.trivy_packages(data)}


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _packages_view(pk: Dict[str, Any], ignored: Dict[str, Any]) -> Dict[str, Any]:
    order = {s: n for n, s in enumerate(SEVERITIES)}
    groups: Dict[str, Dict[str, Any]] = {}
    for v in pk.get("items", []):
        g = groups.setdefault(v["package"], {"package": v["package"], "cves": [], "fixable": 0, "serious": 0, "remote": 0, "severity": "unknown"})
        ign = v["id"] in ignored
        g["cves"].append({**v, "ignored": ign})
        if ign:
            continue
        g["fixable"] += v["fixed"]
        g["serious"] += v["severity"] in SERIOUS
        g["remote"] += v["remote"]
        if order[v["severity"]] < order[g["severity"]]:
            g["severity"] = v["severity"]
    for g in groups.values():
        g["cves"].sort(key=lambda v: (v["ignored"], not v["fixed"], order[v["severity"]], v["id"]))
    rows = sorted(groups.values(), key=lambda g: (-g["fixable"], order[g["severity"]], -len(g["cves"]), g["package"]))
    active = [v for v in pk.get("items", []) if v["id"] not in ignored]
    return {"available": pk.get("available", tools()["debsecan"]), "checked": pk.get("checked", 0), "suite": pk.get("suite", ""),
            "error": pk.get("error", ""), "groups": rows,
            "total": len(active), "fixable": sum(1 for v in active if v["fixed"]),
            "serious_fixable": sum(1 for v in active if v["fixed"] and v["severity"] in SERIOUS),
            "packages": len({v["package"] for v in active})}


def _image_view(img: Dict[str, Any], ignored: Dict[str, Any]) -> Dict[str, Any]:
    vulns = [{**v, "ignored": v["id"] in ignored} for v in img.get("vulns", [])]
    open_ = [v for v in vulns if not v["ignored"]]
    return {**img, "vulns": vulns, "serious_fixable": sum(1 for v in open_ if v["fixed"] and v["severity"] in SERIOUS),
            "ignored_count": len(vulns) - len(open_)}


def overview() -> Dict[str, Any]:
    d = _load()
    ignored = _ignored()
    images = [_image_view(i, ignored) for i in d.get("images", [])]
    images.sort(key=lambda i: (bool(i.get("error")) and not i.get("counts"), -(i.get("counts") or {}).get("critical", 0),
                               -(i.get("counts") or {}).get("high", 0), i["image"]))
    return {"tools": tools(), "checked": d.get("checked", 0), "running": _running["active"], "step": _running["step"],
            "error": d.get("error", ""), "packages": _packages_view(d.get("packages", {}), ignored),
            "images": images, "ignored": [{"id": k, **v} for k, v in sorted(ignored.items())]}


def alerts() -> List[Dict[str, Any]]:
    o = overview()
    out = []
    n = o["packages"]["serious_fixable"]
    if n:
        out.append({"level": "warning", "text": f"{n} {'schwere Sicherheitslücke' if n == 1 else 'schwere Sicherheitslücken'} in Paketen – Update verfügbar"})
    for i in o["images"]:
        if i["serious_fixable"]:
            out.append({"level": "warning", "text": f"Image {i['image']}: {i['serious_fixable']} {'schwere Lücke' if i['serious_fixable'] == 1 else 'schwere Lücken'} mit Fix"})
    return out


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def ignore(vuln_id: str, note: str, user: str, on: bool = True) -> Dict[str, Any]:
    vuln_id = str(vuln_id or "").strip()
    if not ID_RE.match(vuln_id):
        raise VulnError("Ungültige Kennung.")
    with _lock:
        ign = _ignored()
        if on:
            ign[vuln_id] = {"note": str(note or "").strip()[:300], "user": user, "ts": int(time.time())}
        else:
            ign.pop(vuln_id, None)
        settings_store.set(IGNORE_KEY, ign)
    return overview()


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def _serious_keys(d: Dict[str, Any]) -> set:
    keys = {f"pkg|{v['id']}|{v['package']}" for v in d.get("packages", {}).get("items", []) if v["fixed"] and v["severity"] in SERIOUS}
    for i in d.get("images", []):
        keys |= {f"img|{i['ref']}|{v['id']}|{v['package']}" for v in i.get("vulns", []) if v["fixed"] and v["severity"] in SERIOUS}
    return keys


def scan() -> None:
    old = _load()
    new: Dict[str, Any] = {"checked": int(time.time()), "packages": old.get("packages", {}), "images": old.get("images", [])}
    _running["step"] = "Pakete"
    try:
        new["packages"] = _scan_packages()
    except VulnError as e:
        new["packages"] = {**old.get("packages", {}), "available": True, "error": str(e)}
    if shutil.which("trivy"):
        try:
            used = _used_images()
        except Exception as e:
            used = []
            new["error"] = f"Images nicht lesbar: {e}"[:200]
        prev = {i["ref"]: i for i in old.get("images", [])}
        images = []
        for n, i in enumerate(used, 1):
            _running["step"] = f"Image {n} von {len(used)}"
            res = _scan_image(i)
            if res.get("error") and prev.get(i["ref"], {}).get("counts"):
                res = {**prev[i["ref"]], "error": res["error"], "users": i["users"]}   # keep the last result
            images.append(res)
        # The package lists go to the software inventory, not into vulns.json
        import sbom
        old_sbom = sbom._load_images().get("images") or {}
        inventory = {}
        for i in images:
            pkgs = i.pop("packages", None)
            if pkgs is None and i["ref"] in old_sbom:
                pkgs = old_sbom[i["ref"]].get("packages", [])
            inventory[i["ref"]] = {"image": i["image"], "users": i.get("users", []), "packages": pkgs or []}
        sbom.store_images(inventory)
        new["images"] = images
    else:
        new["images"] = []
    _store(new)
    ignored = _ignored()
    fresh = {k for k in _serious_keys(new) - _serious_keys(old) if k.split("|")[-2] not in ignored}
    if old.get("checked") and fresh:
        pkgs = sorted({k.split("|")[-1] for k in fresh})
        title = f"{len(fresh)} {'neue schwere Sicherheitslücke' if len(fresh) == 1 else 'neue schwere Sicherheitslücken'} mit Fix"
        events = (settings_store.get(EVENTS_KEY, []) or [])[-199:]
        events.append({"ts": int(time.time()), "level": "warning", "title": title, "detail": ", ".join(pkgs[:8]) + (" …" if len(pkgs) > 8 else "")})
        settings_store.set(EVENTS_KEY, events)


def _thread() -> None:
    try:
        scan()
    except Exception as e:
        logger.warning(f"Vulnerability scan failed: {e}")
        d = _load()
        d["error"] = f"{type(e).__name__}: {e}"[:200]
        try:
            _store(d)
        except OSError:
            pass
    finally:
        _running.update(active=False, step="")


def start_scan() -> Dict[str, Any]:
    with _lock:
        if not _running["active"]:
            _running.update(active=True, step="")
            threading.Thread(target=_thread, daemon=True).start()
    return overview()


def check_if_due() -> None:
    if (tools()["debsecan"] or tools()["trivy"]) and time.time() - _load().get("checked", 0) >= CHECK_INTERVAL - 60:
        start_scan()

