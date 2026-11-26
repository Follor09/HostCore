"""
The app keeps its data next to app.py (settings.json, auth.json, history.db ...). So tests never
touch a real installation, every test session runs on a fresh copy of the tracked files in a
temporary folder, imported from there.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN, VIEWER, PASSWORD = "admin", "viewer", "Test-Passwort-123!"


def _tracked_files():
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, check=True).stdout
        files = [f for f in out.decode().split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
    # Also take new, not yet committed code files (a test run before the first commit)
    for top in ("app.py",):
        if top not in files:
            files.append(top)
    for folder in ("backend", "static"):
        for root, dirs, names in os.walk(os.path.join(REPO, folder)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), REPO)
                if rel not in files:
                    files.append(rel)
    return files


def _copy_repo(dst):
    for rel in _tracked_files():
        src = os.path.join(REPO, rel)
        if rel.startswith("tests/") or not os.path.isfile(src):
            continue
        os.makedirs(os.path.join(dst, os.path.dirname(rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(dst, rel))


_TMP = None


def pytest_configure(config):
    """Before any test module is imported: copy, then import everything from the copy."""
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="hostcore-test-")
    _copy_repo(_TMP)
    config._hostcore_cwd = os.getcwd()
    os.chdir(_TMP)
    sys.path[:0] = [_TMP, os.path.join(_TMP, "backend")]


def pytest_unconfigure(config):
    os.chdir(getattr(config, "_hostcore_cwd", REPO))
    if _TMP:
        shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def appdir():
    return _TMP


@pytest.fixture(scope="session")
def app_module():
    import app as app_mod
    import auth
    assert os.path.dirname(app_mod.__file__) == _TMP, "tests must never run on the real installation"
    auth.set_password(ADMIN, PASSWORD)          # first user = admin
    auth.create_user(VIEWER, PASSWORD, "viewer")
    return app_mod


@pytest.fixture(autouse=True)
def _app_loaded(app_module):
    """Every test runs with the app imported and the test users in place."""


def _client(app_module, user):
    from fastapi.testclient import TestClient
    import auth
    c = TestClient(app_module.app, base_url="http://testserver")
    if user:
        token, _ = auth.make_session(user, remember=False, ip="127.0.0.1", agent="pytest")
        c.cookies.set(auth.COOKIE_NAME, token)
    # A TestClient without "with" runs each request in its own event loop: the shared
    # httpx client for remote servers must not outlive one
    orig = c.request

    def request(*a, **k):
        app_module._proxy_client = None
        return orig(*a, **k)
    c.request = request
    return c


@pytest.fixture(scope="session")
def admin(app_module):
    return _client(app_module, ADMIN)


@pytest.fixture(scope="session")
def viewer(app_module):
    return _client(app_module, VIEWER)


@pytest.fixture(scope="session")
def anon(app_module):
    return _client(app_module, None)
