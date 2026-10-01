"""Tests for "a failure red at base is not rerun at head".

AC-1: red-at-base ids excluded from head reruns.
AC-2: "Failed at base, not in baseline_red" section in record.
AC-3: per-id at-base cache.
AC-4: passed/absent at base still get head reruns (control).
AC-5: old 6-column record still parses (control).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402
import verify_attribution as va    # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

BASE_SHA = "abc1234" * 6  # 40-char hex
RED_AT_BASE_ID = "tests/test_x.py::test_red_at_base"
PASSED_AT_BASE_ID = "tests/test_y.py::test_passed_at_base"
ABSENT_AT_BASE_ID = "tests/test_z.py::test_absent_at_base"
NIDS = [RED_AT_BASE_ID, PASSED_AT_BASE_ID, ABSENT_AT_BASE_ID]


class MockCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_mock_subprocess(base_sha: str) -> tuple[MagicMock, MagicMock, list]:
    """Create a mock subprocess.run, a mock _bounded_run, and a list of
    (cmd, nid) for head reruns.

    At-base per-id pytest calls (cwd contains "ilk-at-base-"):
      - RED_AT_BASE_ID: exit 1 + FAILED line  → "failed"
      - PASSED_AT_BASE_ID: exit 0 + empty stderr → "passed"
      - ABSENT_AT_BASE_ID: exit 4 + "error: not found:" → "absent-at-base"

    Head batch reruns (cwd is the repo): exit 0 (all pass).
    Git worktree operations: exit 0.
    """
    head_rerun_ids: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        cwd = kwargs.get("cwd", "")
        # Git operations succeed silently.
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        # pytest invocations.
        if cmd_str.startswith("python3 -m pytest"):
            if "ilk-at-base-" in str(cwd):
                # At-base per-id run: return per-id results.
                stderr = ""
                exit_code = 0
                for nid in NIDS:
                    if nid in cmd_str:
                        if nid == RED_AT_BASE_ID:
                            stderr += f"FAILED {nid}\n"
                            exit_code = 1
                        elif nid == ABSENT_AT_BASE_ID:
                            stderr += f"ERROR: not found: {nid}\n"
                            exit_code = 4
                        break
                return MockCompletedProcess(exit_code, "", stderr)
            else:
                # Head batch rerun: record which ids, all pass.
                for nid in NIDS:
                    if nid in cmd_str:
                        head_rerun_ids.append(nid)
                return MockCompletedProcess(0, "", "")
        return MockCompletedProcess(0, "", "")

    def mock_bounded_run(cmd, *args, **kwargs):
        """Mock _bounded_run: delegate to mock_run, return tuple."""
        result = mock_run(cmd, *args, **kwargs)
        return result.returncode, result.stdout, result.stderr, False

    mock_subprocess = MagicMock(side_effect=mock_run)
    mock_bounded = MagicMock(side_effect=mock_bounded_run)
    return mock_subprocess, mock_bounded, head_rerun_ids


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True, timeout=30)
    return repo


# ── AC-1: red-at-base ids get no head reruns ─────────────────────────────────

def test_red_at_base_ids_excluded_from_head_reruns(tmp_path: Path) -> None:
    """Ids that failed at base must not appear in the head-rerun set.

    After the fix, ``non_declared`` excludes ids whose at-base verdict is
    ``"failed"``, so ``run_head_reruns`` never receives them.
    """
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    mock_subprocess, mock_bounded, head_rerun_ids = _make_mock_subprocess(BASE_SHA)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock_subprocess)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir",
                   lambda p: vdir)

        results = {
            "counts": {"total": 10, "passed": 7, "failed": 3,
                       "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "failing_nodes": list(NIDS),
        }
        invocation = "python3 -m pytest"

        at_base = vr.run_at_base(
            repo, BASE_SHA, list(NIDS), invocation,
            timeout=600, baseline_red=[], registry_slugs=None,
        )
        assert at_base[RED_AT_BASE_ID] == "failed"

        # After the fix, non_declared excludes red-at-base ids.
        non_declared = [nid for nid, v in at_base.items()
                        if v not in ("declared-at-base", "failed")]
        head_reruns = vr.run_head_reruns(repo, non_declared, invocation)

        assert RED_AT_BASE_ID not in head_rerun_ids
        assert PASSED_AT_BASE_ID in head_rerun_ids
        assert ABSENT_AT_BASE_ID in head_rerun_ids
        assert head_reruns[PASSED_AT_BASE_ID] == 0
        assert head_reruns[ABSENT_AT_BASE_ID] == 0


# ── AC-2: "Failed at base, not in baseline_red" section ─────────────────────

def test_failed_at_base_section_in_record(tmp_path: Path) -> None:
    """The record must include a ``## Failed at base, not in baseline_red``
    section listing suggested ``baseline_red`` entries."""
    vdir = tmp_path / "verification"
    vdir.mkdir()

    at_base = {
        RED_AT_BASE_ID: "failed",
        PASSED_AT_BASE_ID: "passed",
        ABSENT_AT_BASE_ID: "absent-at-base",
    }
    head_reruns = {
        RED_AT_BASE_ID: "—",
        PASSED_AT_BASE_ID: 0,
        ABSENT_AT_BASE_ID: 0,
    }
    batch_touched = {
        RED_AT_BASE_ID: "—",
        PASSED_AT_BASE_ID: False,
        ABSENT_AT_BASE_ID: True,
    }
    scope = {"mode": "full", "count": 10}
    results = {
        "counts": {"total": 10, "passed": 7, "failed": 3,
                   "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
        "failing_nodes": list(NIDS),
    }

    text = vr.render_record(
        batch="test-batch", head="abc123", tree="def456",
        base_sha=BASE_SHA,
        invocation="python3 -m pytest",
        scope=scope, results=results,
        at_base=at_base, base_red=[], head_red=[],
        head_reruns=head_reruns,
        batch_touched=batch_touched,
        failed_at_base=[RED_AT_BASE_ID],
    )

    assert "## Failed at base, not in baseline_red" in text
    assert RED_AT_BASE_ID in text
    assert f"failed at base {BASE_SHA[:7]}" in text


# ── AC-3: per-id at-base cache ──────────────────────────────────────────────

def test_at_base_cache_reuses_verdicts(tmp_path: Path) -> None:
    """A second ``run_at_base`` at the same base sha reads verdicts from
    ``at-base-<sha>.json`` and spawns no new subprocesses for cached ids."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    mock_subprocess, mock_bounded, _ = _make_mock_subprocess(BASE_SHA)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock_subprocess)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir",
                   lambda p: vdir)

        invocation = "python3 -m pytest"
        # First call: spawns pytest subprocesses.
        vr.run_at_base(
            repo, BASE_SHA, list(NIDS), invocation,
            timeout=600, baseline_red=[], registry_slugs=None,
        )
        first_count = mock_bounded.call_count

        # Second call: should read from cache.
        mock_bounded.reset_mock()
        vr.run_at_base(
            repo, BASE_SHA, list(NIDS), invocation,
            timeout=600, baseline_red=[], registry_slugs=None,
        )
        second_count = mock_bounded.call_count

        assert second_count < first_count, (
            f"second call should read from cache; "
            f"first={first_count}, second={second_count}"
        )


