"""Red-first pins: the verify reads the ledger.

Part of sub-plan ``the-verify-reads-the-ledger`` (MASTER-2026-10-03i).

Tests that ``verification_record.py --run-suite`` looks up HEAD and base
verdicts from the ledger instead of re-running the suite, and that the
record cites the ledger entries it used.  Also tests that
``verify_attribution.py`` refuses a record whose cited entry is missing
or tampered.

AC-1: head from the ledger (prefer mode) — no sentinel, head_source cites
      the entry, suite_failed matches.
AC-2: base from the ledger — at-base table reads from the entry, no
      worktree was created.
AC-3 (control): no base entry ⇒ base_source: rerun, at-base verdicts are
      what run_at_base gives today.
AC-4: ledger require with no entry measures in-process, writes the entry,
      and the record cites it.
AC-5: verify_attribution refuses after the cited head entry is tampered or
      deleted.
AC-6: ledger_mode require + head_source: run ⇒ refused with "no ledger
      entry".
AC-7: template step-0 command contains --ledger require.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


# ── module-level fixture: pin HOME + ILK_DATA_HOME, clear worker session ──


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


def _make_repo_with_suite(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a temp git repo with a pytest suite and .ilk-launch.json.

    Two commits:
      - Commit 1 (base): only test_pass (passes).
      - Commit 2 (HEAD): adds test_fail (fails).

    Returns (repo, sentinel_path, test_file_path).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    # .ilk-launch.json — configure suite invocation.
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            }
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8"
    )

    sentinel = tmp_path / "sentinel.txt"

    # Commit 1 (base): only a passing test.
    test_file = repo / "test_stuff.py"
    test_file.write_text(
        textwrap.dedent("""\
            def test_pass():
                assert 1 + 1 == 2
        """),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base commit — passing test only")

    # Commit 2 (HEAD): add the failing test.
    test_file.write_text(
        textwrap.dedent(f"""\
            import pathlib

            SENTINEL = pathlib.Path({str(sentinel)!r})

            def test_pass():
                assert 1 + 1 == 2

            def test_fail():
                SENTINEL.write_text("ran")
                assert False, "intentional failure"
        """),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add failing test")
    return repo, sentinel, test_file


def _write_ledger_entry(repo: Path, tree: str, *,
                        failing_nodes: list[str] | None = None,
                        counts: dict | None = None,
                        invocation: str | None = None) -> dict:
    """Write a ledger entry directly (bypassing measure).

    Returns the entry dict.
    """
    import suite_ledger
    ld = suite_ledger.ledger_dir(repo)
    ld.mkdir(parents=True, exist_ok=True)

    if counts is None:
        counts = {"total": 2, "passed": 1, "failed": 1, "errors": 0,
                  "skipped": 0}
    if failing_nodes is None:
        failing_nodes = ["test_stuff.py::test_fail"]
    if invocation is None:
        invocation = "python3 -m pytest -q -p no:cacheprovider"

    entry = {
        "tree": tree,
        "invocation": invocation,
        "counts": counts,
        "failing_nodes": failing_nodes,
        "suite_duration_sec": 5,
        "digest": "",  # placeholder
    }
    # Compute the real digest.
    entry["digest"] = suite_ledger._compute_digest(entry)

    entry_path = ld / f"{tree}.json"
    entry_path.write_text(
        json.dumps(entry, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return entry


def _write_output_text(repo: Path, tree: str, text: str) -> None:
    """Write <tree>.output.txt beside the ledger entry."""
    import suite_ledger
    ld = suite_ledger.ledger_dir(repo)
    (ld / f"{tree}.output.txt").write_text(text, encoding="utf-8")


# ── AC-1: head from the ledger ──────────────────────────────────────────────


def test_ac1_head_from_ledger_no_sentinel(tmp_path: Path) -> None:
    """With a ledger entry for HEAD, --run-suite --ledger prefer leaves no
    sentinel, cites head_source: ledger, and suite_failed matches the entry.
    """
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Write a ledger entry with test_fail failing.
    entry = _write_ledger_entry(repo, tree)
    # Write output text so the record can include it.
    _write_output_text(repo, tree, "FAILED test_stuff.py::test_fail\n")

    # Delete the sentinel so a real suite run would re-create it.
    sentinel.unlink(missing_ok=True)

    # Run verification_record --run-suite --ledger prefer.
    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "prefer",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    # No sentinel was written — the suite did not run.
    assert not sentinel.exists(), "sentinel written: the suite ran instead of using the ledger"

    # Record cites the ledger.
    text = record_path.read_text(encoding="utf-8")
    assert "head_source: ledger" in text, f"head_source not ledger:\n{text}"
    assert tree[:12] in text, f"tree sha not in record:\n{text}"

    # suite_failed matches the entry.
    assert "suite_failed: 1" in text, f"suite_failed not 1:\n{text}"


# ── AC-2: base from the ledger ──────────────────────────────────────────────


def test_ac2_base_from_ledger_no_worktree(tmp_path: Path) -> None:
    """With ledger entries for HEAD and base, at-base reads from the entry;
    no worktree was created and base_source cites the ledger.
    """
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)

    # Commit 1 (base): test_fail exists and fails.
    base_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Commit 2 (head): add a second failing test.
    test_file.write_text(
        textwrap.dedent("""\
            import pathlib

            def test_pass():
                assert 1 + 1 == 2

            def test_fail():
                assert False, "intentional failure"

            def test_fail2():
                assert False, "second failure"
        """),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add second failing test")
    head_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Ledger entry for HEAD: both test_fail and test_fail2 failing.
    _write_ledger_entry(repo, head_tree,
                        failing_nodes=["test_stuff.py::test_fail",
                                       "test_stuff.py::test_fail2"],
                        counts={"total": 3, "passed": 1, "failed": 2,
                                "errors": 0, "skipped": 0})
    _write_output_text(repo, head_tree, "FAILED 2\n")

    # Ledger entry for base: only test_fail failing.
    _write_ledger_entry(repo, base_tree,
                        failing_nodes=["test_stuff.py::test_fail"],
                        counts={"total": 2, "passed": 1, "failed": 1,
                                "errors": 0, "skipped": 0})
    _write_output_text(repo, base_tree, "FAILED 1\n")

    # Run verification_record --run-suite --ledger prefer.
    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "prefer",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    text = record_path.read_text(encoding="utf-8")

    # base_source cites the ledger.
    assert "base_source: ledger" in text, f"base_source not ledger:\n{text}"

    # test_fail failed at base (it was in the base's failing set).
    # test_fail2 passed at base (it was NOT in the base's failing set).
    assert "test_fail" in text
    # The at-base table should show "failed" for test_fail and "passed" for
    # test_fail2 — parsed from the ledger entry, not from a subprocess run.
    lines = text.split("\n")
    for line in lines:
        if "test_fail " in line and "test_fail2" not in line:
            assert "failed" in line, f"test_fail should be failed at base: {line}"
        if "test_fail2" in line:
            assert "passed" in line, f"test_fail2 should be passed at base: {line}"


# ── AC-3 (control): no base entry ⇒ rerun ────────────────────────────────────


def test_ac3_no_base_entry_rerun(tmp_path: Path) -> None:
    """With no base ledger entry, base_source: rerun and at-base verdicts
    come from run_at_base as today.  This is a CONTROL — once --ledger is
    implemented, this test should pass without any ledger-specific code path.
    """
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Write a ledger entry for HEAD only (no base entry).
    _write_ledger_entry(repo, tree)
    _write_output_text(repo, tree, "FAILED test_stuff.py::test_fail\n")

    sentinel.unlink(missing_ok=True)

    # Run verification_record --run-suite --ledger prefer.
    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "prefer",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    # With no base entry, the verify falls through to run_at_base.
    # head_source should be ledger (we had a head entry), base_source should
    # be rerun (no base entry).
    text = record_path.read_text(encoding="utf-8")
    assert "head_source: ledger" in text, f"head_source not ledger:\n{text}"
    assert "base_source: rerun" in text, f"base_source not rerun:\n{text}"


# ── AC-4: ledger require with no entry measures in-process ──────────────────


def test_ac4_ledger_require_no_entry(tmp_path: Path) -> None:
    """--ledger require with no entry measures in-process, writes the entry,
    and the record cites it.
    """
    # This test requires pytest to be importable by the system python3.
    check = subprocess.run(
        [sys.executable, "-c", "import pytest"],
        capture_output=True, timeout=10,
    )
    if check.returncode != 0:
        pytest.skip("pytest not available in the system python3")
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)

    # No ledger entry at all.
    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "require",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    text = record_path.read_text(encoding="utf-8")

    # The record should cite the ledger entry it wrote.
    assert "head_source: ledger" in text, f"head_source not ledger:\n{text}"
    assert "ledger_mode: require" in text, f"ledger_mode not require:\n{text}"

    # The ledger entry should now exist on disk.
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    import suite_ledger
    ld = suite_ledger.ledger_dir(repo)
    entry_path = ld / f"{tree}.json"
    assert entry_path.is_file(), f"ledger entry not written: {entry_path}"


# ── AC-5: verify_attribution refuses tampered/deleted entry ──────────────────


def test_ac5_verify_refuses_tampered_entry(tmp_path: Path) -> None:
    """verify_attribution refuses after the cited head entry is tampered."""
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    entry = _write_ledger_entry(repo, tree)
    _write_output_text(repo, tree, "FAILED test_stuff.py::test_fail\n")
    sentinel.unlink(missing_ok=True)

    # Run verification_record to get a record that cites the ledger.
    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "prefer",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    # Tamper: add a spurious failing id to the ledger entry.
    import suite_ledger
    ld = suite_ledger.ledger_dir(repo)
    entry_path = ld / f"{tree}.json"
    tampered = json.loads(entry_path.read_text(encoding="utf-8"))
    tampered["failing_nodes"].append("test_stuff.py::test_fake")
    tampered["digest"] = suite_ledger._compute_digest(tampered)
    entry_path.write_text(
        json.dumps(tampered, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # verify_attribution should refuse.
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_attribution.py"),
         
         str(record_path),
         "--project", str(repo)],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "verify_attribution should refuse tampered entry"
    combined = (result.stdout or "") + (result.stderr or "")
    assert tree[:12] in combined or "digest" in combined.lower() or "ledger" in combined.lower(), \
        f"error should name the tree or digest:\n{combined}"


def test_ac5_verify_refuses_deleted_entry(tmp_path: Path) -> None:
    """verify_attribution refuses after the cited head entry is deleted."""
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    _write_ledger_entry(repo, tree)
    _write_output_text(repo, tree, "FAILED test_stuff.py::test_fail\n")
    sentinel.unlink(missing_ok=True)

    record_path = tmp_path / "record.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite",
         "--ledger", "prefer",
         "--record", str(record_path)],
        cwd=repo, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    # Delete the ledger entry.
    import suite_ledger
    ld = suite_ledger.ledger_dir(repo)
    entry_path = ld / f"{tree}.json"
    entry_path.unlink()

    # verify_attribution should refuse.
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_attribution.py"),
         
         str(record_path),
         "--project", str(repo)],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "verify_attribution should refuse deleted entry"


# ── AC-6: ledger require + head_source: run ⇒ refused ────────────────────────


def test_ac6_require_mode_refuses_run_source(tmp_path: Path) -> None:
    """A record with ledger_mode: require and head_source: run is refused."""
    repo, sentinel, test_file = _make_repo_with_suite(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Write a record that has ledger_mode: require and head_source: run.
    # This simulates what would happen if the ledger was unavailable and
    # the record fell back to running — but require mode should refuse that.
    record_path = tmp_path / "record.md"
    record_path.write_text(textwrap.dedent(f"""\
        # Batch verification record — test-batch

        record_writer: verification_record.py
        batch: test-batch
        verified_head: {_git(repo, "rev-parse", "HEAD")}
        verified_tree: {tree}
        base_sha: {_git(repo, "rev-parse", "HEAD~1")}
        suite_invocation: python3 -m pytest -q -p no:cacheprovider
        suite_scope: full
        selection_size: 2
        suite_total: 2
        suite_passed: 1
        suite_failed: 1
        suite_errors: 0
        suite_skipped: 0
        ledger_mode: require
        head_source: run
        base_source: rerun
        ledger_wait_sec: 0
        record_elapsed_sec: 10

        ## At-base rerun

        | node id | at base | in baseline_red |
        |---|---|---|
        | test_stuff.py::test_fail | failed | no |
    """), encoding="utf-8")

    # verify_attribution should refuse.
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_attribution.py"),
         
         str(record_path),
         "--project", str(repo)],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "verify_attribution should refuse require+run"
    combined = (result.stdout or "") + (result.stderr or "")
    assert "no ledger entry" in combined.lower() or "require" in combined.lower(), \
        f"error should mention 'no ledger entry':\n{combined}"


# ── AC-7: template step-0 command contains --ledger require ──────────────────


def test_ac7_template_has_ledger_require() -> None:
    """The step-0 command in batch-verification-subplan.md contains --ledger require."""
    template = TEMPLATES_DIR / "batch-verification-subplan.md"
    assert template.is_file(), f"template not found: {template}"
    text = template.read_text(encoding="utf-8")
    assert "--ledger require" in text, \
        f"--ledger require not found in template step-0 command:\n{text[:500]}"


# ── Batch Z regression: scoped ledger cache-miss runs full suite ─────────────
#
# These tests pin the contract from AC-2 through AC-5 for the
# ``ledger=require`` cache-miss path.  When ``compute_suite_scope``
# returns scoped with a ``selection``, the ``require`` path must pass
# that selection to ``run_suite`` so that only the selected tests run.
#
# MEASURED 2026-10-06 (Batch Z): ``run_suite(project, invocation,
# suite_budget)`` at ``verification_record.py:1941`` was called without
# ``selection``, so the full 5,142-test suite ran while the record
# claimed ``suite_scope: scoped, selection_size: 8``.
#
# These pins are ``xfail(strict=True)`` — the current code is the
# reason they fail.  Step 1 of the sub-plan will fix the bug and
# remove the xfail markers.

_SCOPED_SCOPE_RESULT = {
    "mode": "scoped", "count": 2, "reason": "2 test file(s) selected",
    "selection": ["test_a_selection.py", "test_b_sentinel.py"],
}
_SCOPED_SUITE_RESULT = {
    "exit_code": 0,
    "counts": {"passed": 2, "failed": 0, "errors": 0, "skipped": 0,
               "total": 2},
    "failing_nodes": [],
}
_FULL_SUITE_RESULT = {
    "exit_code": 0,
    "counts": {"passed": 3, "failed": 0, "errors": 0, "skipped": 0,
               "total": 3},
    "failing_nodes": [],
}


def _tracking_run_suite(project, invocation, timeout, selection=None):
    """Return scoped result when selection is given, full result otherwise."""
    if selection is None:
        return dict(_FULL_SUITE_RESULT)
    return dict(_SCOPED_SUITE_RESULT)


@pytest.mark.xfail(strict=True, reason="red-first: Batch Z ledger=require "
                   "cache-miss drops computed selection")
def test_z1_require_cache_miss_passes_selection(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    """AC-2: ledger=require cache-miss passes computed selection to run_suite.

    A scoped scope with a ``selection`` list must reach ``run_suite`` so
    that only the selected tests execute.  The persisted record must
    reflect the scoped count, not the full-suite count.
    """
    from verification_record import main as vr_main  # type: ignore[import-untyped]

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project,
                   capture_output=True, check=True)

    launch = {"ship": {"suite": {"command": "python3 -m pytest",
                                 "flags": ["-q", "-p", "no:cacheprovider"]}}}
    (project / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8")

    (project / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=project,
                   capture_output=True, check=True)

    (project / "test_a_selection.py").write_text("def test_a1(): pass\n",
                                                 encoding="utf-8")
    (project / "test_b_sentinel.py").write_text("def test_b1(): pass\n",
                                                encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add tests"], cwd=project,
                   capture_output=True, check=True)

    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD~1"], cwd=project, text=True).strip()

    # Mock suite_ledger: no entry exists (cache miss), so require path
    # must measure in-process.
    mock_ledger = MagicMock()
    mock_ledger.lookup.return_value = None
    mock_ledger.wait_for.return_value = None
    mock_ledger._compute_digest.return_value = "abc123"
    mock_ledger.ledger_dir.return_value = tmp_path / "ledger"
    monkeypatch.setitem(sys.modules, "suite_ledger", mock_ledger)

    monkeypatch.setattr("verification_record.compute_suite_scope",
                        lambda *_a, **_kw: dict(_SCOPED_SCOPE_RESULT))
    monkeypatch.setattr("verification_record.run_suite", _tracking_run_suite)
    monkeypatch.setattr("verification_record.run_at_base",
                        lambda *a, **kw: {})
    monkeypatch.setattr("verification_record.read_baseline_red",
                        lambda *a, **kw: {})
    monkeypatch.setattr("verification_record.read_baseline_red_at",
                        lambda *a, **kw: {})

    record_path = tmp_path / "record.md"
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
        "--ledger", "require",
    ])
    assert rc == 0, f"expected exit 0, got {rc}"

    # The record must reflect the scoped run, not the full run.
    text = record_path.read_text(encoding="utf-8")
    assert "suite_scope: scoped" in text, (
        f"expected suite_scope: scoped in record:\n{text}")
    assert "suite_total: 2" in text, (
        "expected suite_total: 2 (scoped selection), "
        f"got record:\n{text}")
    assert "selection_size: 2" in text, (
        f"expected selection_size: 2 in record:\n{text}")

    # The ledger entry must have been written with scoped counts.
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=project, text=True).strip()
    ledger_dir = tmp_path / "ledger"
    entry_path = ledger_dir / f"{tree}.json"
    assert entry_path.is_file(), f"ledger entry not written: {entry_path}"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    assert entry["tree"] == tree, (
        f"ledger tree mismatch: {entry['tree']} != {tree}")
    assert entry["counts"]["total"] == 2, (
        f"ledger total should be 2 (scoped), got {entry['counts']['total']}")
    assert entry["digest"] == "abc123", (
        f"ledger digest not computed: {entry['digest']}")


@pytest.mark.xfail(strict=True, reason="red-first: Batch Z ledger=require "
                   "cache-miss drops computed selection")
def test_z2_require_cache_miss_record_identity(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    """AC-2 through AC-5: the persisted record and ledger entry carry
    consistent identity fields after a scoped ledger=require cache miss.

    Checks: ``ledger_mode: require``, ``head_source`` cites the ledger
    tree+digest, ``selection_size`` matches the scope, and the ledger
    entry's ``tree`` matches ``verified_tree`` in the record.
    """
    from verification_record import main as vr_main  # type: ignore[import-untyped]

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project,
                   capture_output=True, check=True)

    launch = {"ship": {"suite": {"command": "python3 -m pytest",
                                 "flags": ["-q", "-p", "no:cacheprovider"]}}}
    (project / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8")

    (project / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=project,
                   capture_output=True, check=True)

    (project / "test_a_selection.py").write_text("def test_a1(): pass\n",
                                                 encoding="utf-8")
    (project / "test_b_sentinel.py").write_text("def test_b1(): pass\n",
                                                encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add tests"], cwd=project,
                   capture_output=True, check=True)

    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD~1"], cwd=project, text=True).strip()
    expected_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=project, text=True).strip()

    mock_ledger = MagicMock()
    mock_ledger.lookup.return_value = None
    mock_ledger.wait_for.return_value = None
    mock_ledger._compute_digest.return_value = "deadbeef01234567"
    mock_ledger.ledger_dir.return_value = tmp_path / "ledger"
    monkeypatch.setitem(sys.modules, "suite_ledger", mock_ledger)

    monkeypatch.setattr("verification_record.compute_suite_scope",
                        lambda *_a, **_kw: dict(_SCOPED_SCOPE_RESULT))
    monkeypatch.setattr("verification_record.run_suite", _tracking_run_suite)
    monkeypatch.setattr("verification_record.run_at_base",
                        lambda *a, **kw: {})
    monkeypatch.setattr("verification_record.read_baseline_red",
                        lambda *a, **kw: {})
    monkeypatch.setattr("verification_record.read_baseline_red_at",
                        lambda *a, **kw: {})

    record_path = tmp_path / "record.md"
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
        "--ledger", "require",
    ])
    assert rc == 0, f"expected exit 0, got {rc}"

    text = record_path.read_text(encoding="utf-8")

    # Record identity: verified_tree matches the HEAD tree.
    assert f"verified_tree: {expected_tree}" in text, (
        f"verified_tree not in record:\n{text}")

    # Ledger citation: ledger_mode is require, head_source cites the tree.
    assert "ledger_mode: require" in text, (
        f"ledger_mode not require:\n{text}")
    assert "head_source: ledger" in text, (
        f"head_source not ledger:\n{text}")
    assert expected_tree[:12] in text, (
        f"tree prefix not in head_source:\n{text}")

    # Ledger entry identity: tree matches, digest is set.
    ledger_dir = tmp_path / "ledger"
    entry_path = ledger_dir / f"{expected_tree}.json"
    assert entry_path.is_file(), f"ledger entry not written: {entry_path}"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    assert entry["tree"] == expected_tree
    assert entry["digest"] == "deadbeef01234567"
    assert entry["invocation"] == "python3 -m pytest -q -p no:cacheprovider"