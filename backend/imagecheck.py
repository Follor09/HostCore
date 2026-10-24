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
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "HostCore-Dashboard"}) as client:
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


# ---------------------------------------------------------------------------
# Release notes: what changed in the new version (GitHub releases of the image's source)
# ---------------------------------------------------------------------------
RELEASES_TTL = 6 * 3600
SOURCES_KEY = "release_sources"   # own mapping image repo -> GitHub owner/repo
_releases: Dict[str, Dict[str, Any]] = {}
GH_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)", re.I)


def _local_image(item: Dict[str, Any]) -> Dict[str, Any]:
    """Labels and creation time of the local image (Docker only)."""
    if not shutil.which("docker"):
        return {}
    repo = item["repo"][8:] if item["registry"] == "docker.io" and item["repo"].startswith("library/") else item["repo"]
    name = f"{repo}:{item['tag']}" if item["registry"] == "docker.io" else f"{item['registry']}/{item['repo']}:{item['tag']}"
    try:
        res = subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}\t{{.Created}}", name],
                             capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {}
    if res.returncode != 0 or "\t" not in res.stdout:
        return {}
    labels_raw, created = res.stdout.strip().split("\t", 1)
    try:
        import json
        labels = json.loads(labels_raw) or {}
    except ValueError:
        labels = {}
    return {"labels": labels, "created": created}


def _remote_labels(item: Dict[str, Any], client: httpx.Client) -> Dict[str, str]:
    """Labels from the image config in the registry (works for k3s images too)."""
    host = _registry_host(item["registry"])
    base = f"https://{host}/v2/{item['repo']}"
    headers = {"Accept": ACCEPT}

    def get(url: str, accept: str = ACCEPT) -> httpx.Response:
        h = {**headers, "Accept": accept}
        res = client.get(url, headers=h)
        if res.status_code == 401 and "Authorization" not in headers:
            m = dict(re.findall(r'(\w+)="([^"]*)"', res.headers.get("www-authenticate", "")))
            if "realm" in m:
                tok = client.get(m["realm"], params={k: v for k, v in (("service", m.get("service")),
                                                                       ("scope", m.get("scope") or f"repository:{item['repo']}:pull")) if v})
                if tok.status_code == 200:
                    headers["Authorization"] = f"Bearer {tok.json().get('token') or tok.json().get('access_token')}"
                    h["Authorization"] = headers["Authorization"]
                    res = client.get(url, headers=h)
        return res

    res = get(f"{base}/manifests/{item['tag']}")
    if res.status_code != 200:
        return {}
    man = res.json()
    if man.get("manifests"):  # multi-arch index: prefer arm64, then the first real image
        ms = [m for m in man["manifests"] if (m.get("platform") or {}).get("os") != "unknown"]
        pick = next((m for m in ms if (m.get("platform") or {}).get("architecture") == "arm64"), ms[0] if ms else None)
        if not pick:
            return {}
        res = get(f"{base}/manifests/{pick['digest']}")
        if res.status_code != 200:
            return {}
        man = res.json()
    digest = (man.get("config") or {}).get("digest")
    if not digest:
        return {}
    res = get(f"{base}/blobs/{digest}", "application/json")
    if res.status_code != 200:
        return {}
    try:
        return (res.json().get("config") or {}).get("Labels") or {}
    except ValueError:
        return {}


def _github_repo(item: Dict[str, Any], labels: Dict[str, str], guess: bool = True) -> str:
    import settings_store
    own = (settings_store.get(SOURCES_KEY) or {}).get(item["repo"])
    if own:
        return own
    for key in ("org.opencontainers.image.source", "org.opencontainers.image.url", "org.label-schema.vcs-url"):
        m = GH_RE.search(labels.get(key, "") or "")
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    if not guess:
        return ""
    # The image names no source: the usual places for ghcr.io and linuxserver.io images
    if item["registry"] == "ghcr.io" and item["repo"].count("/") >= 1:
        owner, name = item["repo"].split("/")[:2]
        return f"{owner}/{name}"
    if item["registry"] in ("lscr.io", "docker.io") and item["repo"].startswith("linuxserver/"):
        return f"linuxserver/docker-{item['repo'].split('/', 1)[1]}"
    return ""


