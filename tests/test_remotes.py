"""Which API calls stay on the main dashboard and which go to the selected remote server."""

import pytest

import remotes


@pytest.mark.parametrize("path", [
    "/api/auth/login", "/api/users", "/api/remotes", "/api/remotes/select", "/api/security",
    "/api/wol", "/api/wol/abcd/wake", "/api/settings/wol", "/api/settings/wol/abcd/update",
    "/api/agent/wol", "/api/tools/lan/AA:BB:CC:DD:EE:FF/wol", "/api/boards", "/api/wiki",
])
def test_local_only(path):
    assert remotes.LOCAL_ONLY.match(path), path


@pytest.mark.parametrize("path", [
    "/api/system", "/api/containers", "/api/settings", "/api/tools/lan", "/api/tools/lan/scan",
    "/api/terminal/sessions", "/api/vms", "/api/packages/run",
])
def test_forwarded(path):
    assert not remotes.LOCAL_ONLY.match(path), path


@pytest.mark.parametrize("url,expected", [
    ("192.168.1.5", "http://192.168.1.5:8000"),
    ("http://pi.lan:9000/", "http://pi.lan:9000"),
    ("https://agent.example.org", "https://agent.example.org"),
])
def test_normalize_url(url, expected):
    assert remotes.normalize_url(url) == expected


@pytest.mark.parametrize("url", ["http://user@host", "http://host/path", "http://", "", "https:"])
def test_normalize_url_rejects(url):
    with pytest.raises(remotes.RemoteError):
        remotes.normalize_url(url)