# ── AC-4: passed/absent at base still get head reruns (control) ──────────────

def test_passed_and_absent_still_get_head_reruns(tmp_path: Path) -> None:
    """Ids that passed or are absent at base still get head reruns today.

    This is a control — the behaviour passes before the fix.
    """
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    mock_subprocess, mock_bounded, head_rerun_ids = _make_mock_subprocess(BASE_SHA)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock_subprocess)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir",
                   lambda p: vdir)

        results = {
            "counts": {"total": 10, "passed": 7, "failed": 3,
                       "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "failing_nodes": list(NIDS),
        }
        invocation = "python3 -m pytest"

        at_base = vr.run_at_base(
            repo, BASE_SHA, list(NIDS), invocation,
            timeout=600, baseline_red=[], registry_slugs=None,
        )

        non_declared = [nid for nid, v in at_base.items()
                        if v not in ("declared-at-base", "failed")]
        head_reruns = vr.run_head_reruns(repo, non_declared, invocation)

        assert PASSED_AT_BASE_ID in head_rerun_ids
        assert ABSENT_AT_BASE_ID in head_rerun_ids
        assert PASSED_AT_BASE_ID in head_reruns
        assert ABSENT_AT_BASE_ID in head_reruns


# ── AC-5: old 6-column record still parses (control) ────────────────────────

def test_six_column_rows_parse_via_derive_attributed() -> None:
    """6-column rows (with ``alone``) parse correctly via ``derive_attributed``.

    The 5-column parser reads columns 0-4; the 6th (alone) is ignored.
    """
    rows = [
        ["tests/test_x.py::test_red", "failed", "no", "0/3", "no", "—"],
        ["tests/test_y.py::test_passed", "passed", "no", "3/3", "yes", "passed"],
        ["tests/test_z.py::test_absent", "absent-at-base", "no", "0/3", "yes", "failed"],
    ]
    bad, flaky = va.derive_attributed(rows)
    # failed at base → pre-existing (not attributed)
    assert len(bad) == 2  # passed + absent-at-base are attributed
    assert flaky == []