def set_source(repo: str, github: str) -> Dict[str, Any]:
    import settings_store
    github = (github or "").strip()
    m = GH_RE.search(github) if "github.com" in github else re.fullmatch(r"([\w.-]+)/([\w.-]+)", github)
    if github and not m:
        raise ValueError("GitHub-Repository als owner/name oder https://github.com/owner/name angeben.")
    sources = dict(settings_store.get(SOURCES_KEY) or {})
    if github:
        sources[repo] = f"{m.group(1)}/{m.group(2)}"
    else:
        sources.pop(repo, None)
    settings_store.set(SOURCES_KEY, sources)
    _releases.pop(repo, None)
    return {"repo": repo, "github": sources.get(repo, "")}


def releases(ref: str, refresh: bool = False) -> Dict[str, Any]:
    item = next((i for i in status()["images"] if i["ref"] == ref), None)
    if not item:
        p = parse_ref(ref)
        if not p:
            raise ValueError("Unbekanntes Image.")
        item = {**p, "local": []}
    hit = _releases.get(item["repo"])
    if hit and not refresh and time.time() - hit["ts"] < RELEASES_TTL:
        return hit["data"]
    local = _local_image(item)
    labels = local.get("labels") or {}
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "HostCore-Dashboard"}) as client:
        gh = _github_repo(item, labels, guess=False)
        if not gh:
            try:
                labels = {**_remote_labels(item, client), **labels}
            except (httpx.HTTPError, ValueError):
                pass
            gh = _github_repo(item, labels)
        # Version that runs here: only the local image knows it (the registry labels describe the newest one)
        current = (local.get("labels") or {}).get("org.opencontainers.image.version") or (item["tag"] if re.match(r"^v?\d", item["tag"]) else "")
        data: Dict[str, Any] = {"ref": item["ref"], "repo": item["repo"], "github": gh, "current": current,
                                "created": local.get("created", ""), "releases": [], "error": ""}
        if not gh:
            data["error"] = "Kein GitHub-Repository bekannt – das Image nennt keine Quelle. Unten eintragen."
        else:
            res = client.get(f"https://api.github.com/repos/{gh}/releases", params={"per_page": 10},
                             headers={"Accept": "application/vnd.github+json"})
            if res.status_code == 404:
                data["error"] = f"{gh}: kein öffentliches Repository oder keine Releases."
            elif res.status_code in (403, 429):
                data["error"] = "GitHub-Limit erreicht (60 Abfragen pro Stunde) – später erneut."
            elif res.status_code >= 400:
                data["error"] = f"GitHub-Fehler {res.status_code}"
            else:
                created = _iso_ts(local.get("created", ""))
                for r in res.json():
                    if r.get("draft"):
                        continue
                    published = _iso_ts(r.get("published_at") or "")
                    tag = r.get("tag_name", "")
                    is_current = bool(current) and tag.lstrip("v") == current.lstrip("v")
                    data["releases"].append({
                        "tag": tag, "name": r.get("name") or tag, "url": r.get("html_url", ""),
                        "published": int(published) if published else 0, "prerelease": bool(r.get("prerelease")),
                        "body": (r.get("body") or "")[:6000], "current": is_current,
                        "newer": bool(published and created and published > created + 3600) and not is_current})
                if not data["releases"]:
                    data["error"] = f"{gh} veröffentlicht keine Releases."
    _releases[item["repo"]] = {"ts": time.time(), "data": data}
    return data


def _iso_ts(value: str) -> Optional[float]:
    import datetime
    if not value:
        return None
    v = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        return datetime.datetime.fromisoformat(v).timestamp()
    except ValueError:
        return None
