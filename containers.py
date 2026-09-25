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

import homelab

logger = logging.getLogger("pi_dashboard.containers")

CACHE_TTL = 8
STATS_RETENTION = 7 * 86400
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
def _docker(args: List[str], timeout: float = 15, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, errors="replace", cwd=cwd)


def _labels(text: str) -> Dict[str, str]:
    out = {}
    for part in (text or "").split(","):
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
        state, status = c.get("State", ""), c.get("Status", "")
        health = "unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status else ""
        level = ("error" if health == "unhealthy" or state in ("dead", "restarting")
                 or (state == "exited" and not status.startswith("Exited (0)")) else
                 "active" if state == "running" else "inactive")
        containers.append({
            "id": c.get("ID", "")[:12], "name": c.get("Names", ""), "image": c.get("Image", ""), "state": state,
            "status": status, "health": health, "level": level, "ports": c.get("Ports", ""),
            "created": c.get("CreatedAt", ""), "stack": labels.get("com.docker.compose.project", ""),
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
                stacks[s.get("Name", "")] = {"name": s.get("Name", ""), "status": s.get("Status", ""),
                                             "config_files": [f for f in (s.get("ConfigFiles") or "").split(",") if f]}
    except (subprocess.TimeoutExpired, ValueError):
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
                    "images": sorted({c["image"] for c in members})})
    rank = {"error": 0, "warning": 1, "active": 2, "inactive": 3}
    out.sort(key=lambda s: (rank[s["level"]], s["name"]))
    return out


def stacks() -> List[Dict[str, Any]]:
    return _cached("stacks", CACHE_TTL, _stacks)


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
    with _lock:
        if any(j["stack"] == name and j["status"] == "running" for j in _jobs.values()):
            raise ContainerError("Für diesen Stack läuft schon eine Aktion.")
        job_id = f"{int(time.time() * 1000):x}"
        label, steps = STACK_ACTIONS[action]
        job = {"id": job_id, "stack": name, "action": action, "label": label, "status": "running", "output": "",
               "started": time.time(), "ended": None, "user": user}
        _jobs[job_id] = job
        for old in sorted(_jobs, key=lambda k: _jobs[k]["started"])[:-20]:
            if _jobs[old]["status"] != "running":
                _jobs.pop(old)
    base = ["-p", name]
    for f in stack["files"]:
        base += ["-f", f]
    threading.Thread(target=_run_job, args=(job, base, steps, stack["dir"]), daemon=True).start()
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
        conn.execute("DELETE FROM container_samples WHERE ts < ?", (now - STATS_RETENTION,))


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
