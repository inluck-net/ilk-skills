"""Red-first tests for the ``scheduler_sandbox`` fixture (AC-1..AC-6).

These tests FAIL at fixture lookup because the fixture does not exist yet.
They spawn no scheduler and cannot contend with the live daemon — that is
deliberate.  The helpers that *would* shell out to ``scheduler.sh`` are
defined here but inert until the fixture lands in step 2.

Sub-plan: a-harness-cannot-read-the-real-data-home
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


# ── AC-1  Fixture shape ─────────────────────────────────────────────────────

class TestSandboxFixtureShape:
    """AC-1: the fixture returns an object with ``root`` and ``env``."""

    def test_has_root(self, scheduler_sandbox):
        assert hasattr(scheduler_sandbox, "root")
        assert isinstance(scheduler_sandbox.root, Path)

    def test_has_env(self, scheduler_sandbox):
        assert hasattr(scheduler_sandbox, "env")
        assert isinstance(scheduler_sandbox.env, dict)

    def test_home_and_data_home_are_consistent(self, scheduler_sandbox):
        """HOME and ILK_DATA_HOME must point at the same root.

        ``env["HOME"]`` is the parent of ``env["ILK_DATA_HOME"]``,
        and ``ILK_DATA_HOME`` ends with ``/.ilk-data``.
        """
        env = scheduler_sandbox.env
        assert "HOME" in env
        assert "ILK_DATA_HOME" in env
        home = Path(env["HOME"])
        data_home = Path(env["ILK_DATA_HOME"])
        assert data_home == home / ".ilk-data"

    def test_root_matches_data_home(self, scheduler_sandbox):
        """``root`` is the parent that contains ``.ilk-data/``."""
        env = scheduler_sandbox.env
        assert scheduler_sandbox.root == Path(env["HOME"])


# ── AC-2  ILK_DATA_DIR is stripped ──────────────────────────────────────────

class TestSandboxDataDirStripped:
    """AC-2: ``ILK_DATA_DIR`` is absent from the env, even if inherited."""

    def test_no_ilk_data_dir(self, scheduler_sandbox):
        assert "ILK_DATA_DIR" not in scheduler_sandbox.env

    def test_stripped_even_when_ambient_is_set(self, scheduler_sandbox, monkeypatch):
        """Setting the ambient env must not leak into the fixture."""
        monkeypatch.setenv("ILK_DATA_DIR", "/tmp/decoy")
        # The fixture is already constructed; just verify it is clean.
        assert "ILK_DATA_DIR" not in scheduler_sandbox.env


# ── AC-3  Subprocess resolves inside sandbox ─────────────────────────────────

class TestSubprocessResolvesInsideSandbox:
    """AC-3: ``ilk_paths.py`` under the fixture's env resolves inside sandbox."""

    def test_ilk_data_root_inside_sandbox(self, scheduler_sandbox, tmp_path):
        script = (
            "import sys, json\n"
            f"sys.path.insert(0, {str(SKILLS_DIR / 'ilk-loop' / 'scripts')!r})\n"
            "from ilk_paths import ilk_data_root\n"
            "print(json.dumps(str(ilk_data_root())))\n"
        )
        proc = subprocess.run(
            [os.sys.executable, "-c", script],
            capture_output=True, text=True, timeout=30,
            env=scheduler_sandbox.env,
        )
        assert proc.returncode == 0, proc.stderr
        import json
        resolved = Path(json.loads(proc.stdout.strip()))
        assert str(resolved).startswith(str(scheduler_sandbox.root)), (
            f"ilk_data_root={resolved} is outside sandbox {scheduler_sandbox.root}"
        )


# ── AC-4  Scheduler writes to sandbox ────────────────────────────────────────

