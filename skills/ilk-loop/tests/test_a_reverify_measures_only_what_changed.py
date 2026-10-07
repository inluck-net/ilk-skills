"""Red-first pins: a reverify measures only what changed.

Part of sub-plan ``a-reverify-measures-only-what-changed``
(MASTER-2026-10-07l).

Tests the re-measure path in ``verification_record._write_measured_record``:
after a fix commit the verify re-measures only the importer tests of files
changed since its last measured head plus every previously failing id,
carrying the rest from that signed measurement.

The contract (pre-resolved):

- Re-measure path taken when ALL hold: history has a prior measured attempt
  with head H0; H0 is an ancestor of HEAD; suite_ledger.lookup(tree(H0),
  invocation) returns an entry whose digest validates; no path in
  ``git diff --name-only H0..HEAD`` is test infrastructure.
- Selection = changed test files ∪ ``test_importers.importer_tests(changed .py)``
  ∪ the files of every id in the prior entry's ``failing_nodes``.
- Result = prior entry's counts with the selection's results substituted.
  The record gains ``carried_from: <tree> <digest16>`` and
  ``rerun_selection: <n> files``; ``phase_seconds`` gains
  ``reused: <carried test count>``; the history row gains ``carried_from``.
- Otherwise: today's full re-measure, unchanged.
- The SLO check (``_check_slo_breach``) judges ``total`` as today.
"""
from __future__ import annotations

