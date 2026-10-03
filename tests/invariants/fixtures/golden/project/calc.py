"""A tiny calculator with three functions.

``scale`` is intentionally broken (multiplies instead of dividing) so
the golden-real sub-plan can pin it and then fix it.
"""


def add(a: float, b: float) -> float:
    """Return a + b."""
    return a + b


def scale(value: float, factor: float) -> float:
    """Return value / factor.

    BUG: multiplies instead of dividing — planted for golden-real to fix.
    """
    return value * factor


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))