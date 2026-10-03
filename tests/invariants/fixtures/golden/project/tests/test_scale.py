"""Tests for scale — isolated so golden-real's gate is not affected by other sub-plans."""
from calc import scale


def test_scale():
    """Pin: scale(10, 2) should be 5.0 (10 / 2).  BUG: returns 20.0."""
    assert scale(10, 2) == 5.0