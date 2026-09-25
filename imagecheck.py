"""
Image update check for Docker containers and k3s pods: compares the digest of the
image that is running with what the registry serves for the same tag right now
(HEAD on the manifest - doesn't count against Docker Hub's pull limit).
Public images only: registries are asked anonymously, credentials are never read.
"""

import logging
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("pi_dashboard.imagecheck")

CHECK_INTERVAL = 12 * 3600
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])
_state: Dict[str, Any] = {"running": False}
_lock = threading.Lock()


def parse_ref(image: str) -> Optional[Dict[str, str]]:
    """nginx -> docker.io / library/nginx : latest; ghcr.io/org/app:1.2 -> ghcr.io / org/app : 1.2"""
    image = image.strip()
    if not image or image.startswith("sha256:"):
        return None
    digest = ""
    if "@" in image:
        image, digest = image.split("@", 1)
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = "docker.io", image
    tag = "latest"
    m = re.match(r"^(.*):([\w][\w.-]{0,127})$", path)
    if m:
        path, tag = m.group(1), m.group(2)
    if registry in ("docker.io", "index.docker.io", "registry-1.docker.io"):
        registry = "docker.io"
        if "/" not in path:
            path = f"library/{path}"
    if not re.match(r"^[a-z0-9]+([._/-][a-z0-9]+)*$", path):
        return None
    return {"registry": registry, "repo": path, "tag": tag, "pinned": digest,
            "ref": f"{registry}/{path}:{tag}"}


def _registry_host(registry: str) -> str:
    return "registry-1.docker.io" if registry == "docker.io" else registry


def remote_digest(registry: str, repo: str, tag: str, client: httpx.Client) -> Tuple[Optional[str], str]:
    url = f"https://{_registry_host(registry)}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    res = client.head(url, headers=headers)
    if res.status_code == 401:
        auth = res.headers.get("www-authenticate", "")
        m = dict(re.findall(r'(\w+)="([^"]*)"', auth))
        if not auth.lower().startswith("bearer") or "realm" not in m:
            return None, "Anmeldung nötig"
        params = {k: v for k, v in (("service", m.get("service")), ("scope", m.get("scope") or f"repository:{repo}:pull")) if v}
        tok = client.get(m["realm"], params=params)
        if tok.status_code != 200:
            return None, "Anmeldung nötig (privates Image?)"
        token = tok.json().get("token") or tok.json().get("access_token")
        headers["Authorization"] = f"Bearer {token}"
        res = client.head(url, headers=headers)
        if res.status_code == 405:  # some registries don't do HEAD
            res = client.get(url, headers=headers)
    if res.status_code == 404:
        return None, "Tag nicht (mehr) in der Registry"
    if res.status_code in (401, 403):
        return None, "Anmeldung nötig (privates Image?)"
    if res.status_code == 429:
        return None, "Registry-Limit erreicht, später erneut"
    if res.status_code >= 400:
        return None, f"Registry-Fehler {res.status_code}"
    digest = res.headers.get("docker-content-digest", "")
    return (digest, "") if digest.startswith("sha256:") else (None, "Registry liefert keinen Digest")


# ---------------------------------------------------------------------------
# What runs locally
# ---------------------------------------------------------------------------
def _docker_images() -> List[Dict[str, Any]]:
    """Images used by containers with their local repo digests."""
    if not shutil.which("docker"):
        return []
    import containers
    data = containers._docker_list()
    if data.get("error"):
        return []
    try:
        res = subprocess.run(["docker", "image", "ls", "--digests", "--format", "{{.Repository}}\t{{.Tag}}\t{{.Digest}}"],
                             capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return []
    digests: Dict[str, set] = {}
    for line in res.stdout.splitlines():
        repo, tag, digest = (line.split("\t") + ["", "", ""])[:3]
        if digest.startswith("sha256:"):
            p = parse_ref(f"{repo}:{tag}" if tag and tag != "<none>" else repo)
            if p:
                digests.setdefault(p["ref"], set()).add(digest)
                # digest also valid for the repo without tag (docker lists <none> for re-pulled tags)
                digests.setdefault(f"{p['registry']}/{p['repo']}@", set()).add(digest)
    out = []
    for c in data["containers"]:
        p = parse_ref(c["image"])
        if not p:
            continue
        out.append({"ref": p, "local": sorted(digests.get(p["ref"], set())),
                    "user": {"kind": "stack" if c["stack"] else "docker", "name": c["stack"] or c["name"], "container": c["name"]}})
    return out


def _pod_images() -> List[Dict[str, Any]]:
    import containers
    import homelab
    if not homelab._kube_cmd() or homelab._kube_env() is None:
        return []
    data = containers._kube_overview()
    out = []
    for pod in data.get("pods", []):
        owner = pod["owner"] or {"kind": "Pod", "name": pod["name"]}
        for c in pod["containers"]:
            p = parse_ref(c["image"])
            if not p:
                continue
            m = re.search(r"@(sha256:[0-9a-f]{64})", c.get("image_id", ""))
            out.append({"ref": p, "local": [m.group(1)] if m else [],
                        "user": {"kind": owner["kind"].lower(), "name": owner["name"], "namespace": pod["namespace"]}})
    return out


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
def _check() -> None:
    import settings_store
    try:
        used = _docker_images() + _pod_images()
        images: Dict[str, Dict[str, Any]] = {}
        for u in used:
            p = u["ref"]
            item = images.setdefault(p["ref"], {**p, "local": set(), "users": []})
            item["local"].update(u["local"])
            if u["user"] not in item["users"]:
                item["users"].append(u["user"])
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "PiCore-Dashboard"}) as client:
            for item in images.values():
                if item["pinned"]:
                    item.update(status="pinned", remote="", error="")
                    continue
                if not item["local"]:
                    item.update(status="local", remote="", error="Kein Registry-Digest (lokal gebaut?)")
                    continue
                try:
                    digest, err = remote_digest(item["registry"], item["repo"], item["tag"], client)
                except httpx.HTTPError as e:
                    digest, err = None, f"Nicht erreichbar ({type(e).__name__})"
                if not digest:
                    item.update(status="unknown", remote="", error=err)
                else:
                    item.update(status="current" if digest in item["local"] else "update", remote=digest, error="")
        result = []
        for item in images.values():
            item["local"] = sorted(item["local"])
            result.append(item)
        order = {"update": 0, "unknown": 1, "local": 2, "pinned": 3, "current": 4}
        result.sort(key=lambda i: (order.get(i["status"], 5), i["ref"]))
        settings_store.set("image_updates", {"checked": int(time.time()), "images": result})
        logger.info(f"Image check: {sum(1 for i in result if i['status'] == 'update')} of {len(result)} have updates")
    except Exception as e:
        logger.warning(f"Image check failed: {e}")
        _state["error"] = str(e)[:200]
    finally:
        _state["running"] = False


def start_check() -> Dict[str, Any]:
    with _lock:
        if not _state["running"]:
            _state.update(running=True, error="")
            threading.Thread(target=_check, daemon=True).start()
    return status()


def status() -> Dict[str, Any]:
    import settings_store
    data = settings_store.get("image_updates", {}) or {}
    images = data.get("images", [])
    return {"checked": data.get("checked", 0), "images": images, "running": _state["running"],
            "error": _state.get("error", ""), "updates": sum(1 for i in images if i.get("status") == "update")}


def check_if_due() -> None:
    import settings_store
    data = settings_store.get("image_updates", {}) or {}
    if time.time() - data.get("checked", 0) > CHECK_INTERVAL and (shutil.which("docker") or shutil.which("kubectl") or shutil.which("k3s")):
        start_check()
