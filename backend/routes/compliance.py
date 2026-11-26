"""
NIS-2 deadlines, policies, suppliers, internal audits - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import auditplan
import baseline
import changes
import evidence
import homelab
import insights
import monitor
import nis2
import patchsla
import policies
import privacy
import problems
import sensors
import services_manager
import storage
import suppliers
from webutil import call400 as _call400, heavy, md_response as _md_response, serve_page as _serve_page

router = APIRouter()


@router.get("/api/nis2")
async def api_nis2():
    return await asyncio.to_thread(nis2.overview)


@router.get("/api/nis2/markdown")
async def api_nis2_markdown():
    return _md_response(await asyncio.to_thread(nis2.markdown), "nis2-meldefristen.md")


@router.get("/api/nis2/{cid}/draft/{stage}")
async def api_nis2_draft(cid: str, stage: str):
    return _md_response(await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.draft, cid, stage), f"nis2-{stage}.md")


@router.post("/api/nis2/profile")
async def api_nis2_profile(payload: dict):
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.save_profile, payload)


@router.post("/api/nis2")
async def api_nis2_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:160]
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.save, None, payload, request.state.user or "")


@router.post("/api/nis2/{cid}")
async def api_nis2_update(cid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.save, cid, payload, request.state.user or "")


@router.post("/api/nis2/{cid}/report")
async def api_nis2_report(cid: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("stage", ""))
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.report, cid, str(payload.get("stage", "")), payload.get("submitted"),
                                   str(payload.get("reference", "")), str(payload.get("note", "")), request.state.user or "")


@router.post("/api/nis2/{cid}/close")
async def api_nis2_close(cid: str, request: Request):
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.close, cid, request.state.user or "")


@router.post("/api/nis2/{cid}/delete")
async def api_nis2_delete(cid: str):
    return await asyncio.to_thread(_call400, (nis2.Nis2Error,), nis2.delete, cid)


@router.get("/api/policies")
async def api_policies(request: Request):
    return await asyncio.to_thread(policies.overview, request.state.user or "")


@router.get("/api/policies/markdown")
async def api_policies_markdown():
    return _md_response(await asyncio.to_thread(policies.markdown), "richtlinien.md")


@router.get("/api/policies/export.csv")
async def api_policies_csv():
    text = await asyncio.to_thread(policies.csv_export)
    return Response("\ufeff" + text, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="richtlinien-kenntnisnahme.csv"'})


@router.get("/api/policies/{pid}")
async def api_policy(pid: str, request: Request):
    return await asyncio.to_thread(_call400, (policies.PolicyError,), policies.detail, pid, request.state.user or "")


@router.get("/api/policies/{pid}/version/{version}")
async def api_policy_version(pid: str, version: int):
    return await asyncio.to_thread(_call400, (policies.PolicyError,), policies.version_text, pid, version)


@router.post("/api/policies")
async def api_policy_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_call400, (policies.PolicyError,), policies.save, None, payload, request.state.user or "")


@router.post("/api/policies/{pid}")
async def api_policy_update(pid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, (policies.PolicyError,), policies.save, pid, payload, request.state.user or "")


@router.post("/api/policies/{pid}/{act}")
async def api_policy_action(pid: str, act: str, payload: dict, request: Request):
    user = request.state.user or ""
    fns = {"submit": (policies.submit, pid, user), "release": (policies.release, pid, user), "review": (policies.confirm_review, pid, user),
           "retire": (policies.retire, pid, bool(payload.get("on", True)), user), "acknowledge": (policies.acknowledge, pid, user), "delete": (policies.delete, pid, user)}
    if act not in fns:
        raise HTTPException(status_code=404, detail="Unbekannte Aktion.")
    return await asyncio.to_thread(_call400, (policies.PolicyError,), *fns[act])


@router.get("/api/suppliers")
async def api_suppliers():
    return await asyncio.to_thread(suppliers.overview)


@router.get("/api/suppliers/markdown")
async def api_suppliers_markdown():
    return _md_response(await asyncio.to_thread(suppliers.markdown), "dienstleister.md")


@router.post("/api/suppliers")
async def api_supplier_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_call400, (suppliers.SupplierError,), suppliers.save, None, payload, request.state.user or "")


@router.post("/api/suppliers/{sid}")
async def api_supplier_update(sid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, (suppliers.SupplierError,), suppliers.save, sid, payload, request.state.user or "")


@router.post("/api/suppliers/{sid}/review")
async def api_supplier_review(sid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, (suppliers.SupplierError,), suppliers.review, sid, str(payload.get("date", "")), str(payload.get("result", "")),
                                   str(payload.get("note", "")), request.state.user or "")


@router.post("/api/suppliers/{sid}/delete")
async def api_supplier_delete(sid: str):
    return await asyncio.to_thread(_call400, (suppliers.SupplierError,), suppliers.delete, sid)


AUDIT_ERRORS = (auditplan.AuditPlanError,)


@router.get("/api/internal-audits")
async def api_audits():
    return await asyncio.to_thread(auditplan.overview)


@router.get("/api/internal-audits/markdown")
async def api_audits_markdown(id: str = ""):
    return _md_response(await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.markdown, id), "interne-audits.md")


@router.post("/api/internal-audits")
async def api_audit_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:140]
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.save, None, payload, request.state.user or "")


@router.post("/api/internal-audits/{aid}")
async def api_audit_update(aid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.save, aid, payload, request.state.user or "")


@router.post("/api/internal-audits/{aid}/checks")
async def api_audit_checks(aid: str, payload: dict):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.add_checks, aid, payload.get("texts") or [], bool(payload.get("baseline")))


@router.post("/api/internal-audits/{aid}/checks/{cid}")
async def api_audit_rate(aid: str, cid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.rate, aid, cid, str(payload.get("result", "")), str(payload.get("note", "")), request.state.user or "")


@router.post("/api/internal-audits/{aid}/findings/{fid}")
async def api_audit_finding(aid: str, fid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.update_finding, aid, fid, payload, request.state.user or "")


@router.post("/api/internal-audits/{aid}/findings/{fid}/close")
async def api_audit_finding_close(aid: str, fid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.close_finding, aid, fid, str(payload.get("note", "")), request.state.user or "")


@router.post("/api/internal-audits/{aid}/finish")
async def api_audit_finish(aid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.finish, aid, str(payload.get("summary", "")), request.state.user or "")


@router.post("/api/internal-audits/{aid}/delete")
async def api_audit_delete(aid: str):
    return await asyncio.to_thread(_call400, AUDIT_ERRORS, auditplan.delete, aid)


@router.get("/api/patch-sla")
async def api_patch_sla():
    return await asyncio.to_thread(patchsla.overview)


@router.get("/api/patch-sla/markdown")
async def api_patch_sla_markdown():
    return _md_response(await asyncio.to_thread(patchsla.markdown), "patch-fristen.md")


@router.post("/api/patch-sla/settings")
async def api_patch_sla_settings(payload: dict):
    return await asyncio.to_thread(_call400, (patchsla.PatchSlaError,), patchsla.save_settings, payload)


@router.post("/api/patch-sla/exception")
async def api_patch_sla_exception(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('id', '')} · {'entfernt' if payload.get('remove') else payload.get('until', '')}"
    return await asyncio.to_thread(_call400, (patchsla.PatchSlaError,), patchsla.set_exception, str(payload.get("id", "")),
                                   str(payload.get("reason", "")), str(payload.get("until", "")), request.state.user or "", bool(payload.get("remove")))


@router.get("/api/problems")
async def api_problems():
    return await asyncio.to_thread(problems.overview)


@router.get("/api/problems/markdown")
async def api_problems_markdown():
    return _md_response(await asyncio.to_thread(problems.markdown), "probleme.md")


@router.post("/api/problems")
async def api_problems_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:100]
    return await asyncio.to_thread(_call400, (problems.ProblemError,), problems.save, None, payload, request.state.user or "")


@router.post("/api/problems/{pid}")
async def api_problems_update(pid: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:100]
    return await asyncio.to_thread(_call400, (problems.ProblemError,), problems.save, pid, payload, request.state.user or "")


@router.post("/api/problems/{pid}/status")
async def api_problems_status(pid: str, payload: dict, request: Request):
    request.state.audit_detail = f"{pid} · {payload.get('status', '')}"
    return await asyncio.to_thread(_call400, (problems.ProblemError,), problems.set_status, pid, str(payload.get("status", "")),
                                   str(payload.get("note", "")), request.state.user or "")


@router.post("/api/problems/{pid}/change")
async def api_problems_change(pid: str, request: Request):
    return await asyncio.to_thread(_call400, (problems.ProblemError, changes.ChangeError), problems.create_change, pid, request.state.user or "")


@router.post("/api/problems/{pid}/delete")
async def api_problems_delete(pid: str):
    return await asyncio.to_thread(_call400, (problems.ProblemError,), problems.delete, pid)


@router.get("/api/compliance/baseline")
async def api_baseline(refresh: int = 0):
    return await heavy(baseline.overview, bool(refresh))


@router.get("/api/compliance/baseline/markdown")
async def api_baseline_markdown():
    return _md_response(await heavy(baseline.markdown), "grundschutz-check.md")


@router.post("/api/compliance/baseline/assess")
async def api_baseline_assess(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('id', '')} · {payload.get('state', '') or 'zurückgesetzt'}"
    return await asyncio.to_thread(_call400, (baseline.BaselineError,), baseline.assess, str(payload.get("id", "")), str(payload.get("state", "")),
                                   str(payload.get("note", "")), str(payload.get("evidence", "")), request.state.user or "")


@router.get("/api/compliance/privacy")
async def api_privacy():
    return await asyncio.to_thread(privacy.overview)


@router.get("/api/compliance/privacy/markdown")
async def api_privacy_markdown(part: str = "all"):
    part = part if part in ("all", "vvt", "tom") else "all"
    return _md_response(await heavy(privacy.markdown, part), f"datenschutz-{part}.md")


@router.post("/api/compliance/privacy/controller")
async def api_privacy_controller(payload: dict, request: Request):
    return await asyncio.to_thread(_call400, (privacy.PrivacyError,), privacy.save_controller, payload, request.state.user or "")


@router.post("/api/compliance/privacy/activities")
async def api_privacy_activity_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:100]
    return await asyncio.to_thread(_call400, (privacy.PrivacyError,), privacy.save_activity, None, payload, request.state.user or "")


@router.post("/api/compliance/privacy/activities/{aid}")
async def api_privacy_activity_update(aid: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:100]
    return await asyncio.to_thread(_call400, (privacy.PrivacyError,), privacy.save_activity, aid, payload, request.state.user or "")


@router.post("/api/compliance/privacy/activities/{aid}/delete")
async def api_privacy_activity_delete(aid: str):
    return await asyncio.to_thread(privacy.delete_activity, aid)


@router.post("/api/compliance/privacy/tom")
async def api_privacy_tom(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("area", ""))
    return await asyncio.to_thread(_call400, (privacy.PrivacyError,), privacy.save_tom, str(payload.get("area", "")), str(payload.get("text", "")),
                                   request.state.user or "")


@router.get("/api/compliance/evidence")
async def api_evidence_history():
    return await asyncio.to_thread(evidence.history)


@router.post("/api/compliance/evidence")
async def api_evidence_build(payload: dict, request: Request):
    try:
        since, until = int(payload.get("since") or 0), int(payload.get("until") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum.")
    content, name = await heavy(_call400, (evidence.EvidenceError,), evidence.build, since, until, request.state.user or "")
    request.state.audit_detail = f"{name} · {len(content) // 1024} KB"
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/nachweise")
async def get_compliance_page():
    return _serve_page("compliance.html")


@router.get("/druck")
async def get_print_page():
    return _serve_page("print.html")


@router.get("/api/history/interfaces")
async def api_history_interfaces():
    return {"interfaces": await asyncio.to_thread(monitor.interfaces)}


@router.get("/api/monitor/hogs")
async def api_monitor_hogs(hours: int = 24):
    return await heavy(monitor.resource_hogs, 168 if hours > 24 else 24)


@router.get("/api/sensors")
async def api_sensors():
    return await asyncio.to_thread(sensors.read_all)


@router.get("/api/sensors/history")
async def api_sensors_history(hours: int = 24):
    return await asyncio.to_thread(monitor.sensor_history, 168 if hours > 24 else 24)


@router.get("/api/monitor/usage")
async def api_monitor_usage(iface: str = ""):
    return await asyncio.to_thread(monitor.data_usage, iface[:32])


@router.get("/api/events")
async def api_events(days: int = 7):
    return await asyncio.to_thread(insights.events, max(1, min(days, 30)))


@router.get("/api/status-summary")
async def api_status_summary():
    return await asyncio.to_thread(insights.status_summary)


@router.post("/api/status-summary/ack")
async def api_status_ack(payload: dict, request: Request):
    request.state.audit_detail = "alle" if payload.get("all") else str(payload.get("text", ""))[:150]
    try:
        return await asyncio.to_thread(insights.acknowledge_status, str(payload.get("key", "")), str(payload.get("mode", "change")),
                                       request.state.user or "", bool(payload.get("all")))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/status-summary/unack")
async def api_status_unack(payload: dict):
    return await asyncio.to_thread(insights.unacknowledge_status, str(payload.get("key", "")))


@router.get("/api/disk-forecast")
async def api_disk_forecast():
    return await asyncio.to_thread(insights.disk_forecast)


@router.get("/api/report")
async def api_report():
    return await heavy(insights.weekly_report)


@router.get("/api/monitor/throttle")
async def api_monitor_throttle(days: int = 30):
    return await asyncio.to_thread(monitor.throttle_log, max(1, min(days, monitor.RETENTION_DAYS)))


@router.get("/api/monitor/uptime")
async def api_monitor_uptime(days: int = 7):
    return await asyncio.to_thread(monitor.service_uptime, max(1, min(days, monitor.RETENTION_DAYS)))


@router.get("/api/homelab")
async def api_homelab():
    """Apt updates, NVMe health, Docker containers and Kubernetes pods."""
    return await asyncio.to_thread(homelab.get_all)


class AptRunRequest(BaseModel):
    mode: str  # "update" or "upgrade"


@router.get("/api/updates/apt")
async def api_apt_status():
    """Status and log of the running / last apt update or upgrade."""
    return await asyncio.to_thread(homelab.get_apt_run)


@router.post("/api/updates/apt")
async def api_apt_start(req: AptRunRequest, request: Request):
    request.state.audit_detail = req.mode
    """Start apt update or update+full-upgrade via its systemd unit."""
    result = await asyncio.to_thread(homelab.start_apt_run, req.mode, request.state.user or "")
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.get("/api/services")
async def api_services():
    """Return status of homelab services."""
    return {"services": await asyncio.to_thread(services_manager.get_services_status)}


@router.get("/api/drives")
async def api_drives(refresh: bool = False):
    """Mounted partitions (legacy list) and the lsblk-style storage overview."""
    if refresh:
        storage._cache["data"] = None
    return {"drives": await asyncio.to_thread(services_manager.get_mounted_drives), "storage": await asyncio.to_thread(storage.overview)}


@router.get("/api/logs")
async def api_logs(lines: int = 30):
    """Return recent system journal logs."""
    return {"logs": await asyncio.to_thread(services_manager.get_recent_logs, lines=lines)}
