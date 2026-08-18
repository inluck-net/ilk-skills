"""Fixture: a test file that FAILS TO COLLECT on Python 3.9.

The module-scope `list[str] | None` evaluates at import time without
``from __future__ import annotations``.  On Python 3.9 the ``|`` operator
for types does not exist, so pytest emits:

  ERROR collecting .../test_bad_annotation.py
    TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'

This is the exact shape of the live collection error in
``skills/ilk-loop/tests/test_loop_status_json_clean.py`` (present since
2026-07-04, commit 2dd3532).  That file has ``from __future__ import annotations``
which defers evaluation; this fixture deliberately omits it.

59 of 63 files in ``skills/ilk-loop/tests/`` have the future import.
The 4 that do not are the ones that break.
"""
# NO `from __future__ import annotations` — this is intentional.

def function_with_union(x: list[str] | None) -> str:
    """Uses PEP 604 union at module scope — fails on Python < 3.10."""
    return str(x)


def test_placeholder() -> None:
    """This test is never reached because collection fails first."""
    assert True
