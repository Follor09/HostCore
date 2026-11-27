"""Storage cleanup: what the root tool (backend/cleanuptool.py) picks - only rotated logs, only
container logs of real containers, only images no container uses, only autoremove's packages."""

import os

import cleanuptool


def _touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_only_rotated_logs_outside_the_journal(tmp_path):
    root = str(tmp_path)
    for name in ("syslog", "syslog.1", "kern.log.2.gz", "auth.log", "Xorg.0.log", "messages-20261101",
                 "nginx/access.log", "nginx/access.log.3.gz", "apt/history.log.1.zst", "journal/abc/system@x.journal~.1"):
        _touch(os.path.join(root, name))
    os.symlink(os.path.join(root, "syslog"), os.path.join(root, "link.1"))
    found = {os.path.relpath(p, root) for p in cleanuptool.rotated_logs(root)}
    assert found == {"syslog.1", "kern.log.2.gz", "messages-20261101", "nginx/access.log.3.gz", "apt/history.log.1.zst"}


def test_docker_logs_live_and_rotated(tmp_path, monkeypatch):
    cid = "a" * 64
    base = tmp_path / "docker" / "containers"
    _touch(str(base / cid / f"{cid}-json.log"))
    _touch(str(base / cid / f"{cid}-json.log.1"))
    _touch(str(base / cid / "config.v2.json"))
    _touch(str(base / "not-a-container" / "x-json.log"))
    monkeypatch.setattr(cleanuptool, "DOCKER_ROOTS", (str(tmp_path / "docker"),))
    monkeypatch.setattr(cleanuptool, "out", lambda *a, **k: "")
    logs = cleanuptool.docker_logs()
    assert [os.path.basename(p) for p in logs["live"]] == [f"{cid}-json.log"]
    assert [os.path.basename(p) for p in logs["rotated"]] == [f"{cid}-json.log.1"]


def test_autoremove_parsing():
    sim = """Reading package lists...
The following packages will be REMOVED:
  linux-image-6.8.0-1010-raspi* linux-modules-6.8.0-1010-raspi*
Purg linux-image-6.8.0-1010-raspi [6.8.0-1010.11]
Purg linux-modules-6.8.0-1010-raspi [6.8.0-1010.11]
Remv libfoo1:arm64 [1.2-3]
Purg bad;rm [1]
"""
    names = cleanuptool.parse_autoremove(sim)
    assert names == ["linux-image-6.8.0-1010-raspi", "linux-modules-6.8.0-1010-raspi", "libfoo1:arm64"]
    assert [n for n in names if cleanuptool.KERNEL_RE.match(n)] == ["linux-image-6.8.0-1010-raspi"]


def test_unused_k3s_images():
    images = {"images": [
        {"id": "sha256:" + "1" * 64, "repoTags": ["docker.io/library/nginx:1.27"], "size": "50000"},
        {"id": "sha256:" + "2" * 64, "repoTags": ["docker.io/library/redis:7"], "size": "30000"},
        {"id": "sha256:" + "3" * 64, "repoTags": ["docker.io/rancher/mirrored-pause:3.6"], "size": "300", "pinned": True},
        {"id": "sha256:" + "4" * 64, "repoTags": [], "size": "1000"},
    ]}
    containers = {"containers": [{"imageRef": "sha256:" + "1" * 64, "image": {"image": "sha256:" + "1" * 64}}]}
    found = cleanuptool.unused_images(images, containers)
    assert [f["name"] for f in found] == ["docker.io/library/redis:7", "4" * 12]
    assert sum(f["size"] for f in found) == 31000


def test_unknown_item_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(cleanuptool.os, "geteuid", lambda: 0)
    try:
        cleanuptool.main(["run", "/etc"])
    except SystemExit as e:
        assert e.code == 2
    assert "Unbekannter Befehl." in capsys.readouterr().err
