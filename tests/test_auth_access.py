"""Login and permissions: what an anonymous visitor, a viewer and an admin may do."""

import pytest

import access


def test_anonymous_gets_no_data(anon):
    assert anon.get("/api/settings").status_code == 401
    assert anon.get("/api/wol").status_code in (401, 403)


def test_anonymous_cannot_act(anon):
    assert anon.post("/api/settings/wol", json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"}).status_code in (401, 403)


def test_pages_redirect_to_login(anon):
    res = anon.get("/settings", follow_redirects=False)
    assert res.status_code in (302, 303, 307) and "/login" in res.headers["location"]


def test_viewer_may_read_but_not_change(viewer):
    assert viewer.get("/api/system").status_code == 200
    assert viewer.post("/api/settings/wol", json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"}).status_code == 403
    assert viewer.post("/api/users", json={"username": "evil", "password": "x" * 12, "group": "admin"}).status_code == 403


def test_admin_may_change(admin):
    res = admin.post("/api/settings/wol", json={"name": "Perm-Test", "mac": "02:00:00:00:00:01"})
    assert res.status_code == 200
    admin.post(f"/api/settings/wol/{res.json()['device']['id']}/delete")


def test_foreign_origin_is_refused(admin):
    res = admin.post("/api/settings/wol", json={"name": "x", "mac": "02:00:00:00:00:02"},
                     headers={"Origin": "https://evil.example"})
    assert res.status_code == 403


@pytest.mark.parametrize("method,path,perm", [
    ("POST", "/api/settings/wol", "settings.manage"),
    ("POST", "/api/settings/wol/abcd1234/update", "settings.manage"),
    ("POST", "/api/settings/wol/abcd1234/delete", "settings.manage"),
    ("POST", "/api/wol/abcd1234/wake", "network.tools"),
    ("POST", "/api/users", "users.manage"),
    ("POST", "/api/some/new/route/nobody/classified", "users.manage"),   # unknown = admins only
    ("GET", "/api/users", "users.manage"),
    ("GET", "/api/system", None),            # readable without login when public view is on
    ("GET", "/api/audit", "login"),
])
def test_required_permission(method, path, perm):
    assert access.required_permission(method, path) == perm
