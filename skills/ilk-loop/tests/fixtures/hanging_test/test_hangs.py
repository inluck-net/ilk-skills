"""Fixture: a test that sleeps past any reasonable timeout.

Used by test_bare_pytest_bounded to prove that pytest.ini addopts
kills a hanging test even when the worker omits --timeout on the
command line.
"""
import time


def test_deliberate_hang():
    """Sleep 90s — must be killed by config-level timeout, not by human."""
    time.sleep(90)
