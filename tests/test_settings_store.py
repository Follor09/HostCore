"""settings.json: atomic writes, never wiped by a broken file, no lost updates."""

import json
import os
import threading

import pytest

import settings_store as ss


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(ss, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(ss, "BACKUP_FILE", str(tmp_path / "settings.json.bak"))
    return tmp_path


def test_roundtrip_and_permissions(store):
    ss.set("a", 1)
    ss.set("b", [1, 2])
    assert ss.get("a") == 1 and ss.get("b") == [1, 2] and ss.get("x", "d") == "d"
    assert oct(os.stat(store / "settings.json").st_mode & 0o777) == "0o600"


def test_broken_file_is_not_wiped(store):
    ss.set("keep", "me")
    ss.set("other", 2)            # backup now holds {"keep": "me"}
    (store / "settings.json").write_text("{ kaputt")
    assert ss.get("keep") == "me"            # restored from the backup
    assert any(p.name.startswith("settings.json.broken-") for p in store.iterdir())
    ss.set("new", 3)
    data = json.loads((store / "settings.json").read_text())
    assert data["keep"] == "me" and data["new"] == 3


def test_no_lost_updates(store):
    ss.set("items", [])

    def add(i):
        ss.update("items", lambda items: items + [i], [])
    threads = [threading.Thread(target=add, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(ss.get("items")) == list(range(40))
