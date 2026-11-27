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


@pytest.mark.parametrize("url", ["http://user@host", "http://host/a?x=1", "http://host/#frag", "http://", "", "https:",
                                 "http://bad_name!.com", "http://host:99999"])
def test_normalize_url_rejects(url):
    with pytest.raises(remotes.RemoteError):
        remotes.normalize_url(url)


@pytest.mark.parametrize("raw,expected", [
    # IP or LAN name: the agent itself on 8000, http first (https after ./agent.sh tls on)
    ("192.168.1.5", ["http://192.168.1.5:8000", "https://192.168.1.5:8000"]),
    ("pi.lan", ["http://pi.lan:8000", "https://pi.lan:8000"]),
    # A domain: reverse proxy with a real certificate first, then the agent port
    ("agent.example.org", ["https://agent.example.org", "http://agent.example.org:8000",
                           "https://agent.example.org:8000", "http://agent.example.org"]),
    ("Agent.Example.org/hostcore/", ["https://agent.example.org/hostcore", "http://agent.example.org:8000/hostcore",
                                     "https://agent.example.org:8000/hostcore", "http://agent.example.org/hostcore"]),
    ("https://example.org/agent", ["https://example.org/agent", "https://example.org:8000/agent"]),
    ("http://example.org", ["http://example.org:8000", "http://example.org"]),
    ("example.org:8443", ["http://example.org:8443", "https://example.org:8443"]),
    ("https://example.org:443", ["https://example.org"]),
    ("müller-server.de", ["https://xn--mller-server-dlb.de", "http://xn--mller-server-dlb.de:8000",
                          "https://xn--mller-server-dlb.de:8000", "http://xn--mller-server-dlb.de"]),
    ("[fd00::5]", ["http://[fd00::5]:8000", "https://[fd00::5]:8000"]),
])
def test_candidates(raw, expected):
    assert remotes.candidates(raw) == expected


def test_locate_takes_the_first_address_with_an_agent(monkeypatch):
    import asyncio
    tried = []

    async def connect(url, token, trust="", cert=""):
        tried.append(url)
        if url == "http://agent.example.org:8000":
            return {"agent": True, "hostname": "garage"}, ""
        if url == "https://agent.example.org":
            raise remotes.RemoteError("Dort läuft kein HostCore-Agent (oder eine zu alte Version).")
        raise remotes.RemoteError("Nicht erreichbar (ConnectError) – Adresse, Port und Firewall prüfen.")
    monkeypatch.setattr(remotes, "_connect", connect)
    url, info, cert = asyncio.run(remotes.locate("agent.example.org", "t" * 40))
    assert url == "http://agent.example.org:8000" and info["hostname"] == "garage"
    assert tried == ["https://agent.example.org", "http://agent.example.org:8000"]


def test_locate_reports_the_telling_error_and_stops_on_a_wrong_token(monkeypatch):
    import asyncio

    async def nothing(url, token, trust="", cert=""):
        if url.startswith("https://example.org"):
            raise remotes.RemoteError("Dort läuft kein HostCore-Agent (oder eine zu alte Version).")
        raise remotes.RemoteError("Nicht erreichbar (ConnectError) – Adresse, Port und Firewall prüfen.")
    monkeypatch.setattr(remotes, "_connect", nothing)
    with pytest.raises(remotes.RemoteError, match="kein HostCore-Agent"):
        asyncio.run(remotes.locate("example.org", "t" * 40))

    tried = []

    async def wrong_token(url, token, trust="", cert=""):
        tried.append(url)
        raise remotes.RemoteError("Token falsch.")
    monkeypatch.setattr(remotes, "_connect", wrong_token)
    with pytest.raises(remotes.RemoteError, match="Token falsch"):
        asyncio.run(remotes.locate("example.org", "t" * 40))
    assert len(tried) == 1


def test_agent_allow_list_takes_domains(monkeypatch):
    import socket

    import agent
    assert agent.parse_allow("192.168.1.10, Dash.Example.org.") == ["192.168.1.10/32", "dash.example.org"]
    with pytest.raises(ValueError):
        agent.parse_allow("not a host!")
    addr = {"dash.example.org": "203.0.113.7"}

    def fake(name, *a, **k):
        if name not in addr:
            raise socket.gaierror("no")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr[name], 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.setattr(agent, "_config", {"token": "t" * 40, "allow": ["dash.example.org"]})
    agent._resolved.clear()
    assert agent.allowed("203.0.113.7")
    assert not agent.allowed("198.51.100.1")
    # DNS fails later: the last known address stays valid
    del addr["dash.example.org"]
    agent._refresh("dash.example.org")
    assert agent.allowed("203.0.113.7")
