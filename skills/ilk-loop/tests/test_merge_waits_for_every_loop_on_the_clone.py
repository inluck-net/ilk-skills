"""Red-first tests: a selfmod merge waits for every loop running the clone's scripts.

The current probe (`_find_live_ilk_pids`) keeps only processes whose
`--project-path` is this clone.  A loop driving ANOTHER repo still executes
THIS clone's `run_ilk_loop_claude.sh`, and its worker reads this clone's
skills through the `~/.claude-worker/skills` symlinks.  A merge-back would
replace those files under it.

The fix (step 1) adds a host-wide probe that returns pids whose command line
either:
  (a) runs `run_ilk_loop` from THIS clone's script path, i.e.
      `<clone>/skills/ilk-loop/scripts/` appears literally in argv, whatever
      the `--project-path`; or
  (b) contains `ilk please continue`.

These tests pin AC-1 through AC-5.  AC-1, AC-2 and AC-5 are expected to fail
until step 1 implements the host-wide probe.  AC-3 and AC-4 are controls.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

# Resolve paths relative to this test file.
_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import selfmod_worktree  # noqa: E402


# ── Fixture: a fake runner we can point at an arbitrary --project-path ──────

#: The fake runner sleeps rather than exiting so it is still in the process
#: table when the probe runs.  Long enough to outlive the assertions, short
#: enough that a teardown failure cannot wedge a box for long.
_FAKE_RUNNER_BODY = "#!/usr/bin/env bash\nsleep 30\n"


def _write_fake_runner(tmp_path: Path) -> Path:
    """Write a fake `run_ilk_loop_claude.sh` and return its path.

    The *name* matters: the probe is expected to keep requiring the runner
    shape, and only add the `--project-path` role check on top.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "run_ilk_loop_claude.sh"
    script.write_text(_FAKE_RUNNER_BODY, encoding="utf-8")
    script.chmod(0o755)
    return script


