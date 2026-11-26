"""
Security watchers: file integrity, sudo log, malware scan, Falco - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, Request

import falco
import fim
import malware
import sudolog
from webutil import call400 as _call400

router = APIRouter()


def _ids(payload: dict) -> list:
    return [i for i in payload.get("ids") or [] if isinstance(i, int)]


@router.get("/api/fim")
async def api_fim():
    return await asyncio.to_thread(fim.overview)


@router.post("/api/fim/settings")
async def api_fim_settings(payload: dict):
    return await asyncio.to_thread(_call400, (fim.FimError, ValueError, TypeError), fim.save_config, payload)


@router.post("/api/fim/scan")
async def api_fim_scan(request: Request):
    return await asyncio.to_thread(_call400, (fim.FimError,), fim.start_scan, request.state.user or "")


@router.post("/api/fim/close")
async def api_fim_close(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("note", ""))[:200]
    return await asyncio.to_thread(_call400, (fim.FimError,), fim.close, _ids(payload), str(payload.get("note", "")), request.state.user or "")


@router.get("/api/sudo-log")
async def api_sudo_log():
    return await asyncio.to_thread(sudolog.overview)


@router.get("/api/sudo-log/entries")
async def api_sudo_log_entries(days: int = 7, user: str = "", q: str = "", flagged: int = 0, own: int = 0):
    return await asyncio.to_thread(sudolog.entries, days, user, q, bool(flagged), bool(own))


@router.get("/api/sudo-log/fleet")
async def api_sudo_log_fleet(days: int = 7, user: str = "", q: str = "", flagged: int = 0):
    return await sudolog.fleet_entries(days, user, q, bool(flagged))


@router.post("/api/sudo-log/rules")
async def api_sudo_log_rules(payload: dict):
    return await asyncio.to_thread(_call400, (sudolog.SudoLogError,), sudolog.save_rules, payload.get("rules") or [])


@router.post("/api/sudo-log/reviewed")
async def api_sudo_log_reviewed(request: Request):
    return await asyncio.to_thread(sudolog.mark_reviewed, request.state.user or "")


@router.get("/api/malware")
async def api_malware():
    return await asyncio.to_thread(malware.overview)


@router.post("/api/malware/settings")
async def api_malware_settings(payload: dict):
    return await asyncio.to_thread(_call400, (malware.MalwareError, ValueError, TypeError), malware.save_config, payload)


@router.post("/api/malware/scan")
async def api_malware_scan(request: Request):
    return await asyncio.to_thread(_call400, (malware.MalwareError,), malware.start_scan, request.state.user or "")


@router.post("/api/malware/baseline")
async def api_malware_baseline(request: Request):
    return await asyncio.to_thread(_call400, (malware.MalwareError,), malware.rkhunter_baseline, request.state.user or "")


@router.post("/api/malware/install/{scanner}")
async def api_malware_install(scanner: str):
    return await asyncio.to_thread(_call400, (malware.MalwareError,), malware.install, scanner)


@router.post("/api/malware/close")
async def api_malware_close(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("note", ""))[:200]
    return await asyncio.to_thread(_call400, (malware.MalwareError,), malware.close, _ids(payload), str(payload.get("note", "")), request.state.user or "")


@router.get("/api/falco")
async def api_falco():
    return await asyncio.to_thread(falco.overview)


@router.post("/api/falco/setup")
async def api_falco_setup(request: Request):
    return await asyncio.to_thread(_call400, (falco.FalcoError,), falco.setup, request.state.user or "")


@router.post("/api/falco/close")
async def api_falco_close(payload: dict, request: Request):
    return await asyncio.to_thread(falco.close, _ids(payload), request.state.user or "")


@router.post("/api/falco/mute")
async def api_falco_mute(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("rule", ""))[:200]
    return await asyncio.to_thread(_call400, (falco.FalcoError,), falco.mute, str(payload.get("rule", "")), str(payload.get("container", "")), bool(payload.get("undo")))
