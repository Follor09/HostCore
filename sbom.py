"""
Software inventory / SBOM (System -> Schwachstellen & Support -> Software-Inventar): which software
in which version runs on this Pi, with licenses.

- System packages: dpkg (version, architecture, size, source package); licenses from the
  machine-readable /usr/share/doc/<package>/copyright files
- Python packages of the dashboard itself (its venv)
- Container images: the package lists Trivy writes during the daily vulnerability scan

Searchable here and - via /api/fleet/sbom - across all Pis ("where does openssl 3.0 run?").
Export as CycloneDX 1.5 JSON or CSV.
"""

import csv
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List

logger = logging.getLogger("pi_dashboard.sbom")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_FILE = os.path.join(BASE_DIR, "sbom_images.json")
SYSTEM_TTL = 3600
MAX_RESULTS = 500
KINDS = {"deb": "Systempaket", "python": "Python (Dashboard)", "image": "Container"}
_cache: Dict[str, Any] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def _licenses_deb(name: str) -> List[str]:
    """License names from a machine-readable copyright file (DEP-5), else []."""
    try:
        with open(f"/usr/share/doc/{name}/copyright", errors="replace") as f:
            text = f.read(200_000)
    except OSError:
        return []
    if not re.match(r"^Format:\s*\S+", text):
        return []
    found = []
    for m in re.finditer(r"^License:\s*(.+)$", text, re.M):
        for part in re.split(r"\s+(?:or|and)\s+|\s*,\s*", m.group(1).strip()):
            part = part.strip()
            if part and part not in found and len(part) < 60:
                found.append(part)
    return found[:8]


def _deb() -> List[Dict[str, Any]]:
    if not shutil.which("dpkg-query"):
        return []
    try:
        res = subprocess.run(["dpkg-query", "-W", "-f", "${db:Status-Abbrev}\t${Package}\t${Version}\t${Architecture}\t${Installed-Size}\t${source:Package}\n"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or not parts[0].startswith("ii"):
            continue
        _, name, version, arch, size, source = parts[:6]
        out.append({"kind": "deb", "name": name, "version": version, "arch": arch, "source": source or name,
                    "size_kb": int(size) if size.isdigit() else None, "licenses": _licenses_deb(name), "where": ""})
    return out


def _python() -> List[Dict[str, Any]]:
    from importlib import metadata
    out = []
    for d in metadata.distributions():
        meta = d.metadata
        name = meta.get("Name") or ""
        if not name:
            continue
        lic = meta.get("License-Expression") or ""
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
            free_text = (meta.get("License") or "").strip().splitlines()
            lic = ", ".join(classifiers) or (free_text[0][:60] if free_text else "")
        out.append({"kind": "python", "name": name, "version": d.version, "arch": "", "source": "", "size_kb": None,
                    "licenses": [lic] if lic else [], "where": "venv"})
    return sorted(out, key=lambda p: p["name"].lower())


def _load_images() -> Dict[str, Any]:
    try:
        with open(IMAGES_FILE) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def store_images(images: Dict[str, Dict[str, Any]]) -> None:
    """Called by the vulnerability scan: {image ref: {image, users, packages: [...]}}"""
    tmp = IMAGES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"checked": int(time.time()), "images": images}, f, ensure_ascii=False)
    os.replace(tmp, IMAGES_FILE)


