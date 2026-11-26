"""Agent with https (./agent.sh tls on): certificate pinning on the main dashboard."""

import asyncio
import json
import socket
import threading
import time

import pytest

import agent
import remotes


@pytest.fixture(scope="module")
def tls_agent(tmp_path_factory):
    """A tiny https server that answers /api/agent/info like an agent, with a certificate from agent.py."""
    import uvicorn
    d = tmp_path_factory.mktemp("agent")
    old = (agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV)
    agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV = str(d / "tls.key"), str(d / "tls.crt"), str(d / "agent-tls.env")
    fp = agent.tls_enable()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        ok = dict(scope["headers"]).get(b"authorization") == b"Bearer " + b"t" * 40
        body = json.dumps({"agent": True, "hostname": "tls-test"} if ok else {"detail": "no"}).encode()
        await send({"type": "http.response.start", "status": 200 if ok else 401, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                                           ssl_keyfile=agent.TLS_KEY, ssl_certfile=agent.TLS_CERT))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"url": f"https://127.0.0.1:{port}", "fingerprint": fp, "pem": open(agent.TLS_CERT).read()}
    server.should_exit = True
    t.join(5)
    agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV = old


def test_agent_certificate(tls_agent):
    assert len(tls_agent["fingerprint"].split(":")) == 32
    assert remotes.cert_fingerprint(tls_agent["pem"]) == tls_agent["fingerprint"]


def test_unknown_certificate_needs_confirmation(tls_agent):
    with pytest.raises(remotes.UntrustedCert) as e:
        asyncio.run(remotes._connect(tls_agent["url"], "t" * 40))
    assert e.value.fingerprint == tls_agent["fingerprint"]
    with pytest.raises(remotes.UntrustedCert):    # a wrong confirmation is no confirmation
        asyncio.run(remotes._connect(tls_agent["url"], "t" * 40, trust="00:11:22"))


def test_confirmed_certificate_is_pinned(tls_agent):
    info, cert = asyncio.run(remotes._connect(tls_agent["url"], "t" * 40, trust=tls_agent["fingerprint"].lower()))
    assert info["hostname"] == "tls-test" and cert == tls_agent["pem"]
    assert asyncio.run(remotes.probe(tls_agent["url"], "t" * 40, cert))["agent"] is True


def test_other_pinned_certificate_is_refused(tls_agent, tmp_path):
    old = (agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV)
    agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV = str(tmp_path / "k"), str(tmp_path / "c"), str(tmp_path / "e")
    try:
        agent.tls_enable()
        other = open(agent.TLS_CERT).read()
    finally:
        agent.TLS_KEY, agent.TLS_CERT, agent.TLS_ENV = old
    with pytest.raises(remotes.RemoteError, match="geändert"):
        asyncio.run(remotes.probe(tls_agent["url"], "t" * 40, other))


def test_https_is_not_flagged_insecure():
    assert remotes.transport_risk("https://203.0.113.7:8000") is False
