"""
Containers page: Kubernetes, Docker, compose stacks, image updates - routes moved out of app.py.
"""

import asyncio
import subprocess
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import autoupdate
import containers
import imagecheck
import stacktemplates
from webutil import logger

router = APIRouter()


def _container_call(fn, *args):
    try:
        return fn(*args)
    except containers.ContainerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Zeitüberschreitung.")


@router.get("/api/containers/overview")
async def api_containers_overview():
    def collect():
        return {"kube": containers.kube_overview(), "docker": containers.docker_list(), "stacks": containers.stacks(),
                "jobs": containers.running_jobs(), "images": imagecheck.status(), "scaled": containers.kube_scaled(),
                "autoupdate": autoupdate.overview()}
    return await asyncio.to_thread(collect)


@router.get("/api/containers/stats")
async def api_containers_stats():
    return await asyncio.to_thread(containers.stats_summary)


@router.get("/api/containers/stats/history")
async def api_containers_stats_history(kind: str, name: str, hours: int = 24):
    return await asyncio.to_thread(_container_call, containers.stats_history, kind, name, hours)


class AutoUpdateRequest(BaseModel):
    enabled: bool = True
    time: str = "04:00"
    days: str = "daily"
    weekday: int = 6


@router.post("/api/containers/autoupdate/{stack}")
async def api_autoupdate_save(stack: str, req: AutoUpdateRequest, request: Request):
    request.state.audit_detail = f"{stack}: {'an, ' + req.time if req.enabled else 'aus'}"
    try:
        return await asyncio.to_thread(autoupdate.save, stack, req.enabled, req.time, req.days, req.weekday)
    except autoupdate.AutoUpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/containers/autoupdate/{stack}/run")
async def api_autoupdate_run(stack: str, request: Request):
    request.state.audit_detail = stack
    autoupdate.run_in_background(stack)
    return {"message": f"{stack} wird aktualisiert."}


class LimitsRequest(BaseModel):
    service: str
    cpus: Optional[float] = None
    mem_mb: Optional[int] = None


@router.post("/api/containers/stacks/{name}/limits")
async def api_stack_limits(name: str, req: LimitsRequest, request: Request):
    request.state.audit_detail = f"{name}/{req.service}: CPU {req.cpus or '–'}, RAM {req.mem_mb or '–'} MB"
    try:
        return await asyncio.to_thread(containers.set_limits, name, req.service, req.cpus, req.mem_mb)
    except containers.ContainerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Override-Datei: {e.strerror or e}")


@router.get("/api/containers/logs/multi")
async def api_container_multi_logs(names: str, since: float = 0, tail: int = 200):
    return await asyncio.to_thread(containers.multi_logs, [n for n in names.split(",") if n], since, tail)


@router.get("/api/containers/kube/logs")
async def api_kube_logs(namespace: str, name: str, container: str = "", lines: int = 300, previous: bool = False):
    return await asyncio.to_thread(_container_call, containers.kube_logs, namespace, name, container, lines, previous)


class ScaleRequest(BaseModel):
    replicas: int


@router.post("/api/containers/kube/{kind}/{namespace}/{name}/restart")
async def api_kube_restart(kind: str, namespace: str, name: str, request: Request):
    request.state.audit_detail = f"{kind} {namespace}/{name}"
    return {"message": await asyncio.to_thread(_container_call, containers.kube_restart, kind, namespace, name)}


@router.post("/api/containers/kube/{kind}/{namespace}/{name}/scale")
async def api_kube_scale(kind: str, namespace: str, name: str, req: ScaleRequest, request: Request):
    request.state.audit_detail = f"{kind} {namespace}/{name} → {req.replicas}"
    return {"message": await asyncio.to_thread(_container_call, containers.kube_scale, kind, namespace, name, req.replicas)}


@router.post("/api/containers/kube/pod/{namespace}/{name}/delete")
async def api_kube_delete_pod(namespace: str, name: str, request: Request):
    request.state.audit_detail = f"{namespace}/{name}"
    return {"message": await asyncio.to_thread(_container_call, containers.kube_delete_pod, namespace, name)}


@router.post("/api/containers/stacks/{name}/{action}")
async def api_stack_action(name: str, action: str, request: Request):
    request.state.audit_detail = f"{name}: {action}"
    job = await asyncio.to_thread(_container_call, containers.stack_action, name, action, request.state.user)
    logger.info(f"Stack {name} {action} by '{request.state.user}'")
    return job


@router.get("/api/containers/jobs/{job_id}")
async def api_container_job(job_id: str):
    return _container_call(containers.get_job, job_id)


@router.get("/api/containers/docker/disk")
async def api_docker_disk():
    return await asyncio.to_thread(_container_call, containers.disk_usage)


class DockerCleanupRequest(BaseModel):
    kinds: list = []
    volumes: list = []


@router.post("/api/containers/docker/cleanup")
async def api_docker_cleanup(req: DockerCleanupRequest, request: Request):
    request.state.audit_detail = f"{', '.join(str(k) for k in req.kinds)} {' '.join(str(v) for v in req.volumes)}"[:250]
    return await asyncio.to_thread(_container_call, containers.cleanup, [str(k) for k in req.kinds],
                                   [str(v) for v in req.volumes][:100], request.state.user or "")


@router.get("/api/containers/docker/volumes")
async def api_docker_volumes():
    return await asyncio.to_thread(_container_call, containers.volumes_and_networks)


class StackCreateRequest(BaseModel):
    name: str
    compose: str
    env: str = ""


@router.post("/api/containers/stacks/create")
async def api_stack_create(req: StackCreateRequest, request: Request):
    request.state.audit_detail = req.name[:60]
    return await asyncio.to_thread(_container_call, containers.create_stack, req.name, req.compose, req.env, request.state.user or "")


@router.get("/api/containers/stacks/{name}/compose")
async def api_stack_compose(name: str):
    return await asyncio.to_thread(_container_call, containers.stack_compose, name)


def _template_call(fn, *args):
    try:
        return fn(*args)
    except stacktemplates.TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/stack-templates")
async def api_stack_templates():
    return await asyncio.to_thread(stacktemplates.overview)


@router.post("/api/stack-templates")
async def api_stack_templates_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:80]
    return await asyncio.to_thread(_template_call, stacktemplates.save, None, payload, request.state.user or "")


@router.post("/api/stack-templates/{template_id}")
async def api_stack_templates_update(template_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:80]
    return await asyncio.to_thread(_template_call, stacktemplates.save, template_id, payload, request.state.user or "")


@router.post("/api/stack-templates/{template_id}/delete")
async def api_stack_templates_delete(template_id: str):
    return await asyncio.to_thread(stacktemplates.delete, template_id)


@router.post("/api/stack-templates/{template_id}/deploy")
async def api_stack_templates_deploy(template_id: str, payload: dict, request: Request):
    targets = [str(t) for t in (payload.get("targets") or [])][:20]
    request.state.audit_detail = f"{payload.get('name', '')} · {len(targets)} Server"
    try:
        return await stacktemplates.deploy(template_id, targets, str(payload.get("name") or ""), str(payload.get("env") or ""),
                                           request.state.user or "")
    except stacktemplates.TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e))
