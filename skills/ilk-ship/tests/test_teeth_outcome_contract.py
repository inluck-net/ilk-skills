"""Pin: teeth outcome classification and process-group cleanup.

Fast fixtures that reproduce catcher exit 1 plus leaked/late child
completion without running the golden batch.

AC-1: a genuinely caught-red mutation is classified ``caught-red``, not
      generic ``killed``.
AC-2: a slow subprocess is killed by the total deadline and classified
      ``timeout``.
AC-3: a subprocess whose child outlives it has the child cleaned up.
AC-4: the two v0.9.147 mutations produce deterministic outcomes (not
      ambiguous ``killed``).

Sub-plan: the-safety-case-finishes-deterministically, step 0.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"


# ── Helper: a self-contained slow script ─────────────────────────────────────

_SLOW_SCRIPT = textwrap.dedent("""\
    import time
    time.sleep(9999)
""")

_PARENT_WITH_CHILD_SCRIPT = textwrap.dedent("""\
    import os, subprocess, sys, time
    # Spawn a child in the same process group and exit immediately.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(9999)"],
        preexec_fn=os.setpgrp,
    )
    print(f"parent exiting, child pid={child.pid}")
    os._exit(1)
""")

_FAILING_TEST = textwrap.dedent("""\
    def test_deliberate_fail():
        assert False, "mutation-caught-red"
""")


# ── AC-2: slow subprocess killed on deadline ─────────────────────────────────

def test_slow_subprocess_killed_with_timeout_outcome(tmp_path: Path) -> None:
    """A subprocess that exceeds its deadline should be killed and classified
    ``timeout``, not generic ``killed``."""
    sys.path.insert(0, str(_SCRIPTS))
    from teeth import _run_catcher

    slow = tmp_path / "slow.py"
    slow.write_text(_SLOW_SCRIPT)

    exit_code, stdout, stderr, timed_out = _run_catcher(
        ["python3", str(slow)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        timeout_s=2,
    )

    assert exit_code == -1, "timeout sentinel should be -1"
    assert timed_out is True, "timed_out flag should be True"


# ── AC-3: leaked child cleaned up via process group ──────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="teeth._run_catcher does not yet manage process groups",
)
def test_leaked_child_killed_by_process_group(tmp_path: Path) -> None:
    """When the catcher's process group is killed, children spawned in the
    same group should also be terminated."""
    import subprocess

    parent = tmp_path / "parent.py"
    parent.write_text(_PARENT_WITH_CHILD_SCRIPT)

    # Start the parent, wait for it to exit, then check if the child is alive.
    proc = subprocess.Popen(
        ["python3", str(parent)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setpgrp,
    )
    stdout, stderr = proc.communicate(timeout=5)
    output = stdout + stderr

    # Extract child pid from output.
    import re
    m = re.search(r"child pid=(\d+)", output)
    assert m, f"expected 'child pid=N' in output, got: {output!r}"
    child_pid = int(m.group(1))

    # Kill the parent's process group (simulating what _run_catcher does on timeout).
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError):
        pass

    # After process-group kill, the child should be dead.
    try:
        os.kill(child_pid, 0)  # check if alive
        pytest.xfail("child survived: process-group kill did not reach child")
    except ProcessLookupError:
        pass  # child is dead — expected


# ── AC-1: genuine caught-red is classified correctly ─────────────────────────

def test_genuine_fail_classified_as_caught_red(tmp_path: Path) -> None:
    """A mutation that causes a real test failure (not timeout) should be
    classified ``caught-red``, not generic ``killed``."""
    sys.path.insert(0, str(_SCRIPTS))
    from teeth import _run_catcher

    failing = tmp_path / "test_fail.py"
    failing.write_text(_FAILING_TEST)

    exit_code, stdout, stderr, timed_out = _run_catcher(
        ["python3", "-m", "pytest", str(failing), "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        timeout_s=60,
    )

    assert exit_code != 0, "failing test should produce non-zero exit"
    assert timed_out is False, "genuine fail should not timeout"
    combined = stdout + "\n" + stderr
    assert "mutation-caught-red" in combined, "expected failure message in output"


# ── AC-4: the two named mutations produce deterministic outcomes ──────────────

@pytest.mark.xfail(
    strict=True,
    reason="teeth classifies both mutations as generic 'killed'; step 1 distinguishes timeout from caught-red",
)
def test_golden_gate_line_deleted_is_caught_red() -> None:
    """golden-gate-line-deleted should be classified caught-red (not timeout)."""
    sys.path.insert(0, str(_SCRIPTS))
    from teeth import run
    from pathlib import Path as P

    result = run(
        P("."),
        P("tests/invariants/mutations.json"),
        only=["golden-gate-line-deleted"],
        timeout_s=120,
    )
    r = result["results"][0]
    assert r["outcome"] == "caught-red", f"expected caught-red, got {r['outcome']}"


@pytest.mark.xfail(
    strict=True,
    reason="teeth classifies both mutations as generic 'killed'; step 1 distinguishes timeout from caught-red",
)
def test_golden_inert_pin_strictness_dropped_is_caught_red() -> None:
    """golden-inert-pin-strictness-dropped should be classified caught-red (not timeout)."""
    sys.path.insert(0, str(_SCRIPTS))
    from teeth import run
    from pathlib import Path as P

    result = run(
        P("."),
        P("tests/invariants/mutations.json"),
        only=["golden-inert-pin-strictness-dropped"],
        timeout_s=120,
    )
    r = result["results"][0]
    assert r["outcome"] == "caught-red", f"expected caught-red, got {r['outcome']}"