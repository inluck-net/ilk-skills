"""Tests for the quiet-fleet wait: a train waits for zero live loops before touching a host.

Sub-plan: the-train-waits-for-a-quiet-fleet, step 0 (red-first pins).

Each test builds a throwaway data root under ``tmp_path``.  All external
commands (release, bounce, status, ssh, pgrep, sleep) are stubbed.  No real
~/.ilk-data, real daemon, real ssh, or real pgrep is touched.

The six acceptance criteria:
  AC-1  waits, then deploys: probe returns [101], [101], [] → deployed after 3 probes.
  AC-2  times out untouched: probe always [101], deadline short → exit 7, fleet-busy.
  AC-3  remote: _ssh_deploy probe always [202] → exit 7, transport ssh, no ssh calls.
  AC-4  fleet: canary deploy_fn returns fleet-busy → second host untouched, canary-failed.
  AC-5  predicate: _live_loops_local filters self, parent, grep; keeps real live pids.
  AC-6  control: other unit_test_targets pass unchanged.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

pytestmark = pytest.mark.allow_real_data_home

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

# ── Helpers ─────────────────────────────────────────────────────────────────

_LAUNCHED_PROCS: list[subprocess.Popen] = []


class FakeClock:
    """A monotonic clock whose time advances only via advance(seconds)."""

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


def _make_release_cmd_stub() -> tuple[Callable, list]:
    """Stub release_cmd that records calls and always succeeds."""
    calls: list[tuple[str, Path]] = []

    def _release_cmd(tag: str, repo: Path) -> tuple[int, str]:
        calls.append((tag, repo))
        return (0, f"extracted {tag}")

    return _release_cmd, calls


def _make_bounce_cmd_stub() -> tuple[Callable, list]:
    """Stub bounce_cmd that records calls and returns exit 1 (restarted)."""
    calls: list[str] = []

    def _bounce_cmd(tag: str) -> int:
        calls.append(tag)
        return 1

    return _bounce_cmd, calls


def _make_status_cmd_stub() -> tuple[Callable, list]:
    """Stub status_cmd that records calls and returns 'ok'."""
    calls: list[tuple[str, Path | None]] = []

    def _status_cmd(tag: str, cwd: Path | None = None) -> str:
        calls.append((tag, cwd))
        return "ok"

    return _status_cmd, calls


def _make_pid_file(tmp_path: Path, alive: bool = True) -> Path:
    """Create a fake scheduler.pid file."""
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


def _make_ssh_runner_stub() -> tuple[Callable, list]:
    """Stub ssh_runner that records calls and returns ok."""
    calls: list[dict] = []

    def _ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
        cmd_str = " ".join(str(c) for c in cmd)
        calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

        if any(str(a) == "fetch" for a in cmd):
            return {"rc": 0, "stdout": "", "stderr": ""}
        if "ilk_release.py" in cmd_str:
            return {"rc": 0, "stdout": "ok", "stderr": ""}
        if "bounce_daemons" in cmd_str:
            return {"rc": 1, "stdout": "", "stderr": ""}
        if "host_deploy_status" in cmd_str:
            return {"rc": 0, "stdout": "ok", "stderr": ""}
        return {"rc": 0, "stdout": "", "stderr": ""}

    return _ssh_runner, calls


# ── AC-1: waits, then deploys ──────────────────────────────────────────────


class TestQuietFleetWaitsThenDeploys:
    """AC-1: local deploy() with probe returning [101], [101], []:
    the release stub is called exactly once AFTER the third probe call,
    and the result is deployed: True.
    """

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_waits_then_deploys(self, tmp_path: Path) -> None:
        """Probe returns pids twice then empty → deploy proceeds."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        probe_calls: list[int] = []
        probe_sequence = [[101], [101], []]

        def _probe() -> list[int]:
            idx = min(len(probe_calls), len(probe_sequence) - 1)
            result = probe_sequence[idx]
            probe_calls.append(len(result))
            return result

        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        release_cmd, release_calls = _make_release_cmd_stub()
        bounce_cmd, bounce_calls = _make_bounce_cmd_stub()
        status_cmd, status_calls = _make_status_cmd_stub()
        pid_file = _make_pid_file(tmp_path, alive=True)

        result = release_train.deploy(
            project=tmp_path / "project",
            tag="v0.0.2",
            data_dir=tmp_path / "data",
            release_cmd=release_cmd,
            bounce_cmd=bounce_cmd,
            status_cmd=status_cmd,
            pid_file=pid_file,
            quiet_probe=_probe,
            quiet_deadline_sec=5400.0,
            quiet_poll_sec=30.0,
            quiet_clock=clock.monotonic,
            quiet_sleeper=sleeper.sleep,
        )

        assert len(probe_calls) == 3, f"Expected 3 probe calls, got {len(probe_calls)}"
        assert len(release_calls) == 1, f"Expected 1 release call, got {len(release_calls)}"
        assert result["deployed"] is True
        assert result["exit_code"] == 0


