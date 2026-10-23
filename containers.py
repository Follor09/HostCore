"""
Container page: Kubernetes (k3s) workloads, pods, services, nodes and warnings; Docker
containers with CPU/RAM/network history; docker compose stacks with start/stop/update
jobs. Runs as the dashboard user (docker group, kubeconfig in ~/.kube) - no sudo.

Every name that comes from the browser is checked against what docker / kubectl
report right now before it ends up in a command.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import history
import homelab
import settings_store

logger = logging.getLogger("pi_dashboard.containers")

CACHE_TTL = 8
_cache: Dict[str, Any] = {}
_lock = threading.Lock()

K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
KINDS = {"deployment": "Deployment", "statefulset": "StatefulSet", "daemonset": "DaemonSet"}


class ContainerError(Exception):
    pass


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def invalidate() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# Unit parsing
# ---------------------------------------------------------------------------
_UNITS = {"b": 1, "kb": 1e3, "kib": 1024, "mb": 1e6, "mib": 1024 ** 2, "gb": 1e9, "gib": 1024 ** 3,
          "tb": 1e12, "tib": 1024 ** 4, "k": 1e3, "ki": 1024, "m": 1e6, "mi": 1024 ** 2, "g": 1e9, "gi": 1024 ** 3}


def parse_size(text: str) -> float:
    m = re.match(r"^\s*([\d.]+)\s*([A-Za-z]*)\s*$", text or "")
    if not m:
        return 0.0
    return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1)


def parse_cpu_quantity(text: str) -> float:
    """kubectl top: "250m" -> 25.0 (% of one core), "2" -> 200.0"""
    text = (text or "").strip()
    if text.endswith("m"):
        return float(text[:-1] or 0) / 10
    if text.endswith("n"):
        return float(text[:-1] or 0) / 1e7
    try:
        return float(text) * 100
    except ValueError:
        return 0.0


def _age_seconds(ts: str) -> Optional[int]:
    from datetime import datetime, timezone
    try:
        return int((datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds())
    except Exception:
        return None


def _epoch(ts: str) -> int:
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------
def _kubectl(args: List[str], timeout: float = 15) -> subprocess.CompletedProcess:
    cmd, env = homelab._kube_cmd(), homelab._kube_env()
    if not cmd:
        raise ContainerError("kubectl / k3s nicht gefunden.")
    if env is None:
        raise ContainerError("Keine lesbare kubeconfig gefunden.")
    return subprocess.run(cmd + args + ["--request-timeout=10s"], capture_output=True, text=True,
                          timeout=timeout, env=env, errors="replace")


def _owner(meta: Dict[str, Any]) -> Dict[str, str]:
    for ref in meta.get("ownerReferences", []) or []:
        kind, name = ref.get("kind", ""), ref.get("name", "")
        if kind == "ReplicaSet":
            # web-5d9f8c7b6d -> Deployment web
            return {"kind": "Deployment", "name": re.sub(r"-[a-z0-9]{6,10}$", "", name)}
        if kind in ("StatefulSet", "DaemonSet", "Job", "Deployment"):
            return {"kind": kind, "name": name}
        if kind == "Node":
            return {"kind": "Node", "name": name}  # static pod
    return {}


def _parse_pod(item: Dict[str, Any], top: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    meta, status, spec = item.get("metadata", {}), item.get("status", {}), item.get("spec", {})
    cstat = {c.get("name"): c for c in status.get("containerStatuses", []) or []}
    containers = []
    for c in spec.get("containers", []):
        s = cstat.get(c.get("name"), {})
        state = next(iter(s.get("state", {}) or {}), "")
        reason = (s.get("state", {}).get(state, {}) or {}).get("reason", "") if state else ""
        last = (s.get("lastState", {}) or {}).get("terminated", {}) or {}
        containers.append({"name": c.get("name", ""), "image": c.get("image", ""), "ready": bool(s.get("ready")),
                           "restarts": s.get("restartCount", 0), "state": reason or state,
                           "last_reason": last.get("reason", ""), "last_exit": last.get("exitCode"),
                           "image_id": s.get("imageID", "")})
    ready = sum(1 for c in containers if c["ready"])
    phase = status.get("phase", "Unknown")
    waiting = next((c["state"] for c in containers if c["state"] not in ("running", "terminated", "") and c["state"]), "")
    display = "Terminating" if meta.get("deletionTimestamp") else waiting or phase
    if phase == "Succeeded":
        level = "inactive"
    elif display == "Terminating" or waiting in ("ContainerCreating", "PodInitializing") or phase == "Pending":
        level = "warning"
    elif waiting or phase in ("Failed", "Unknown") or (phase == "Running" and ready < len(containers)):
        level = "error"
    else:
        level = "active" if phase == "Running" else "inactive"
    key = f"{meta.get('namespace')}/{meta.get('name')}"
    use = top.get(key, {})
    return {
        "namespace": meta.get("namespace", ""), "name": meta.get("name", ""), "status": display, "level": level,
        "ready": f"{ready}/{len(containers)}", "restarts": sum(c["restarts"] for c in containers),
        "age": _age_seconds(meta.get("creationTimestamp", "")), "node": spec.get("nodeName", ""),
        "ip": status.get("podIP", ""), "owner": _owner(meta), "containers": containers,
        "cpu": use.get("cpu"), "mem": use.get("mem"),
        "oom": any(c["last_reason"] == "OOMKilled" for c in containers),
    }


def _parse_workload(item: Dict[str, Any]) -> Dict[str, Any]:
    meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
    kind = item.get("kind", "")
    if kind == "DaemonSet":
        desired, ready = status.get("desiredNumberScheduled", 0), status.get("numberReady", 0)
        updated = status.get("updatedNumberScheduled", 0)
    else:
        desired, ready = spec.get("replicas", 1) if spec.get("replicas") is not None else 1, status.get("readyReplicas", 0) or 0
        updated = status.get("updatedReplicas", 0) or 0
    images = [c.get("image", "") for c in spec.get("template", {}).get("spec", {}).get("containers", [])]
    message = ""
    for cond in status.get("conditions", []) or []:
        if cond.get("status") == "False" and cond.get("message"):
            message = cond["message"]
    level = "inactive" if desired == 0 else "active" if ready >= desired else "error" if ready == 0 else "warning"
    return {"kind": kind, "namespace": meta.get("namespace", ""), "name": meta.get("name", ""),
            "desired": desired, "ready": ready, "updated": updated, "images": images, "level": level,
            "age": _age_seconds(meta.get("creationTimestamp", "")), "message": message[:300],
            "restarted": (spec.get("template", {}).get("metadata", {}).get("annotations", {}) or {}).get("kubectl.kubernetes.io/restartedAt", "")}


def _parse_service(item: Dict[str, Any]) -> Dict[str, Any]:
    meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
    ports = []
    for p in spec.get("ports", []) or []:
        text = f"{p.get('port')}"
        if p.get("targetPort") not in (None, p.get("port")):
            text += f"→{p.get('targetPort')}"
        if p.get("nodePort"):
            text += f" (Node {p['nodePort']})"
        ports.append(f"{text}/{p.get('protocol', 'TCP').lower()}")
    external = [i.get("ip") or i.get("hostname") for i in (status.get("loadBalancer", {}) or {}).get("ingress", []) or []]
    external += spec.get("externalIPs", []) or []
    return {"namespace": meta.get("namespace", ""), "name": meta.get("name", ""), "type": spec.get("type", ""),
            "cluster_ip": spec.get("clusterIP", ""), "ports": ports, "external": [e for e in external if e],
            "age": _age_seconds(meta.get("creationTimestamp", ""))}


def _parse_node(item: Dict[str, Any], top: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    meta, status = item.get("metadata", {}), item.get("status", {})
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []) or [])
    pressure = [c.get("type") for c in status.get("conditions", []) or []
                if c.get("type") in ("MemoryPressure", "DiskPressure", "PIDPressure") and c.get("status") == "True"]
    info, cap = status.get("nodeInfo", {}), status.get("capacity", {})
    roles = [k.split("/", 1)[1] for k in (meta.get("labels", {}) or {}) if k.startswith("node-role.kubernetes.io/")]
    use = top.get(meta.get("name", ""), {})
    return {"name": meta.get("name", ""), "ready": ready, "pressure": pressure, "roles": roles,
            "version": info.get("kubeletVersion", ""), "os": info.get("osImage", ""), "arch": info.get("architecture", ""),
            "runtime": info.get("containerRuntimeVersion", ""), "cpu_cores": parse_cpu_quantity(cap.get("cpu", "0")) / 100,
            "mem_bytes": parse_size(cap.get("memory", "0")), "pods_max": int(cap.get("pods", 0) or 0),
            "cpu": use.get("cpu"), "mem": use.get("mem"), "age": _age_seconds(meta.get("creationTimestamp", ""))}


def _kube_top(kind: str) -> Dict[str, Dict[str, float]]:
    """kubectl top pods -A / nodes (metrics-server, built into k3s). Empty if not available."""
    args = ["top", "pods", "-A", "--no-headers"] if kind == "pods" else ["top", "nodes", "--no-headers"]
    try:
        res = _kubectl(args, timeout=12)
    except (ContainerError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if kind == "pods" and len(parts) >= 4:
            out[f"{parts[0]}/{parts[1]}"] = {"cpu": parse_cpu_quantity(parts[2]), "mem": parse_size(parts[3])}
        elif kind == "nodes" and len(parts) >= 4:
            out[parts[0]] = {"cpu": parse_cpu_quantity(parts[1]), "mem": parse_size(parts[3])}
    return out


def _kube_overview() -> Dict[str, Any]:
    if not homelab._kube_cmd():
        return {"available": False}
    if homelab._kube_env() is None:
        return {"available": True, "error": "Keine lesbare kubeconfig gefunden.",
                "hint": "mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config"}
    try:
        res = _kubectl(["get", "pods,deployments,statefulsets,daemonsets,services", "-A", "-o", "json"], timeout=20)
        nodes_res = _kubectl(["get", "nodes", "-o", "json"])
    except subprocess.TimeoutExpired:
        return {"available": True, "error": "kubectl antwortet nicht."}
    except ContainerError as e:
        return {"available": True, "error": str(e)}
    if res.returncode != 0:
        return {"available": True, "error": res.stderr.strip()[:300] or "kubectl fehlgeschlagen"}
    top_pods, top_nodes = _kube_top("pods"), _kube_top("nodes")
    items = json.loads(res.stdout).get("items", [])
    pods, workloads, services = [], [], []
    for item in items:
        kind = item.get("kind")
        if kind == "Pod":
            pods.append(_parse_pod(item, top_pods))
        elif kind in ("Deployment", "StatefulSet", "DaemonSet"):
            workloads.append(_parse_workload(item))
        elif kind == "Service":
            services.append(_parse_service(item))
    nodes = [_parse_node(n, top_nodes) for n in json.loads(nodes_res.stdout or "{}").get("items", [])] if nodes_res.returncode == 0 else []
    rank = {"error": 0, "warning": 1, "active": 2, "inactive": 3}
    pods.sort(key=lambda p: (rank[p["level"]], p["namespace"], p["name"]))
    workloads.sort(key=lambda w: (rank[w["level"]], w["namespace"], w["name"]))
    services.sort(key=lambda s: (s["namespace"], s["name"]))
    namespaces = sorted({p["namespace"] for p in pods} | {w["namespace"] for w in workloads} | {s["namespace"] for s in services})
    return {"available": True, "pods": pods, "workloads": workloads, "services": services, "nodes": nodes,
            "namespaces": namespaces, "metrics": bool(top_pods), "events": _kube_events()}


def _kube_events() -> List[Dict[str, Any]]:
    try:
        res = _kubectl(["get", "events", "-A", "--field-selector", "type=Warning", "-o", "json"])
    except (ContainerError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    out = []
    for e in json.loads(res.stdout).get("items", []):
        obj = e.get("involvedObject", {}) or e.get("regarding", {})
        ts = e.get("lastTimestamp") or e.get("eventTime") or (e.get("metadata", {}) or {}).get("creationTimestamp", "")
        out.append({"namespace": obj.get("namespace", "") or e.get("metadata", {}).get("namespace", ""),
                    "kind": obj.get("kind", ""), "name": obj.get("name", ""), "reason": e.get("reason", ""),
                    "message": (e.get("message") or e.get("note") or "")[:400], "count": e.get("count") or 1,
                    "ts": _epoch(ts)})
    out.sort(key=lambda e: -e["ts"])
    return out[:60]


def kube_overview() -> Dict[str, Any]:
    return _cached("kube", CACHE_TTL, _kube_overview)


def _find_workload(kind: str, namespace: str, name: str) -> Dict[str, Any]:
    if kind not in KINDS or not K8S_NAME.match(namespace or "") or not K8S_NAME.match(name or ""):
        raise ContainerError("Ungültiger Name.")
    data = _kube_overview()
    w = next((w for w in data.get("workloads", []) if w["kind"] == KINDS[kind] and w["namespace"] == namespace and w["name"] == name), None)
    if not w:
        raise ContainerError(f"{KINDS[kind]} {namespace}/{name} nicht gefunden.")
    return w


def kube_restart(kind: str, namespace: str, name: str) -> str:
    _find_workload(kind, namespace, name)
    res = _kubectl(["rollout", "restart", f"{kind}/{name}", "-n", namespace])
    invalidate()
    if res.returncode != 0:
        raise ContainerError(res.stderr.strip()[:300] or "Neustart fehlgeschlagen.")
    return f"{name} wird neu gestartet."


def kube_scale(kind: str, namespace: str, name: str, replicas: int) -> str:
    if kind not in ("deployment", "statefulset"):
        raise ContainerError("Nur Deployments und StatefulSets lassen sich skalieren.")
    if not 0 <= int(replicas) <= 20:
        raise ContainerError("Replikas: 0–20.")
    w = _find_workload(kind, namespace, name)
    import settings_store
    remembered = settings_store.get("kube_scaled", {}) or {}
    key = f"{kind}/{namespace}/{name}"
    if int(replicas) == 0 and w["desired"] > 0:
        remembered[key] = w["desired"]      # "Starten" brings back the old count
    elif int(replicas) > 0:
        remembered.pop(key, None)
    settings_store.set("kube_scaled", remembered)
    res = _kubectl(["scale", f"{kind}/{name}", f"--replicas={int(replicas)}", "-n", namespace])
    invalidate()
    if res.returncode != 0:
        raise ContainerError(res.stderr.strip()[:300] or "Skalieren fehlgeschlagen.")
    return f"{name}: {int(replicas)} {'Replika' if int(replicas) == 1 else 'Replikas'}."


def kube_scaled() -> Dict[str, int]:
    import settings_store
    return settings_store.get("kube_scaled", {}) or {}


def _find_pod(namespace: str, name: str) -> Dict[str, Any]:
    if not K8S_NAME.match(namespace or "") or not K8S_NAME.match(name or ""):
        raise ContainerError("Ungültiger Name.")
    pod = next((p for p in _kube_overview().get("pods", []) if p["namespace"] == namespace and p["name"] == name), None)
    if not pod:
        raise ContainerError(f"Pod {namespace}/{name} nicht gefunden.")
    return pod


def kube_delete_pod(namespace: str, name: str) -> str:
    pod = _find_pod(namespace, name)
    if pod["owner"].get("kind") not in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        raise ContainerError("Dieser Pod gehört zu keinem Controller und würde nicht neu erstellt.")
    res = _kubectl(["delete", "pod", name, "-n", namespace, "--wait=false"])
    invalidate()
    if res.returncode != 0:
        raise ContainerError(res.stderr.strip()[:300] or "Löschen fehlgeschlagen.")
    return f"{name} wird neu erstellt."


def kube_logs(namespace: str, name: str, container: str = "", lines: int = 300, previous: bool = False) -> Dict[str, Any]:
    pod = _find_pod(namespace, name)
    names = [c["name"] for c in pod["containers"]]
    container = container or (names[0] if names else "")
    if container not in names:
        raise ContainerError("Container nicht gefunden.")
    lines = max(10, min(int(lines), 2000))
    args = ["logs", name, "-n", namespace, "-c", container, f"--tail={lines}", "--timestamps"]
    if previous:
        args.append("--previous")
    res = _kubectl(args, timeout=20)
    log = res.stdout if res.returncode == 0 else res.stderr.strip()
    return {"namespace": namespace, "name": name, "container": container, "containers": names,
            "log": log[-300_000:], "ok": res.returncode == 0}


# ---------------------------------------------------------------------------
# Docker containers + compose stacks
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    """A field of Docker's JSON output as text, whether it comes as string, list or map."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return ",".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _docker(args: List[str], timeout: float = 15, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, errors="replace", cwd=cwd)


