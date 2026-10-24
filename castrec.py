"""
Recorder for one web terminal session (see recordings.py): tmux pipes everything the session
prints into this process (pipe-pane), which writes it with timing as an asciicast v2 file.
Runs inside the tmux server, so it records whether a browser is connected or not and keeps
going when the dashboard restarts. Only the standard library.

    python3 castrec.py <file.cast> <cols> <rows>
"""

import codecs
import json
import os
import sys
import time

path, cols, rows = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MAX_BYTES = 50 * 1024 * 1024

start = time.time()
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path) as f:
            start = json.loads(f.readline()).get("timestamp", start)
    except (OSError, ValueError):
        pass
    out = open(path, "a", buffering=1)
else:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    out = os.fdopen(fd, "a", buffering=1)
    out.write(json.dumps({"version": 2, "width": cols, "height": rows, "timestamp": int(start),
                          "env": {"TERM": "xterm-256color"}}) + "\n")

decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
full = False
while True:
    data = os.read(0, 65536)
    if not data:
        break
    if full:
        continue          # keep reading, or tmux would block the session
    text = decoder.decode(data)
    if text:
        out.write(json.dumps([round(time.time() - start, 3), "o", text]) + "\n")
    if out.tell() > MAX_BYTES:
        out.write(json.dumps([round(time.time() - start, 3), "o", "\r\n[Aufzeichnung beendet: 50 MB erreicht]\r\n"]) + "\n")
        full = True
out.close()