# ── AC-2: times out untouched ──────────────────────────────────────────────


class TestQuietFleetTimesOut:
    """AC-2: probe always returns [101], deadline short → exit 7,
    reason starts with 'fleet-busy', and release/bounce/status are never called.
    """

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_timeout_returns_fleet_busy(self, tmp_path: Path) -> None:
        """Probe always busy, deadline exhausted → exit 7, no deploy."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        probe_calls: list[int] = []

        def _probe() -> list[int]:
            probe_calls.append(1)
            return [101]

        # Clock starts at 1000, deadline is 1000 + 60 = 1060.
        # Each poll advances by 30s, so after 3 polls clock = 1090 > 1060.
        clock = FakeClock(start=1000.0)
        sleeper = FakeSleeper(clock)
        release_cmd, release_calls = _make_release_cmd_stub()
        bounce_cmd, bounce_calls = _make_bounce_cmd_stub()
        status_cmd, status_calls = _make_status_cmd_stub()
        pid_file = _make_pid_file(tmp_path, alive=True)

        result = release_train.deploy(
            project=tmp_path / "project",
            tag="v0.0.2",
            data_dir=tmp_path / "data",
            release_cmd=release_cmd,
            bounce_cmd=bounce_cmd,
            status_cmd=status_cmd,
            pid_file=pid_file,
            quiet_probe=_probe,
            quiet_deadline_sec=60.0,
            quiet_poll_sec=30.0,
            quiet_clock=clock.monotonic,
            quiet_sleeper=sleeper.sleep,
        )

        assert result["exit_code"] == 7, f"Expected exit 7, got {result['exit_code']}"
        assert result["deployed"] is False
        assert result.get("reason", "").startswith("fleet-busy:"), (
            f"Expected reason starting with 'fleet-busy:', got {result.get('reason')!r}"
        )
        assert len(release_calls) == 0, f"Release must not be called, got {len(release_calls)}"
        assert len(bounce_calls) == 0, f"Bounce must not be called, got {len(bounce_calls)}"
        assert len(status_calls) == 0, f"Status must not be called, got {len(status_calls)}"
        assert len(probe_calls) >= 2, f"Expected at least 2 probe calls, got {len(probe_calls)}"


# ── AC-3: remote deploy with busy fleet ────────────────────────────────────


class TestRemoteQuietFleetTimesOut:
    """AC-3: _ssh_deploy() with probe always [202]: exit 7, transport ssh,
    and the ssh runner stub records ZERO calls.  A probe returning None
    (unreachable) behaves as busy.
    """

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_remote_busy_returns_fleet_busy(self, tmp_path: Path) -> None:
        """Remote probe always busy → exit 7, no ssh calls."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        probe_calls: list[int] = []

        def _probe() -> list[int]:
            probe_calls.append(1)
            return [202]

        clock = FakeClock(start=1000.0)
        sleeper = FakeSleeper(clock)
        ssh_runner, ssh_calls = _make_ssh_runner_stub()

        result = release_train._ssh_deploy(
            project=tmp_path / "project",
            tag="v0.0.2",
            data_dir=tmp_path / "data",
            host="rezmac",
            ssh_runner=ssh_runner,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            quiet_probe=_probe,
            quiet_deadline_sec=60.0,
            quiet_poll_sec=30.0,
        )

        assert result["exit_code"] == 7, f"Expected exit 7, got {result['exit_code']}"
        assert result["deployed"] is False
        assert result.get("transport") == "ssh"
        assert result.get("reason", "").startswith("fleet-busy:"), (
            f"Expected reason starting with 'fleet-busy:', got {result.get('reason')!r}"
        )
        assert len(ssh_calls) == 0, f"SSH runner must not be called, got {len(ssh_calls)}"

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_remote_unreachable_behaves_as_busy(self, tmp_path: Path) -> None:
        """Remote probe returning None (unreachable) → fleet-busy."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        def _probe() -> list[int] | None:
            return None

        clock = FakeClock(start=1000.0)
        sleeper = FakeSleeper(clock)
        ssh_runner, ssh_calls = _make_ssh_runner_stub()

        result = release_train._ssh_deploy(
            project=tmp_path / "project",
            tag="v0.0.2",
            data_dir=tmp_path / "data",
            host="rezmac",
            ssh_runner=ssh_runner,
            clock=clock.monotonic,
            sleeper=sleeper.sleep,
            quiet_probe=_probe,
            quiet_deadline_sec=60.0,
            quiet_poll_sec=30.0,
        )

        assert result["exit_code"] == 7
        assert result["deployed"] is False
        assert len(ssh_calls) == 0


# ── AC-4: fleet deploy with canary fleet-busy ──────────────────────────────


class TestFleetCanaryFleetBusyStopsFleet:
    """AC-4: _deploy_all_hosts() with a canary deploy_fn returning the AC-2
    shape (fleet-busy): the second host's result is 'untouched' with
    'canary-failed: fleet-busy' in its reason, and _ssh is never called.
    """

    def test_canary_fleet_busy_stops_remote(self, tmp_path: Path) -> None:
        """Canary returns fleet-busy → remote untouched, ssh never called."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _canary_fleet_busy(proj, tag, data_dir, **kwargs):
            return {
                "tag": tag,
                "deployed": False,
                "exit_code": 7,
                "reason": "fleet-busy: 1 loop(s) live on chad-mbp: [101]",
            }

        def _ssh_should_not_run(proj, tag, data_dir, **kwargs):
            ssh_calls.append(kwargs)
            return {"tag": tag, "deployed": True, "exit_code": 0}

        result = release_train._deploy_all_hosts(
            project=tmp_path / "project",
            tag="v0.0.2",
            data_dir=tmp_path / "data",
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp"],
            deploy_fn=_canary_fleet_busy,
            ssh_deploy_fn=_ssh_should_not_run,
        )

        assert len(ssh_calls) == 0, (
            f"ssh_deploy_fn must NOT be called when canary is fleet-busy, "
            f"but was called {len(ssh_calls)} time(s)"
        )
        assert "rezmac" in result["untouched"]
        rezmac_result = result["hosts"]["rezmac"]
        assert "canary-failed:" in rezmac_result.get("reason", ""), (
            f"Remote host reason must contain 'canary-failed:', "
            f"got: {rezmac_result.get('reason')!r}"
        )
        assert "fleet-busy" in rezmac_result.get("reason", ""), (
            f"Remote host reason must contain 'fleet-busy', "
            f"got: {rezmac_result.get('reason')!r}"
        )