def _labels(text: Any) -> Dict[str, str]:
    """Labels of `docker ps --format json`: "a=1,b=2" - newer Docker versions may give a map."""
    if isinstance(text, dict):
        return {str(k): str(v) for k, v in text.items()}
    out = {}
    for part in _text(text).split(","):
        k, _, v = part.partition("=")
        if k:
            out[k.strip()] = v
    return out


def _docker_list() -> Dict[str, Any]:
    if not shutil.which("docker"):
        return {"available": False, "containers": []}
    try:
        res = _docker(["ps", "-a", "--no-trunc", "--format", "{{json .}}"], timeout=10)
    except subprocess.TimeoutExpired:
        return {"available": True, "error": "Docker antwortet nicht.", "containers": []}
    if res.returncode != 0:
        err = res.stderr.strip()
        if "permission denied" in err.lower():
            return {"available": True, "containers": [], "error": "Keine Berechtigung für Docker.",
                    "hint": f"sudo usermod -aG docker {os.environ.get('USER', '$USER')} && sudo systemctl restart raspberry-dashboard"}
        return {"available": True, "containers": [], "error": err[:300] or "docker ps fehlgeschlagen"}
    containers = []
    for line in res.stdout.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        labels = _labels(c.get("Labels", ""))
        if not isinstance(c, dict):
            continue
        state, status = _text(c.get("State")), _text(c.get("Status"))
        health = "unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status else ""
        level = ("error" if health == "unhealthy" or state in ("dead", "restarting")
                 or (state == "exited" and not status.startswith("Exited (0)")) else
                 "active" if state == "running" else "inactive")
        containers.append({
            "id": _text(c.get("ID"))[:12], "name": _text(c.get("Names")).lstrip("/"), "image": _text(c.get("Image")), "state": state,
            "status": status, "health": health, "level": level, "ports": _text(c.get("Ports")),
            "created": _text(c.get("CreatedAt")), "stack": labels.get("com.docker.compose.project", ""),
            "service": labels.get("com.docker.compose.service", ""),
            "config_files": labels.get("com.docker.compose.project.config_files", ""),
            "working_dir": labels.get("com.docker.compose.project.working_dir", ""),
        })
    rank = {"error": 0, "active": 1, "inactive": 2}
    containers.sort(key=lambda c: (rank[c["level"]], c["stack"] or "~", c["name"]))
    return {"available": True, "containers": containers}


