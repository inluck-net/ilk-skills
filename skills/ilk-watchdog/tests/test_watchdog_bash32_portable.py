"""Regression: watchdog.sh must be bash 3.2 (macOS /bin/bash) clean.

The watchdog ran terminal-classification banners through `${classification^^}`,
a bash-4-only expansion. Under macOS bash 3.2 that raises "bad substitution"
and crashes the watchdog in the exact path that should relaunch or block a
stopped loop — so an interrupted run was never recovered. These tests lock in
the portable `to_upper` (tr-based) replacement.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
SYSTEM_BASH = "/bin/bash"  # macOS ships bash 3.2 here


def test_no_bash4_case_expansions():
    """No ${var^^} / ${var^} / ${var,,} / ${var,} anywhere in the script."""
    text = WATCHDOG.read_text(encoding="utf-8")
    # Ignore comment lines (the fix's explanatory comment mentions ${var^^}).
    code = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )
    bad = re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?:\^\^?|,,?)\}", code)
    assert not bad, f"bash-4-only case expansions found: {bad}"


def test_syntax_valid_under_system_bash():
    """`bash -n` parses cleanly under the system (3.2) bash."""
    res = subprocess.run([SYSTEM_BASH, "-n", str(WATCHDOG)],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr


def test_banner_sites_use_to_upper():
    """All four terminal-classification banners route through to_upper."""
    text = WATCHDOG.read_text(encoding="utf-8")
    assert text.count('to_upper "$classification"') == 4, \
        "expected 4 banner sites calling to_upper"


def _extract_function(name: str) -> str:
    """Pull a `name() { ... }` definition out of the script (brace-balanced
    on the simple one-level body used here)."""
    lines = WATCHDOG.read_text(encoding="utf-8").splitlines()
    out, capturing, depth = [], False, 0
    for ln in lines:
        if not capturing and re.match(rf"^{re.escape(name)}\(\)\s*\{{", ln):
            capturing = True
        if capturing:
            out.append(ln)
            depth += ln.count("{") - ln.count("}")
            if depth == 0 and len(out) > 1:
                break
    assert out, f"function {name} not found"
    return "\n".join(out)


@pytest.mark.parametrize("raw,expected", [
    ("blocked", "BLOCKED"),
    ("shipped-unverified", "SHIPPED-UNVERIFIED"),
    ("", ""),
    ("MixedCase", "MIXEDCASE"),
])
def test_to_upper_runs_under_system_bash(raw, expected):
    """The real to_upper definition produces uppercase under bash 3.2 with no
    'bad substitution'."""
    func = _extract_function("to_upper")
    script = f"{func}\nto_upper {raw!r}"
    res = subprocess.run([SYSTEM_BASH, "-c", script],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert "bad substitution" not in res.stderr.lower()
    assert res.stdout == expected
