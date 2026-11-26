"""
Finds German UI texts without an English translation in static/i18n.js, with the same rules
as i18n.js itself: exact entry in DICT, else one of PATTERNS; only texts that look German
(the GERMAN heuristic of i18n.js) count.

Looked at: text and title / placeholder / aria-label of the HTML pages, texts the scripts
show (t('…'), toasts, textContent / title / placeholder assignments) and error messages of
the backend (detail="…", raise …Error("…")).

    venv/bin/python tests/i18n_check.py          list the missing ones
"""

import html.parser
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")

GERMAN = re.compile(r"[äöüÄÖÜß]|\b(und|der|die|das|nicht|kein|keine|wird|werden|bitte|oder|für|mit|nach|vor|Uhr|jetzt|läuft|gespeichert)\b", re.I)
JS_STR = r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\""


def _unescape(tok: str) -> str:
    body = tok[1:-1]
    return re.sub(r"\\u([0-9a-fA-F]{4})|\\(.)", lambda m: chr(int(m.group(1), 16)) if m.group(1) else
                  {"n": "\n", "t": "\t"}.get(m.group(2), m.group(2)), body)


def load_translations():
    src = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
    a = src.index("const DICT = {")
    b = src.index("\n  };", a)
    dict_src = src[a:b]
    keys = set()
    for m in re.finditer(rf"({JS_STR})\s*:", dict_src):
        keys.add(_unescape(m.group(1)))
    p = src.index("const PATTERNS = [")
    q = src.index("\n  ];", p)
    patterns = []
    for m in re.finditer(r"^\s*\[/(.+?)/([a-z]*),\s", src[p:q], re.M):
        rx = re.sub(r"\(\?<(?![=!])", "(?P<", m.group(1))
        try:
            patterns.append(re.compile(rx, re.I if "i" in m.group(2) else 0))
        except re.error:
            pass
    return keys, patterns


def translated(s: str, keys, patterns) -> bool:
    s = s.strip()
    return s in keys or any(p.search(s) for p in patterns)


def needs_translation(s: str) -> bool:
    s = s.strip()
    return bool(s) and bool(re.search(r"[A-Za-zÄÖÜäöüß]{2}", s)) and bool(GERMAN.search(s))


class _Html(html.parser.HTMLParser):
    SKIP = {"script", "style", "code"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = tag in self.SKIP or "data-no-i18n" in a or (self.stack and self.stack[-1])
        if tag not in ("br", "img", "input", "meta", "link", "hr", "source", "wbr"):
            self.stack.append(bool(skip))
        if not skip:
            for k in ("title", "placeholder", "aria-label"):
                if a.get(k):
                    self.out.append(a[k])

    def handle_endtag(self, tag):
        if self.stack and tag not in ("br", "img", "input", "meta", "link", "hr", "source", "wbr"):
            self.stack.pop()

    def handle_data(self, data):
        if not (self.stack and self.stack[-1]) and data.strip():
            self.out.append(" ".join(data.split()))


JS_CALLS = re.compile(rf"(?:\bt|toast|showToast|msg|setMsg)\(\s*(?:[\w$.]+,\s*)?({JS_STR})")
JS_ASSIGN = re.compile(rf"\.(?:textContent|title|placeholder)\s*=\s*({JS_STR})")
PY_MSG = re.compile(r"""(?:detail=|raise\s+\w*Error\()\s*(?P<q>"|')((?:(?!(?P=q)).)+)(?P=q)""")


def candidates():
    """(text, where) for every UI text found."""
    for name in sorted(os.listdir(STATIC)):
        path = os.path.join(STATIC, name)
        if name.endswith(".html"):
            p = _Html()
            p.feed(open(path, encoding="utf-8").read())
            for s in p.out:
                yield s, name
        elif name.endswith(".js") and name != "i18n.js":
            src = open(path, encoding="utf-8").read()
            for rx in (JS_CALLS, JS_ASSIGN):
                for m in rx.finditer(src):
                    s = _unescape(m.group(1))
                    if "${" not in s:
                        yield s, name
    py = [os.path.join(ROOT, "app.py")] + [os.path.join(d, f) for d in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend", "routes"))
                                           for f in sorted(os.listdir(d)) if f.endswith(".py")]
    for path in py:
        src = open(path, encoding="utf-8").read()
        for m in PY_MSG.finditer(src):
            # f-strings and %-formatting are dynamic: only plain messages can be checked
            before = src[max(0, m.start(1) - 1):m.start(1)]
            if before in ("f", "r") or "{" in m.group(2):
                continue
            yield m.group(2), os.path.relpath(path, ROOT)


def missing():
    keys, patterns = load_translations()
    out = {}
    for s, where in candidates():
        s = s.strip()
        if needs_translation(s) and not translated(s, keys, patterns):
            out.setdefault(" ".join(s.split()), where)   # one line per text in the report
    return out


if __name__ == "__main__":
    miss = missing()
    for s, where in sorted(miss.items(), key=lambda x: (x[1], x[0])):
        print(f"{where}: {s}")
    print(f"\n{len(miss)} texts without English translation", file=sys.stderr)
