"""Every API route without path parameters answers a GET without crashing (no 500)."""

import pytest

from helpers import iter_routes

# Routes that reach out to the internet or run long jobs - not part of a quick smoke test
SKIP = set()


def _get_routes(app):
    out = []
    for r in iter_routes(app.routes):
        path = getattr(r, "path", "")
        if "GET" in (getattr(r, "methods", None) or set()) and path.startswith("/api/") and "{" not in path and path not in SKIP:
            out.append(path)
    return sorted(set(out))


def test_there_are_routes(app_module):
    assert len(_get_routes(app_module.app)) > 50


def test_get_routes_do_not_crash(app_module, admin):
    failed = []
    for path in _get_routes(app_module.app):
        res = admin.get(path)
        if res.status_code >= 500 and res.status_code not in (502, 503, 504):
            failed.append(f"{path}: {res.status_code} {res.text[:200]}")
    assert not failed, "\n".join(failed)


@pytest.mark.parametrize("path", ["/", "/settings", "/system", "/login"])
def test_pages_load(admin, path):
    res = admin.get(path)
    assert res.status_code == 200
    assert "<html" in res.text.lower()


def test_pages_load_all_style_parts(admin):
    import os
    import webutil
    parts = sorted(n for n in os.listdir(os.path.join(webutil.STATIC_DIR, "css")) if n.endswith(".css"))
    html = admin.get("/settings").text
    assert "/static/style.css" not in html
    found = [p for p in parts if f"/static/css/{p}?v=" in html]
    assert found == parts
    assert [html.index(p) for p in parts] == sorted(html.index(p) for p in parts)   # in cascade order
    assert admin.get(f"/static/css/{parts[0]}").status_code == 200