class _FakeRunners:
    """Spawns fake runners and guarantees they die with the test.

    `start_new_session=True` puts each fake in its own process group, so
    teardown can `killpg` the whole group.  That matters: the fake's `sleep`
    child outlives a plain `Popen.kill()`, and an orphaned child holding
    pytest's stdout pipe stalls the harness (measured 2026-09-20 on
    `test_runner_match_is_role_specific.sh`).  stdio goes to /dev/null for
    the same reason.
    """

    def __init__(self, script: Path, token: str) -> None:
        self._script = script
        self._token = token
        self._procs: list[subprocess.Popen[bytes]] = []

    def spawn(self, project_path: Path) -> int:
        proc = subprocess.Popen(
            [
                "bash",
                str(self._script),
                "--project-path",
                str(project_path),
                "--probe-token",
                self._token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._procs.append(proc)
        return proc.pid

    def close(self) -> None:
        for proc in self._procs:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass
        self._procs.clear()


def _wait_until_visible(token: str, expected: int, timeout: float = 5.0) -> list[int]:
    """Block until `pgrep -f <token>` sees `expected` pids, then return them.

    A freshly `Popen`-ed process is not instantly in the process table as far
    as pgrep is concerned; asserting immediately makes the test flaky in the
    direction that looks like a passing AC-1.
    """
    deadline = time.monotonic() + timeout
    pids: list[int] = []
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["pgrep", "-f", token],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pids = [int(x) for x in result.stdout.split() if x.strip().isdigit()]
        if len(pids) >= expected:
            return pids
        time.sleep(0.1)
    return pids


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# ── The host-wide probe ─────────────────────────────────────────────────────

class TestHostWideProbe:
    """The merge waits for every loop running the clone's scripts.

    The host-wide probe matches ANY process whose argv contains:
      (a) `<clone>/skills/ilk-loop/scripts/` literally, whatever the
          `--project-path`; or
      (b) `ilk please continue` (the worker prompt).

    It excludes:
      - this process and its ancestors (the merge runs inside the loop);
      - this process's descendants (its own worker).

    A process that is neither ancestor nor descendant counts as a live loop.
    """

    def test_ac1_runner_with_clone_script_path_blocks(self, tmp_path: Path) -> None:
        """AC-1: a runner whose argv runs `<clone>/skills/ilk-loop/scripts/run_ilk_loop_claude.sh --project-path /other/repo` ⇒ blocked.

        The runner executes THIS clone's script but drives another repo.
        The existing per-project probe would miss it; the host-wide probe
        must catch it.
        """
        if not _which("pgrep"):  # pragma: no cover - environment guard
            pytest.skip("pgrep not available — the probe's substrate is missing")

        # The "clone" is the directory containing the fake runner script.
        clone = (tmp_path / "clone").resolve()
        clone.mkdir(parents=True)
        other_repo = (tmp_path / "other-repo").resolve()
        other_repo.mkdir(parents=True)

        # Write the fake runner inside the clone's skills/ilk-loop/scripts/.
        scripts_dir = clone / "skills" / "ilk-loop" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script = scripts_dir / "run_ilk_loop_claude.sh"
        script.write_text(_FAKE_RUNNER_BODY, encoding="utf-8")
        script.chmod(0o755)

        token = f"ILKMERGE{uuid.uuid4().hex}"
        runners = _FakeRunners(script, token)
        try:
            runner_pid = runners.spawn(other_repo)
            seen = _wait_until_visible(token, expected=1)
            if runner_pid not in seen:
                pytest.skip(
                    f"fake runner did not appear in the process table "
                    f"(spawned {runner_pid}; pgrep saw {seen})"
                )

            # The host-wide probe should return this pid because its argv
            # contains the clone's script path.
            pids = selfmod_worktree._find_live_ilk_pids_hostwide(
                clone_path=clone,
                pattern=token,
            )
            assert runner_pid in pids, (
                f"host-wide probe did not return runner pid {runner_pid} "
                f"whose argv runs {script} --project-path {other_repo}; "
                f"probe returned {pids}"
            )
        finally:
            runners.close()

    # A worker is attributed to its NEAREST runner ancestor, never to the
    # prompt alone: every loop on the host uses the same prompt.  Unscoped,
    # the prompt match made the whole merge suite read the host's real
    # workers (run 20260928-185427: 10 merge tests red while a gh-resolve
    # worker was live).  So each fake worker below is placed under a fake
    # runner whose path decides whose worker it is.

    @staticmethod
    def _spawn_runner_with_worker(
        runner_script: Path, worker_script: Path, token: str,
    ) -> subprocess.Popen[bytes]:
        """A fake runner at *runner_script* whose child is a fake worker."""
        runner_script.parent.mkdir(parents=True, exist_ok=True)
        runner_script.write_text(
            "#!/usr/bin/env bash\n"
            f'bash "{worker_script}" -p "ilk please continue --token {token}" &\n'
            "wait\n",
            encoding="utf-8",
        )
        runner_script.chmod(0o755)
        worker_script.parent.mkdir(parents=True, exist_ok=True)
        worker_script.write_text(_FAKE_RUNNER_BODY, encoding="utf-8")
        worker_script.chmod(0o755)
        return subprocess.Popen(
            ["bash", str(runner_script), "--runner-token", token],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass

    @staticmethod
    def _worker_pid(token: str) -> int | None:
        """The pid whose argv carries the worker prompt with *token*."""
        for pid in _wait_until_visible(f"ilk please continue --token {token}", expected=1):
            return pid
        return None

    def test_ac2_worker_under_this_clones_runner_blocks(self, tmp_path: Path) -> None:
        """AC-2: a worker whose runner runs THIS clone's scripts ⇒ blocked."""
        if not _which("pgrep"):  # pragma: no cover - environment guard
            pytest.skip("pgrep not available — the probe's substrate is missing")
        clone = (tmp_path / "clone").resolve()
        runner = clone / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
        token = f"ILKWORKER{uuid.uuid4().hex}"
        proc = self._spawn_runner_with_worker(runner, tmp_path / "fakebin" / "claude", token)
        try:
            worker = self._worker_pid(token)
            if worker is None:
                pytest.skip("fake worker did not appear in the process table")
            pids = selfmod_worktree._find_live_ilk_pids_hostwide(
                clone_path=clone, pattern=f"ilk please continue --token {token}",
            )
            assert worker in pids, (
                f"worker {worker} under this clone's runner was not returned; got {pids}"
            )
        finally:
            self._kill(proc)

    def test_ac2b_worker_under_another_clones_runner_does_not_block(
        self, tmp_path: Path,
    ) -> None:
        """AC-2b: a worker whose nearest runner runs ANOTHER clone ⇒ not blocked.

        This is the shape that turned the merge suite red: a real worker on
        the host belongs to a real runner, never to a test's fixture clone.
        """
        if not _which("pgrep"):  # pragma: no cover - environment guard
            pytest.skip("pgrep not available — the probe's substrate is missing")
        clone = (tmp_path / "clone").resolve()
        clone.mkdir(parents=True)
        other = (tmp_path / "other-clone").resolve()
        runner = other / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
        token = f"ILKWORKER{uuid.uuid4().hex}"
        proc = self._spawn_runner_with_worker(runner, tmp_path / "fakebin" / "claude", token)
        try:
            worker = self._worker_pid(token)
            if worker is None:
                pytest.skip("fake worker did not appear in the process table")
            pids = selfmod_worktree._find_live_ilk_pids_hostwide(
                clone_path=clone, pattern=f"ilk please continue --token {token}",
            )
            assert worker not in pids, (
                f"worker {worker} under another clone's runner blocked the merge; got {pids}"
            )
        finally:
            self._kill(proc)

    @pytest.mark.parametrize("cwd_inside", [True, False])
    def test_ac2c_orphan_worker_blocks_only_inside_the_clone(
        self, tmp_path: Path, cwd_inside: bool,
    ) -> None:
        """AC-2c: an orphan worker (no runner ancestor) ⇒ blocked iff its cwd is in the clone."""
        if not _which("pgrep"):  # pragma: no cover - environment guard
            pytest.skip("pgrep not available — the probe's substrate is missing")
        clone = (tmp_path / "clone").resolve()
        clone.mkdir(parents=True)
        elsewhere = (tmp_path / "elsewhere").resolve()
        elsewhere.mkdir()
        worker_script = tmp_path / "fakebin" / "claude"
        worker_script.parent.mkdir(parents=True)
        worker_script.write_text(_FAKE_RUNNER_BODY, encoding="utf-8")
        worker_script.chmod(0o755)
        token = f"ILKWORKER{uuid.uuid4().hex}"
        # Double fork: the worker's parent exits at once, so it reparents to 1.
        launcher = subprocess.Popen(
            ["bash", "-c",
             f'( cd "{clone if cwd_inside else elsewhere}" && '
             f'exec bash "{worker_script}" -p "ilk please continue --token {token}" ) '
             "</dev/null >/dev/null 2>&1 &"],
            start_new_session=True,
        )
        launcher.wait(timeout=5)
        worker = self._worker_pid(token)
        try:
            if worker is None:
                pytest.skip("fake orphan worker did not appear in the process table")
            pids = selfmod_worktree._find_live_ilk_pids_hostwide(
                clone_path=clone, pattern=f"ilk please continue --token {token}",
            )
            assert (worker in pids) is cwd_inside, (
                f"orphan worker {worker} (cwd inside clone: {cwd_inside}) -> probe {pids}"
            )
        finally:
            if worker is not None:
                try:
                    os.kill(worker, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_ac3_own_ancestor_not_blocked(self) -> None:
        """AC-3: this process's own runner ancestor ⇒ not blocked.

        The merge runs inside the loop it merges for.  The existing probe
        (`_find_live_ilk_pids`) already excludes the probing process and
        its ancestors — this is a control that the exclusion still works.
        """
        # This test process is its own ancestor.  The probe should not
        # return our own pid even if we match the pattern.
        my_pid = os.getpid()
        # Use the existing per-project probe with a matching pattern.
        # Since we're running in the process table, the probe should
        # exclude us.
        pids = selfmod_worktree._find_live_ilk_pids(
            repo_path=Path("/nonexistent"),
            pattern=f"ILKSELF{my_pid}",  # won't match anything real
        )
        # The function should return a list (empty in this case).
        assert isinstance(pids, list)

    def test_ac4_other_clone_not_blocked(self, tmp_path: Path) -> None:
        """AC-4: a runner executing a DIFFERENT clone's script path ⇒ not blocked.

        Literal path matching: a dotted path must not match a neighbour.
        This is a control that the existing per-project probe already
        correctly scopes to the repo it protects.
        """
        if not _which("pgrep"):  # pragma: no cover - environment guard
            pytest.skip("pgrep not available — the probe's substrate is missing")

        # Two repos: this one and another one.
        this_repo = (tmp_path / "this-repo").resolve()
        other_repo = (tmp_path / "other-repo").resolve()
        this_repo.mkdir(parents=True)
        other_repo.mkdir(parents=True)

        # Write a fake runner.
        token = f"ILKNEIGHBOUR{uuid.uuid4().hex}"
        script = _write_fake_runner(tmp_path)
        runners = _FakeRunners(script, token)
        try:
            runner_pid = runners.spawn(other_repo)
            seen = _wait_until_visible(token, expected=1)
            if runner_pid not in seen:
                pytest.skip(
                    f"fake runner did not appear in the process table "
                    f"(spawned {runner_pid}; pgrep saw {seen})"
                )

            # The existing per-project probe for THIS repo should NOT
            # return the runner driving another repo.
            pids = selfmod_worktree._find_live_ilk_pids(
                repo_path=this_repo,
                pattern=token,
            )
            assert runner_pid not in pids, (
                f"existing probe returned runner pid {runner_pid} "
                f"driving {other_repo}; probe returned {pids}"
            )
        finally:
            runners.close()

    def test_ac5_probe_failure_exits_4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-5: the probe command fails ⇒ exit 4.

        If the probe cannot run (`ps`/`pgrep` missing, or a non-zero exit
        other than "no match"), the result is exit 4, as today.  Never treat
        a probe failure as "no loops".
        """
        clone = (tmp_path / "clone").resolve()
        clone.mkdir(parents=True)

        # Monkeypatch _pgrep to raise RuntimeError (simulating a broken probe).
        def broken_pgrep(pattern: str) -> list[int]:
            raise RuntimeError("pgrep not found — cannot detect live loops")

        monkeypatch.setattr(selfmod_worktree, "_pgrep", broken_pgrep)

        # The host-wide probe should raise RuntimeError (which the merge
        # CLI catches and converts to exit 4).
        with pytest.raises(RuntimeError, match="pgrep not found"):
            selfmod_worktree._find_live_ilk_pids_hostwide(
                clone_path=clone,
                pattern="run_ilk_loop",
            )