"""One loop, once a minute, for the watchers without their own runner (VM schedules, security scans)."""

import asyncio
import logging

import falco
import fim
import malware
import sudolog
import vmops

logger = logging.getLogger("pi_dashboard.scheduler")
JOBS = [("VM-Zeitpläne", vmops.tick), ("Datei-Integrität", fim.tick), ("Malware-Scan", malware.tick), ("sudo-Protokoll", sudolog.sync), ("Falco", falco.sync)]


async def background_runner() -> None:
    while True:
        await asyncio.sleep(60)
        for name, job in JOBS:
            try:
                await asyncio.to_thread(job)
            except Exception as e:
                logger.warning(f"{name}: {e}")
