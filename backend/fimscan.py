"""
File integrity: one scan, run as root through pi-helper.sh fim-scan (from the root-owned copy).

    fimscan.py <path> [<path> ...]     one JSON line per file on stdout: {"p", "s", "m", "u", "g", "z", "t"}

Only hashes and metadata leave this script, never file contents. Only a fixed set of system
folders may be scanned: the dashboard asks for paths, but it isn't trusted to choose freely.
Only the standard library; symlinks are recorded by target, not followed.
"""

import hashlib
import json
import os
import stat
import sys

ALLOWED = ("/etc", "/usr/bin", "/usr/sbin", "/usr/lib/systemd", "/usr/local", "/bin", "/sbin", "/boot", "/opt", "/root/.ssh", "/var/spool/cron")
SKIP = ("/etc/mtab", "/etc/ld.so.cache", "/etc/adjtime", "/etc/resolv.conf", "/etc/.pwd.lock", "/etc/passwd-", "/etc/shadow-", "/etc/group-", "/etc/gshadow-")
SKIP_SUFFIX = (".dpkg-new", ".dpkg-tmp", ".swp", "~")
MAX_SIZE = 256 * 1024 * 1024


def allowed(path: str) -> bool:
    real = os.path.normpath(path)
    return any(real == a or real.startswith(a + "/") for a in ALLOWED)


def digest(path: str) -> str:
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def one(path: str) -> None:
    if path in SKIP or path.endswith(SKIP_SUFFIX):
        return
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            sha = "link:" + os.readlink(path)
        elif stat.S_ISREG(st.st_mode):
            sha = "big" if st.st_size > MAX_SIZE else digest(path)
        else:
            return
    except OSError:
        return
    print(json.dumps({"p": path, "s": sha, "m": stat.S_IMODE(st.st_mode), "u": st.st_uid, "g": st.st_gid, "z": st.st_size, "t": int(st.st_mtime)}, ensure_ascii=False))


def main() -> None:
    paths = sys.argv[1:]
    if not paths or not all(p.startswith("/") and allowed(p) for p in paths):
        sys.exit("Nicht erlaubter Pfad.")
    os.nice(10)
    for top in paths:
        if os.path.isfile(top):
            one(top)
            continue
        for folder, dirs, names in os.walk(top, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d))]
            for name in names:
                one(os.path.join(folder, name))


if __name__ == "__main__":
    main()
