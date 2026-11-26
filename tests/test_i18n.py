"""Every German UI text has an English translation (static/i18n.js) - see tests/i18n_check.py.

Texts that were already untranslated when the check was introduced are listed in
tests/i18n_missing.txt; the list may only shrink. After translating some of them:
    UPDATE_I18N=1 venv/bin/python -m pytest tests/test_i18n.py
"""

import os

import i18n_check

KNOWN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n_missing.txt")


def _known():
    try:
        with open(KNOWN, encoding="utf-8") as f:
            lines = f.read().split("\n")
        return {line for line in lines[1:] if line.strip()}     # line 1 is the header
    except FileNotFoundError:
        return set()


def test_translations_load():
    keys, patterns = i18n_check.load_translations()
    assert len(keys) > 1000 and len(patterns) > 100
    assert i18n_check.translated("Speichern", keys, patterns)
    assert i18n_check.translated("Weckruf an NAS gesendet.", keys, patterns)


def test_no_new_untranslated_texts():
    now = set(i18n_check.missing())
    if os.environ.get("UPDATE_I18N"):
        with open(KNOWN, "w", encoding="utf-8") as f:
            f.write("# German UI texts still without English translation (tests/test_i18n.py) - may only shrink\n")
            f.write("".join(f"{s}\n" for s in sorted(now)))
    known = _known()
    new = sorted(now - known)
    assert not new, ("German texts without English translation - add them to static/i18n.js:\n" + "\n".join(new))
    done = sorted(known - now)
    assert not done, ("Translated now, remove from tests/i18n_missing.txt (UPDATE_I18N=1):\n" + "\n".join(done))
