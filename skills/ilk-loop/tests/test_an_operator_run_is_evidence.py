"""Tests for "an operator run is evidence".

AC-1: --from-suite-output writes a full record (same as --run-suite).
AC-2: bindings refuse with a named reason:
  AC-2a: --suite-head must equal HEAD.
  AC-2b: worktree must be clean (tracked files).
  AC-2c: file mtime must be >= HEAD's committer time.
  AC-2d: file with no summary line refuses.
  AC-2e: file with "PARTIAL RUN" banner refuses.
  AC-2f: collected count below --collect-only count refuses.
AC-3: invocation must match after normalisation (strip env prefix + output-only flags).
  AC-3a: -k in the file refuses even if configured.
AC-4: provenance: suite_source: operator:<path> + sha256 digest.
AC-5: --run-suite behaviour unchanged (control — passes today).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402
import ship_audit as sa           # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

BASE_SHA = "abc1234" * 6  # 40-char hex
HEAD_SHA = "def5678" * 6
TREE_SHA = "fedcba0" * 6
CONFIGURED_INVOCATION = "python3 -m pytest --timeout=60 --timeout-method=signal -n 8 --dist loadfile"

# A well-formed pytest summary line.
PASSING_OUTPUT = textwrap.dedent("""\
    ========================= test session starts ==========================
    platform darwin -- Python 3.12.0, pytest-8.0.0
    collected 100 items

    tests/test_a.py ..........                                         [ 10%]
    tests/test_b.py ..........                                         [ 20%]

    ========================= 100 passed in 5.00s ==========================
""")

FAILING_OUTPUT = textwrap.dedent("""\
    ========================= test session starts ==========================
    platform darwin -- Python 3.12.0, pytest-8.0.0
    collected 100 items

    tests/test_a.py ..F......                                          [ 10%]
    tests/test_b.py ..........                                         [ 20%]

    ========================== FAILURES ===================================
    _________________________ test_x _____________________________________

    FAILED tests/test_a.py::test_x

    ================= 1 failed, 99 passed in 6.00s ========================
""")

PARTIAL_OUTPUT = textwrap.dedent("""\
    ========================= PARTIAL RUN ================================
    collected 50 items

    tests/test_a.py ..........                                         [ 20%]

    ========================= 50 passed in 3.00s ==========================
""")


class MockCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a tmp git repo with one commit so HEAD resolves."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.name", "test"],
                   cwd=repo, capture_output=True, timeout=30)
    # An initial commit so HEAD exists.
    (repo / "placeholder").write_text("init")
    subprocess.run(["git", "add", "placeholder"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                   cwd=repo, capture_output=True, timeout=30)
    return repo


def _write_suite_output(tmp_path: Path, content: str, filename: str = "suite-output.txt") -> Path:
    """Write a canned suite output file and return its path."""
    out = tmp_path / filename
    out.write_text(content, encoding="utf-8")
    return out


def _make_passing_mock() -> MagicMock:
    """Mock subprocess.run that succeeds for git + at-base + head-rerun."""
    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if cmd_str.startswith("python3 -m pytest"):
            return MockCompletedProcess(0, "", PASSING_OUTPUT)
        return MockCompletedProcess(0, "", "")
    return MagicMock(side_effect=mock_run)


# ── AC-1: --from-suite-output writes a full record ──────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_from_suite_output_writes_record(tmp_path: Path) -> None:
    """--from-suite-output reads the file through the same parser and writes
    a complete record, including at-base reruns."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, FAILING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc == 0
    text = record.read_text()
    assert "verified_head:" in text
    assert "suite_failed:" in text
    assert "## At-base rerun" in text


