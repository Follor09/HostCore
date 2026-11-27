"""Files page (own cloud): private and shared spaces, paths that must not leave a space,
quota, recycle bin, favorites, share links, and the file explorer not reaching private files."""

import os

import pytest

import auth
import cloud
import files
import settings_store
from conftest import PASSWORD, _client


@pytest.fixture(scope="module")
def users(app_module):
    for name in ("bob", "carol"):
        auth.create_user(name, PASSWORD, "user")
    return {"bob": _client(app_module, "bob"), "carol": _client(app_module, "carol")}


def put(c, name, data=b"hallo", space="me", path="", **extra):
    return c.put("/api/cloud/upload", params={"space": space, "path": path, "name": name, **extra}, content=data)


def names(c, space="me", path=""):
    r = c.get("/api/cloud/list", params={"space": space, "path": path})
    assert r.status_code == 200, r.text
    return [e["name"] for e in r.json()["entries"]]


def test_user_group_has_cloud_but_viewer_not(users, viewer):
    assert auth.can("bob", "cloud")
    assert viewer.get("/api/cloud").status_code == 403


def test_upload_list_download(users):
    bob = users["bob"]
    assert put(bob, "notiz.txt", b"Inhalt").status_code == 200
    assert "notiz.txt" in names(bob)
    r = bob.get("/api/cloud/file", params={"space": "me", "path": "notiz.txt"})
    assert r.status_code == 200 and r.content == b"Inhalt"
    assert put(bob, "notiz.txt").status_code == 409                       # no silent overwrite
    assert put(bob, "notiz.txt", b"neu", overwrite=1).status_code == 200


def test_private_space_stays_private(users, admin):
    bob, carol = users["bob"], users["carol"]
    put(bob, "geheim.txt")
    assert "geheim.txt" not in names(carol)
    assert carol.get("/api/cloud/list", params={"space": "user:bob"}).status_code == 400
    assert "geheim.txt" in names(admin, "user:bob")                       # admins see every user's files
    assert carol.get("/api/cloud/search", params={"q": "geheim"}).json()["results"] == []


def test_shared_space_is_shared(users):
    put(users["bob"], "plan.txt", space="shared")
    assert "plan.txt" in names(users["carol"], "shared")


@pytest.mark.parametrize("path", ["../carol", "../../", "a/../../x", "/etc", ".trash"])
def test_paths_cannot_leave_the_space(users, path):
    r = users["bob"].get("/api/cloud/list", params={"space": "me", "path": path})
    assert r.status_code in (400, 404)
    assert put(users["bob"], "x.txt", path=path).status_code in (400, 404)


def test_symlink_out_of_the_space_is_refused(users):
    base = cloud.key_dir("u/bob")
    os.symlink("/etc", os.path.join(base, "raus"))
    try:
        assert users["bob"].get("/api/cloud/list", params={"space": "me", "path": "raus"}).status_code == 400
    finally:
        os.remove(os.path.join(base, "raus"))


def test_file_explorer_cannot_reach_private_files(users):
    put(users["bob"], "privat.txt")
    real = os.path.join(cloud.key_dir("u/bob"), "privat.txt")
    assert files.is_denied(os.path.realpath(real))
    assert not files.is_denied(os.path.realpath(cloud.key_dir("s")))


def test_folder_upload_rename_move_and_favorites(users):
    bob = users["bob"]
    assert put(bob, "a.txt", sub="Urlaub/Tag 1").status_code == 200
    assert names(bob, path="Urlaub/Tag 1") == ["a.txt"]
    assert bob.post("/api/cloud/favorite", json={"space": "me", "path": "Urlaub/Tag 1/a.txt", "on": True}).status_code == 200
    assert bob.post("/api/cloud/rename", json={"space": "me", "path": "Urlaub", "name": "Reise"}).status_code == 200
    favs = bob.get("/api/cloud/favorites").json()["items"]
    assert [f["path"] for f in favs] == ["Reise/Tag 1/a.txt"]             # favorites follow a rename
    r = bob.post("/api/cloud/transfer", json={"space": "me", "paths": ["Reise"], "target_space": "shared", "target": "", "mode": "move"})
    assert r.status_code == 200
    assert "Reise" in names(bob, "shared") and "Reise" not in names(bob)
    r = bob.post("/api/cloud/transfer", json={"space": "shared", "paths": ["Reise"], "target_space": "shared", "target": "Reise/Tag 1", "mode": "move"})
    assert r.status_code == 400                                           # not into itself


def test_trash_restore_and_delete(users):
    bob = users["bob"]
    put(bob, "weg.txt")
    assert bob.post("/api/cloud/delete", json={"space": "me", "paths": ["weg.txt"]}).status_code == 200
    assert "weg.txt" not in names(bob)
    item = next(i for i in bob.get("/api/cloud/trash").json()["items"] if i["name"] == "weg.txt")
    assert item["space"] == "me"
    assert users["carol"].post("/api/cloud/trash/restore", json={"space": "me", "id": item["id"]}).status_code == 404
    assert bob.post("/api/cloud/trash/restore", json={"space": "me", "id": item["id"]}).status_code == 200
    assert "weg.txt" in names(bob)


def test_quota(users):
    settings_store.set("cloud", {**cloud.config(), "quota_mb": 1})
    try:
        r = put(users["carol"], "gross.bin", b"x" * (2 * 1024 * 1024))
        assert r.status_code == 400 and "Kontingent" in r.json()["detail"]
        assert put(users["carol"], "klein.bin", b"x" * 1024, space="shared").status_code == 200   # shared: no quota
    finally:
        settings_store.set("cloud", {**cloud.config(), "quota_mb": 0})


def test_share_link(users, anon):
    bob = users["bob"]
    put(bob, "teilen.txt", b"oeffentlich")
    r = bob.post("/api/cloud/shares", json={"space": "me", "path": "teilen.txt", "hours": 24})
    assert r.status_code == 200
    token = r.json()["token"]
    got = anon.get(f"/s/c/{token}")
    assert got.status_code == 200 and got.content == b"oeffentlich"
    assert users["carol"].post(f"/api/cloud/shares/{token}/delete").status_code == 404   # not hers
    assert bob.post(f"/api/cloud/shares/{token}/delete").status_code == 200
    assert anon.get(f"/s/c/{token}").status_code == 404


def test_only_admins_change_storage_settings(users, admin, monkeypatch):
    import websec
    monkeypatch.setattr(websec, "needs_reauth", lambda *a, **k: False)
    assert users["bob"].post("/api/settings/cloud", json={"quota_mb": 5}).status_code == 403
    assert admin.post("/api/settings/cloud", json={"root": "/"}).status_code == 400
    assert admin.post("/api/settings/cloud", json={"quota_mb": 0, "trash_days": 14}).status_code == 200
    assert cloud.config()["trash_days"] == 14


def test_builtin_group_saved_before_gets_new_permission(app_module):
    cfg = auth.load_config()
    groups = auth.all_groups({**cfg, "groups": {"user": {"name": "Benutzer", "perms": ["system.view"]}}})
    assert "cloud" in groups["user"]["perms"]                             # never had the choice
    groups = auth.all_groups({**cfg, "groups": {"user": {"name": "Benutzer", "perms": ["system.view"], "seen": auth.ALL_PERMISSIONS}}})
    assert "cloud" not in groups["user"]["perms"]                         # removed on purpose
