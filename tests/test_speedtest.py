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


def test_fleet_run_starts_one_task_in_order(admin, two_agents, monkeypatch):
    import tasks
    import websec
    monkeypatch.setattr(websec, "needs_reauth", lambda *a, **k: False)
    started = {}

    def start(kind, title, steps, **kw):
        started.update(kind=kind, title=title, argv=steps[0]["argv"], key=kw.get("key"))
        return {"id": "t1", "state": "running"}
    monkeypatch.setattr(tasks, "start", start)
    r = admin.post("/api/speedtest/fleet", json={"servers": ["aa11", "local", "nope"]})
    assert r.status_code == 200 and r.json()["task"]["id"] == "t1"
    assert started["argv"][-3:] == ["fleet", "aa11", "local"] and started["key"] == "speedtest-fleet"
    assert started["title"] == "Speedtest auf 2 Servern"


def test_single_test_starts_a_task(admin, monkeypatch):
    import tasks
    monkeypatch.setattr(speedtest, "is_running", lambda: False)
    monkeypatch.setattr(tasks, "start", lambda kind, title, steps, **kw: {"id": "t2", "argv": steps[0]["argv"], "key": kw.get("key")})
    r = admin.post("/api/tools/speedtest", json={})
    assert r.status_code == 200 and r.json()["task"]["argv"][-1] == "run" and r.json()["task"]["key"] == "speedtest"


def test_wait_measures_right_away(admin, monkeypatch, clean_history):
    monkeypatch.setattr(speedtest, "run", lambda: {"ts": 5, "down": 1.0, "up": 2.0, "ping": 3.0, "jitter": 0.1})
    r = admin.post("/api/tools/speedtest", json={"wait": True})
    assert r.status_code == 200 and r.json()["result"]["up"] == 2.0


def test_task_script_measures_one_after_the_other(two_agents, monkeypatch, capsys):
    order = []
    monkeypatch.setattr(speedtest, "run", lambda say=None: order.append("local") or {"down": 1, "up": 2, "ping": 3, "jitter": 0})

    def remote(rem, transport=None):
        order.append(rem["id"])
        if rem["id"] == "bb22":
            raise speedtest.SpeedtestError("Agent zu alt – dort HostCore aktualisieren.")
        return {"down": 900, "up": 400, "ping": 4, "jitter": 0.5}
    monkeypatch.setattr(speedtest, "_remote_run", remote)
    code = speedtest.main(["fleet", "aa11", "local", "bb22"])
    out = capsys.readouterr().out
    assert order == ["aa11", "local", "bb22"] and code == 1         # one failed -> the task shows an error
    assert "✅ rz-server: ↓ 900" in out and "❌ pi-alt: Agent zu alt" in out


def test_remote_run_asks_the_agent_to_wait():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"result": {"down": 50.0}})
    r = speedtest._remote_run({"id": "x", "url": "http://agent", "token": "t" * 40}, transport=httpx.MockTransport(handler))
    assert r == {"down": 50.0} and seen["body"] == {"wait": True} and seen["auth"] == "Bearer " + "t" * 40


def test_lock_works_across_processes(tmp_path, monkeypatch, clean_history):
    import fcntl
    import os
    monkeypatch.setattr(speedtest, "LOCK_FILE", str(tmp_path / "lock"))
    fd = os.open(speedtest.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)              # held by "another process" (a running task)
    try:
        assert speedtest.is_running()
        with pytest.raises(speedtest.SpeedtestError, match="läuft schon"):
            speedtest.run(base="https://cf.test", transport=fake_cloudflare([]), duration=0.1, cap=100_000)
    finally:
        os.close(fd)
    assert not speedtest.is_running()


def test_fleet_needs_a_server(admin):
    assert admin.post("/api/speedtest/fleet", json={"servers": []}).status_code == 400


def test_viewer_may_not_start(viewer):
    assert viewer.post("/api/speedtest/fleet", json={"servers": ["local"]}).status_code == 403
    assert viewer.post("/api/tools/speedtest", json={}).status_code == 403


def test_json_roundtrip_of_result_shape(clean_history):
    r = speedtest.run(base="https://cf.test", transport=fake_cloudflare([]), duration=0.1, cap=200_000, streams=1)
    json.dumps(r)   # stored in settings.json
    assert asyncio.iscoroutinefunction(fleet._call)