def docker_list() -> Dict[str, Any]:
    return _cached("docker", CACHE_TTL, _docker_list)


def _stacks() -> List[Dict[str, Any]]:
    data = _docker_list()
    if not data.get("available") or data.get("error"):
        return []
    stacks: Dict[str, Dict[str, Any]] = {}
    try:
        res = _docker(["compose", "ls", "-a", "--format", "json"], timeout=10)
        if res.returncode == 0:
            for s in json.loads(res.stdout or "[]"):
                if not isinstance(s, dict):
                    continue
                files = s.get("ConfigFiles") or ""
                files = files if isinstance(files, list) else _text(files).split(",")
                stacks[_text(s.get("Name"))] = {"name": _text(s.get("Name")), "status": _text(s.get("Status")),
                                                "config_files": [str(f).strip() for f in files if str(f).strip()]}
    except (subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        pass
    # Stacks docker compose ls doesn't know (older compose): from container labels
    for c in data["containers"]:
        if c["stack"]:
            s = stacks.setdefault(c["stack"], {"name": c["stack"], "status": "",
                                               "config_files": [f for f in c["config_files"].split(",") if f]})
            if not s["config_files"] and c["config_files"]:
                s["config_files"] = [f for f in c["config_files"].split(",") if f]
    out = []
    for s in stacks.values():
        members = [c for c in data["containers"] if c["stack"] == s["name"]]
        running = sum(1 for c in members if c["state"] == "running")
        files = [f for f in s["config_files"] if f.startswith("/")]
        level = ("error" if any(c["level"] == "error" for c in members) else
                 "active" if members and running == len(members) else "warning" if running else "inactive")
        out.append({"name": s["name"], "files": files, "file_exists": bool(files) and all(os.path.isfile(f) for f in files),
                    "dir": os.path.dirname(files[0]) if files else "", "running": running, "total": len(members),
                    "level": level, "containers": [{"name": c["name"], "service": c["service"], "state": c["state"],
                                                    "status": c["status"], "image": c["image"], "level": c["level"]} for c in members],
                    "images": sorted({c["image"] for c in members}),
                    "services": sorted({c["service"] for c in members if c["service"]}),
                    "limits": limits().get(s["name"], {})})
    rank = {"error": 0, "warning": 1, "active": 2, "inactive": 3}
    out.sort(key=lambda s: (rank[s["level"]], s["name"]))
    return out


def stacks() -> List[Dict[str, Any]]:
    return _cached("stacks", CACHE_TTL, _stacks)


# ---------------------------------------------------------------------------
# Combined live logs of several containers (like Dozzle)
# ---------------------------------------------------------------------------
_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})\s?(.*)$")
MULTI_LOG_MAX = 3000


