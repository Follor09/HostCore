"""
Health check after a stack update - the last step of the "Aktualisieren" task (see containers.py).

    python3 stackhealth.py <compose project> [timeout seconds]

Waits until every container of the project runs and Docker healthchecks are no longer
"starting", then watches them a little longer: a container that keeps restarting fails the
check as well. Exit code 0 = healthy, 1 = not. Only the standard library (runs in the task).
"""

import json
import subprocess
import sys
import time

project = sys.argv[1]
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 120
SETTLE = 20


def containers():
    res = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{json .}}"],
                         capture_output=True, text=True, timeout=30)
    out = []
    for line in res.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def restarts(names):
    if not names:
        return {}
    res = subprocess.run(["docker", "inspect", "-f", "{{.Name}} {{.RestartCount}}", *names], capture_output=True, text=True, timeout=30)
    return {l.split()[0].lstrip("/"): int(l.split()[1]) for l in res.stdout.splitlines() if len(l.split()) == 2}


def judge(c):
    """ok / wait / bad, with a short reason."""
    state, status = c.get("State", ""), c.get("Status", "")
    if state == "running":
        if "(health: starting)" in status:
            return "wait", "Healthcheck läuft noch"
        if "(unhealthy)" in status:
            return "bad", "Healthcheck meldet Fehler"
        return "ok", "läuft" + (" (gesund)" if "(healthy)" in status else "")
    if state == "exited" and status.startswith("Exited (0)"):
        return "ok", "einmalig gelaufen und sauber beendet"
    if state in ("restarting", "created"):
        return "wait", "startet"
    return "bad", status or state


print(f"Prüfe die Container von {project} (bis zu {timeout} s) …", flush=True)
deadline = time.time() + timeout
while True:
    cs = containers()
    verdicts = {c["Names"]: judge(c) for c in cs}
    if not cs:
        print("Keine Container gefunden.", flush=True)
        sys.exit(1)
    if all(v[0] == "ok" for v in verdicts.values()) or time.time() > deadline or any(v[0] == "bad" for v in verdicts.values()):
        break
    time.sleep(3)

if all(v[0] == "ok" for v in verdicts.values()):
    running = [n for n, c in ((c["Names"], c) for c in cs) if c.get("State") == "running"]
    before = restarts(running)
    time.sleep(SETTLE)
    after = restarts(running)
    for n in running:
        if after.get(n, 0) > before.get(n, 0):
            verdicts[n] = ("bad", f"startet ständig neu ({after[n] - before.get(n, 0)}× in {SETTLE} s)")
    for c in containers():
        if c["Names"] in verdicts and verdicts[c["Names"]][0] == "ok" and judge(c)[0] != "ok":
            verdicts[c["Names"]] = judge(c)

failed = False
for name, (verdict, reason) in sorted(verdicts.items()):
    mark = "OK    " if verdict == "ok" else "FEHLER" if verdict == "bad" else "OFFEN "
    print(f"  {mark} {name}: {reason}", flush=True)
    failed = failed or verdict != "ok"
print("Gesundheitsprüfung fehlgeschlagen." if failed else "Alle Container laufen.", flush=True)
sys.exit(1 if failed else 0)
