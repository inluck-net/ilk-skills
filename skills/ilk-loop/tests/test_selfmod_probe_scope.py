"""Red-first tests: the selfmod merge probe matches its repo, not the world.

`_find_live_ilk_pids` detects "live loops" with `pgrep -f run_ilk_loop` — a
bare substring over the whole process table.  Every runner of every project
carries `run_ilk_loop_claude.sh` in its argv, so the merge-back refused
whenever ANY loop anywhere ran.  Measured 2026-09-20: kira-cloudflare running
pv5-verify made every ilk-skills run end `selfmod_merge_failed`
(`MERGE BLOCKED: live loop(s) detected`).

The contract these tests pin, mirroring `_ilk_pid.sh:ilk_project_runners`
(187f8ff): a runner counts as live for THIS repo only when the repo path
appears **in its role as the `--project-path` value**, never as a bare
substring of the command line.

Both tests spawn a fake runner — a `run_ilk_loop_claude.sh` that sleeps —
and pin the probe pattern to a per-test token so the assertion is about the
two fakes and not about whatever else the host happens to be running.
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


@pytest.fixture
def two_repos_one_probe(tmp_path: Path):
    """Two repo roots, a fake runner for each, and the token that finds them.

    Yields `(this_repo, other_repo, this_pid, other_pid, token)`.
    """
    if not _which("pgrep"):  # pragma: no cover - environment guard
        pytest.skip("pgrep not available — the probe's substrate is missing")

    this_repo = (tmp_path / "this-repo").resolve()
    other_repo = (tmp_path / "other-repo").resolve()
    this_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)

    token = f"ILKPROBE{uuid.uuid4().hex}"
    runners = _FakeRunners(_write_fake_runner(tmp_path), token)
    try:
        this_pid = runners.spawn(this_repo)
        other_pid = runners.spawn(other_repo)
        seen = _wait_until_visible(token, expected=2)
        if this_pid not in seen or other_pid not in seen:
            pytest.skip(
                "fake runners did not appear in the process table "
                f"(spawned {this_pid}, {other_pid}; pgrep saw {seen})"
            )
        yield this_repo, other_repo, this_pid, other_pid, token
    finally:
        runners.close()


# ── AC-1 / AC-2: the probe is scoped to the repo it protects ────────────────

class TestProbeIsScopedToItsRepo:
    """`--project-path <root>` is the match, not `run_ilk_loop` anywhere."""

    def test_cross_repo_runner_does_not_block(self, two_repos_one_probe) -> None:
        """AC-1: another project's runner must not appear as live for us.

        This is the production failure of 2026-09-20 in miniature: the other
        runner is a real `run_ilk_loop_claude.sh` process, so the bare
        substring pattern matches it, and the merge for THIS repo is refused
        for work happening in a repo it shares nothing with.
        """
        this_repo, _other_repo, _this_pid, other_pid, token = two_repos_one_probe

        pids = selfmod_worktree._find_live_ilk_pids(
            repo_path=this_repo, pattern=token
        )

        assert other_pid not in pids, (
            f"a runner for another repo (pid {other_pid}) was reported live "
            f"for {this_repo}; probe returned {pids}"
        )

    def test_same_repo_runner_still_blocks(self, two_repos_one_probe) -> None:
        """AC-2: our own repo's other runner must still block the merge.

        The narrowing must not become a blanket exemption — a second loop on
        THIS repo is exactly the hazard the probe exists to catch.  The fake
        is a child of the test process, so it is neither the probing process
        nor one of its ancestors, and self-exclusion must not cover it.
        """
        this_repo, _other_repo, this_pid, _other_pid, token = two_repos_one_probe

        pids = selfmod_worktree._find_live_ilk_pids(
            repo_path=this_repo, pattern=token
        )

        assert this_pid in pids, (
            f"a runner for this repo (pid {this_pid}) was not reported live "
            f"for {this_repo}; probe returned {pids}"
        )