def _container_log_lines(name: str, since: float, tail: int) -> List[Dict[str, Any]]:
    import calendar
    args = ["logs", "--timestamps", "--tail", str(tail)]
    if since:
        args += ["--since", f"{since:.6f}"]
    try:
        res = _docker(args + [name], timeout=15)
    except subprocess.TimeoutExpired:
        return [{"ts": time.time(), "container": name, "stream": "err", "text": "(docker logs antwortet nicht)"}]
    out = []
    for stream, text in (("out", res.stdout), ("err", res.stderr)):
        for line in text.splitlines():
            m = _LOG_TS.match(line)
            if not m:
                continue
            ts = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) + float(m.group(2) or 0)
            out.append({"ts": ts, "container": name, "stream": stream, "text": m.group(4)[:4000]})
    return out


def multi_logs(names: List[str], since: float = 0, tail: int = 200) -> Dict[str, Any]:
    """Log lines of several containers merged by time. since: only newer lines (for live polling)."""
    import concurrent.futures
    known = {c["name"] for c in _docker_list().get("containers", [])}
    names = [n for n in dict.fromkeys(names) if n in known][:12]
    if not names:
        return {"lines": [], "names": [], "until": since}
    tail = max(10, min(int(tail), 1000))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(names))) as pool:
        parts = list(pool.map(lambda n: _container_log_lines(n, since, tail), names))
    lines = sorted((l for p in parts for l in p if not since or l["ts"] > since), key=lambda l: l["ts"])[-MULTI_LOG_MAX:]
    return {"lines": lines, "names": names, "until": lines[-1]["ts"] if lines else since}


# ---------------------------------------------------------------------------
# Jobs: long compose commands run in the background, output polled by the page
# ---------------------------------------------------------------------------
_jobs: Dict[str, Dict[str, Any]] = {}
STACK_ACTIONS = {
    "up": ("Starten", [["up", "-d"]]),
    "stop": ("Stoppen", [["stop"]]),
    "restart": ("Neu starten", [["restart"]]),
    "update": ("Aktualisieren", [["pull"], ["up", "-d"]]),
    "down": ("Entfernen", [["down"]]),
}


