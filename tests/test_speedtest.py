"""Speed test: measuring against a stand-in for Cloudflare, limits, history, several servers."""

import asyncio
import json

import httpx
import pytest

import fleet
import remotes
import settings_store
import speedtest


def fake_cloudflare(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/meta":
            return httpx.Response(200, json={"colo": {"iata": "FRA", "city": "Frankfurt"}, "asOrganization": "Beispiel-Provider"})
        if request.url.path == "/__down":
            return httpx.Response(200, content=b"\0" * int(request.url.params.get("bytes", "0")))
        if request.url.path == "/__up":
            body = request.read()
            return httpx.Response(200, json={"got": len(body)})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def clean_history():
    old = settings_store.get(speedtest.KEY, [])
    settings_store.set(speedtest.KEY, [])
    yield
    settings_store.set(speedtest.KEY, old)


def test_measures_and_keeps_the_result(clean_history):
    calls = []
    r = speedtest.run(base="https://cf.test", transport=fake_cloudflare(calls), duration=0.5, cap=3_000_000, streams=2)
    assert r["down"] and r["down"] > 0 and r["up"] and r["up"] > 0 and r["ping"] >= 0
    assert r["server"] == "Cloudflare Frankfurt (FRA)" and r["isp"] == "Beispiel-Provider"
    assert r["down_mb"] <= 3.5 and r["up_mb"] <= 3.5                       # stays near the data limit
    assert sum(1 for c in calls if c[1] == "/__down" and c[0] == "GET") >= speedtest.PING_COUNT
    assert any(c == ("POST", "/__up") for c in calls)
    h = speedtest.history()
    assert h["results"][0]["ts"] == r["ts"] and h["running"] is False


def test_history_keeps_the_last_ones(clean_history):
    for _ in range(speedtest.KEEP + 3):
        speedtest.run(base="https://cf.test", transport=fake_cloudflare([]), duration=0.1, cap=200_000, streams=1)
    assert len(speedtest.history()["results"]) == speedtest.KEEP


def test_one_test_at_a_time(clean_history):
    assert speedtest._running.acquire(blocking=False)
    try:
        with pytest.raises(speedtest.SpeedtestError, match="läuft schon"):
            speedtest.run(base="https://cf.test", transport=fake_cloudflare([]), duration=0.1, cap=100_000)
    finally:
        speedtest._running.release()


def test_unreachable_is_explained(clean_history):
    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(speedtest.SpeedtestError, match="Keine Verbindung"):
        speedtest.run(base="https://cf.test", transport=httpx.MockTransport(down), duration=0.1, cap=100_000)


@pytest.fixture
def two_agents(monkeypatch):
    rs = [{"id": "aa11", "name": "rz-server", "url": "http://x", "token": "t" * 40},
          {"id": "bb22", "name": "pi-alt", "url": "http://y", "token": "t" * 40}]
    monkeypatch.setattr(remotes, "all_remotes", lambda: rs)
    monkeypatch.setattr(remotes, "_all", lambda: rs)
    order = []

    async def call(rid, method, path, body=None, user="", timeout=30.0):
        if rid == "bb22":
            raise ValueError("Antwort 404")              # an agent without the speed test yet
        order.append((rid, method))
        result = {"ts": 1, "down": 900.0, "up": 400.0, "ping": 4.0, "jitter": 0.5, "server": "Cloudflare FRA", "isp": "RZ"}
        return {"result": result} if method == "POST" else {"results": [result], "running": False}
    monkeypatch.setattr(fleet, "_call", call)

    def local_run():
        order.append(("local", "POST"))
        return {"ts": 2, "down": 100.0, "up": 40.0, "ping": 20.0, "jitter": 2.0, "server": "Cloudflare STR", "isp": "Zuhause"}
    monkeypatch.setattr(speedtest, "run", local_run)
    return order


def test_fleet_overview(admin, two_agents):
    d = admin.get("/api/speedtest/fleet").json()
    by = {s["id"]: s for s in d["servers"]}
    assert set(by) == {"local", "aa11", "bb22"}
    assert by["aa11"]["last"]["down"] == 900.0
    assert "zu alt" in by["bb22"]["error"]


def test_fleet_run_one_after_the_other(admin, two_agents, monkeypatch):
    import websec
    monkeypatch.setattr(websec, "needs_reauth", lambda *a, **k: False)
    r = admin.post("/api/speedtest/fleet", json={"servers": ["aa11", "local", "bb22", "nope"]})
    assert r.status_code == 200
    res = {x["id"]: x for x in r.json()["results"]}
    assert res["aa11"]["ok"] and res["aa11"]["result"]["down"] == 900.0
    assert res["local"]["ok"] and res["local"]["result"]["isp"] == "Zuhause"
    assert not res["bb22"]["ok"] and "zu alt" in res["bb22"]["message"]
    assert "nope" not in res
    assert two_agents == [("aa11", "POST"), ("local", "POST")]            # in the chosen order, one by one


def test_fleet_needs_a_server(admin):
    assert admin.post("/api/speedtest/fleet", json={"servers": []}).status_code == 400


def test_viewer_may_not_start(viewer):
    assert viewer.post("/api/speedtest/fleet", json={"servers": ["local"]}).status_code == 403
    assert viewer.post("/api/tools/speedtest", json={}).status_code == 403


def test_json_roundtrip_of_result_shape(clean_history):
    r = speedtest.run(base="https://cf.test", transport=fake_cloudflare([]), duration=0.1, cap=200_000, streams=1)
    json.dumps(r)   # stored in settings.json
    assert asyncio.iscoroutinefunction(fleet._call)
