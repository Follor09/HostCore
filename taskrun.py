"""
Runner for one dashboard task (see tasks.py) - started as its own process, normally as a
transient systemd user unit, so it carries on when the browser is closed and even when the
dashboard itself restarts (auto-update).

    python3 taskrun.py <task folder> <task id>

Reads <id>.json (what to run), writes the output to <id>.log and the outcome to <id>.status.
Steps run one after the other; the first failing step ends the task. SIGTERM (cancel)
stops the running step and its children. Only the standard library: it must not depend
on the dashboard's state.
"""

import json
import os
import signal
import subprocess
import sys
import time

folder, task_id = sys.argv[1], sys.argv[2]
base = os.path.join(folder, task_id)
spec = json.load(open(base + ".json"))
state = {"state": "running", "pid": os.getpid(), "started": int(time.time()), "ended": None, "exit": None, "step": 0}
child = None
cancelled = False


def write_status(**kw):
    state.update(kw)
    tmp = base + ".status.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, base + ".status")


def stop_child():
    if child and child.poll() is None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                child.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                continue


def on_term(signum, frame):
    global cancelled
    cancelled = True
    stop_child()


signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
write_status()

env = dict(os.environ)
env.update(spec.get("env") or {})
deadline = time.time() + int(spec.get("timeout") or 3600)
code = 0
with open(base + ".log", "ab", buffering=0) as log:
    for n, step in enumerate(spec["steps"]):
        if cancelled:
            break
        write_status(step=n)
        argv = step["argv"] if "argv" in step else ["bash", "-o", "pipefail", "-c", step["shell"]]
        log.write(f"$ {step.get('show') or step.get('shell') or ' '.join(argv)}\n".encode())
        try:
            child = subprocess.Popen(argv, cwd=spec.get("cwd") or None, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            log.write(f"{e}\n".encode())
            code = 127
            break
        while True:
            try:
                code = child.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if time.time() > deadline:
                    log.write("\nZeitüberschreitung – Aufgabe abgebrochen.\n".encode())
                    stop_child()
                    code = child.wait()
                    write_status(state="timeout")
                    break
        if state["state"] == "timeout" or code != 0:
            break
    if cancelled:
        log.write("\nAbgebrochen.\n".encode())

final = "cancelled" if cancelled else state["state"] if state["state"] == "timeout" else ("ok" if code == 0 else "error")
write_status(state=final, exit=code, ended=int(time.time()))