def _run_job(job: Dict[str, Any], base: List[str], steps: List[List[str]], cwd: str) -> None:
    try:
        for step in steps:
            job["output"] += f"$ docker compose {' '.join(step)}\n"
            proc = subprocess.Popen(["docker", "compose", *base, *step], cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace")
            job["pid"] = proc.pid
            for line in proc.stdout:
                job["output"] = (job["output"] + line)[-200_000:]
            proc.wait(timeout=1800)
            if proc.returncode != 0:
                job.update(status="error", exit=proc.returncode)
                return
        job["status"] = "ok"
        if job["action"] == "update":
            import imagecheck
            imagecheck.start_check()   # the "update available" marks should go away
    except Exception as e:
        job["output"] += f"\n{e}\n"
        job["status"] = "error"
    finally:
        job["ended"] = time.time()
        invalidate()


def stack_action(name: str, action: str, user: str = "") -> Dict[str, Any]:
    if action not in STACK_ACTIONS:
        raise ContainerError("Unbekannte Aktion.")
    stack = next((s for s in _stacks() if s["name"] == name), None)
    if not stack:
        raise ContainerError(f"Stack {name} nicht gefunden.")
    if not stack["file_exists"]:
        raise ContainerError("Compose-Datei nicht gefunden – der Stack lässt sich nur über docker selbst steuern.")
    files = list(stack["files"])
    override = os.path.join(stack["dir"], LIMITS_FILE)
    if os.path.isfile(override) and override not in files:
        files.append(override)      # resource limits set here survive "Aktualisieren" and recreates
    return compose_job(name, files, stack["dir"], action, user)


# ---------------------------------------------------------------------------
# Resource limits per stack service: applied at once with `docker update` and kept
# in an override file next to the stack (docker-compose.picore.yml) for recreates
# ---------------------------------------------------------------------------
LIMITS_KEY = "container_limits"
LIMITS_FILE = "docker-compose.picore.yml"


def limits() -> Dict[str, Dict[str, Dict[str, Any]]]:
    return dict(settings_store.get(LIMITS_KEY, {}) or {})


def _write_override(stack: Dict[str, Any], services: Dict[str, Dict[str, Any]]) -> None:
    path = os.path.join(stack["dir"], LIMITS_FILE)
    active = {svc: l for svc, l in services.items() if l.get("cpus") or l.get("mem_mb")}
    if not active and not os.path.isfile(path):
        return
    # Once used, compose remembers the file in the stack's labels: keep it (empty) instead of deleting it
    lines = ["# Ressourcen-Limits aus dem PiCore-Dashboard – wird dort verwaltet", "services:" if active else "services: {}"]
    for svc, l in sorted(active.items()):
        lines.append(f"  {json.dumps(svc)}:")
        if l.get("cpus"):
            lines.append(f"    cpus: {l['cpus']}")
        if l.get("mem_mb"):
            lines.append(f"    mem_limit: {json.dumps(str(l['mem_mb']) + 'm')}")
            lines.append(f"    memswap_limit: {json.dumps(str(l['mem_mb'] * 2) + 'm')}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def set_limits(stack_name: str, service: str, cpus: Optional[float], mem_mb: Optional[int]) -> Dict[str, Any]:
    import psutil
    stack = next((s for s in _stacks() if s["name"] == stack_name), None)
    if not stack:
        raise ContainerError(f"Stack {stack_name} nicht gefunden.")
    if not stack["dir"] or not stack["file_exists"]:
        raise ContainerError("Compose-Datei nicht gefunden – Limits brauchen den Stack-Ordner.")
    members = [c for c in stack["containers"] if c["service"] == service]
    if not members:
        raise ContainerError(f"Dienst {service} gehört nicht zu {stack_name}.")
    cores = psutil.cpu_count() or 4
    total_mb = psutil.virtual_memory().total // (1024 * 1024)
    if cpus is not None and not 0.1 <= float(cpus) <= cores:
        raise ContainerError(f"CPU: 0,1 bis {cores} Kerne.")
    if mem_mb is not None and not 32 <= int(mem_mb) <= total_mb:
        raise ContainerError(f"RAM: 32 bis {total_mb} MB.")
    all_limits = limits()
    services = dict(all_limits.get(stack_name, {}))
    services[service] = {"cpus": round(float(cpus), 2) if cpus else None, "mem_mb": int(mem_mb) if mem_mb else None}
    if not services[service]["cpus"] and not services[service]["mem_mb"]:
        services.pop(service)
    _write_override(stack, services)
    if services:
        all_limits[stack_name] = services
    else:
        all_limits.pop(stack_name, None)
    settings_store.set(LIMITS_KEY, all_limits)

    # Running containers get the limits right away
    args = ["update", "--cpus", str(round(float(cpus), 2)) if cpus else "0"]
    if mem_mb:
        args += ["--memory", f"{int(mem_mb)}m", "--memory-swap", f"{int(mem_mb) * 2}m"]
    notes = []
    for c in members:
        res = _docker(args + [c["name"]], timeout=20)
        if res.returncode != 0:
            err = res.stderr.strip()
            if "memory" in err.lower() and "cgroup" in err.lower():
                err = "Der Kernel unterstützt keine RAM-Limits (cgroup memory ist aus)."
            notes.append(f"{c['name']}: {err[:200]}")
    invalidate()
    msg = "Limits gesetzt." if not notes else "Limits gespeichert, aber nicht sofort übernommen: " + "; ".join(notes)
    if not mem_mb and (all_limits.get(stack_name, {}).get(service) is None):
        msg += " Ein entferntes RAM-Limit gilt erst nach dem nächsten „Aktualisieren“ oder Neuerstellen."
    return {"message": msg, "limits": limits(), "applied": not notes}


def compose_job(name: str, files: List[str], cwd: str, action: str, user: str = "") -> Dict[str, Any]:
    """Run a STACK_ACTIONS entry (or "install" from the app store) for a compose project in the background."""
    label, steps = STACK_ACTIONS.get(action) or ("Installieren", [["pull"], ["up", "-d"]])
    with _lock:
        if any(j["stack"] == name and j["status"] == "running" for j in _jobs.values()):
            raise ContainerError("Für diesen Stack läuft schon eine Aktion.")
        job_id = f"{int(time.time() * 1000):x}"
        job = {"id": job_id, "stack": name, "action": action, "label": label, "status": "running", "output": "",
               "started": time.time(), "ended": None, "user": user}
        _jobs[job_id] = job
        for old in sorted(_jobs, key=lambda k: _jobs[k]["started"])[:-20]:
            if _jobs[old]["status"] != "running":
                _jobs.pop(old)
    base = ["-p", name]
    for f in files:
        base += ["-f", f]
    threading.Thread(target=_run_job, args=(job, base, steps, cwd), daemon=True).start()
    return job


def get_job(job_id: str) -> Dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise ContainerError("Aktion nicht gefunden.")
    return job


def running_jobs() -> List[Dict[str, Any]]:
    return [j for j in _jobs.values() if j["status"] == "running" or (j["ended"] and time.time() - j["ended"] < 600)]


# ---------------------------------------------------------------------------
# Stats history (sampled once a minute from monitor.Monitor)
# ---------------------------------------------------------------------------
def init_db(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS container_samples (
        ts INTEGER, kind TEXT, name TEXT, cpu REAL, mem REAL, net_rx REAL, net_tx REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_container_samples ON container_samples (kind, name, ts)")


def _docker_stats() -> List[Dict[str, Any]]:
    if not shutil.which("docker"):
        return []
    try:
        res = _docker(["stats", "--no-stream", "--format", "{{json .}}"], timeout=30)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in res.stdout.splitlines():
        try:
            s = json.loads(line)
        except ValueError:
            continue
        rx, _, tx = (s.get("NetIO") or "").partition("/")
        out.append({"name": s.get("Name", ""), "cpu": float((s.get("CPUPerc") or "0").rstrip("%") or 0),
                    "mem": parse_size((s.get("MemUsage") or "").split("/")[0]), "net_rx": parse_size(rx), "net_tx": parse_size(tx)})
    return out


def sample(conn, now: int) -> None:
    init_db(conn)
    rows = [(now, "docker", s["name"], s["cpu"], s["mem"], s["net_rx"], s["net_tx"]) for s in _docker_stats() if s["name"]]
    if homelab._kube_cmd() and homelab._kube_env() is not None:
        for key, use in _kube_top("pods").items():
            rows.append((now, "pod", key, use["cpu"], use["mem"], None, None))
    if rows:
        conn.executemany("INSERT INTO container_samples VALUES (?,?,?,?,?,?,?)", rows)
    if now % 3600 < 60:
        conn.execute("DELETE FROM container_samples WHERE ts < ?", (now - history.retention_seconds(),))


def _pod_group(name: str) -> str:
    """ns/web-5d9f8c7b6d-x7k2p -> ns/web: pods of a deployment share one history line"""
    ns, _, pod = name.partition("/")
    base = re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$", "", pod)       # Deployment: name-<rs hash>-<id>
    if base == pod:
        base = re.sub(r"-\d{1,3}$", "", pod)                        # StatefulSet: name-0
    if base == pod:
        base = re.sub(r"-[a-z0-9]{5}$", "", pod)                     # DaemonSet / Job: name-<id>
    return f"{ns}/{base}"


def stats_summary() -> Dict[str, Any]:
    """Latest values + 24 h CPU sparkline per container / pod group."""
    import monitor
    now = int(time.time())
    since = now - 86400
    bucket = 1800
    out: Dict[str, Dict[str, Any]] = {}
    with monitor.connect() as conn:
        init_db(conn)
        rows = conn.execute("SELECT ts, kind, name, cpu, mem FROM container_samples WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    for ts, kind, name, cpu, mem in rows:
        key = f"{kind}:{_pod_group(name) if kind == 'pod' else name}"
        o = out.setdefault(key, {"kind": kind, "name": _pod_group(name) if kind == "pod" else name, "cpu": None, "mem": None,
                                 "ts": 0, "spark": {}, "mem_max": 0.0, "_last": {}})
        # several pods of one group at the same minute: add them up
        last = o["_last"]
        if last.get("ts") == ts:
            last["cpu"] += cpu or 0
            last["mem"] += mem or 0
        else:
            o["_last"] = last = {"ts": ts, "cpu": cpu or 0, "mem": mem or 0}
        o["cpu"], o["mem"], o["ts"] = last["cpu"], last["mem"], ts
        o["mem_max"] = max(o["mem_max"], last["mem"])
        b = (ts - since) // bucket
        o["spark"][b] = max(o["spark"].get(b, 0), last["cpu"])
    items = []
    for o in out.values():
        spark = [round(o["spark"].get(i, 0), 1) if i in o["spark"] else None for i in range(48)]
        items.append({"kind": o["kind"], "name": o["name"], "cpu": round(o["cpu"] or 0, 1), "mem": o["mem"],
                      "mem_max": o["mem_max"], "fresh": now - o["ts"] < 180, "spark": spark})
    return {"items": items}


def stats_history(kind: str, name: str, hours: int = 24) -> Dict[str, Any]:
    import monitor
    if kind not in ("docker", "pod") or not name or len(name) > 300:
        raise ContainerError("Ungültige Auswahl.")
    hours = 168 if int(hours) > 24 else 24
    bucket = 300 if hours == 24 else 1800
    since = int(time.time()) - hours * 3600
    with monitor.connect() as conn:
        init_db(conn)
        if kind == "docker":
            rows = conn.execute("SELECT ts, cpu, mem, net_rx, net_tx FROM container_samples WHERE kind = 'docker' AND name = ? AND ts >= ? ORDER BY ts",
                                (name, since)).fetchall()
        else:
            ns = name.partition("/")[0]
            summed: Dict[int, List[float]] = {}
            for ts, pod, cpu, mem in conn.execute(
                    "SELECT ts, name, cpu, mem FROM container_samples WHERE kind = 'pod' AND name LIKE ? AND ts >= ? ORDER BY ts",
                    (f"{ns}/%", since)):
                if _pod_group(pod) == name:
                    acc = summed.setdefault(ts, [0.0, 0.0])
                    acc[0] += cpu or 0
                    acc[1] += mem or 0
            rows = [(ts, c, m, None, None) for ts, (c, m) in sorted(summed.items())]
    points: Dict[int, Dict[str, Any]] = {}
    prev = None
    for ts, cpu, mem, rx, tx in rows:
        b = ts // bucket * bucket
        p = points.setdefault(b, {"ts": b, "cpu": [], "mem": [], "rx": [], "tx": []})
        p["cpu"].append(cpu or 0)
        p["mem"].append(mem or 0)
        if rx is not None and prev and prev[3] is not None and ts > prev[0] and rx >= prev[3] and tx >= prev[4]:
            p["rx"].append((rx - prev[3]) / (ts - prev[0]))
            p["tx"].append((tx - prev[4]) / (ts - prev[0]))
        prev = (ts, cpu, mem, rx, tx)
    avg = lambda v: round(sum(v) / len(v), 2) if v else None
    series = [{"ts": p["ts"], "cpu": avg(p["cpu"]), "cpu_max": round(max(p["cpu"]), 2) if p["cpu"] else None,
               "mem": avg(p["mem"]), "rx": avg(p["rx"]), "tx": avg(p["tx"])} for p in sorted(points.values(), key=lambda p: p["ts"])]
    return {"kind": kind, "name": name, "hours": hours, "bucket": bucket, "points": series}


# ---------------------------------------------------------------------------
# Disk use, cleanup, volumes & networks
# ---------------------------------------------------------------------------
def _json_lines(args: List[str], timeout: float = 30) -> List[Dict[str, Any]]:
    res = _docker(args, timeout=timeout)
    if res.returncode != 0:
        raise ContainerError((res.stderr or res.stdout).strip()[:300] or "docker fehlgeschlagen")
    out = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except ValueError:
            continue
        out.extend(v if isinstance(v, list) else [v])
    return [v for v in out if isinstance(v, dict)]


_SIZE_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}


def _size(text: Any) -> int:
    """'1.234GB' / '12.5MB (34%)' -> bytes (docker prints decimal units)."""
    m = re.match(r"^\s*([\d.]+)\s*([KMGT]?i?B)", _text(text), re.I)
    return int(float(m.group(1)) * _SIZE_UNITS.get(m.group(2).upper(), 1)) if m else 0


def _need_docker() -> None:
    if not shutil.which("docker"):
        raise ContainerError("Docker ist auf diesem Pi nicht installiert.")


def _container_mounts() -> Dict[str, List[str]]:
    """volume name -> containers using it."""
    used: Dict[str, List[str]] = {}
    for c in _json_lines(["ps", "-a", "--no-trunc", "--format", "{{json .}}"]):
        for m in [x.strip() for x in _text(c.get("Mounts")).split(",") if x.strip()]:
            used.setdefault(m, []).append(_text(c.get("Names")).lstrip("/"))
    return used


def disk_usage() -> Dict[str, Any]:
    """What Docker takes on disk and what could be freed - with the candidates for cleanup."""
    _need_docker()
    df = {_text(r.get("Type")): {"count": int(_text(r.get("TotalCount")) or 0), "active": int(_text(r.get("Active")) or 0),
                                  "size": _size(r.get("Size")), "reclaimable": _size(r.get("Reclaimable"))}
          for r in _json_lines(["system", "df", "--format", "{{json .}}"])}
    images = _json_lines(["image", "ls", "-a", "--no-trunc", "--format", "{{json .}}"])
    used_ids = set()
    res = _docker(["ps", "-a", "-q", "--no-trunc"], timeout=15)
    ids = res.stdout.split()
    if ids:
        ins = _docker(["inspect", "--format", "{{.Image}}", *ids[:500]], timeout=30)
        used_ids = {line.strip() for line in ins.stdout.splitlines() if line.strip()}
    dangling, unused = [], []
    for im in images:
        iid = _text(im.get("ID"))
        full = iid if iid.startswith("sha256:") else f"sha256:{iid}"
        item = {"id": iid.replace("sha256:", "")[:12], "repo": _text(im.get("Repository")), "tag": _text(im.get("Tag")),
                "size": _size(im.get("Size")), "created": _text(im.get("CreatedSince"))}
        if full in used_ids:
            continue
        (dangling if item["repo"] == "<none>" else unused).append(item)
    volumes = [{"name": _text(v.get("Name")), "driver": _text(v.get("Driver"))}
               for v in _json_lines(["volume", "ls", "-f", "dangling=true", "--format", "{{json .}}"])]
    vn = volumes_and_networks()
    sizes = {v["name"]: v["size"] for v in vn["volumes"]}
    for v in volumes:
        v["size"] = sizes.get(v["name"])
    nets = [n for n in vn["networks"] if not n["containers"] and not n["builtin"]]
    return {"df": df, "dangling_images": dangling, "unused_images": unused, "unused_volumes": volumes, "unused_networks": nets,
            "build_cache": df.get("Build Cache", {}).get("reclaimable", 0)}


CLEANUP = {
    "dangling_images": (["image", "prune", "-f"], "Unbenannte Images"),
    "unused_images": (["image", "prune", "-a", "-f"], "Ungenutzte Images"),
    "networks": (["network", "prune", "-f"], "Ungenutzte Netzwerke"),
    "build_cache": (["builder", "prune", "-f"], "Build-Cache"),
}


def cleanup(kinds: List[str], volumes: List[str], user: str = "") -> Dict[str, Any]:
    """Remove what was chosen; volumes only by name (their data is gone afterwards)."""
    _need_docker()
    results = []
    for k in kinds:
        if k not in CLEANUP:
            continue
        args, label = CLEANUP[k]
        res = _docker(args, timeout=600)
        freed = re.search(r"Total reclaimed space:\s*(\S+)", res.stdout)
        results.append({"kind": k, "label": label, "ok": res.returncode == 0,
                        "freed": _size(freed.group(1)) if freed else 0,
                        "message": "" if res.returncode == 0 else (res.stderr or res.stdout).strip()[:300]})
    vols = [v for v in dict.fromkeys(volumes) if re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$", v)]
    if vols:
        in_use = _container_mounts()
        busy = [v for v in vols if v in in_use]
        if busy:
            raise ContainerError(f"Volume wird noch benutzt: {', '.join(busy[:5])}")
        res = _docker(["volume", "rm", *vols], timeout=120)
        results.append({"kind": "volumes", "label": f"{len(vols)} Volumes", "ok": res.returncode == 0, "freed": 0,
                        "message": "" if res.returncode == 0 else (res.stderr or res.stdout).strip()[:300]})
    _cache.clear()
    logger.info(f"Docker cleanup by '{user}': {kinds} {vols}")
    return {"results": results, "usage": disk_usage()}


def volumes_and_networks() -> Dict[str, Any]:
    """All volumes (size, who uses them) and networks (subnet, containers)."""
    _need_docker()
    sizes: Dict[str, Optional[int]] = {}
    links: Dict[str, int] = {}
    res = _docker(["system", "df", "-v", "--format", "{{json .}}"], timeout=120)
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout.strip().splitlines()[0]) if res.stdout.strip() else {}
            for v in data.get("Volumes") or []:
                sizes[_text(v.get("Name"))] = _size(v.get("Size"))
                links[_text(v.get("Name"))] = int(_text(v.get("Links")) or 0)
        except (ValueError, IndexError):
            pass
    used = _container_mounts()
    volumes = []
    for v in _json_lines(["volume", "ls", "--format", "{{json .}}"]):
        name = _text(v.get("Name"))
        labels = _labels(v.get("Labels"))
        volumes.append({"name": name, "driver": _text(v.get("Driver")), "size": sizes.get(name),
                        "containers": used.get(name, []), "stack": labels.get("com.docker.compose.project", ""),
                        "anonymous": bool(re.fullmatch(r"[0-9a-f]{64}", name))})
    volumes.sort(key=lambda v: (-(v["size"] or 0), v["name"]))
    networks = []
    nets = _json_lines(["network", "ls", "--no-trunc", "--format", "{{json .}}"])
    if nets:
        ids = [_text(n.get("ID")) for n in nets]
        details = {}
        if ids:
            ins = _docker(["network", "inspect", *ids[:200]], timeout=30)
            try:
                for d in json.loads(ins.stdout or "[]"):
                    details[d.get("Id", "")] = d
            except ValueError:
                pass
        for n in nets:
            d = details.get(_text(n.get("ID")), {})
            ipam = (d.get("IPAM") or {}).get("Config") or []
            name = _text(n.get("Name"))
            networks.append({"name": name, "driver": _text(n.get("Driver")), "scope": _text(n.get("Scope")),
                             "subnet": ", ".join(c.get("Subnet", "") for c in ipam if c.get("Subnet")),
                             "gateway": ", ".join(c.get("Gateway", "") for c in ipam if c.get("Gateway")),
                             "internal": bool(d.get("Internal")), "builtin": name in ("bridge", "host", "none"),
                             "containers": sorted(c.get("Name", "") for c in (d.get("Containers") or {}).values()),
                             "stack": _labels(d.get("Labels") or {}).get("com.docker.compose.project", "")})
    networks.sort(key=lambda n: (n["builtin"], n["name"]))
    return {"volumes": volumes, "networks": networks, "total_size": sum(v["size"] or 0 for v in volumes)}


# ---------------------------------------------------------------------------
# New stack from a template (Container -> Stack-Vorlagen, also on remote Pis)
# ---------------------------------------------------------------------------
STACK_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def create_stack(name: str, compose: str, env: str = "", user: str = "") -> Dict[str, Any]:
    import appstore
    _need_docker()
    name = (name or "").strip().lower()
    if not STACK_NAME.match(name):
        raise ContainerError("Name: Kleinbuchstaben, Ziffern, - und _ (höchstens 41 Zeichen).")
    if any(s["name"] == name for s in stacks()):
        raise ContainerError(f"Es gibt schon einen Stack „{name}“.")
    if not compose.strip() or len(compose) > 200_000 or len(env) > 50_000:
        raise ContainerError("Compose-Datei fehlt oder ist zu groß.")
    folder = os.path.join(appstore.APPS_DIR, name)
    if os.path.exists(folder):
        raise ContainerError(f"Den Ordner {folder} gibt es schon.")
    os.makedirs(folder, mode=0o755)
    path = os.path.join(folder, "docker-compose.yml")
    with open(path, "w") as f:
        f.write(compose if compose.endswith("\n") else compose + "\n")
    if env.strip():
        with open(os.path.join(folder, ".env"), "w") as f:
            f.write(env if env.endswith("\n") else env + "\n")
        os.chmod(os.path.join(folder, ".env"), 0o600)
    check = _docker(["compose", "-p", name, "-f", path, "config", "-q"], timeout=30, cwd=folder)
    if check.returncode != 0:
        import shutil as _sh
        _sh.rmtree(folder, ignore_errors=True)
        raise ContainerError(f"Compose-Datei ungültig: {(check.stderr or check.stdout).strip()[:300]}")
    _cache.clear()
    return {**compose_job(name, [path], folder, "install", user), "dir": folder}


def stack_compose(name: str) -> Dict[str, Any]:
    """Compose file (and .env) of an existing stack - to save it as a template."""
    s = next((x for x in stacks() if x["name"] == name), None)
    if not s or not s.get("config_files"):
        raise ContainerError("Stack oder Compose-Datei nicht gefunden.")
    path = s["config_files"][0]
    try:
        with open(path, errors="replace") as f:
            compose = f.read(200_001)
    except OSError as e:
        raise ContainerError(f"Compose-Datei nicht lesbar: {e.strerror or e}")
    env_path = os.path.join(os.path.dirname(path), ".env")
    return {"name": name, "compose": compose[:200_000], "has_env": os.path.exists(env_path)}
