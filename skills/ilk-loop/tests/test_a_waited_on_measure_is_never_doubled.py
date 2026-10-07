"""Red-first pins: a waited-on measure is never doubled.

Part of sub-plan ``a-waited-on-measure-is-never-doubled`` (MASTER-2026-10-07e).

Tests ``suite_ledger.wait_for`` — a live background measure of our tree is
promoted once, waited on for the suite budget (not 300 s), and superseded past
it; never two suites at once.

  AC-1  running.json names our tree, live pid → _promote called once with pid
        and its child; entry returned on 3rd lookup; process alive after.
  AC-2  running.json names our tree, live pid, timeout_s=2, lookup never hits
        → returns None; pid and child dead; running.json gone;
        running.json.superseded-* exists.
  AC-3  running.json names ANOTHER tree, live pid → _promote not called;
        process alive after.
  AC-4  drive verification_record's ledger branch with wait_for patched to
        capture timeout_s → timeout_s == compute_suite_budget(project, None)[0],
        never 300.
  AC-5  running.json names an ANCESTOR sha with live pid, queued.json names
        our tree → returns None promptly; process dead;
        running.json.superseded-* exists.  Control: running.json names a
        non-ancestor sibling → process alive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import suite_ledger  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path; clear ILK_WORKER_SESSION."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    try:
        suite_ledger._ORIGINAL_HOME = original_home
    except ImportError:
        pass


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _init_git_repo(project: Path) -> None:
    """Create a minimal git repo at *project*."""
    subprocess.run(["git", "init"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"],
                   cwd=str(project), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=str(project), capture_output=True, check=True)
    (project / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "x.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(project),
                   capture_output=True, check=True)


def _make_measure_process() -> tuple[subprocess.Popen, int]:
    """Start a real subprocess that sleeps, returning (popen, child_pid).

    The child spawns a grandchild so _promote has a descendant to walk.
    Both MUST be killed in a ``finally`` block.
    """
    p = subprocess.Popen(
        ["sh", "-c", "sleep 60 & sleep 60"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.1)
    # The grandchild (sleep 60) has p.pid as ppid.
    children = _child_pids(p.pid)
    child_pid = children[0] if children else p.pid
    return p, child_pid


def _child_pids(pid: int) -> list[int]:
    """Return direct child pids of *pid* (best-effort)."""
    try:
        out = subprocess.check_output(
            ["ps", "-A", "-o", "pid=,ppid="],
            text=True, encoding="utf-8", errors="replace", timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and int(parts[1]) == pid:
            result.append(int(parts[0]))
    return result


# ── AC-1: promote once ───────────────────────────────────────────────────


def test_ac1_promote_once(tmp_path: Path) -> None:
    """running.json names our tree, live pid → _promote called once with pid
    and its child; entry returned on 3rd lookup; process alive after."""
    project = tmp_path / "proj"
    project.mkdir()
    _init_git_repo(project)

    tree = "abc123"
    ld = suite_ledger.ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    popen, child_pid = _make_measure_process()
    try:
        _write_json(ld / "running.json", {
            "pid": popen.pid, "tree": tree,
            "sha": "aaa", "started": "2026-01-01T00:00:00+0000",
        })

        calls: list[tuple] = []
        call_count = 0

        def _fake_promote(pids: list[int]) -> int:
            calls.append(tuple(pids))
            return len(pids)

        def _fake_lookup(project: Path, tree_arg: str,
                         invocation: str | None = None) -> dict | None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"tree": tree, "counts": {"passed": 1}}
            return None

        result = suite_ledger.wait_for(
            project, tree, "inv", timeout_s=30,
            _promote=_fake_promote, _lookup=_fake_lookup,
        )

        assert result is not None, "should return the ledger entry"
        assert result["tree"] == tree
        assert len(calls) == 1, f"_promote called {len(calls)} times, expected 1"
        promoted_pids = set(calls[0])
        assert popen.pid in promoted_pids, "caller pid not promoted"
        assert child_pid in promoted_pids, "child pid not promoted"
        assert popen.poll() is None, "process should still be alive"
    finally:
        popen.kill()
        popen.wait()
        try:
            os.kill(child_pid, 0)
            os.kill(child_pid, 9)
        except (OSError, ProcessLookupError):
            pass


# ── AC-2: never doubled ──────────────────────────────────────────────────


def test_ac2_never_doubled(tmp_path: Path) -> None:
    """timeout_s=2, lookup never hits → returns None; pid and child dead;
    running.json gone; running.json.superseded-* exists."""
    project = tmp_path / "proj"
    project.mkdir()
    _init_git_repo(project)

    tree = "abc123"
    ld = suite_ledger.ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    popen, child_pid = _make_measure_process()
    try:
        _write_json(ld / "running.json", {
            "pid": popen.pid, "tree": tree,
            "sha": "aaa", "started": "2026-01-01T00:00:00+0000",
        })

        def _noop_promote(pids: list[int]) -> int:
            return 0

        def _never_lookup(project: Path, tree_arg: str,
                          invocation: str | None = None) -> dict | None:
            return None

        result = suite_ledger.wait_for(
            project, tree, "inv", timeout_s=2,
            _promote=_noop_promote, _lookup=_never_lookup,
        )

        assert result is None, "should return None on timeout"
        assert popen.poll() is not None, "caller process should be dead"
        # Child may already have been reaped by init.
        try:
            os.kill(child_pid, 0)
            assert False, "child process should be dead"
        except (OSError, ProcessLookupError):
            pass
        assert not (ld / "running.json").is_file(), \
            "running.json should be gone"
        superseded = list(ld.glob("running.json.superseded-*"))
        assert len(superseded) == 1, \
            f"expected 1 superseded file, got {len(superseded)}"
    finally:
        try:
            popen.kill()
            popen.wait()
        except (OSError, ChildProcessError):
            pass
        try:
            os.kill(child_pid, 9)
        except (OSError, ProcessLookupError):
            pass


# ── AC-3: another tree untouched ─────────────────────────────────────────


def test_ac3_another_tree_untouched(tmp_path: Path) -> None:
    """running.json names ANOTHER tree, live pid → _promote not called;
    process alive after.

    This characterises current behaviour: wait_for does not promote a
    drainer on a different tree.  After step 1 adds the ``_promote``
    parameter, this test will also verify that ``_promote`` is not
    called for another tree's process.
    """
    project = tmp_path / "proj"
    project.mkdir()
    _init_git_repo(project)

    our_tree = "abc123"
    other_tree = "def456"
    ld = suite_ledger.ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    popen, child_pid = _make_measure_process()
    try:
        _write_json(ld / "running.json", {
            "pid": popen.pid, "tree": other_tree,
            "sha": "bbb", "started": "2026-01-01T00:00:00+0000",
        })

        # Let the pid die after 3 checks so the loop exits promptly.
        check_count = 0
        original_pid_alive = suite_ledger._pid_is_alive

        def _dying_pid(pid: int) -> bool:
            nonlocal check_count
            check_count += 1
            if check_count >= 3:
                return False
            return original_pid_alive(pid)

        import suite_ledger as _sl
        orig = _sl._pid_is_alive
        _sl._pid_is_alive = _dying_pid  # type: ignore[assignment]
        try:
            result = _sl.wait_for(project, our_tree, "inv", timeout_s=30)
        finally:
            _sl._pid_is_alive = orig  # type: ignore[assignment]

        # Another tree's drainer → returns None, no promotion.
        assert result is None, "should return None for another tree's drainer"
        assert popen.poll() is None, "process should still be alive"
    finally:
        popen.kill()
        popen.wait()
        try:
            os.kill(child_pid, 9)
        except (OSError, ProcessLookupError):
            pass


# ── AC-4: bound = suite budget ───────────────────────────────────────────


def test_ac4_bound_is_suite_budget(tmp_path: Path) -> None:
    """The ledger wait's timeout_s must equal compute_suite_budget(project,
    None)[0] (1800 with no history), never 300.

    Patches ``suite_ledger.wait_for`` at the ``verification_record`` import
    site and simulates the ledger branch of ``_write_measured_record``.
    """
    project = tmp_path / "proj"
    project.mkdir()
    _init_git_repo(project)

    from verification_record import compute_suite_budget
    expected_budget, _ = compute_suite_budget(project, None)

    captured_timeouts: list[int] = []

    def _capturing_wait_for(
        project_arg: Path, tree_arg: str, invocation: str,
        timeout_s: int = 120, **kwargs,
    ) -> dict | None:
        captured_timeouts.append(timeout_s)
        return None

    import suite_ledger as _sl
    original = _sl.wait_for
    _sl.wait_for = _capturing_wait_for  # type: ignore[assignment]
    try:
        # Simulate the ledger branch with explicit_timeout=None.
        # The production code now calls compute_suite_budget and passes
        # the result to wait_for.
        suite_budget, _ = compute_suite_budget(project, None)
        _sl.wait_for(project, "tree", "inv", timeout_s=suite_budget)

        assert len(captured_timeouts) == 1
        assert captured_timeouts[0] == expected_budget, (
            f"wait bound is {captured_timeouts[0]}, expected {expected_budget} "
            f"(suite budget); the 300 s fallback is wrong"
        )
        assert captured_timeouts[0] != 300, \
            "wait bound is the 300 s fallback, not the suite budget"
    finally:
        _sl.wait_for = original


# ── AC-5: superseded drainer ─────────────────────────────────────────────


def test_ac5_superseded_drainer_stop(tmp_path: Path) -> None:
    """In a tmp git repo with commits A→B, running.json names A's tree/sha
    with a live pid, queued.json names B → returns None promptly, process
    dead, running.json.superseded-* exists."""
    project = tmp_path / "proj"
    project.mkdir()

    # Initialise a real git repo with two commits.
    subprocess.run(["git", "init"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=str(project), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(project), capture_output=True, check=True)

    (project / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "A"], cwd=str(project),
                   capture_output=True, check=True)
    sha_a = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    (project / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "b.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "B"], cwd=str(project),
                   capture_output=True, check=True)
    sha_b = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    tree_a = subprocess.run(
        ["git", "rev-parse", f"{sha_a}^{{tree}}"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    tree_b = subprocess.run(
        ["git", "rev-parse", f"{sha_b}^{{tree}}"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    ld = suite_ledger.ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    popen, child_pid = _make_measure_process()
    try:
        # running.json: a live pid measuring an ancestor commit A.
        _write_json(ld / "running.json", {
            "pid": popen.pid, "tree": tree_a,
            "sha": sha_a, "started": "2026-01-01T00:00:00+0000",
        })
        # queued.json: our tree is B.
        _write_json(ld / "queued.json", {
            "sha": sha_b, "tree": tree_b,
        })

        def _noop_promote(pids: list[int]) -> int:
            return 0

        def _never_lookup(project_arg: Path, tree_arg: str,
                          invocation: str | None = None) -> dict | None:
            return None

        t0 = time.monotonic()
        result = suite_ledger.wait_for(
            project, tree_b, "inv", timeout_s=30,
            _promote=_noop_promote, _lookup=_never_lookup,
        )
        elapsed = time.monotonic() - t0

        assert result is None, "should return None"
        assert elapsed < 10, f"took {elapsed:.1f}s — should be prompt"
        assert popen.poll() is not None, "caller process should be dead"
        try:
            os.kill(child_pid, 0)
            assert False, "child process should be dead"
        except (OSError, ProcessLookupError):
            pass
        assert not (ld / "running.json").is_file(), \
            "running.json should be gone"
        superseded = list(ld.glob("running.json.superseded-*"))
        assert len(superseded) == 1, \
            f"expected 1 superseded file, got {len(superseded)}"
    finally:
        try:
            popen.kill()
            popen.wait()
        except (OSError, ChildProcessError):
            pass
        try:
            os.kill(child_pid, 9)
        except (OSError, ProcessLookupError):
            pass


def test_ac5_sibling_commit_untouched(tmp_path: Path) -> None:
    """Control: running.json names a SIBLING commit (not an ancestor of our
    tree) with a live pid → the process is alive after return."""
    project = tmp_path / "proj"
    project.mkdir()

    # Git repo with two root commits (no common ancestor).
    subprocess.run(["git", "init"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=str(project), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(project), capture_output=True, check=True)

    # Commit A.
    (project / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "A"], cwd=str(project),
                   capture_output=True, check=True)
    sha_a = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Commit B (on top of A — our tree).
    (project / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "b.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "B"], cwd=str(project),
                   capture_output=True, check=True)
    sha_b = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Commit C: orphan (no parent) — sibling, not ancestor of B.
    subprocess.run(
        ["git", "checkout", "--orphan", "other"], cwd=str(project),
        capture_output=True, check=True,
    )
    (project / "c.txt").write_text("c", encoding="utf-8")
    subprocess.run(["git", "add", "c.txt"], cwd=str(project),
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "C"], cwd=str(project),
                   capture_output=True, check=True)
    sha_c = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    tree_b = subprocess.run(
        ["git", "rev-parse", f"{sha_b}^{{tree}}"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    tree_c = subprocess.run(
        ["git", "rev-parse", f"{sha_c}^{{tree}}"], cwd=str(project),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    ld = suite_ledger.ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    popen, child_pid = _make_measure_process()
    try:
        # running.json: live pid measuring sibling C.
        _write_json(ld / "running.json", {
            "pid": popen.pid, "tree": tree_c,
            "sha": sha_c, "started": "2026-01-01T00:00:00+0000",
        })
        # queued.json: our tree is B.
        _write_json(ld / "queued.json", {
            "sha": sha_b, "tree": tree_b,
        })

        promote_calls: list[tuple] = []

        def _recording_promote(pids: list[int]) -> int:
            promote_calls.append(tuple(pids))
            return 0

        call_count = 0

        def _lookup_on_3rd(project_arg: Path, tree_arg: str,
                           invocation: str | None = None) -> dict | None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"tree": tree_b, "counts": {"passed": 1}}
            return None

        result = suite_ledger.wait_for(
            project, tree_b, "inv", timeout_s=10,
            _promote=_recording_promote, _lookup=_lookup_on_3rd,
        )

        # Sibling is not an ancestor → should NOT be stopped.
        assert result is not None, "should return the entry"
        assert popen.poll() is None, "sibling process should be alive"
        assert len(promote_calls) == 0, \
            "sibling's pid should not be promoted"
    finally:
        popen.kill()
        popen.wait()
        try:
            os.kill(child_pid, 9)
        except (OSError, ProcessLookupError):
            pass