"""Red-first tests for pinned-loops-do-not-block.

Covers AC-1..AC-5 from sub-plan pinned-loops-do-not-block:

  AC-1  release_dir_of returns the release dir for a release runner, None for a clone runner.
  AC-2  unpinned([pinned, clone]) returns [clone]; with no marker returns both.
  AC-3  CLI pinned_loops.py filter prints the unpinned pid.
  AC-4  bounce_daemons.sh wires through pinned_loops.py filter; still exits 2 when unpinned loops remain.
  AC-5  _live_loops_local / _live_loops_remote drop pinned pids.

All tests are xfail(strict=True) because the production modules do not yet exist.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WATCHDOG_SCRIPTS = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
_SHIP_SCRIPTS = _REPO_ROOT / "skills" / "ilk-ship" / "scripts"
_LOOP_SCRIPTS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"

# ── AC-1: release_dir_of ─────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_release_dir_of_returns_release_dir() -> None:
    """release_dir_of extracts the release dir from a runner command."""
    from skills.ilk_watchdog.scripts.pinned_loops import release_dir_of

    cmd = "bash /u/.ilk/releases/v9/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x"
    assert release_dir_of(cmd) == "/u/.ilk/releases/v9"


@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_release_dir_of_returns_none_for_clone() -> None:
    """release_dir_of returns None for a runner under a clone, not a release."""
    from skills.ilk_watchdog.scripts.pinned_loops import release_dir_of

    cmd = "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x"
    assert release_dir_of(cmd) is None


# ── AC-2: unpinned ────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_unpinned_returns_only_unpinned_with_marker(tmp_path: Path) -> None:
    """unpinned([pinned, clone]) returns [clone] when the marker exists."""
    from skills.ilk_watchdog.scripts.pinned_loops import unpinned

    release_dir = tmp_path / ".ilk" / "releases" / "v9" / "skills" / "ilk-loop" / "scripts"
    release_dir.mkdir(parents=True)
    (release_dir / "RUN_PINS_RELEASE").write_text("")

    entries = [
        (1, f"bash {release_dir.parent.parent.parent.parent.parent / 'v9' / 'skills' / 'ilk-loop' / 'scripts' / 'run_ilk_loop_claude.sh'} --project-path x"),
        (2, "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x"),
    ]
    # The first entry's release dir is tmp_path/.ilk/releases/v9
    result = unpinned(entries, exists=lambda p: p == str(release_dir / "RUN_PINS_RELEASE"))
    assert result == [2]


@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_unpinned_returns_all_without_marker(tmp_path: Path) -> None:
    """unpinned returns all pids when no marker exists."""
    from skills.ilk_watchdog.scripts.pinned_loops import unpinned

    entries = [
        (1, "bash /u/.ilk/releases/v9/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x"),
        (2, "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x"),
    ]
    result = unpinned(entries, exists=lambda p: False)
    assert result == [1, 2]


# ── AC-3: CLI filter ──────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_cli_filter_prints_unpinned_pid(tmp_path: Path) -> None:
    """pinned_loops.py filter prints only unpinned pids."""
    release_dir = tmp_path / ".ilk" / "releases" / "v9" / "skills" / "ilk-loop" / "scripts"
    release_dir.mkdir(parents=True)
    (release_dir / "RUN_PINS_RELEASE").write_text("")

    stdin_data = (
        f"1\tbash {tmp_path / '.ilk' / 'releases' / 'v9' / 'skills' / 'ilk-loop' / 'scripts' / 'run_ilk_loop_claude.sh'} --project-path x\n"
        "2\tbash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path x\n"
    )
    result = subprocess.run(
        [sys.executable, str(_WATCHDOG_SCRIPTS / "pinned_loops.py"), "filter"],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2"


# ── AC-4: bounce_daemons.sh wiring ────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_bounce_pipes_through_pinned_loops_filter() -> None:
    """bounce_daemons.sh text pipes through pinned_loops.py filter."""
    script = (_WATCHDOG_SCRIPTS / "bounce_daemons.sh").read_text(encoding="utf-8")
    assert "pinned_loops.py" in script and "filter" in script, (
        "bounce_daemons.sh must call pinned_loops.py filter to drop pinned loops"
    )


@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_bounce_filter_inserted_before_refusal_guard() -> None:
    """bounce_daemons.sh inserts the pinned_loops.py filter BEFORE the refusal check.

    The filter must sit between the loop-detection while loop and the
    ``_running_count`` test, so pinned loops are dropped before the exit-2
    decision.  We verify by checking the script text ordering.
    """
    script = (_WATCHDOG_SCRIPTS / "bounce_daemons.sh").read_text(encoding="utf-8")
    # Find the filter call and the refusal check
    filter_pos = script.find("pinned_loops.py")
    assert filter_pos != -1, "bounce_daemons.sh must call pinned_loops.py"
    # The refusal message must come AFTER the filter
    refusal_pos = script.find("refused:")
    assert refusal_pos != -1, "bounce_daemons.sh must have the refusal message"
    assert filter_pos < refusal_pos, (
        "pinned_loops.py filter must run BEFORE the refusal check"
    )


# ── AC-5: train drops pinned pids ─────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_live_loops_local_drops_pinned(tmp_path: Path) -> None:
    """_live_loops_local with monkeypatched subprocess returns only unpinned pid."""
    sys.path.insert(0, str(_SHIP_SCRIPTS.parent.parent))
    from skills.ilk_ship.scripts.release_train import _live_loops_local

    release_dir = tmp_path / ".ilk" / "releases" / "v9"
    scripts_dir = release_dir / "skills" / "ilk-loop" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "RUN_PINS_RELEASE").write_text("")

    pinned_cmd = f"bash {scripts_dir / 'run_ilk_loop_claude.sh'} --project-path /tmp/p"
    clone_cmd = "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /tmp/c"

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if cmd[0] == "pgrep":
            r = subprocess.CompletedProcess(cmd, 0, stdout="100\n200\n", stderr="")
            return r
        if cmd[0] == "ps":
            pid = cmd[-1]  # -p <pid>
            if pid == "100":
                return subprocess.CompletedProcess(cmd, 0, stdout=pinned_cmd + "\n", stderr="")
            if pid == "200":
                return subprocess.CompletedProcess(cmd, 0, stdout=clone_cmd + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    import unittest.mock
    with unittest.mock.patch("subprocess.run", side_effect=fake_run):
        with unittest.mock.patch("os.getpid", return_value=1):
            with unittest.mock.patch("os.getppid", return_value=2):
                with unittest.mock.patch("os.kill", return_value=None):
                    result = _live_loops_local()

    assert result == [200], f"expected only unpinned pid 200, got {result}"


@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_live_loops_remote_drops_pinned_when_marker_ls_succeeds(tmp_path: Path) -> None:
    """_live_loops_remote with a marker ls success returns only unpinned pid."""
    sys.path.insert(0, str(_SHIP_SCRIPTS.parent.parent))
    from skills.ilk_ship.scripts.release_train import _live_loops_remote

    release_dir = "/u/.ilk/releases/v9"
    pinned_cmd = f"bash {release_dir}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /tmp/p"
    clone_cmd = "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /tmp/c"

    calls: list[tuple] = []

    def fake_run(host, cmd_list, timeout=10):
        calls.append((host, cmd_list))
        if cmd_list[0] == "pgrep":
            return {"rc": 0, "stdout": "100\n200\n", "stderr": ""}
        if cmd_list[0] == "ps":
            pid = cmd_list[-1]
            if pid == "100":
                return {"rc": 0, "stdout": pinned_cmd + "\n", "stderr": ""}
            if pid == "200":
                return {"rc": 0, "stdout": clone_cmd + "\n", "stderr": ""}
        if cmd_list[0] == "ls":
            # marker exists
            return {"rc": 0, "stdout": "", "stderr": ""}
        return {"rc": 1, "stdout": "", "stderr": ""}

    result = _live_loops_remote(fake_run, "host1")
    assert result == [200], f"expected only unpinned pid 200, got {result}"


@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_live_loops_remote_keeps_both_when_marker_ls_fails(tmp_path: Path) -> None:
    """_live_loops_remote with a marker ls failure returns both pids (fail closed)."""
    sys.path.insert(0, str(_SHIP_SCRIPTS.parent.parent))
    from skills.ilk_ship.scripts.release_train import _live_loops_remote

    release_dir = "/u/.ilk/releases/v9"
    pinned_cmd = f"bash {release_dir}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /tmp/p"
    clone_cmd = "bash /u/clone/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /tmp/c"

    def fake_run(host, cmd_list, timeout=10):
        if cmd_list[0] == "pgrep":
            return {"rc": 0, "stdout": "100\n200\n", "stderr": ""}
        if cmd_list[0] == "ps":
            pid = cmd_list[-1]
            if pid == "100":
                return {"rc": 0, "stdout": pinned_cmd + "\n", "stderr": ""}
            if pid == "200":
                return {"rc": 0, "stdout": clone_cmd + "\n", "stderr": ""}
        if cmd_list[0] == "ls":
            # marker does NOT exist
            return {"rc": 1, "stdout": "", "stderr": ""}
        return {"rc": 1, "stdout": "", "stderr": ""}

    result = _live_loops_remote(fake_run, "host1")
    assert result == [100, 200], f"expected both pids (fail closed), got {result}"


# ── AC-6: control — other unit_test_targets pass unchanged ────────────────────

@pytest.mark.xfail(strict=True, reason="every live loop blocks a release")
def test_control_pinned_loops_module_exists() -> None:
    """pinned_loops.py must exist as a module importable by bounce_daemons.sh and release_train.py.

    AC-6 control: the other unit_test_targets files pass unchanged — verified by
    running them in the step-1 gate.  This test pins the new module's existence
    so the control is not vacuous.
    """
    pinned_loops_path = _WATCHDOG_SCRIPTS / "pinned_loops.py"
    assert pinned_loops_path.exists(), f"pinned_loops.py must exist at {pinned_loops_path}"
    code = pinned_loops_path.read_text(encoding="utf-8")
    assert "release_dir_of" in code, "pinned_loops.py must export release_dir_of"
    assert "unpinned" in code, "pinned_loops.py must export unpinned"