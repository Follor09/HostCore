"""Wake-on-LAN: one list for all servers, each device woken from its chosen server."""

import pytest

import remotes
import settings_store
import wol


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(wol, "send", lambda mac, broadcast="": calls.append((mac, broadcast)))
    return calls


@pytest.fixture
def one_remote():
    old = settings_store.get("remotes", [])
    settings_store.set("remotes", [{"id": "r1", "name": "Pi-Zuhause", "url": "http://127.0.0.1:9", "token": "t" * 40}])
    settings_store.set("wol_imported_remotes", ["r1"])   # no import attempt against the fake address
    yield "r1"
    settings_store.set("remotes", old)


@pytest.mark.parametrize("mac,expected", [
    ("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF"),
    ("AA-BB-CC-DD-EE-FF", "AA:BB:CC:DD:EE:FF"),
    ("aabbccddeeff", "AA:BB:CC:DD:EE:FF"),
])
def test_normalize_mac(mac, expected):
    assert wol.normalize_mac(mac) == expected


@pytest.mark.parametrize("mac", ["", "AA:BB:CC:DD:EE", "AA:BB-CC:DD:EE:FF", "GG:BB:CC:DD:EE:FF"])
def test_normalize_mac_rejects(mac):
    with pytest.raises(ValueError):
        wol.normalize_mac(mac)


def test_add_wake_delete_local(admin, sent):
    res = admin.post("/api/settings/wol", json={"name": "NAS", "mac": "02:00:00:00:00:10", "broadcast": "192.168.1.255"})
    assert res.status_code == 200
    d = res.json()["device"]
    assert d["via"] == "" and d["broadcast"] == "192.168.1.255"
    res = admin.post(f"/api/wol/{d['id']}/wake")
    assert res.status_code == 200
    assert sent == [("02:00:00:00:00:10", "192.168.1.255")]
    admin.post(f"/api/settings/wol/{d['id']}/delete")
    assert all(x["id"] != d["id"] for x in admin.get("/api/wol").json()["devices"])


def test_via_unknown_server_is_refused(admin):
    res = admin.post("/api/settings/wol", json={"name": "X", "mac": "02:00:00:00:00:11", "via": "nope"})
    assert res.status_code == 400


def test_list_is_global_and_via_remote(admin, sent, one_remote):
    res = admin.post("/api/settings/wol", json={"name": "PC", "mac": "02:00:00:00:00:12", "via": one_remote})
    d = res.json()["device"]
    assert d["via_name"] == "Pi-Zuhause"
    # Another server selected: still the same list, served by this dashboard
    admin.cookies.set(remotes.COOKIE, one_remote)
    try:
        listed = admin.get("/api/wol").json()
        assert any(x["id"] == d["id"] for x in listed["devices"])
        assert [h["id"] for h in listed["hosts"]] == ["", one_remote]
        # Waking goes to that server's agent - unreachable here, and nothing is sent locally
        res = admin.post(f"/api/wol/{d['id']}/wake", headers={"X-PiCore-Host": one_remote})
        assert res.status_code == 502 and "Pi-Zuhause" in res.json()["detail"]
        assert sent == []
    finally:
        admin.cookies.delete(remotes.COOKIE)
    # Switched to "this dashboard": now sent from here
    res = admin.post(f"/api/settings/wol/{d['id']}/update", json={"via": ""})
    assert res.json()["device"]["via"] == ""
    assert admin.post(f"/api/wol/{d['id']}/wake").status_code == 200
    assert sent == [("02:00:00:00:00:12", "255.255.255.255")]
    admin.post(f"/api/settings/wol/{d['id']}/delete")


def test_import_skips_known_macs():
    before = settings_store.get("wol_devices", [])
    settings_store.set("wol_devices", [{"id": "a", "name": "A", "mac": "02:00:00:00:00:20", "broadcast": "255.255.255.255", "via": ""}])
    try:
        n = wol.import_devices([{"name": "A2", "mac": "02-00-00-00-00-20"}, {"name": "B", "mac": "02:00:00:00:00:21"},
                                {"name": "", "mac": "02:00:00:00:00:22"}, {"name": "C", "mac": "kaputt"}], "r9")
        assert n == 1
        assert [(d["name"], d["via"]) for d in wol.list_devices()] == [("A", ""), ("B", "r9")]
    finally:
        settings_store.set("wol_devices", before)