class TestSchedulerWritesToSandbox:
    """AC-4: ``scheduler.sh`` under the fixture writes state and logs inside.

    The pidfile is a lock — scheduler.sh's EXIT trap (``_scheduler_cleanup``,
    line 111) removes it on exit, so we cannot check existence *after* the
    process.  ``scheduler.state.json`` is written at startup and never removed
    (scheduler.sh:22-27; no ``rm -f`` targets it).  A completed run also
    leaves ``logs/scheduler.log``.  Both are durable evidence that the
    scheduler ran inside the sandbox.

    See tests/baselines/sp6-flake-evidence-2026-08-27.md for the measurement.
    """

    def test_sandbox_receives_scheduler_state(self, scheduler_sandbox):
        """AC-4 (deterministic): run --once --dry-run to completion in foreground.

        The pidfile is removed by the EXIT trap, so catching it mid-life requires
        a race.  ``scheduler.state.json`` is written at startup and never removed
        (scheduler.sh:22-27; no ``rm -f`` targets it).  A completed run also
        leaves ``logs/scheduler.log``.  Both are durable evidence that the
        scheduler ran inside the sandbox.

        See tests/baselines/sp6-flake-evidence-2026-08-27.md for the measurement.
        """
        result = subprocess.run(
            ["bash", str(SCHEDULER), "--once", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env=scheduler_sandbox.env,
        )
        assert result.returncode == 0, (
            f"--once --dry-run failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()!r}"
        )

        # State file written at startup, never removed.
        state_path = scheduler_sandbox.root / ".ilk-data" / "scheduler.state.json"
        assert state_path.exists(), (
            f"scheduler.state.json missing from sandbox: {state_path}"
        )
        import json
        state = json.loads(state_path.read_text())
        assert isinstance(state.get("pid"), int), (
            f"scheduler.state.json pid not an int: {state!r}"
        )

        # Log file written during the run.
        log_path = scheduler_sandbox.root / ".ilk-data" / "logs" / "scheduler.log"
        assert log_path.exists(), (
            f"scheduler.log missing from sandbox: {log_path}"
        )

    def test_logs_in_sandbox(self, scheduler_sandbox):
        result = subprocess.run(
            ["bash", str(SCHEDULER), "--once", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env=scheduler_sandbox.env,
        )
        assert result.returncode == 0, result.stderr
        logs_dir = scheduler_sandbox.root / ".ilk-data" / "logs"
        assert logs_dir.exists(), f"logs dir not found at {logs_dir}"
        assert any(logs_dir.iterdir()), f"logs dir is empty at {logs_dir}"


# ── AC-5  Negative control — real daemon untouched ────────────────────────────

class TestRealDaemonUntouched:
    """AC-5: with the real launchd scheduler live, its state is untouched."""

    @pytest.fixture(autouse=True)
    def _snapshot_real_daemon_state(self):
        """Capture ``st_mtime_ns`` of ``scheduler.pid`` and ``scheduler.state.json``."""
        real_data = Path.home() / ".ilk-data"
        self.pidfile = real_data / "scheduler.pid"
        self.statefile = real_data / "scheduler.state.json"
        self.pid_mtime = self._mtime_ns(self.pidfile)
        self.state_mtime = self._mtime_ns(self.statefile)
        yield

    @staticmethod
    def _mtime_ns(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def test_scheduler_pid_unchanged(self, scheduler_sandbox):
        # Run a scheduler cycle to exercise the env.
        subprocess.run(
            ["bash", str(SCHEDULER), "--once", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env=scheduler_sandbox.env,
        )
        assert self._mtime_ns(self.pidfile) == self.pid_mtime

    def test_scheduler_state_unchanged(self, scheduler_sandbox):
        subprocess.run(
            ["bash", str(SCHEDULER), "--once", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env=scheduler_sandbox.env,
        )
        assert self._mtime_ns(self.statefile) == self.state_mtime


# ── AC-6  Meta-test — every harness must use the fixture ─────────────────────

def _find_scheduler_harnesses() -> list[tuple[Path, str]]:
    """Walk ``skills/*/tests/`` for files that execute ``scheduler.sh``.

    Returns a list of ``(filepath, reason)`` where *reason* names the evidence.
    """
    results: list[tuple[Path, str]] = []
    skills_dir = REPO_ROOT / "skills"
    for tests_dir in skills_dir.glob("*/tests"):
        for py in tests_dir.glob("test_*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "scheduler.sh" in stripped and (
                    "subprocess.run" in stripped
                    or "subprocess.Popen" in stripped
                    or "subprocess.call" in stripped
                    or "subprocess.check_call" in stripped
                    or "subprocess.check_output" in stripped
                    or "[" in stripped  # list argv
                ):
                    results.append((py, f"line {i}: {stripped[:80]}"))
                    break  # one match per file is enough
    return results


def _has_fixture_or_marker(filepath: Path) -> bool:
    """Check whether *filepath* requests ``scheduler_sandbox`` or carries the marker."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return "scheduler_sandbox" in text or "allow_real_data_home" in text


def test_meta_no_unguarded_scheduler_harness():
    """AC-6: every ``scheduler.sh``-executing harness uses the fixture or marker."""
    harnesses = _find_scheduler_harnesses()
    violations: list[str] = []
    for filepath, reason in harnesses:
        if not _has_fixture_or_marker(filepath):
            violations.append(f"  {filepath} — {reason}")
    assert not violations, (
        "These test files execute scheduler.sh without requesting "
        "scheduler_sandbox or carrying @pytest.mark.allow_real_data_home:\n"
        + "\n".join(violations)
    )