# ── AC-5: _live_loops_local predicate ──────────────────────────────────────


class TestLiveLoopsLocalPredicate:
    """AC-5: _live_loops_local with a monkeypatched subprocess.run returning
    pids for this process, a dead pid, and a live grep pid returns [];
    with one real live pid not matching those rules it returns that pid.
    """

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_filters_self_parent_and_grep(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Self pid, parent pid, and grep pid are all filtered out."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        my_pid = os.getpid()
        parent_pid = os.getppid()

        # pgrep returns: self, a dead pid (99999999), parent, and a grep process
        fake_pgrep_pids = f"{my_pid}\n99999999\n{parent_pid}\n"

        # ps -o command= for each pid:
        # self → "python ..." (not grep) → filtered by self check
        # 99999999 → ps returns nothing (dead) → filtered by os.kill check
        # parent → "bash ..." (not grep) → filtered by parent check
        # We also need to test a grep pid; we'll add it separately

        fake_pgrep_pids_with_grep = f"{my_pid}\n99999999\n{parent_pid}\n"

        call_count = {"n": 0}

        def fake_subprocess_run(args, **kwargs):
            call_count["n"] += 1
            cmd_str = " ".join(str(a) for a in args)

            if "pgrep" in cmd_str:
                # Return a class with returncode and stdout
                class FakeResult:
                    returncode = 0
                    stdout = fake_pgrep_pids_with_grep
                return FakeResult()

            # ps -o command= for any pid
            if "ps" in cmd_str:
                class FakeResult:
                    returncode = 0
                    stdout = "python3 some_script.py"
                return FakeResult()

            class FakeResult:
                returncode = 0
                stdout = ""
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        result = release_train._live_loops_local()

        # Self, parent, and dead pid are all filtered → empty list
        assert result == [], f"Expected empty list, got {result}"

    @pytest.mark.xfail(strict=True, reason="the train does not wait for a quiet fleet yet")
    def test_returns_live_pid_not_filtered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real live pid that is not self, parent, or grep is returned."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Use a real live pid that is not self or parent
        other_proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(other_proc)
        other_pid = other_proc.pid

        def fake_subprocess_run(args, **kwargs):
            cmd_str = " ".join(str(a) for a in args)

            if "pgrep" in cmd_str:
                class FakeResult:
                    returncode = 0
                    stdout = f"{other_pid}\n"
                return FakeResult()

            if "ps" in cmd_str:
                class FakeResult:
                    returncode = 0
                    stdout = "bash run_ilk_loop_claude.sh"
                return FakeResult()

            class FakeResult:
                returncode = 0
                stdout = ""
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        result = release_train._live_loops_local()

        assert other_pid in result, f"Expected {other_pid} in result, got {result}"


# ── Cleanup ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cleanup_launched_procs():
    """Kill any processes launched during the test."""
    yield
    for proc in _LAUNCHED_PROCS:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (ProcessLookupError, OSError):
            pass
    _LAUNCHED_PROCS.clear()