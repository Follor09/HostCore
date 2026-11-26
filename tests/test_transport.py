"""Warning for agents reached with plain http across the internet."""

import pytest

import remotes


@pytest.mark.parametrize("url,risky", [
    ("http://192.168.1.10:8000", False),
    ("http://10.0.0.5:8000", False),
    ("http://100.90.1.2:8000", False),      # NetBird / Tailscale range
    ("http://127.0.0.1:8000", False),
    ("http://[fd00::5]:8000", False),
    ("http://pi.fritz.box:8000", False),
    ("http://nas.local:8000", False),
    ("http://raspberry:8000", False),       # single label = LAN name
    ("http://203.0.113.7:8000", True),
    ("http://8.8.8.8:8000", True),
    ("http://[2001:db8::1]:8000", True),
    ("https://203.0.113.7", False),         # encrypted
])
def test_transport_risk(url, risky):
    assert remotes.transport_risk(url) is risky


def test_names_are_resolved_and_cached(monkeypatch):
    calls = []

    def fake(host, *a, **k):
        calls.append(host)
        return [(2, 1, 6, "", ("198.51.100.4", 0))]
    monkeypatch.setattr(remotes.socket, "getaddrinfo", fake)
    remotes._risk_cache.pop("agent.example.org", None)
    assert remotes.transport_risk("http://agent.example.org:8000", resolve=False) is False   # unknown yet, no lookup
    assert calls == []
    assert remotes.transport_risk("http://agent.example.org:8000") is True
    assert remotes.transport_risk("http://agent.example.org:8000", resolve=False) is True    # from the cache
    assert calls == ["agent.example.org"]


def test_listing_shows_the_flag(admin, monkeypatch):
    import settings_store
    old = settings_store.get("remotes", [])
    settings_store.set("remotes", [{"id": "r7", "name": "VPS", "url": "http://203.0.113.9:8000", "token": "t" * 40}])
    monkeypatch.setitem(remotes._status, "r7", {"online": False, "checked": 9e12})   # no probe
    try:
        items = admin.get("/api/remotes").json()["remotes"]
        assert items[0]["insecure"] is True
    finally:
        settings_store.set("remotes", old)
