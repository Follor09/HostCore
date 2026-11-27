"""Off-site backup: restic locks of interrupted runs never block restoring, checking or listing."""

import os
import stat

import pytest

import offsitetool as ot

LOCKED = """repo already locked, waiting up to 0s for the lock
unable to create lock in backend: repository is already locked by PID 4711 on pi4 by root (UID 0, GID 0)
lock was created at 2026-11-26 22:01:02 (3h12m0s ago)
storage ID 1a2b3c4d
the `unlock` command can be used to remove stale locks"""


def test_locked_message_in_plain_words():
    msg = ot.explain(LOCKED)
    assert msg.startswith("Das Repository ist gesperrt")
    assert "pi4 (Prozess 4711)" in msg and "2026-11-26 22:01:02" in msg and "3h12m0s" in msg
    assert "unlock" not in msg


def test_other_errors_unchanged():
    assert ot.explain("Fatal: wrong password or no key found") == "Fatal: wrong password or no key found"


@pytest.fixture
def fake_restic(tmp_path, monkeypatch):
    """A restic that is locked until 'unlock' ran (the lock of an interrupted run)."""
    log = tmp_path / "calls"
    script = tmp_path / "restic"
    script.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$1" in
  unlock) touch {tmp_path}/unlocked; echo "successfully removed 1 locks"; exit 0 ;;
  snapshots) case "$*" in *--no-lock*) echo '[]'; exit 0 ;; esac; echo "{LOCKED.splitlines()[1]}" >&2; exit 1 ;;
  restore|check) if [ -e {tmp_path}/unlocked ]; then echo "restoring"; exit 0; fi
                 printf '%s\\n' "{LOCKED.splitlines()[1]}" "the \\`unlock\\` command can be used to remove stale locks" >&2; exit 1 ;;
esac
exit 0
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    r = object.__new__(ot.Restic)
    r.env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    r.opts, r.host, r.repo = [], "pi4", "/tmp/repo"
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return r, log, tmp_path


def test_listing_needs_no_lock(fake_restic):
    r, log, _ = fake_restic
    assert r.snapshots() == {"exists": True, "snapshots": []}
    assert "--no-lock" in log.read_text()


def test_restore_removes_stale_lock_first(fake_restic, monkeypatch, capsys):
    r, log, tmp = fake_restic
    monkeypatch.setattr(ot, "RESTORE_ROOT", str(tmp / "restore"))
    ot.restore({}, r, "a1b2c3d4", "")
    calls = log.read_text().splitlines()
    assert calls[0].startswith("unlock") and calls[1].startswith("restore")
    out = capsys.readouterr().out
    assert "verwaiste Sperre" in out and "Wiederhergestellt" in out


def test_active_lock_is_explained(fake_restic, monkeypatch, capsys):
    r, log, tmp = fake_restic
    # unlock finds nothing stale: the lock belongs to a run that is still working
    (tmp / "restic").write_text((tmp / "restic").read_text().replace('touch ' + str(tmp) + '/unlocked; echo "successfully removed 1 locks"',
                                                                     'echo "successfully removed 0 locks"'))
    monkeypatch.setattr(ot, "RESTORE_ROOT", str(tmp / "restore"))
    with pytest.raises(SystemExit):
        ot.restore({}, r, "a1b2c3d4", "")
    out = capsys.readouterr()
    text = out.out + out.err
    assert "Das Repository ist gesperrt" in text and "pi4 (Prozess 4711)" in text
