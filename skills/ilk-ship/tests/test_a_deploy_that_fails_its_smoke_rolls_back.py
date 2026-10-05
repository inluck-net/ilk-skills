"""Tests for release_train deploy — flip, bounce, smoke, and automatic rollback.

Sub-plan: a-deploy-that-fails-its-smoke-rolls-back, step 0 (pins) + step 1 (impl).

Each test builds a throwaway releases root under ``tmp_path`` with v0.0.1
as current and a v0.0.2 release dir already extracted.  All external
commands (ilk_release, bounce, host_deploy_status, pid file) are injected
as stubs so no real launchctl, no real scheduler, and no real releases
root are touched.

The five acceptance criteria:
  AC-1  deploy v0.0.2 with stub status ok + stub pid naming releases/v0.0.2/:
        exit 0, current points to v0.0.2.
  AC-2  stub status prints tag-mismatch for v0.0.2 and ok for v0.0.1:
        exit 5, current points back to v0.0.1, rolled_back_to: v0.0.1.
  AC-3  both smokes fail: exit 6, current back to v0.0.1.
  AC-4  extraction fails (no such tag): exit 4, current unchanged, no bounce.
  AC-5  the fake launchctl never ran outside the stub bounce (host guard).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train import deploy  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_releases_root(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tmp releases root with v0.0.1 as current.

    Returns (releases_root, parent) where parent holds current/previous symlinks.
    The env var ILK_RELEASES_ROOT is set to releases_root for the test.
    """
    releases_root = tmp_path / "releases"
    releases_root.mkdir()
    parent = releases_root.parent

    # Create v0.0.1 release dir with a manifest
    v001_dir = releases_root / "v0.0.1"
    v001_dir.mkdir()
    manifest = {
        "tag": "v0.0.1",
        "sha": "a" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T00:00:00+00:00",
    }
    (v001_dir / ".ilk-release.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Create v0.0.2 release dir with a manifest
    v002_dir = releases_root / "v0.0.2"
    v002_dir.mkdir()
    manifest2 = {
        "tag": "v0.0.2",
        "sha": "b" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T01:00:00+00:00",
    }
    (v002_dir / ".ilk-release.json").write_text(json.dumps(manifest2, indent=2) + "\n")

    # Symlink current → v0.0.1
    current = parent / "current"
    os.symlink(str(v001_dir), str(current))

    # Set env so ilk_release uses our tmp root
    os.environ["ILK_RELEASES_ROOT"] = str(releases_root)

    return releases_root, parent


def _read_current_target(parent: Path) -> str | None:
    """Read the target of the current symlink."""
    current = parent / "current"
    if current.is_symlink():
        return os.readlink(current)
    return None


def _make_fake_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with two tags for the deploy tests."""
    project = tmp_path / "project"
    project.mkdir()

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, check=True, capture_output=True,
    )

    # Initial commit + v0.0.1 tag
    (project / "README.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
        cwd=project, check=True, capture_output=True,
    )

    # Second commit + v0.0.2 tag
    (project / "README.md").write_text("v0.0.2 content")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "v0.0.2"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.2", "-m", "v0.0.2"],
        cwd=project, check=True, capture_output=True,
    )

    return project


def _make_release_cmd(
    allow_tags: set[str] | None = None,
    releases_root: Path | None = None,
):
    """Create a stub release_cmd callable.

    *allow_tags* is the set of tags that extraction succeeds for.
    None means all tags succeed.  Tags not in the set raise SystemExit(4).

    When *releases_root* is set, ``--status`` returns the real status JSON
    and ``--rollback`` swaps current/previous (matching ilk_release.py's
    CLI contract so the deploy function can discover the previous tag).
    """
    def _release_cmd(arg: str, repo: Path) -> tuple[int, str]:
        # --status: return release status JSON
        if arg == "--status" and releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            previous = parent / "previous"
            current_val = os.readlink(current) if current.is_symlink() else None
            previous_val = os.readlink(previous) if previous.is_symlink() else None
            return (0, json.dumps({
                "current": current_val,
                "previous": previous_val,
            }))

        # --rollback: swap current and previous
        if arg == "--rollback" and releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            previous = parent / "previous"
            if current.is_symlink() and previous.is_symlink():
                curr_target = os.readlink(current)
                prev_target = os.readlink(previous)
                os.remove(current)
                os.symlink(prev_target, str(current))
                os.remove(previous)
                os.symlink(curr_target, str(previous))
            return (0, "rolled back")

        # Extraction: check allow_tags and flip current
        if allow_tags is not None and arg not in allow_tags:
            print(f"refused: no such tag: {arg}", file=sys.stderr)
            raise SystemExit(4)
        # Simulate ilk_release's flip: current → tag dir
        if releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            tag_dir = releases_root / arg
            if tag_dir.is_dir():
                old_target = os.readlink(current) if current.is_symlink() else None
                if old_target:
                    previous = parent / "previous"
                    os.symlink(old_target, str(previous))
                os.remove(current)
                os.symlink(str(tag_dir), str(current))
        return (0, f"extracted {arg}")
    return _release_cmd


def _make_bounce_cmd(bouncer_path: Path) -> callable:
    """Create a stub bounce_cmd that runs the fake bouncer script."""
    def _bounce_cmd(tag: str) -> int:
        r = subprocess.run(
            [str(bouncer_path)],
            capture_output=True, text=True,
            timeout=10,
        )
        return r.returncode
    return _bounce_cmd


def _make_status_cmd(mapping: dict[str, str]) -> callable:
    """Create a stub status_cmd that returns values from a tag→status mapping."""
    def _status_cmd(tag: str, cwd: Path | None = None) -> str:
        return mapping.get(tag, "unreachable")
    return _status_cmd


def _write_stub_bouncer(tmp_path: Path) -> Path:
    """Write a fake bounce_daemons.sh that records its argv and prints fresh output."""
    bouncer = tmp_path / "bounce_daemons.sh"
    bouncer.write_text("#!/bin/bash\necho \"$@\" >> \"$(dirname \"$0\")/bouncer_argv\"\n")
    bouncer.chmod(0o755)
    return bouncer


def _make_pid_file(tmp_path: Path, alive: bool = True) -> Path:
    """Create a fake scheduler.pid file.  Returns pid_file."""
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    if alive:
        proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(proc)
        pid_file.write_text(str(proc.pid))
    else:
        pid_file.write_text("99999999")

    return pid_file


def _make_pid_file_for_tag(tmp_path: Path, tag: str) -> Path:
    """Create a fake scheduler.pid whose process command line mentions releases/<tag>/.

    The script is placed in a directory named ``releases/<tag>/`` so that
    ``ps -o command=`` shows the path pattern the deploy smoke checks for.
    """
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    # Create the script under releases/<tag>/ so ps shows the path
    release_dir = tmp_path / "releases" / tag
    release_dir.mkdir(parents=True, exist_ok=True)
    script = release_dir / "scheduler.sh"
    script.write_text("#!/bin/bash\nsleep 60\n")
    script.chmod(0o755)

    proc = subprocess.Popen(["bash", str(script)])
    _LAUNCHED_PROCS.append(proc)
    pid_file.write_text(str(proc.pid))

    return pid_file


# ── AC-1: deploy succeeds when smoke passes ─────────────────────────────────

class TestDeploySucceedsWhenSmokePasses:
    """AC-1: deploy v0.0.2 with stub status ok + stub pid alive → exit 0, current → v0.0.2."""

    def test_deploy_exits_zero_and_flips_current(self, tmp_path: Path) -> None:
        """Smoke passes: exit 0, current points to v0.0.2."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        # Start a new process from releases/v0.0.2/ to simulate a restart
        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        def _bounce_with_restart(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            return 1

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_bounce_with_restart,
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is True
        assert result["tag"] == "v0.0.2"

        # current should now point to v0.0.2
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.2" in current_target


# ── AC-2: deploy rolls back when smoke fails ────────────────────────────────

class TestDeployRollsBackOnSmokeFailure:
    """AC-2: smoke fails for v0.0.2, ok for v0.0.1 → rolled_back_to: v0.0.1."""

    def test_deploy_rolls_back_on_smoke_failure(self, tmp_path: Path) -> None:
        """Smoke fails for new tag, passes for previous → rollback."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # The pid file's process must mention releases/v0.0.1/ for the rollback smoke
        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.1")

        # Start a new process from releases/v0.0.1/ to simulate a restart
        release_dir = releases_root / "v0.0.1"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        call_count = {"n": 0}

        def _bounce_with_restart(tag: str) -> int:
            call_count["n"] += 1
            pid_file.write_text(str(new_proc.pid))
            return 1

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_bounce_with_restart,
            status_cmd=_make_status_cmd({"v0.0.2": "tag-mismatch", "v0.0.1": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert result["rolled_back_to"] == "v0.0.1"
        assert result["rollback_smoke"] == "ok"

        # current should point back to v0.0.1
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.1" in current_target


# ── AC-3: both smokes fail → exit 6 ─────────────────────────────────────────

class TestDeployBothSmokesFail:
    """AC-3: both smokes fail → current back to v0.0.1, rollback_smoke: failed."""

    def test_deploy_both_smokes_fail(self, tmp_path: Path) -> None:
        """Both smokes fail → current back to v0.0.1, rollback_smoke: failed."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file(tmp_path, alive=True)

        # Start a new process to simulate a restart
        new_proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(new_proc)

        def _bounce_with_restart(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            return 1

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_bounce_with_restart,
            status_cmd=_make_status_cmd({"v0.0.2": "unreachable", "v0.0.1": "unreachable"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert result["rollback_smoke"] == "failed"

        # current should point back to v0.0.1
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.1" in current_target


# ── AC-4: extraction fails → exit 4 ─────────────────────────────────────────

class TestDeployExtractionFails:
    """AC-4: extraction fails (no such tag) → exit 4, current unchanged, no bounce."""

    def test_deploy_exits_four_when_extraction_fails(self, tmp_path: Path) -> None:
        """No such tag in repo → exit 4, current unchanged, no bounce called."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        pid_file = _make_pid_file(tmp_path, alive=True)

        # Record the current target before deploy
        current_before = _read_current_target(parent)

        with pytest.raises(SystemExit) as exc_info:
            deploy(
                project=project,
                tag="v99.99.99",  # tag that doesn't exist
                data_dir=data_dir,
                release_cmd=_make_release_cmd(allow_tags=set(), releases_root=releases_root),  # nothing allowed
                bounce_cmd=_make_bounce_cmd(bouncer),
                status_cmd=_make_status_cmd({"v99.99.99": "ok"}),
                pid_file=pid_file,
            )
        assert exc_info.value.code == 4

        # current unchanged
        current_after = _read_current_target(parent)
        assert current_after == current_before

        # bouncer should not have been called
        bouncer_argv = tmp_path / "bouncer_argv"
        assert not bouncer_argv.exists(), "bouncer was called despite extraction failure"


# ── AC-5: no real launchctl calls ────────────────────────────────────────────

class TestNoRealLaunchctlCalls:
    """AC-5 (control): deploy uses only injected commands, no real launchctl."""

    def test_no_real_launchctl_in_test_env(self, tmp_path: Path) -> None:
        """The conftest host guard ensures no real launchctl is called.

        This test exists as a control: if launchctl ran, the guard would
        have already failed the test session.  The assertion is that the
        deploy verb uses only injected commands.
        """
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        # Start a new process from releases/v0.0.2/ to simulate a restart
        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        def _bounce_with_restart(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            return 1

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_bounce_with_restart,
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
            pid_file=pid_file,
        )

        # If we got here without the host guard failing, no real launchctl ran.
        assert result["deployed"] is True


# ── Cleanup ─────────────────────────────────────────────────────────────────

_LAUNCHED_PROCS: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _cleanup():
    """Clean up any sleep processes started by test helpers."""
    _LAUNCHED_PROCS.clear()
    yield
    for proc in _LAUNCHED_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass


# ── Step 0: xfail pins for bounded daemon settling ─────────────────────────
#
# These pins assert the contracts that step 1 (implement bounded settling)
# will make green.  Each pin uses a fake monotonic clock/sleeper so no
# wall-clock sleeps appear in the test suite.
#
# AC-1  Delayed PID creation: settle helper waits until PID appears.
# AC-2  Delayed status ok: settle helper waits for status to transition
#       from non-ok to ok inside the bound.
# AC-3  Deadline expiry: persistent absence/mismatch at the deadline stays
#       red and enters rollback.
# AC-4  Persistent tag mismatch at deadline: settle helper returns
#       terminal reason naming the mismatch.
# AC-5  Rollback smoke uses the same bounded settling contract.


class FakeClock:
    """A monotonic clock whose time advances only via advance(seconds).

    Used to test time-dependent settling logic without wall-clock sleeps.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeSleeper:
    """Records each sleep(duration) call without actually sleeping."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


# ── AC-1: delayed PID creation — settle waits ─────────────────────────────


class TestDelayedPidCreationSettles:
    """AC-1: after bounce, the settle helper polls until PID appears.

    The PID file is initially absent; it is written after a few poll
    cycles.  The settle helper must find it within the deadline.
    """

    def test_settle_finds_late_pid(self, tmp_path: Path) -> None:
        """PID absent at bounce time but appears after 2 poll cycles."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Create release dir with a scheduler.sh so ps can find releases/<tag>/
        release_dir = tmp_path / "releases" / "v0.0.2"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"

        # PID is absent initially — settle must poll.
        # After the status cmd sees it, it writes the file.
        poll_count = {"n": 0}

        def _status_with_delayed_pid(tag: str, cwd: Path | None = None) -> str:
            poll_count["n"] += 1
            if poll_count["n"] >= 3:
                # Daemon started — write PID file now
                if not pid_file.exists():
                    proc = subprocess.Popen(["bash", str(script)])
                    _LAUNCHED_PROCS.append(proc)
                    pid_file.write_text(str(proc.pid))
                return "ok"
            return "unreachable"

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train._settle(
            tag="v0.0.2",
            status_cmd=_status_with_delayed_pid,
            pid_file=pid_file,
            deadline_sec=30.0,
            poll_interval_sec=1.0,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            cwd=tmp_path,
        )

        assert result["ok"] is True
        assert result["pid"] is not None
        assert result["attempts"] >= 3
        assert result["terminal_reason"] == "ok"


# ── AC-2: delayed status ok — settle waits for transition ─────────────────


class TestDelayedStatusOkSettles:
    """AC-2: status transitions from non-ok to ok inside the bound.

    The status cmd returns 'tag-mismatch' for the first few calls, then
    'ok'.  The settle helper must wait and succeed.
    """

    def test_settle_waits_for_status_transition(self, tmp_path: Path) -> None:
        """Status starts tag-mismatch, transitions to ok after 4 polls."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Create release dir with a scheduler.sh so ps can find releases/<tag>/
        release_dir = tmp_path / "releases" / "v0.0.2"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(proc)

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        pid_file.write_text(str(proc.pid))

        poll_count = {"n": 0}

        def _status_transitioning(tag: str, cwd: Path | None = None) -> str:
            poll_count["n"] += 1
            if poll_count["n"] >= 5:
                return "ok"
            return "tag-mismatch"

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train._settle(
            tag="v0.0.2",
            status_cmd=_status_transitioning,
            pid_file=pid_file,
            deadline_sec=30.0,
            poll_interval_sec=1.0,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            cwd=tmp_path,
        )

        assert result["ok"] is True
        assert result["attempts"] >= 5
        assert result["terminal_reason"] == "ok"


# ── AC-3: deadline expiry — persistent absence stays red ──────────────────


class TestDeadlineExpiryPersistentAbsence:
    """AC-3: persistent absence at the deadline stays red and enters rollback.

    The PID never appears and the status never returns ok.  The settle
    helper must hit the deadline and return a failure result.
    """

    def test_settle_deadline_expires_on_persistent_absence(
        self, tmp_path: Path,
    ) -> None:
        """PID absent and status unreachable for entire deadline."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        # PID file never created — persistent absence.

        def _status_always_unreachable(tag: str, cwd: Path | None = None) -> str:
            return "unreachable"

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train._settle(
            tag="v0.0.2",
            status_cmd=_status_always_unreachable,
            pid_file=pid_file,
            deadline_sec=10.0,
            poll_interval_sec=1.0,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            cwd=tmp_path,
        )

        assert result["ok"] is False
        assert result["elapsed"] >= 10.0
        assert result["terminal_reason"] != "ok"


# ── AC-4: persistent tag mismatch at deadline ─────────────────────────────


class TestPersistentTagMismatchAtDeadline:
    """AC-4: tag mismatch that never resolves stays red at deadline.

    The status cmd always returns 'tag-mismatch'.  The settle helper
    must hit the deadline and name the mismatch in its terminal reason.
    """

    def test_settle_names_persistent_mismatch(self, tmp_path: Path) -> None:
        """Status always tag-mismatch → settle fails naming mismatch."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Create release dir with a scheduler.sh so ps can find releases/<tag>/
        release_dir = tmp_path / "releases" / "v0.0.2"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(proc)

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        pid_file.write_text(str(proc.pid))

        def _status_always_mismatch(tag: str, cwd: Path | None = None) -> str:
            return "tag-mismatch"

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train._settle(
            tag="v0.0.2",
            status_cmd=_status_always_mismatch,
            pid_file=pid_file,
            deadline_sec=10.0,
            poll_interval_sec=1.0,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            cwd=tmp_path,
        )

        assert result["ok"] is False
        assert result["terminal_reason"] == "tag-mismatch"
        assert result["attempts"] >= 10


# ── AC-5: rollback smoke uses the same bounded settling contract ───────────


class TestRollbackSmokeUsesSameContract:
    """AC-5: rollback smoke uses the same _settle helper as forward smoke.

    After a forward smoke failure triggers rollback, the rollback smoke
    must also use the bounded settling contract (poll + deadline).
    """

    def test_rollback_smoke_uses_settle(self, tmp_path: Path) -> None:
        """Forward smoke fails, rollback smoke settles within bound."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Create release dir with a scheduler.sh for ps check
        release_dir = tmp_path / "releases" / "v0.0.1"
        release_dir.mkdir(parents=True, exist_ok=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(proc)

        pid_dir = data_dir / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        pid_file.write_text(str(proc.pid))

        call_count = {"n": 0}

        def _status_rollback_settles(tag: str, cwd: Path | None = None) -> str:
            call_count["n"] += 1
            if tag == "v0.0.2":
                return "tag-mismatch"  # forward always fails
            # v0.0.1: first call fails, then settles
            if call_count["n"] <= 2:
                return "tag-mismatch"
            return "ok"

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=lambda tag: 1,
            status_cmd=_status_rollback_settles,
            pid_file=pid_file,
            settle_deadline_sec=30.0,
            settle_poll_interval_sec=1.0,
        )

        assert result["deployed"] is False
        assert result["rolled_back_to"] == "v0.0.1"
        assert result["rollback_smoke"] == "ok"
        # Rollback should have polled more than once (settling)
        assert call_count["n"] > 2


# ── Step 0: xfail pins for the production default-value bypass ─────────────
#
# These tests reproduce the v0.9.150 defect: when deploy() is called with
# default settle parameters (the production path), _smoke falls through to
# single-shot because its dispatch condition checks
#   `clock is not None or sleeper is not None or deadline_sec != 30.0 or poll_interval_sec != 2.0`
# and all four are false for the default call.  A delayed daemon startup
# that _settle would handle correctly instead fails immediately via the
# single-shot path.
#
# These pins are xfail(strict=True) — they must FAIL on the current code
# and PASS once step 1 removes the default-value bypass.


@pytest.mark.xfail(
    strict=True,
    reason="production default settle args select single-shot instead of bounded settling",
)
class TestDeployWithDefaultSettleArgsSettles:
    """Production deploy with default settle args must use bounded settling.

    When deploy() is called with its documented defaults (settle_deadline_sec=30.0,
    settle_poll_interval_sec=2.0) and no explicit clock/sleeper, a delayed daemon
    startup must succeed via polling — not fail via the single-shot check.
    """

    def test_forward_deploy_settles_with_default_args(self, tmp_path: Path) -> None:
        """Daemon starts late → deploy must poll and succeed with default settle args."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        release_dir = tmp_path / "releases" / "v0.0.2"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        pid_dir = data_dir / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"

        # Daemon starts after 3 poll cycles
        poll_count = {"n": 0}
        proc_holder: list[subprocess.Popen] = []

        def _status_delayed(tag: str, cwd: Path | None = None) -> str:
            poll_count["n"] += 1
            if poll_count["n"] >= 3:
                if not proc_holder:
                    proc = subprocess.Popen(["bash", str(script)])
                    _LAUNCHED_PROCS.append(proc)
                    proc_holder.append(proc)
                    pid_file.write_text(str(proc.pid))
                return "ok"
            return "unreachable"

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2"}, releases_root=releases_root,
            ),
            bounce_cmd=lambda tag: 1,
            status_cmd=_status_delayed,
            pid_file=pid_file,
            # All settle args at their documented defaults — the production path
        )

        assert result["deployed"] is True
        assert result["exit_code"] == 0
        # Must have polled more than once (bounded settling, not single-shot)
        assert poll_count["n"] > 1


@pytest.mark.xfail(
    strict=True,
    reason="production default settle args select single-shot instead of bounded settling",
)
class TestRollbackWithDefaultSettleArgsSettles:
    """Rollback smoke with default settle args must use bounded settling.

    After forward smoke fails, rollback must poll with the same bounded
    contract — not fall through to a single-shot check.
    """

    def test_rollback_settles_with_default_args(self, tmp_path: Path) -> None:
        """Rollback daemon starts late → rollback must poll and succeed."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        release_dir = tmp_path / "releases" / "v0.0.1"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        pid_dir = data_dir / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"

        # Forward always fails; rollback succeeds after a delay
        call_count = {"n": 0}
        proc_holder: list[subprocess.Popen] = []

        def _status_rollback_delayed(tag: str, cwd: Path | None = None) -> str:
            call_count["n"] += 1
            if tag == "v0.0.2":
                return "tag-mismatch"
            # v0.0.1 (rollback target): delayed start
            if call_count["n"] <= 3:
                return "unreachable"
            if not proc_holder:
                proc = subprocess.Popen(["bash", str(script)])
                _LAUNCHED_PROCS.append(proc)
                proc_holder.append(proc)
                pid_file.write_text(str(proc.pid))
            return "ok"

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=lambda tag: 1,
            status_cmd=_status_rollback_delayed,
            pid_file=pid_file,
            # All settle args at their documented defaults — the production path
        )

        assert result["deployed"] is False
        assert result["rolled_back_to"] == "v0.0.1"
        assert result["rollback_smoke"] == "ok"
        # Rollback must have polled more than once (bounded settling, not single-shot)
        assert call_count["n"] > 2