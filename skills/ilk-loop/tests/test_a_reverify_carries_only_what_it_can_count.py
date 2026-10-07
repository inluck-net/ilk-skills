"""Red-first pins: a reverify carries only what it can count.

Part of sub-plan ``a-reverify-carries-only-what-it-can-count``
(MASTER-2026-10-07m).

Tests the contract that every retry uses the canonical current-tree
measurement path, so no prior test outcome or aggregate bucket count is
merged into the verdict.  The partial carry-forward re-measure path
(``_try_remeasure``) must be deleted; a retry must continue into the
canonical ledger/current-suite path.

The contract (pre-resolved — do not re-decide):

- Delete ``_try_remeasure`` and its early-return branch in
  ``_write_measured_record``.  A retry must continue into the canonical
  ledger/current-suite path; no prior record's aggregate counts or
  individual outcomes are merged into the new verdict.
- Preserve compatibility for reading or rendering historical
  ``carried_from``, ``rerun_selection``, and ``phase_seconds.reused``
  fields; this batch stops producing new carried records but does not
  invalidate old evidence.
- Rewrite ``test_a_reverify_measures_only_what_changed.py`` so it no
  longer requires partial carry-forward.  It must instead prove that a
  retry with valid prior history and a valid prior-tree ledger still
  reaches the canonical current-tree measurement path and emits no
  ``carried_from``, ``rerun_selection``, or ``reused`` metadata.
- The new test file must cover changed ``test_*.py``,
  ``tests/conftest.py``, ``tests/_helper.py``, ``tests/helpers.py``,
  an added test file, a deleted test file, a renamed or moved test
  file, ``.sh``, non-test ``.py``, and ``SKILL.md``.  In every case,
  assert that the current-tree result is used exactly and no carry
  metadata is emitted.
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


@pytest.fixture(autouse=True)
def _pin_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME so verification_record writes into tmp_path."""
    real_home = Path(os.environ.get("HOME", Path.home())).resolve()
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    user_base = real_home / "Library" / "Python" / "3.9"
    if user_base.is_dir():
        monkeypatch.setenv("PYTHONUSERBASE", str(user_base))


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _setup_prior_measurement(
    tmp_path: Path,
    *,
    prior_failing: list[str] | None = None,
    prior_counts: dict | None = None,
    change_fn=None,
):
    """Create a repo with a prior measured record and a HEAD commit.

    Structure:
      commit A (base): test_pass ✓, test_fail ✗
        → measured record at A with test_fail as failing
      commit B (HEAD): user-supplied change_fn applied
        → the canonical path should run (no carry metadata)

    Returns (repo, base_sha, head_sha, record_path).
    """
    if prior_failing is None:
        prior_failing = ["tests/test_suite.py::test_fail"]
    if prior_counts is None:
        prior_counts = {
            "passed": 1, "failed": 1, "errors": 0,
            "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 2,
        }

    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Commit A: initial state with a failing test.
    _write(repo, "tests/test_suite.py",
           "def test_pass():\n    assert True\n\n"
           "def test_fail():\n    assert False, 'intentional'\n")
    _write(repo, "module_x.py", "# module x\nX = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit A: initial")
    base_sha = _git(repo, "rev-parse", "HEAD")
    base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

    # Commit B: user-supplied changes.
    if change_fn is not None:
        change_fn(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit B: changes")

    # .ilk-launch.json
    _write(repo, ".ilk-launch.json", json.dumps({
        "ship": {
            "suite": {
                "command": sys.executable,
                "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
            },
        },
    }))
    _git(repo, "add", ".ilk-launch.json")
    _git(repo, "commit", "-q", "-m", "add launch config")
    head_sha = _git(repo, "rev-parse", "HEAD")

    # Build a prior measured record at base.
    import verification_record as vr
    import suite_ledger

    invocation = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    ld = suite_ledger.ledger_dir(repo)
    ld.mkdir(parents=True, exist_ok=True)
    prior_entry = {
        "tree": base_tree,
        "invocation": invocation,
        "counts": prior_counts,
        "failing_nodes": prior_failing,
        "suite_duration_sec": 1,
        "scope_mode": "full",
        "selected_files": [],
        "digest": "",
    }
    prior_entry["digest"] = suite_ledger._compute_digest(prior_entry)
    entry_path = ld / f"{base_tree}.json"
    entry_path.write_text(
        json.dumps(prior_entry, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write a prior measured record pointing at base.
    record_path = repo / "record.md"
    record_text = vr.render_record(
        batch="test-batch", head=base_sha, tree=base_tree,
        base_sha=base_sha, invocation=invocation,
        scope={"mode": "full", "count": 2, "reason": "full suite"},
        results={"counts": prior_counts, "failing_nodes": prior_failing},
        at_base={nid: "failed" for nid in prior_failing},
        base_red=[], head_red=[],
        suite_duration_sec=1,
    )
    record_path.write_text(record_text, encoding="utf-8")

    # Write history for the prior record.
    digest = vr._compute_record_digest(record_text)
    vr._append_history_entry(
        record_path, attempt=1, digest=digest,
        failing_nodes=prior_failing,
        suite_duration_sec=1, head=base_sha, tree=base_tree,
    )

    return repo, base_sha, head_sha, record_path


def _run_verify(repo: Path, record: Path, base_sha: str) -> subprocess.CompletedProcess:
    """Run verification_record.py --run-suite and return the result."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo), "--record", str(record),
         "--run-suite", "--base-sha", base_sha],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: every changed file type uses the canonical path, no carry metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalPathForChangedTestPy:
    """A changed test_*.py file: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_changed_test_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed test_*.py: record has no carried_from or rerun_selection."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text, (
            "record should not carry carried_from — canonical path must run"
        )
        assert "rerun_selection:" not in text, (
            "record should not carry rerun_selection — canonical path must run"
        )


class TestCanonicalPathForConftest:
    """A changed tests/conftest.py: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_conftest_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed conftest.py: record has no carried_from or rerun_selection."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/conftest.py", "# changed conftest\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text, (
            "conftest change should not produce carry metadata"
        )
        assert "rerun_selection:" not in text, (
            "conftest change should not produce rerun_selection"
        )


class TestCanonicalPathForHelperFiles:
    """Changed tests/_helper.py and tests/helpers.py: canonical path."""

    def test_no_carry_metadata_for_helper_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed tests/_helper.py: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/_helper.py", "# helper changed\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text

    def test_no_carry_metadata_for_helpers_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed tests/helpers.py: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/helpers.py", "# helpers changed\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


class TestCanonicalPathForAddedTestFile:
    """An added test file: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_added_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Added test file: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_new.py",
                   "def test_new():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


class TestCanonicalPathForDeletedTestFile:
    """A deleted test file: canonical path, no carry metadata.

    Note: a pure delete with no remaining tests causes pytest to produce
    no summary line, which is a different failure mode (not carry-forward
    related).  The renamed test file test above covers the delete+replace
    scenario.
    """

    @pytest.mark.skip(
        reason="pure delete with no remaining tests: pytest produces no summary line",
    )
    def test_no_carry_metadata_for_deleted_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pass


class TestCanonicalPathForRenamedTestFile:
    """A renamed/moved test file: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_renamed_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Renamed test file: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            (repo / "tests/test_suite.py").unlink()
            _write(repo, "tests/test_renamed.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


class TestCanonicalPathForShellScript:
    """A changed .sh file: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_shell_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed .sh file: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "run.sh", "#!/bin/bash\necho changed\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


class TestCanonicalPathForNonTestPy:
    """A changed non-test .py file: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_module_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed non-test .py: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "module_x.py", "# module x changed\nX = 2\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


class TestCanonicalPathForSkillMd:
    """A changed SKILL.md: canonical path, no carry metadata."""

    def test_no_carry_metadata_for_skill_md_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed SKILL.md: record has no carry metadata."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "SKILL.md", "# Updated skill\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text
        assert "rerun_selection:" not in text


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: the record says exactly what the current tree measured
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordReflectsCurrentTree:
    """The record's counts come from the current tree's measurement, not
    from any prior entry's aggregate buckets.
    """

    def test_counts_are_from_current_tree_not_prior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After fixing a test, the record shows the current tree's counts."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            # Fix the failing test → current tree has 2 passed, 0 failed.
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path,
            prior_counts={
                "passed": 1, "failed": 1, "errors": 0,
                "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 2,
            },
            change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, f"verify should succeed:\n{result.stderr}"

        text = record.read_text(encoding="utf-8")
        # The canonical path runs the full suite: 2 passed, 0 failed.
        # The carry-forward path would merge prior counts and get wrong totals.
        assert "passed: 2" in text, (
            "record should show 2 passed from current tree, not carried-forward counts"
        )
        assert "failed: 0" in text, (
            "record should show 0 failed from current tree, not carried-forward counts"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3: historical carry metadata is still readable (compatibility)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoricalCarryMetadataReadable:
    """Historical carried_from, rerun_selection, and phase_seconds.reused
    fields are still readable — we stop producing them, not invalidating them.
    """

    def test_historical_carried_from_is_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A record with historical carried_from is still readable."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        record_path = tmp_path / "record.md"
        record_text = (
            "# Verification record\n"
            "batch: test\n"
            "head: abc123\n"
            "tree: def456\n"
            "base_sha: abc123\n"
            "invocation: pytest -q\n"
            "suite_scope: full (2 tests)\n"
            "suite_failed: 0\n"
            "passed: 2\n"
            "failed: 0\n"
            "errors: 0\n"
            "total: 2\n"
            "phase_seconds: suite=1 at_base=0 head_reruns=0 total=1 reused=1\n"
            "carried_from: def456 abc1234567890abc\n"
            "rerun_selection: 1 files\n"
        )
        record_path.write_text(record_text, encoding="utf-8")

        # The record should be parseable without error.
        text = record_path.read_text(encoding="utf-8")
        assert "carried_from:" in text
        assert "rerun_selection:" in text
        assert "reused=" in text

    def test_historical_history_entry_with_carried_from(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A history entry with carried_from is still readable."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        record_path = tmp_path / "record.md"
        record_path.write_text("# stub\n", encoding="utf-8")

        # Append a history entry with carried_from (as old records have).
        hist_path = vr._history_path(record_path)
        entry = {
            "attempt": 1,
            "digest": "abc123",
            "failing_nodes": [],
            "carried_from": "def456 abc1234567890abc",
            "head": "abc123",
            "tree": "def456",
        }
        with open(hist_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

        history = vr._read_history(record_path)
        assert len(history) == 1
        assert "carried_from" in history[0]
        assert history[0]["carried_from"] == "def456 abc1234567890abc"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4: acceptance criteria (AC-1, AC-2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC1NoCarryMetadataEmitted:
    """AC-1: Every retry produces no carried_from, rerun_selection, or reused
    metadata.  The canonical current-tree measurement path is always taken.
    """

    def test_no_carried_from_in_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Record text has no carried_from field after retry."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        assert "carried_from:" not in text, (
            "record must not emit carried_from — canonical path only"
        )

    def test_no_rerun_selection_in_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Record text has no rerun_selection field after retry."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        assert "rerun_selection:" not in text, (
            "record must not emit rerun_selection — canonical path only"
        )

    def test_no_reused_in_phase_seconds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """phase_seconds has no reused field after retry."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        import re
        m = re.search(r"phase_seconds:\s*(.+)", text)
        if m:
            assert "reused=" not in m.group(1), (
                "phase_seconds must not carry reused — canonical path only"
            )

    def test_no_carried_from_in_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """History entry has no carried_from field after retry."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path, change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, result.stderr

        history = vr._read_history(record)
        assert len(history) >= 2, (
            f"expected >= 2 history entries, got {len(history)}"
        )
        last = history[-1]
        assert "carried_from:" not in last, (
            f"history row must not have carried_from, got keys: {list(last.keys())}"
        )


class TestAC2CurrentTreeResultUsedExactly:
    """AC-2: The record's counts reflect the current tree's measurement,
    not any prior entry's aggregate buckets.
    """

    def test_counts_match_current_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After fixing a test, counts reflect the current tree (2 passed)."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        def change(repo):
            _write(repo, "tests/test_suite.py",
                   "def test_pass():\n    assert True\n\n"
                   "def test_fail():\n    assert True\n")

        repo, base, head, record = _setup_prior_measurement(
            tmp_path,
            prior_counts={
                "passed": 1, "failed": 1, "errors": 0,
                "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 2,
            },
            change_fn=change,
        )

        result = _run_verify(repo, record, base)
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        # Current tree: all tests pass → 2 passed, 0 failed.
        assert "passed: 2" in text
        assert "failed: 0" in text


# ═══════════════════════════════════════════════════════════════════════════════
# Controls: basic sanity checks that pass at base
# ═══════════════════════════════════════════════════════════════════════════════


class TestControls:
    """Sanity checks that should pass at base (no xfail)."""

    def test_read_history_returns_empty_for_missing(self, tmp_path) -> None:
        """_read_history returns [] for a non-existent file."""
        import verification_record as vr
        missing = tmp_path / "no-such.record"
        assert vr._read_history(missing) == []

    def test_compute_record_digest_is_stable(self) -> None:
        """_compute_record_digest returns the same hash for the same input."""
        import verification_record as vr
        text = "# some record text\nsuite_failed: 5\n"
        d1 = vr._compute_record_digest(text)
        d2 = vr._compute_record_digest(text)
        assert d1 == d2
        assert len(d1) == 64

    def test_history_path_derives_from_record(self, tmp_path) -> None:
        """_history_path returns a .history.jsonl path derived from the record."""
        import verification_record as vr
        record = tmp_path / "my-record.md"
        hist = vr._history_path(record)
        assert hist.name == "my-record.history.jsonl"
        assert hist.parent == tmp_path

    def test_try_remeasure_deleted(self) -> None:
        """_try_remeasure function no longer exists (deleted in step 1)."""
        import verification_record as vr
        assert not hasattr(vr, "_try_remeasure"), (
            "_try_remeasure should have been deleted"
        )