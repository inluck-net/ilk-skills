"""A second test file that pins clamp.

This file is outside golden-red's gate (which runs only test_calc.py),
so a breakage here is invisible to golden-red's own gate but caught by
the batch verify (golden-verify).
"""
from calc import clamp


def test_clamp_boundary():
    """Pin: clamp at exact boundaries."""
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10
