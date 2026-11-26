"""requirements.txt: the self-test understands every line, and the installed versions fit."""

import pytest

import selftest


def test_every_requirement_is_parsed():
    names = [n for n, _ in selftest._requirements()]
    assert "fastapi" in names and "uvicorn" in names
    assert len(names) == len(set(names))


@pytest.mark.parametrize("have,specs,ok", [
    ("0.141.1", [(">=", "0.110.0"), ("<", "0.142")], True),
    ("0.142.0", [(">=", "0.110.0"), ("<", "0.142")], False),
    ("3.4.4", [("==", "3.4.4")], True),
    ("3.4.5", [("==", "3.4.4")], False),
    ("1.2.9", [("~=", "1.2")], True),
    ("2.0.0", [("~=", "1.2")], False),
    ("17.1", [(">=", "12.0"), ("<", "18")], True),
    ("2.0.0rc1", [(">=", "2")], True),
])
def test_satisfies(have, specs, ok):
    assert selftest._satisfies(have, specs) is ok


def test_installed_versions_fit():
    from importlib import metadata
    wrong = [f"{n} {metadata.version(n)}" for n, specs in selftest._requirements()
             if specs and not selftest._satisfies(metadata.version(n), specs)]
    assert not wrong
