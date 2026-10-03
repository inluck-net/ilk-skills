"""Tests for calc.py — add, scale, clamp."""
from calc import add, scale, clamp


def test_add():
    assert add(2, 3) == 5


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(15, 0, 10) == 10