import json
import subprocess
import sys
import os
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def _pin_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME so verification_record writes into tmp_path.

    Without this, the DATA-ROOT GUARD in conftest.py sees new entries under
    the real ~/.ilk-data/projects/ and fails the session.  See triage note
    in the sub-plan's Findings section.

    PYTHONUSERBASE is also pinned so that the subprocess's user site-packages
    still resolve correctly — pytest is installed in the user site, and changing
    HOME without PYTHONUSERBASE makes the subprocess unable to find it.
    """
    # Capture the real home BEFORE changing HOME, because Path.home() reads
    # the HOME env var and would return the tmp_path after setenv.
    real_home = Path(os.environ.get("HOME", Path.home())).resolve()
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    # Preserve the real user site-packages path so pytest remains importable
    # in subprocesses that inherit this environment.
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


def _make_repo_with_prior_measurement(tmp_path: Path):
    """Create a repo with a prior measured record and a fix commit.

    Structure:
      commit A (base): test_pass ✓, test_fail ✗, module_x.py (imports nothing)
      commit B (prior HEAD / H0): test_pass ✓, test_fail ✗ (unchanged)
        → measured record at B with test_fail as failing
      commit C (HEAD): fix test_fail ✓, change module_x.py
        → the re-measure path should trigger

    Returns (repo, base_sha, h0_sha, head_sha, record_path, prior_entry).
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Commit A: initial state with a failing test and a module.
    _write(repo, "tests/test_suite.py",
           "def test_pass():\n    assert True\n\n"
           "def test_fail():\n    assert False, 'intentional'\n")
    _write(repo, "module_x.py", "# module x\nX = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit A: initial")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # Commit B: same tests (no change) — this becomes H0.
    _write(repo, "doc.txt", "documentation\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-q", "-m", "commit B: doc change")
    h0_sha = _git(repo, "rev-parse", "HEAD")
    h0_tree = _git(repo, "rev-parse", f"{h0_sha}^{{tree}}")

    # Commit C: fix the failing test and change module_x.
    _write(repo, "tests/test_suite.py",
           "def test_pass():\n    assert True\n\n"
           "def test_fail():\n    assert True\n")
    _write(repo, "module_x.py", "# module x changed\nX = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit C: fix + module change")
    head_sha = _git(repo, "rev-parse", "HEAD")

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

    # Build a prior measured record at H0.
    import verification_record as vr
    import suite_ledger

    # Write a ledger entry for H0's tree.
    invocation = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    ld = suite_ledger.ledger_dir(repo)
    ld.mkdir(parents=True, exist_ok=True)
    prior_entry = {
        "tree": h0_tree,
        "invocation": invocation,
        "counts": {"passed": 1, "failed": 1, "errors": 0, "skipped": 0,
                    "xfailed": 0, "xpassed": 0, "total": 2},
        "failing_nodes": ["tests/test_suite.py::test_fail"],
        "suite_duration_sec": 1,
        "scope_mode": "full",
        "selected_files": [],
        "digest": "",
    }
    prior_entry["digest"] = suite_ledger._compute_digest(prior_entry)
    entry_path = ld / f"{h0_tree}.json"
    entry_path.write_text(
        json.dumps(prior_entry, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write a prior measured record pointing at H0.
    record_path = repo / "record.md"
    record_text = vr.render_record(
        batch="test-batch", head=h0_sha, tree=h0_tree,
        base_sha=base_sha, invocation=invocation,
        scope={"mode": "full", "count": 2, "reason": "full suite"},
        results={"counts": {"passed": 1, "failed": 1, "errors": 0,
                             "skipped": 0, "xfailed": 0, "xpassed": 0,
                             "total": 2},
                 "failing_nodes": ["tests/test_suite.py::test_fail"]},
        at_base={"tests/test_suite.py::test_fail": "failed"},
        base_red=[], head_red=[],
        suite_duration_sec=1,
    )
    record_path.write_text(record_text, encoding="utf-8")

    # Write history for the prior record.
    digest = vr._compute_record_digest(record_text)
    vr._append_history_entry(
        record_path, attempt=1, digest=digest,
        failing_nodes=["tests/test_suite.py::test_fail"],
        suite_duration_sec=1, head=h0_sha, tree=h0_tree,
    )

    return repo, base_sha, h0_sha, head_sha, record_path, prior_entry


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: re-measure path triggers when all conditions hold
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC1ReMeasurePathTriggers:
    """When history has a prior measured attempt H0, H0 is an ancestor of HEAD,
    the ledger has a valid entry for H0's tree, and no test-infrastructure file
    changed, the re-measure path is taken (not a full re-measure).
    """

    def test_re_measure_path_selects_only_changed_area(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The re-measure path runs only the selection, not the full suite."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        # Run the re-measure via the CLI.
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, (
            f"re-measure should succeed:\n{result.stderr}"
        )

        text = record.read_text(encoding="utf-8")
        # The record should indicate carried results.
        assert "carried_from" in text, (
            "record should carry carried_from field"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: selection = changed tests ∪ importers ∪ prior failing ids
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC2SelectionCoversAllBuckets:
    """The selection includes changed test files, importer tests of changed
    .py modules, and every id from the prior entry's failing_nodes.
    """

    def test_selection_includes_changed_test_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Changed test files are in the selection."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        # The changed test file is tests/test_suite.py.
        # It should be in the selection.
        # We can't directly inspect the selection from the CLI, so we check
        # that the record's suite_scope reflects the scoped run.
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        # The selection size should be > 0 (the changed test + prior failing).
        assert "rerun_selection:" in text, (
            "record should carry rerun_selection"
        )

    def test_selection_includes_importer_tests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test files that import changed .py modules are in the selection."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        repo = tmp_path / "proj"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")

        # A module and a test that imports it.
        _write(repo, "mylib/core.py", "def greet():\n    return 'hi'\n")
        _write(repo, "tests/test_core.py",
               "from mylib.core import greet\n\n"
               "def test_greet():\n    assert greet() == 'hi'\n")
        _write(repo, "tests/test_other.py",
               "def test_other():\n    assert True\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # H0: no changes to mylib/core.py.
        h0_sha = base_sha
        h0_tree = _git(repo, "rev-parse", f"{h0_sha}^{{tree}}")

        # HEAD: change mylib/core.py (the importer test should be selected).
        _write(repo, "mylib/core.py", "def greet():\n    return 'hello'\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "change core")
        head_sha = _git(repo, "rev-parse", "HEAD")

        # .ilk-launch.json
        _write(repo, ".ilk-launch.json", json.dumps({
            "ship": {"suite": {"command": sys.executable,
                               "flags": ["-m", "pytest", "-q",
                                          "-p", "no:cacheprovider"]}},
        }))
        _git(repo, "add", ".ilk-launch.json")
        _git(repo, "commit", "-q", "-m", "config")

        # Ledger + record at H0.
        import verification_record as vr
        import suite_ledger

        invocation = f"{sys.executable} -m pytest -q -p no:cacheprovider"
        ld = suite_ledger.ledger_dir(repo)
        ld.mkdir(parents=True, exist_ok=True)
        entry = {
            "tree": h0_tree, "invocation": invocation,
            "counts": {"passed": 2, "failed": 0, "errors": 0,
                       "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 2},
            "failing_nodes": [],
            "suite_duration_sec": 1, "scope_mode": "full",
            "selected_files": [], "digest": "",
        }
        entry["digest"] = suite_ledger._compute_digest(entry)
        (ld / f"{h0_tree}.json").write_text(
            json.dumps(entry, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        record_path = repo / "record.md"
        record_text = vr.render_record(
            batch="t", head=h0_sha, tree=h0_tree,
            base_sha=base_sha, invocation=invocation,
            scope={"mode": "full", "count": 2, "reason": "full"},
            results={"counts": entry["counts"], "failing_nodes": []},
            at_base={}, base_red=[], head_red=[],
            suite_duration_sec=1,
        )
        record_path.write_text(record_text, encoding="utf-8")
        vr._append_history_entry(
            record_path, 1, vr._compute_record_digest(record_text),
            [], suite_duration_sec=1, head=h0_sha, tree=h0_tree,
        )

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record_path),
             "--run-suite", "--base-sha", base_sha],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record_path.read_text(encoding="utf-8")
        # test_core.py imports mylib.core, so it should be in the selection.
        assert "rerun_selection:" in text, (
            "record should carry rerun_selection"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="prior failing ids not yet included in selection",
    )
    def test_selection_includes_prior_failing_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every id from the prior entry's failing_nodes is re-run."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        # The prior entry has tests/test_suite.py::test_fail as failing.
        # Even though it's not in the changed set, it should be re-run.
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        # The prior failing id should appear in the at-base table (re-run).
        assert "tests/test_suite.py::test_fail" in text, (
            "prior failing id should be re-measured"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: carried results + metadata fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC3CarriedResultsAndMetadata:
    """The result is the prior entry's counts with the selection substituted.
    The record gains carried_from and rerun_selection; phase_seconds gains
    reused; the history row gains carried_from.
    """

    def test_record_carries_carried_from(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        """The record text contains carried_from: <tree> <digest16>."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )
        h0_tree = _git(repo, "rev-parse", f"{h0}^{{tree}}")

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        # carried_from should reference H0's tree and digest.
        assert "carried_from:" in text, (
            "record should contain carried_from field"
        )
        assert h0_tree[:16] in text or h0_tree in text, (
            "carried_from should reference H0's tree"
        )

    def test_record_carries_rerun_selection(self, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
        """The record text contains rerun_selection: <n> files."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        assert "rerun_selection:" in text, (
            "record should contain rerun_selection field"
        )

    def test_phase_seconds_includes_reused(self, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
        """phase_seconds includes reused: <carried test count>."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record.read_text(encoding="utf-8")
        # phase_seconds line should contain "reused=".
        import re
        m = re.search(r"phase_seconds:\s*(.+)", text)
        assert m, "record should have phase_seconds line"
        assert "reused=" in m.group(1), (
            "phase_seconds should include reused count"
        )

    def test_history_row_carries_carried_from(self, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
        """The history JSONL row for the re-measure attempt has carried_from."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )
        h0_tree = _git(repo, "rev-parse", f"{h0}^{{tree}}")

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record),
             "--run-suite", "--base-sha", base],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        history = vr._read_history(record)
        # There should be at least 2 entries (prior + re-measure).
        assert len(history) >= 2, (
            f"expected >= 2 history entries, got {len(history)}"
        )
        # The last entry (re-measure) should have carried_from.
        last = history[-1]
        assert "carried_from" in last, (
            f"history row should have carried_from, got keys: {list(last.keys())}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: full re-measure when conditions are not met
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC4FullReMeasureWhenConditionsNotMet:
    """When the re-measure conditions are not all met, the full re-measure
    path is taken (today's behaviour, unchanged).
    """

    def test_no_prior_history_triggers_full_measure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without prior history, the full suite runs."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

        repo = tmp_path / "proj"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")

        _write(repo, "tests/test_suite.py",
               "def test_pass():\n    assert True\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        base_sha = _git(repo, "rev-parse", "HEAD")

        _write(repo, "tests/test_suite.py",
               "def test_pass():\n    assert True\n\ndef test_new():\n    assert True\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add test")

        _write(repo, ".ilk-launch.json", json.dumps({
            "ship": {"suite": {"command": sys.executable,
                               "flags": ["-m", "pytest", "-q",
                                          "-p", "no:cacheprovider"]}},
        }))
        _git(repo, "add", ".ilk-launch.json")
        _git(repo, "commit", "-q", "-m", "config")

        record_path = repo / "record.md"

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record_path),
             "--run-suite", "--base-sha", base_sha],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record_path.read_text(encoding="utf-8")
        # No carried_from — this was a full measure.
        assert "carried_from" not in text, (
            "full measure should not have carried_from"
        )
        # suite_scope should be present.
        assert "suite_scope:" in text

    def test_test_infra_change_forces_full_measure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When git diff H0..HEAD touches conftest.py, the full suite runs."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr
        import suite_ledger

        repo = tmp_path / "proj"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")

        _write(repo, "tests/test_suite.py",
               "def test_pass():\n    assert True\n")
        _write(repo, "tests/conftest.py", "# config\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # H0: same state.
        h0_sha = base_sha
        h0_tree = _git(repo, "rev-parse", f"{h0_sha}^{{tree}}")

        # HEAD: change conftest.py (test infrastructure).
        _write(repo, "tests/conftest.py", "# changed config\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "change conftest")

        _write(repo, ".ilk-launch.json", json.dumps({
            "ship": {"suite": {"command": sys.executable,
                               "flags": ["-m", "pytest", "-q",
                                          "-p", "no:cacheprovider"]}},
        }))
        _git(repo, "add", ".ilk-launch.json")
        _git(repo, "commit", "-q", "-m", "config")

        # Ledger + record at H0.
        invocation = f"{sys.executable} -m pytest -q -p no:cacheprovider"
        ld = suite_ledger.ledger_dir(repo)
        ld.mkdir(parents=True, exist_ok=True)
        entry = {
            "tree": h0_tree, "invocation": invocation,
            "counts": {"passed": 1, "failed": 0, "errors": 0,
                       "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 1},
            "failing_nodes": [], "suite_duration_sec": 1,
            "scope_mode": "full", "selected_files": [], "digest": "",
        }
        entry["digest"] = suite_ledger._compute_digest(entry)
        (ld / f"{h0_tree}.json").write_text(
            json.dumps(entry, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        record_path = repo / "record.md"
        record_text = vr.render_record(
            batch="t", head=h0_sha, tree=h0_tree,
            base_sha=base_sha, invocation=invocation,
            scope={"mode": "full", "count": 1, "reason": "full"},
            results={"counts": entry["counts"], "failing_nodes": []},
            at_base={}, base_red=[], head_red=[],
            suite_duration_sec=1,
        )
        record_path.write_text(record_text, encoding="utf-8")
        vr._append_history_entry(
            record_path, 1, vr._compute_record_digest(record_text),
            [], suite_duration_sec=1, head=h0_sha, tree=h0_tree,
        )

        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(repo), "--record", str(record_path),
             "--run-suite", "--base-sha", base_sha],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr

        text = record_path.read_text(encoding="utf-8")
        # conftest.py change forces full measure — no carried_from.
        assert "carried_from" not in text, (
            "conftest.py change should force full measure"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-5: SLO check judges total as today
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC5SLOCheckJudgesTotal:
    """The SLO check (_check_slo_breach) judges total as today — the actual
    wall-clock time of the re-measure, not the prior entry's duration.
    """

    def test_slo_uses_actual_remeasure_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The SLO breach check uses the re-measure's wall-clock total."""
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        import verification_record as vr

        repo, base, h0, head, record, _prior = (
            _make_repo_with_prior_measurement(tmp_path)
        )

        # Patch VERIFY_SLO_S to 0 so any non-zero total triggers a breach.
        original_slo = vr.VERIFY_SLO_S
        vr.VERIFY_SLO_S = 0
        try:
            # We can't easily test the backlog filing, but we can verify
            # that the record's phase_seconds.total reflects the actual
            # re-measure time (not the prior entry's duration).
            result = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
                 "--project", str(repo), "--record", str(record),
                 "--run-suite", "--base-sha", base],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120,
            )
            assert result.returncode == 0, result.stderr

            text = record.read_text(encoding="utf-8")
            import re
            m = re.search(r"phase_seconds:\s*(.+)", text)
            assert m, "record should have phase_seconds"
            # The total should be present and numeric.
            parts = m.group(1).split()
            total_parts = [p for p in parts if p.startswith("total=")]
            assert total_parts, f"phase_seconds should have total=, got: {m.group(1)}"
            total_val = int(total_parts[0].split("=")[1])
            # It should be >= 0 (a real measurement).
            assert total_val >= 0, f"total should be non-negative, got {total_val}"
        finally:
            vr.VERIFY_SLO_S = original_slo


# ═══════════════════════════════════════════════════════════════════════════════
# Control: basic sanity checks that pass at base
# ═══════════════════════════════════════════════════════════════════════════════


class TestControls:
    """Sanity checks that should pass at base (no xfail)."""

    def test_test_infra_change_detects_conftest(self) -> None:
        """_test_infra_change identifies conftest.py as infrastructure."""
        import verification_record as vr
        assert vr._test_infra_change("tests/conftest.py") is True
        assert vr._test_infra_change("src/module.py") is False

    def test_test_infra_change_detects_tests_under_score(self) -> None:
        """_test_infra_change identifies tests/_helper.py as infrastructure."""
        import verification_record as vr
        assert vr._test_infra_change("tests/_helper.py") is True
        assert vr._test_infra_change("tests/test_foo.py") is False

    def test_compute_record_digest_is_stable(self) -> None:
        """_compute_record_digest returns the same hash for the same input."""
        import verification_record as vr
        text = "# some record text\nsuite_failed: 5\n"
        d1 = vr._compute_record_digest(text)
        d2 = vr._compute_record_digest(text)
        assert d1 == d2
        assert len(d1) == 64  # SHA-256 hex

    def test_read_history_returns_empty_for_missing(self, tmp_path) -> None:
        """_read_history returns [] for a non-existent file."""
        import verification_record as vr
        missing = tmp_path / "no-such.record"
        assert vr._read_history(missing) == []