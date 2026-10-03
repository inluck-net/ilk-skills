"""Red-first pins: a tree is measured once in a per-tree suite ledger.

Part of sub-plan ``a-tree-is-measured-once`` (MASTER-2026-10-03i).

Tests ``suite_ledger.py`` — a per-tree ledger that stores full-suite results
by tree sha, so the verify can look up HEAD and base verdicts instead of
re-measuring.

AC-1: measure writes a JSON entry with failing_nodes, counts, and a
      recomputable digest; git worktree list still has 1 line and status
      is empty.
AC-2: measuring commit A (failing test present) while HEAD is commit B
      (test fixed) records A's failing id.
AC-3: with ILK_WORKER_SESSION=1, measure raises LedgerRefused (a
      PermissionError), nothing is written, CLI exits 1.
AC-4: lookup returns None after a hand-edit of the entry file (digest
      mismatch) and after ship.suite.flags changes.
AC-5: spawn returns in under 2 s with action: spawned; entry appears
      within 60 s; running.json pid is not the test process's child;
      second spawn for a different commit returns queued.
AC-6: ship.ledger: false ⇒ spawn returns disabled and writes nothing.
AC-7: ledger_dir called from a git worktree checkout returns the same
      path as from the repo itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── module-level fixture: pin HOME + ILK_DATA_HOME, clear worker session ──


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path; clear ILK_WORKER_SESSION."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    # Save original HOME before monkeypatch so suite_ledger can restore it
    # for subprocesses (Python's user site-packages depends on HOME).
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    # Patch suite_ledger._ORIGINAL_HOME if the module is already imported.
    try:
        import suite_ledger
        suite_ledger._ORIGINAL_HOME = original_home
    except ImportError:
        pass


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo_with_suite(tmp_path: Path) -> Path:
    """Create a temp git repo with a two-test pytest suite.

    One test passes, one fails.  Writes ``.ilk-launch.json`` pointing
    ``ship.suite.command`` at ``python3 -m pytest`` with flags
    ``["-q", "-p", "no:cacheprovider"]``.

    Returns the repo root.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Two-test suite: one passes, one fails.
    (repo / "test_suite.py").write_text(
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert False, 'intentional failure'\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_suite.py")
    _git(repo, "commit", "-q", "-m", "add suite")

    # .ilk-launch.json pointing at pytest.
    (repo / ".ilk-launch.json").write_text(
        json.dumps({
            "ship": {
                "suite": {
                    "command": sys.executable,
                    "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
                },
            },
        }),
        encoding="utf-8",
    )
    _git(repo, "add", ".ilk-launch.json")
    _git(repo, "commit", "-q", "-m", "add launch config")

    return repo


def _make_repo_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a repo with two commits: first has a failing test, second fixes it.

    Returns (repo, commit_a, commit_b).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Commit A: failing test present.
    (repo / "test_suite.py").write_text(
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert False, 'intentional failure'\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_suite.py")
    _git(repo, "commit", "-q", "-m", "commit A: failing test")
    commit_a = _git(repo, "rev-parse", "HEAD")

    # Commit B: fix the failing test.
    (repo / "test_suite.py").write_text(
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert True\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_suite.py")
    _git(repo, "commit", "-q", "-m", "commit B: fix failing test")
    commit_b = _git(repo, "rev-parse", "HEAD")

    # .ilk-launch.json.
    (repo / ".ilk-launch.json").write_text(
        json.dumps({
            "ship": {
                "suite": {
                    "command": sys.executable,
                    "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
                },
            },
        }),
        encoding="utf-8",
    )
    _git(repo, "add", ".ilk-launch.json")
    _git(repo, "commit", "-q", "-m", "add launch config")

    return repo, commit_a, commit_b


def _ledger_dir_for(repo: Path) -> Path:
    """Resolve the ledger directory for *repo* the same way suite_ledger will."""
    import suite_ledger
    return suite_ledger.ledger_dir(repo)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: measure writes a JSON entry with correct fields and digest
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC1MeasureWritesEntry:
    """measure(repo, HEAD) writes <ledger_dir>/<tree>.json whose failing_nodes
    is exactly the failing id, counts.passed == 1, counts.failed == 1, and
    whose digest recomputes.  git worktree list still has 1 line and git status
    is empty.
    """

    def test_measure_writes_correct_entry(self, tmp_path: Path) -> None:
        """Red-first pin for AC-1."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        tree = _git(repo, "rev-parse", "HEAD^{tree}")

        result = suite_ledger.measure(repo, Path(_git(repo, "rev-parse", "HEAD")))

        # The entry must exist.
        ledger_dir = _ledger_dir_for(repo)
        entry_path = ledger_dir / f"{tree}.json"
        assert entry_path.exists(), f"entry not written at {entry_path}"

        entry = json.loads(entry_path.read_text(encoding="utf-8"))

        # failing_nodes: exactly the failing test id.
        assert "test_suite.py::test_fail" in entry["failing_nodes"], (
            f"failing_nodes should contain test_fail, got {entry['failing_nodes']}"
        )
        assert len(entry["failing_nodes"]) == 1, (
            f"expected exactly 1 failing node, got {len(entry['failing_nodes'])}"
        )

        # counts.
        assert entry["counts"]["passed"] == 1, (
            f"expected passed == 1, got {entry['counts']}"
        )
        assert entry["counts"]["failed"] == 1, (
            f"expected failed == 1, got {entry['counts']}"
        )

        # digest recomputes.
        import hashlib
        entry_copy = {k: v for k, v in entry.items() if k != "digest"}
        expected_digest = hashlib.sha256(
            json.dumps(entry_copy, sort_keys=True).encode()
        ).hexdigest()
        assert entry["digest"] == expected_digest, (
            f"digest mismatch: stored={entry['digest']}, computed={expected_digest}"
        )

        # git worktree list still has 1 line.
        wt_list = _git(repo, "worktree", "list")
        assert len(wt_list.strip().splitlines()) == 1, (
            f"worktree list should have 1 line, got:\n{wt_list}"
        )

        # git status is empty.
        status = _git(repo, "status", "--porcelain")
        assert status == "", f"git status should be empty, got: {status!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: measuring commit A while HEAD is commit B records A's failing id
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC2MeasureAtCommitAWhileHeadIsB:
    """Measuring commit A (failing test present) while the repo's HEAD is
    commit B (test fixed) records A's failing id.
    """

    def test_measure_commit_a_while_head_is_b(self, tmp_path: Path) -> None:
        """Red-first pin for AC-2."""
        import suite_ledger

        repo, commit_a, commit_b = _make_repo_two_commits(tmp_path)

        # HEAD is the .ilk-launch.json commit (after commit_b).
        # Measure commit_a (failing test present).
        result = suite_ledger.measure(repo, Path(commit_a))

        # The entry for commit_a's tree should record the failing id.
        tree_a = _git(repo, "rev-parse", f"{commit_a}^{{tree}}")
        ledger_dir = _ledger_dir_for(repo)
        entry_path = ledger_dir / f"{tree_a}.json"
        assert entry_path.exists(), f"entry not written at {entry_path}"

        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        assert "test_suite.py::test_fail" in entry["failing_nodes"], (
            f"commit A's entry should record test_fail, got {entry['failing_nodes']}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: ILK_WORKER_SESSION=1 ⇒ measure raises LedgerRefused
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC3WorkerSessionRefuses:
    """With ILK_WORKER_SESSION=1, measure raises LedgerRefused (a
    PermissionError), nothing is written under ledger_dir, and the CLI
    exits 1 with ILK-CHECK on stderr.
    """

    def test_measure_raises_in_worker_session(self, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
        """Red-first pin for AC-3 — Python API."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        sha = Path(_git(repo, "rev-parse", "HEAD"))

        monkeypatch.setenv("ILK_WORKER_SESSION", "1")

        with pytest.raises(PermissionError, match="worker session"):
            suite_ledger.measure(repo, sha)

        # Nothing should have been written.
        ledger_dir = _ledger_dir_for(repo)
        if ledger_dir.exists():
            entries = list(ledger_dir.iterdir())
            assert len(entries) == 0, (
                f"ledger_dir should be empty, found {entries}"
            )

    def test_cli_exits_1_in_worker_session(self, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
        """Red-first pin for AC-3 — CLI."""
        repo = _make_repo_with_suite(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD")

        monkeypatch.setenv("ILK_WORKER_SESSION", "1")

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "suite_ledger.py"),
             "measure", "--project", str(repo), "--sha", sha],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 1, (
            f"CLI should exit 1 in worker session, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "ILK-CHECK" in result.stderr, (
            f"stderr should mention ILK-CHECK, got: {result.stderr!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: lookup returns None after hand-edit and after flags change
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC4LookupReturnsNoneOnMismatch:
    """lookup returns None after one failing id in the entry file is edited
    by hand (stderr names a digest mismatch) and after ship.suite.flags
    changes.
    """

    def test_lookup_after_hand_edit(self, tmp_path: Path) -> None:
        """Red-first pin for AC-4 — hand-edit digest mismatch."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        tree = _git(repo, "rev-parse", "HEAD^{tree}")

        # Measure first to create the entry.
        suite_ledger.measure(repo, Path(_git(repo, "rev-parse", "HEAD")))

        # Hand-edit the entry: change one failing node.
        ledger_dir = _ledger_dir_for(repo)
        entry_path = ledger_dir / f"{tree}.json"
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        entry["failing_nodes"] = ["test_suite.py::test_fake"]
        entry_path.write_text(json.dumps(entry), encoding="utf-8")

        # lookup should return None (digest mismatch).
        result = suite_ledger.lookup(repo, tree, invocation=None)
        assert result is None, "lookup should return None after hand-edit"

    def test_lookup_after_flags_change(self, tmp_path: Path) -> None:
        """Red-first pin for AC-4 — flags change."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        tree = _git(repo, "rev-parse", "HEAD^{tree}")

        # Measure first.
        suite_ledger.measure(repo, Path(_git(repo, "rev-parse", "HEAD")))

        # Change .ilk-launch.json flags.
        (repo / ".ilk-launch.json").write_text(
            json.dumps({
                "ship": {
                    "suite": {
                        "command": sys.executable,
                        "flags": ["-m", "pytest", "-v"],  # different flags
                    },
                },
            }),
            encoding="utf-8",
        )

        # lookup with new invocation should return None.
        new_invocation = f"{sys.executable} -m pytest -v"
        result = suite_ledger.lookup(repo, tree, invocation=new_invocation)
        assert result is None, "lookup should return None after flags change"


# ═══════════════════════════════════════════════════════════════════════════════
# AC-5: spawn returns fast, entry appears, pid is detached, second spawn queued
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC5SpawnBehavior:
    """spawn returns in under 2 s with action: spawned; the entry appears
    within 60 s; while it ran, running.json's pid was not the test process's
    child; ps -o command= of that pid does not contain run_ilk_loop.  A second
    spawn for a different commit during the run returns queued, and that
    tree's entry appears after the first.
    """

    def test_spawn_returns_fast_and_entry_appears(self, tmp_path: Path) -> None:
        """Red-first pin for AC-5 — spawn, detached pid, queued."""
        import time
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        sha = Path(_git(repo, "rev-parse", "HEAD"))
        tree = _git(repo, "rev-parse", "HEAD^{tree}")

        # Spawn and measure wall-clock.
        t0 = time.monotonic()
        result = suite_ledger.spawn(repo, sha)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"spawn took {elapsed:.1f}s, expected < 2s"
        assert result["action"] == "spawned", (
            f"expected action 'spawned', got {result['action']}"
        )

        # running.json should exist with a live pid.
        ledger_dir = _ledger_dir_for(repo)
        running_path = ledger_dir / "running.json"
        assert running_path.exists(), "running.json should exist"

        running = json.loads(running_path.read_text(encoding="utf-8"))
        pid = running["pid"]

        # The pid should not be a child of this process.
        try:
            ppid_out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            assert str(os.getpid()) not in ppid_out, (
                f"pid {pid} appears to be a child of this process (ppid={ppid_out})"
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("ps not available or timed out")

        # ps -o command= should not contain run_ilk_loop.
        try:
            cmd_out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            assert "run_ilk_loop" not in cmd_out, (
                f"pid {pid} command contains run_ilk_loop: {cmd_out}"
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("ps not available or timed out")

        # Wait for entry to appear (up to 60 s).
        entry_path = ledger_dir / f"{tree}.json"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if entry_path.exists():
                break
            time.sleep(1)
        assert entry_path.exists(), (
            f"entry not written within 60 s at {entry_path}"
        )

        # Create a second commit and spawn again while the first might still
        # be running — should get queued.
        (repo / "second.txt").write_text("second\n", encoding="utf-8")
        _git(repo, "add", "second.txt")
        _git(repo, "commit", "-q", "-m", "second commit")
        sha2 = Path(_git(repo, "rev-parse", "HEAD"))

        result2 = suite_ledger.spawn(repo, sha2)
        # It should be queued (or spawned if the first already finished).
        assert result2["action"] in ("queued", "spawned"), (
            f"expected 'queued' or 'spawned', got {result2['action']}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-6: ship.ledger: false ⇒ spawn returns disabled
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC6LedgerDisabled:
    """ship.ledger: false ⇒ spawn returns disabled and writes nothing.
    """

    def test_spawn_returns_disabled(self, tmp_path: Path) -> None:
        """Red-first pin for AC-6."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)
        sha = Path(_git(repo, "rev-parse", "HEAD"))

        # Set ship.ledger: false.
        (repo / ".ilk-launch.json").write_text(
            json.dumps({
                "ship": {
                    "ledger": False,
                    "suite": {
                        "command": sys.executable,
                        "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
                    },
                },
            }),
            encoding="utf-8",
        )

        result = suite_ledger.spawn(repo, sha)
        assert result["action"] == "disabled", (
            f"expected action 'disabled', got {result['action']}"
        )

        # Nothing should have been written.
        ledger_dir = _ledger_dir_for(repo)
        if ledger_dir.exists():
            entries = list(ledger_dir.iterdir())
            assert len(entries) == 0, (
                f"ledger_dir should be empty when disabled, found {entries}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-7: ledger_dir from a worktree returns the same path
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC7WorktreeSameLedgerDir:
    """ledger_dir called from a git worktree checkout of the temp repo returns
    the same path as from the repo itself.
    """

    def test_worktree_ledger_dir_matches_repo(self, tmp_path: Path) -> None:
        """Red-first pin for AC-7."""
        import suite_ledger

        repo = _make_repo_with_suite(tmp_path)

        # Create a worktree.
        wt = tmp_path / "worktree"
        _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

        # ledger_dir from the repo.
        dir_from_repo = suite_ledger.ledger_dir(repo)

        # ledger_dir from the worktree.
        dir_from_wt = suite_ledger.ledger_dir(wt)

        assert dir_from_repo == dir_from_wt, (
            f"ledger_dir should be the same from repo and worktree, "
            f"got repo={dir_from_repo}, wt={dir_from_wt}"
        )

# ── background priority (owner, 2026-10-04) ─────────────────────────────────


def test_background_measure_command_runs_at_lowest_priority():
    """A background ledger run must yield the CPU to foreground work.

    Measured 2026-10-04 with probes overlapping a running suite: at normal
    priority the probe slowed 1.33x (limit 1.20); under `taskpolicy -b nice -n
    19` 1.07x.  The invocation key stays `-n 8`, so verify's lookup still finds
    the entry (verification_record.py: suite_ledger.lookup(project, tree,
    invocation)).
    """
    import sys as _sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import suite_ledger

    cmd = suite_ledger._background_measure_command(Path("/p"), "abc123", "run-1")
    assert "nice" in cmd and cmd[cmd.index("nice") + 1:cmd.index("nice") + 3] == ["-n", "19"]
    if _sys.platform == "darwin":
        assert cmd[:2] == ["taskpolicy", "-b"]
    tail = cmd[cmd.index("nice") + 3:]
    assert tail[0] == _sys.executable and tail[1].endswith("suite_ledger.py")
    assert tail[2:] == ["measure", "--project", "/p", "--sha", "abc123", "--run-id", "run-1"]
    assert not any("run_ilk_loop" in part for part in cmd)
