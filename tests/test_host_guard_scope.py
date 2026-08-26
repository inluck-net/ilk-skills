"""The host-mutation guard must cover the WHOLE suite, not one directory.

/ilk-ship Phase 0, 2026-08-26.  The guard shipped in
``skills/ilk-loop/tests/conftest.py``.  pytest scopes a conftest to its own
directory and below, so the guard covered ``skills/ilk-loop/tests/`` only —
while the offender it was written for lives in
``skills/ilk-watchdog/tests/test_install_scheduler_autostart.py``.

Proven from the batch-gate run at 14:41 rather than inferred: that file
reported ``.....s`` (5 passed, 1 skipped).  Its ``launchctl`` calls went
straight through.  Had the guard been in scope, they would have raised
``HostMutationBlocked``.

The sub-plan's own ``scope_paths`` named the root ``conftest.py``; the
implementation quietly narrowed it, and the guard's docstring said so:
"the guard does not affect the rest of the suite until it is promoted to
the root conftest".  A green suite plus a guard plus tests for the guard
still left the production launchd label exactly as exposed as before —
testing-principles §5, "an absent gate reads exactly like a passing one".

This file lives in ``tests/`` — deliberately OUTSIDE
``skills/ilk-loop/tests/`` — so it can only pass when the guard is
genuinely suite-wide.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── the guard reaches this directory ────────────────────────────────────────

@pytest.mark.expects_blocked_host
def test_guard_blocks_launchctl_from_outside_ilk_loop_tests() -> None:
    """A denied binary must raise here, in a sibling test tree."""
    with pytest.raises(BaseException) as excinfo:
        subprocess.Popen(["launchctl", "list"])
    assert type(excinfo.value).__name__ == "HostMutationBlocked", (
        "expected the host-mutation guard to fire outside "
        f"skills/ilk-loop/tests/, got {type(excinfo.value).__name__}"
    )


@pytest.mark.expects_blocked_host
def test_guard_names_the_binary_and_the_test() -> None:
    with pytest.raises(BaseException) as excinfo:
        subprocess.Popen(["launchctl", "print", "gui/501/net.inluck.ilk.scheduler"])
    msg = str(excinfo.value)
    assert "launchctl" in msg
    assert "test_guard_names_the_binary_and_the_test" in msg, (
        "the refusal must name the offending test so it can be found"
    )


# ── allowed binaries still pass through, here too ───────────────────────────

@pytest.mark.parametrize("argv", [
    ["git", "--version"],
    ["/bin/bash", "-c", "true"],
])
def test_allowed_binaries_pass_through(argv: list[str]) -> None:
    """git and bash must not be denied — testing-principles §2.

    Our git runs offline against tmp_path repos and is frequently the
    property under test; denying it would red most of the suite.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    assert proc.wait(timeout=30) == 0


# ── the guard lives where the whole suite can see it ────────────────────────

def test_guard_is_defined_in_the_root_conftest() -> None:
    """Placement is the defect; assert placement, not just behaviour.

    Behaviour alone would still pass if someone re-added a second copy in a
    subdirectory and deleted the root one for every tree but this file's.
    """
    root_conftest = (REPO_ROOT / "conftest.py").read_text(encoding="utf-8")
    assert "HostMutationBlocked" in root_conftest, (
        "the guard must be defined in the ROOT conftest.py; a subdirectory "
        "conftest only covers its own tree"
    )
    assert "launchctl" in root_conftest


def test_no_stale_duplicate_guard_in_a_subdirectory() -> None:
    """Two copies would drift, and the narrower one would win silently."""
    dupes = [
        p for p in REPO_ROOT.rglob("conftest.py")
        if p != REPO_ROOT / "conftest.py"
        and ".git" not in p.parts
        and "HostMutationBlocked" in p.read_text(encoding="utf-8")
    ]
    assert dupes == [], (
        f"guard defined in more than one conftest: {[str(p) for p in dupes]}"
    )


# ── the root conftest's own duties survive the merge ────────────────────────

def test_root_conftest_still_registers_sys_path(request) -> None:
    """Regression guard for the merge itself.

    The root conftest already defined ``pytest_configure`` to put every test
    directory on ``sys.path`` (importlib mode does not do it).  The guard
    also needs ``pytest_configure`` to register its marker.  Appending a
    second ``def pytest_configure`` would silently REPLACE the first and
    break collection for the sibling-import files — a failure that looks
    like an unrelated import error.
    """
    import sys
    assert str(REPO_ROOT / "skills" / "ilk-loop" / "tests") in sys.path, (
        "root conftest's sys.path setup was lost — the marker registration "
        "probably overwrote pytest_configure instead of merging into it"
    )


def test_allow_launchctl_marker_is_registered(request) -> None:
    """The exemption marker must be known, or pytest warns and it silently no-ops."""
    markers = request.config.getini("markers")
    assert any(m.startswith("allow_launchctl") for m in markers), (
        "allow_launchctl is not registered; an unregistered marker does not "
        "exempt anything"
    )