def trivy_packages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Package list from `trivy image --list-all-pkgs --format json`."""
    out, seen = [], set()
    for r in data.get("Results") or []:
        kind = r.get("Type") or r.get("Class") or ""
        for p in r.get("Packages") or []:
            key = (p.get("Name"), p.get("Version"), kind)
            if key in seen or not p.get("Name"):
                continue
            seen.add(key)
            out.append({"name": p.get("Name", ""), "version": p.get("Version", ""), "type": kind,
                        "licenses": [str(x)[:60] for x in (p.get("Licenses") or [])][:6]})
    return out


def _images() -> List[Dict[str, Any]]:
    out = []
    for ref, img in (_load_images().get("images") or {}).items():
        where = img.get("image", ref)
        for p in img.get("packages", []):
            out.append({"kind": "image", "name": p["name"], "version": p["version"], "arch": "", "source": p.get("type", ""),
                        "size_kb": None, "licenses": p.get("licenses", []), "where": where, "users": img.get("users", [])})
    return out


def inventory(refresh: bool = False) -> List[Dict[str, Any]]:
    with _lock:
        if refresh or time.time() - _cache.get("ts", 0) > SYSTEM_TTL:
            _cache["system"] = _deb() + _python()
            _cache["ts"] = time.time()
        system = _cache["system"]
    return system + _images()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def _match(p: Dict[str, Any], q: str) -> bool:
    if not q:
        return True
    name, _, ver = q.partition(" ")
    if ver:
        return name in p["name"].lower() and p["version"].lower().startswith(ver.strip())
    return q in p["name"].lower() or q in (p.get("source") or "").lower() or any(q in lic.lower() for lic in p["licenses"])


def search(q: str = "", kind: str = "", limit: int = MAX_RESULTS) -> Dict[str, Any]:
    """q: "openssl" or "openssl 3.0" (name and version prefix); also matches source package and license."""
    q = (q or "").strip().lower()[:100]
    items = inventory()
    hits = [p for p in items if (not kind or p["kind"] == kind) and _match(p, q)]
    hits.sort(key=lambda p: (p["name"].lower(), p["kind"], p.get("where", "")))
    return {"items": hits[:limit], "total": len(hits), "truncated": len(hits) > limit}


def overview() -> Dict[str, Any]:
    items = inventory()
    counts = {k: sum(1 for p in items if p["kind"] == k) for k in KINDS}
    lic: Dict[str, int] = {}
    for p in items:
        for name in p["licenses"] or ["(unbekannt)"]:
            lic[name] = lic.get(name, 0) + 1
    top = sorted(lic.items(), key=lambda x: -x[1])[:15]
    images = _load_images()
    return {"counts": counts, "kinds": KINDS, "total": len(items), "licenses": [{"name": k, "count": v} for k, v in top],
            "unknown_license": sum(1 for p in items if not p["licenses"]),
            "images": len(images.get("images") or {}), "images_checked": images.get("checked", 0),
            "trivy": bool(shutil.which("trivy")), "hostname": platform.node()}


def _distro() -> str:
    try:
        with open("/etc/os-release") as f:
            d = dict(line.strip().split("=", 1) for line in f if "=" in line)
        return f"{d.get('ID', 'debian').strip(chr(34))}-{d.get('VERSION_ID', '').strip(chr(34))}"
    except OSError:
        return "debian"


def _purl(p: Dict[str, Any], distro: str) -> str:
    if p["kind"] == "deb":
        return f"pkg:deb/debian/{p['name']}@{p['version']}?arch={p['arch']}&distro={distro}"
    if p["kind"] == "python":
        return f"pkg:pypi/{p['name'].lower()}@{p['version']}"
    typ = {"debian": "deb", "ubuntu": "deb", "alpine": "apk", "python-pkg": "pypi", "node-pkg": "npm", "gobinary": "golang",
           "jar": "maven", "cargo": "cargo", "gemspec": "gem"}.get(p.get("source", ""), "generic")
    return f"pkg:{typ}/{p['name']}@{p['version']}"


def export(fmt: str) -> tuple:
    """(filename, media type, body)"""
    items = inventory()
    host = re.sub(r"[^A-Za-z0-9.-]", "_", platform.node() or "pi")
    stamp = time.strftime("%Y-%m-%d")
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["type", "name", "version", "licenses", "location", "architecture", "source"])
        for p in items:
            w.writerow([p["kind"], p["name"], p["version"], "; ".join(p["licenses"]), p.get("where", ""), p.get("arch", ""), p.get("source", "")])
        return f"sbom-{host}-{stamp}.csv", "text/csv; charset=utf-8", buf.getvalue()
    distro = _distro()
    components = []
    for p in items:
        c: Dict[str, Any] = {"type": "library", "name": p["name"], "version": p["version"], "purl": _purl(p, distro)}
        if p["licenses"]:
            c["licenses"] = [{"license": {"name": lic}} for lic in p["licenses"]]
        props = [{"name": "picore:kind", "value": p["kind"]}]
        if p.get("where"):
            props.append({"name": "picore:location", "value": p["where"]})
        c["properties"] = props
        components.append(c)
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
           "metadata": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "tools": {"components": [{"type": "application", "name": "PiCore Dashboard"}]},
                        "component": {"type": "device", "name": platform.node() or "pi"}},
           "components": components}
    return f"sbom-{host}-{stamp}.cdx.json", "application/vnd.cyclonedx+json", json.dumps(bom, ensure_ascii=False, indent=1)

