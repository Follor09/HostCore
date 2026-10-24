"""
Image policies (Container -> Sicherheitscheck -> Image-Richtlinien): rules for the container
images this Pi runs, checked for every running container and before every compose deployment.

Rules: allowed registries, a fixed version instead of "latest" / no tag, pinning by digest,
blocked images (patterns like *:nightly), maximum image age. Exceptions per stack.

Mode "Warnen" only reports; "Blockieren" also stops compose actions that would start a
violating image (stack update/start, app store, templates, GitOps): the deploy task runs
`python imagepolicy.py check ...` after checking out and before pulling - a violation fails
the task with the reason in its log. The age rule needs the image locally, so it is only
reported, never blocking.

Run as a script: imagepolicy.py check <project> <compose args...>  (exit 1 on a blocking violation)
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.abspath(__file__)
KEY = "image_policy"
MODES = {"off": "Aus", "warn": "Warnen", "block": "Blockieren"}
DEFAULTS: Dict[str, Any] = {"mode": "off", "registries": [], "require_tag": True, "require_digest": False, "denied": [],
                            "max_age_days": 0, "exceptions": []}
RULE_LABELS = {"registry": "Registry nicht erlaubt", "tag": "Keine feste Version", "digest": "Nicht per Digest fixiert",
               "denied": "Gesperrtes Image", "age": "Image zu alt"}


class PolicyError(Exception):
    pass


def config() -> Dict[str, Any]:
    import settings_store
    c = settings_store.get(KEY) or {}
    return {**DEFAULTS, **(c if isinstance(c, dict) else {})}


def _list(value: Any, rx: str, label: str, limit: int = 50) -> List[str]:
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value or ""))
    out = []
    for x in items:
        x = str(x).strip().lower()
        if not x:
            continue
        if not re.fullmatch(rx, x):
            raise PolicyError(f"{label}: „{x}“ ist ungültig.")
        out.append(x)
    return list(dict.fromkeys(out))[:limit]


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    import settings_store
    mode = str(fields.get("mode") or "off")
    if mode not in MODES:
        raise PolicyError("Unbekannter Modus.")
    try:
        age = int(fields.get("max_age_days") or 0)
    except (TypeError, ValueError):
        raise PolicyError("Höchstalter: Anzahl Tage.")
    if not 0 <= age <= 3650:
        raise PolicyError("Höchstalter: 0 (aus) bis 3650 Tage.")
    c = {"mode": mode, "registries": _list(fields.get("registries"), r"[a-z0-9.-]+(:\d+)?", "Registry"),
         "require_tag": bool(fields.get("require_tag")), "require_digest": bool(fields.get("require_digest")),
         "denied": _list(fields.get("denied"), r"[a-z0-9._/:@*?\[\]-]{1,200}", "Gesperrtes Image"),
         "max_age_days": age, "exceptions": _list(fields.get("exceptions"), r"[a-z0-9][a-z0-9_.-]{0,127}", "Ausnahme")}
    settings_store.set(KEY, c)
    return c


# ---------------------------------------------------------------------------
# Image references
# ---------------------------------------------------------------------------
def parse(ref: str) -> Dict[str, str]:
    """registry, repository, tag, digest of an image reference (Docker's defaults applied)."""
    ref = (ref or "").strip()
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first.lower(), rest
    else:
        registry, path = "docker.io", ref
    tag = ""
    last = path.rsplit("/", 1)[-1]
    if ":" in last:
        path, tag = path.rsplit(":", 1)
    if registry == "docker.io" and "/" not in path:
        path = f"library/{path}"
    return {"registry": registry, "repository": path.lower(), "tag": tag, "digest": digest}


def _short(p: Dict[str, str]) -> str:
    repo = p["repository"][8:] if p["registry"] == "docker.io" and p["repository"].startswith("library/") else p["repository"]
    name = repo if p["registry"] == "docker.io" else f"{p['registry']}/{repo}"
    return name + (f":{p['tag']}" if p["tag"] else "") + (f"@{p['digest'][:19]}" if p["digest"] else "")


def violations(ref: str, cfg: Dict[str, Any], created: Optional[float] = None) -> List[Dict[str, Any]]:
    p = parse(ref)
    out = []
    regs = cfg.get("registries") or []
    if regs and p["registry"] not in regs and not (p["registry"] == "docker.io" and "docker.io" in regs):
        out.append({"rule": "registry", "text": f"Registry {p['registry']} ist nicht freigegeben ({', '.join(regs)})",
                    "block": True})
    if cfg.get("require_tag") and not p["digest"] and p["tag"] in ("", "latest"):
        out.append({"rule": "tag", "text": "„latest“ bzw. kein Tag – eine feste Version angeben (z.B. :1.27)", "block": True})
    if cfg.get("require_digest") and not p["digest"]:
        out.append({"rule": "digest", "text": "Ohne Digest – mit @sha256:… fixieren", "block": True})
    name = _short(p)
    full = f"{p['registry']}/{p['repository']}:{p['tag'] or 'latest'}"
    for pat in cfg.get("denied") or []:
        if fnmatch.fnmatch(name.lower(), pat) or fnmatch.fnmatch(full, pat) or fnmatch.fnmatch(ref.lower(), pat):
            out.append({"rule": "denied", "text": f"Passt auf die Sperrliste ({pat})", "block": True})
            break
    if cfg.get("max_age_days") and created and time.time() - created > cfg["max_age_days"] * 86400:
        days = int((time.time() - created) // 86400)
        out.append({"rule": "age", "text": f"Image ist {days} Tage alt (erlaubt: {cfg['max_age_days']})", "block": False})
    return out


# ---------------------------------------------------------------------------
# Audit of what runs now
# ---------------------------------------------------------------------------
def _created(images: List[str]) -> Dict[str, float]:
    import calendar
    out: Dict[str, float] = {}
    for img in images[:100]:
        try:
            res = subprocess.run(["docker", "image", "inspect", "--format", "{{.Created}}", img], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", res.stdout.strip())
        if res.returncode == 0 and m:
            out[img] = calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    return out


def audit() -> Dict[str, Any]:
    import containers
    cfg = config()
    exc = set(cfg["exceptions"])
    # Containers whose image shows as an ID (the tag moved on) can't be judged by name
    items = [c for c in containers.docker_list().get("containers", [])
             if c.get("image") and not re.fullmatch(r"(sha256:)?[0-9a-f]{12,64}", c["image"])]
    images = sorted({c["image"] for c in items})
    created = _created(images) if cfg["max_age_days"] else {}
    rows = []
    for c in items:
        stack = c.get("stack") or ""
        v = violations(c["image"], cfg, created.get(c["image"]))
        excepted = bool(stack and stack in exc) or c["name"] in exc
        rows.append({"container": c["name"], "stack": stack, "service": c.get("service", ""), "image": c["image"],
                     "state": c.get("state", ""), "violations": v, "excepted": excepted})
    rows.sort(key=lambda r: (r["excepted"] or not r["violations"], r["stack"], r["container"]))
    bad = [r for r in rows if r["violations"] and not r["excepted"]]
    return {"config": cfg, "modes": MODES, "rules": RULE_LABELS, "containers": rows, "checked": int(time.time()),
            "counts": {"containers": len(rows), "violating": len(bad), "excepted": sum(1 for r in rows if r["excepted"] and r["violations"]),
                       "registries": sorted({parse(i)["registry"] for i in images})}}


def alerts() -> List[Dict[str, Any]]:
    cfg = config()
    if cfg["mode"] == "off":
        return []
    try:
        n = audit()["counts"]["violating"]
    except Exception:
        return []
    return [{"level": "warning", "text": f"{n} {'Container verstößt' if n == 1 else 'Container verstoßen'} gegen die Image-Richtlinien"}] if n else []


# ---------------------------------------------------------------------------
# Before a deployment
# ---------------------------------------------------------------------------
def gate_step(project: str, compose_args: List[str]) -> Optional[Dict[str, Any]]:
    """The task step that checks the compose file before pulling - None when the policy is off."""
    if config()["mode"] == "off":
        return None
    return {"argv": [sys.executable, SCRIPT, "check", project, *compose_args], "show": "Image-Richtlinien prüfen"}


def _check_cli(project: str, args: List[str]) -> int:
    cfg = config()
    if cfg["mode"] == "off":
        return 0
    if project in set(cfg["exceptions"]):
        print(f"Stack {project} ist von den Image-Richtlinien ausgenommen.")
        return 0
    res = subprocess.run(["docker", "compose", *args, "config", "--format", "json"], capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        print((res.stderr or res.stdout).strip()[-500:])
        return 1
    services = (json.loads(res.stdout).get("services") or {})
    blocking = 0
    for name, svc in services.items():
        image = (svc or {}).get("image")
        if not image:
            print(f"{name}: wird gebaut (build) – nicht geprüft")
            continue
        found = [v for v in violations(image, cfg) if v["block"]]
        if not found:
            print(f"{name}: {image} – OK")
        for v in found:
            blocking += 1
            print(f"{name}: {image} – {v['text']}")
    if blocking and cfg["mode"] == "block":
        print(f"\nAbgebrochen: {blocking} {'Verstoß' if blocking == 1 else 'Verstöße'} gegen die Image-Richtlinien "
              "(Container → Sicherheitscheck → Image-Richtlinien; Ausnahme für den Stack möglich).")
        return 1
    if blocking:
        print(f"\nWarnung: {blocking} {'Verstoß' if blocking == 1 else 'Verstöße'} – Modus „Warnen“, es geht weiter.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        sys.path.insert(0, BASE_DIR)
        sys.exit(_check_cli(sys.argv[2], sys.argv[3:]))
    print(__doc__)
    sys.exit(2)
