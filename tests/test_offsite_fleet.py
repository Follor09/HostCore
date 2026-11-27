"""Off-site backup page: every server's backups exactly once, the server the page shows first."""

import asyncio

import pytest

import fleet as fleet_mod
import offsite
import remotes


@pytest.fixture
def servers(monkeypatch):
    rs = [{"id": "aa11", "name": "pi-garage", "url": "http://x", "token": "t" * 40},
          {"id": "bb22", "name": "pi-keller", "url": "http://y", "token": "t" * 40}]
    monkeypatch.setattr(remotes, "all_remotes", lambda: rs)
    monkeypatch.setattr(offsite, "overview", lambda: {"jobs": [{"id": "m", "name": "Main-Job"}], "tools": {}})

    async def call(rid, method, path, body=None, user=""):
        return {"jobs": [{"id": rid, "name": f"Job von {rid}"}], "tools": {}}
    monkeypatch.setattr(fleet_mod, "_call", call)
    return rs


def jobs(data):
    return [(s["id"], [j["name"] for j in s["jobs"]], bool(s.get("current"))) for s in data["servers"]]


def test_main_dashboard_first(servers):
    assert jobs(asyncio.run(offsite.fleet(""))) == [
        ("local", ["Main-Job"], True), ("aa11", ["Job von aa11"], False), ("bb22", ["Job von bb22"], False)]


def test_selected_agent_first_then_main_and_the_others(servers):
    got = jobs(asyncio.run(offsite.fleet("aa11")))
    assert got == [("aa11", ["Job von aa11"], True), ("local", ["Main-Job"], False), ("bb22", ["Job von bb22"], False)]
    names = [n for _, js, _ in got for n in js]
    assert len(names) == len(set(names))          # nothing twice


def test_unknown_selection_falls_back_to_main(servers):
    assert jobs(asyncio.run(offsite.fleet("zz99")))[0][0] == "local"


def test_actions_for_the_main_dashboard_run_here(servers, monkeypatch):
    monkeypatch.setattr(offsite, "run", lambda jid, user="": {"started": jid, "where": "here"})
    assert asyncio.run(offsite.remote_action("local", "a1b2c3d4e5", "run")) == {"started": "a1b2c3d4e5", "where": "here"}
