"""update-lib.sh: which release a server installs, and how the release key changes - with real
SSH keys and signed tags in a throw-away repository."""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "update-lib.sh")

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen") or not shutil.which("bash"), reason="needs ssh-keygen and bash")


def run(cmd, cwd=None, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check, capture_output=True, text=True)


@pytest.fixture
def world(tmp_path):
    keys = {}
    for k in ("k1", "k2"):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", k, "-f", str(tmp_path / k)])
        pub = (tmp_path / f"{k}.pub").read_text().split()
        keys[k] = f'hostcore-release namespaces="git" {pub[0]} {pub[1]}\n'
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run(["git", "init", "-q", "-b", "main"], cwd=repo)
    run(["git", "config", "gpg.format", "ssh"], cwd=repo)
    state = tmp_path / "state"
    state.mkdir()

    def release(tag, signers, key=None):
        (repo / ".github").mkdir(exist_ok=True)
        (repo / ".github" / "allowed_signers").write_text("".join(keys[s] for s in signers))
        run(["git", "add", "-A"], cwd=repo)
        run(["git", "commit", "-q", "--allow-empty", "-m", tag], cwd=repo, env=env)
        if key:
            run(["git", "-c", f"user.signingkey={tmp_path / key}", "tag", "-s", tag, "-m", tag], cwd=repo, env=env)
        else:
            run(["git", "tag", "-a", "--no-sign", tag, "-m", tag], cwd=repo, env=env)
        run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo)
        return run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    def lib(call, channel="stable"):
        script = f'set -euo pipefail\nSTATE="{state}"\nCHANNEL="{channel}"\ng() {{ git -C "{repo}" "$@"; }}\n. "{LIB}"\n{call}\n'
        r = run(["bash", "-c", script], check=False)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip(), r.stderr

    def anchor(*signers):
        (state / "allowed_signers").write_text("".join(keys[s] for s in signers))

    def trusted():
        text = (state / "allowed_signers").read_text()
        return [k for k in keys if keys[k].split()[3] in text]

    return {"release": release, "lib": lib, "anchor": anchor, "trusted": trusted, "repo": repo}


def test_unsigned_release_is_skipped(world):
    v1 = world["release"]("v1.0.0", ["k1"], key="k1")
    world["release"]("v1.0.1", ["k1"])                       # unsigned
    world["anchor"]("k1")
    out, err = world["lib"](f"target g main {v1}")
    assert out == v1 and "v1.0.1 is not signed" in err       # stays on v1, says why once
    out, err = world["lib"](f"target g main {v1}")
    assert out == v1 and err == ""                           # ... and not every minute
    base = world["lib"]("g rev-list --max-parents=0 HEAD")[0]
    assert world["lib"](f"target g main {base}")[0] == v1     # an older server gets v1, not the unsigned one


def test_without_anchor_every_release_counts(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    v2 = world["release"]("v1.0.1", ["k1"])
    base = world["lib"]("g rev-list --max-parents=0 HEAD")[0]
    assert world["lib"](f"target g main {base}")[0] == v2


def test_key_change(world):
    v1 = world["release"]("v1.0.0", ["k1"], key="k1")
    v2 = world["release"]("v1.0.1", ["k2"], key="k1")        # brings k2, signed with k1
    v3 = world["release"]("v1.0.2", ["k2"], key="k2")        # signed with the new key
    world["anchor"]("k1")
    # A server whose update.sh does not know key changes yet still reaches the change release
    assert world["lib"](f"target g main {v1}")[0] == v2
    # The key change: newest release signed with a trusted key decides
    out, _ = world["lib"]("rotate_signers g")
    assert "trust anchor updated" in out and world["trusted"]() == ["k2"]
    assert world["lib"](f"target g main {v1}")[0] == v3
    assert world["lib"]("rotate_signers g")[0] == ""          # stable now


def test_old_key_is_revoked_after_the_change(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    world["release"]("v1.0.1", ["k2"], key="k1")
    v3 = world["release"]("v1.0.2", ["k2"], key="k2")
    world["anchor"]("k1")
    world["lib"]("rotate_signers g")
    world["release"]("v9.9.9", ["k1"], key="k1")             # someone with the old key
    world["lib"]("rotate_signers g")
    assert world["trusted"]() == ["k2"]                       # the old key cannot take over again
    assert world["lib"](f"target g main {v3}")[0] == v3       # and its release is not installed


def test_untrusted_release_cannot_change_keys(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    world["release"]("v1.0.1", ["k2"], key="k2")             # new key, but not signed by a trusted one
    world["anchor"]("k1")
    assert world["lib"]("rotate_signers g")[0] == ""
    assert world["trusted"]() == ["k1"]


def test_main_channel_follows_the_branch(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    head = world["release"]("v1.0.1", ["k1"])
    world["anchor"]("k1")
    assert world["lib"](f"target g main {head}~1", channel="main")[0] == head


def test_first_anchor_from_signed_release(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    out, _ = world["lib"]("create_anchor g")
    assert "Signed releases" in out and world["trusted"]() == ["k1"]


def test_first_anchor_from_key_change_release(world):
    world["release"]("v1.0.0", ["k1"], key="k1")
    world["release"]("v1.0.1", ["k2"], key="k1")            # newest: brings k2, signed with k1
    world["lib"]("create_anchor g")
    assert world["trusted"]() == ["k2"]


def test_no_anchor_for_unsigned_or_foreign_releases(world, tmp_path):
    world["release"]("v1.0.0", ["k1"])                       # fork without signatures
    world["lib"]("create_anchor g")
    assert not (tmp_path / "state" / "allowed_signers").exists()
    world["release"]("v1.0.1", ["k1"], key="k2")             # signed, but with a key the release does not list
    world["lib"]("create_anchor g")
    assert not (tmp_path / "state" / "allowed_signers").exists()