# ── AC-2a: --suite-head must equal HEAD ─────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_suite_head_must_equal_head(tmp_path: Path) -> None:
    """--suite-head that does not match HEAD refuses with a named reason."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", "0000000" * 6,  # wrong sha
        ])

    assert rc != 0


# ── AC-2b: worktree must be clean ───────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_dirty_worktree_refuses(tmp_path: Path) -> None:
    """A dirty tracked tree refuses with a named reason."""
    repo = _init_repo(tmp_path)
    # Create a dirty tracked file.
    (repo / "placeholder").write_text("dirty")
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-2c: file mtime >= HEAD's committer time ──────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_old_file_mtime_refuses(tmp_path: Path) -> None:
    """A file whose mtime is before HEAD's committer time refuses."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)
    # Set mtime to epoch 0 — older than any commit.
    import os
    os.utime(out_file, (0, 0))

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-2d: no summary line refuses ──────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_no_summary_line_refuses(tmp_path: Path) -> None:
    """A file with no pytest/vitest summary line refuses (parser raises)."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, "just some random text\nno summary here\n")

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-2e: "PARTIAL RUN" banner refuses ─────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_partial_run_banner_refuses(tmp_path: Path) -> None:
    """A file carrying the 'PARTIAL RUN' banner refuses."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PARTIAL_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-2f: collected count below --collect-only count refuses ────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_low_collected_count_refuses(tmp_path: Path) -> None:
    """A file whose collected count is below the configured suite's
    --collect-only count refuses."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    # Output says "collected 10 items" but --collect-only would report 100.
    low_output = textwrap.dedent("""\
        ========================= test session starts ==========================
        collected 10 items

        ========================= 10 passed in 1.00s ==========================
    """)
    out_file = _write_suite_output(tmp_path, low_output)

    # Mock subprocess: the --collect-only call returns 100 items.
    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if "--collect-only" in cmd_str:
            return MockCompletedProcess(0, "collected 100 items\n", "")
        return MockCompletedProcess(0, "", "")

    mock = MagicMock(side_effect=mock_run)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-3: invocation normalisation ──────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_invocation_strips_env_prefix(tmp_path: Path) -> None:
    """A leading VAR=value env prefix is stripped before comparison."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        # The file's invocation has an env prefix; the configured one does not.
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc == 0


@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_invocation_strips_output_only_flags(tmp_path: Path) -> None:
    """Output-only flags (-q, -v, --color=auto, etc.) are stripped."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        # The operator added -q and --color=no; both are output-only.
        operator_invocation = CONFIGURED_INVOCATION + " -q --color=no"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc == 0


# ── AC-3a: -k in the file refuses ───────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_selection_flag_k_refuses(tmp_path: Path) -> None:
    """A -k flag in the file refuses, even if it's also configured."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, PASSING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        # The configured invocation has -k (selection-changing).
        bad_invocation = CONFIGURED_INVOCATION + " -k test_x"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", bad_invocation,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-4: provenance ────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_operator_provenance_in_record(tmp_path: Path) -> None:
    """The record carries suite_source: operator:<path> and sha256 digest."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()
    out_file = _write_suite_output(tmp_path, FAILING_OUTPUT)

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--batch", "test-batch",
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc == 0
    text = record.read_text()
    assert f"suite_source: operator:{out_file.resolve()}" in text
    expected_digest = hashlib.sha256(
        out_file.read_bytes()
    ).hexdigest()
    assert f"suite_source_sha256: {expected_digest}" in text


@pytest.mark.xfail(strict=True, reason="no operator ingest")
def test_tool_run_has_tool_provenance(tmp_path: Path) -> None:
    """A --run-suite record carries suite_source: tool (not operator)."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(sa, "_resolve_expected_invocation",
                   lambda p: CONFIGURED_INVOCATION)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])
        mp.delenv("ILK_WORKER_SESSION", raising=False)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--run-suite",
        ])

    assert rc == 0
    text = record.read_text()
    assert "suite_source: tool" in text


# ── AC-5: --run-suite behaviour unchanged (control) ─────────────────────────

def test_run_suite_writes_record_today(tmp_path: Path) -> None:
    """--run-suite still writes a record today.  This is a control —
    the behaviour passes before and after the fix."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    mock = _make_passing_mock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(sa, "_resolve_expected_invocation",
                   lambda p: CONFIGURED_INVOCATION)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])
        mp.delenv("ILK_WORKER_SESSION", raising=False)

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--run-suite",
        ])

    assert rc == 0
    text = record.read_text()
    assert "verified_head:" in text
    assert "suite_failed:" in text