"""
Small helpers shared by app.py and the route modules in backend/routes/: serving pages,
the thread pool for long computations, and turning errors into 400 answers.
"""

import asyncio
import functools
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

logger = logging.getLogger("pi_dashboard")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Two pools: long computations (baseline check, evidence package, log verification, scans, exports)
# get two workers of their own, so they queue behind each other instead of filling the shared pool
# that live data, logins and the activity log of every request run in.
HEAVY_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="heavy")


async def heavy(fn, *args, **kwargs):
    """Run a long computation in the heavy pool (like asyncio.to_thread, but never crowding out the rest)."""
    return await asyncio.get_running_loop().run_in_executor(HEAVY_POOL, functools.partial(fn, *args, **kwargs))


def serve_page(name: str) -> HTMLResponse:
    """Serve an HTML page with each asset's mtime appended as ?v=..., so browsers never
    mix a new page with a cached old style.css / *.js after an auto-update."""
    with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as f:
        html = f.read()

    def versioned(m: "re.Match") -> str:
        path = os.path.join(STATIC_DIR, m.group(1))
        return f"/static/{m.group(1)}?v={int(os.path.getmtime(path))}\"" if os.path.exists(path) else m.group(0)
    html = re.sub(r'/static/([\w.-]+\.(?:js|css))"', versioned, html)
    # Behind Cloudflare: Rocket Loader would turn every script into one loaded by an inline
    # script, which the CSP blocks (blank page) - data-cfasync="false" makes it skip them.
    # The manifest is fetched without cookies unless asked, so Cloudflare Access redirects it.
    html = html.replace("<script src=", '<script data-cfasync="false" src=')
    html = html.replace('<link rel="manifest" ', '<link rel="manifest" crossorigin="use-credentials" ')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def md_response(text: str, name: str) -> Response:
    return Response(text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'inline; filename="{name}"'})


def call400(errors: tuple, fn, *args):
    """fn(*args); the given error types become a 400 answer with their message."""
    try:
        return fn(*args)
    except errors as e:
        raise HTTPException(status_code=400, detail=str(e))


def same_origin(request: Request) -> bool:
    """Reject cross-site form posts / fetches (CSRF) on state-changing requests."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin navigation / non-browser clients
    return origin.split("://", 1)[-1] == request.headers.get("host", "")
