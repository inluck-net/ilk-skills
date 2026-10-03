"""Tests for "a collect count it cannot read refuses".

AC-1: parse_collect_count reads pytest's real -q summary forms.
AC-2: parse_collect_count returns None for unparseable output.
AC-3: collected-count floor fires when actual < expected.
AC-4: unparseable collect output refuses with a named reason.
AC-5: the collect command carries exactly one -q.
AC-6 (control): a passing suite with matching count writes a record.
"""
from __future__ import annotations

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


# ── helpers ──────────────────────────────────────────────────────────────────

BASE_SHA = "abc1234" * 6  # 40-char hex
HEAD_SHA = "def5678" * 6
TREE_SHA = "fedcba0" * 6
CONFIGURED_INVOCATION = "python3 -m pytest --timeout=60 --timeout-method=signal -n 8 --dist loadfile"


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
    (repo / "placeholder").write_text("init")
    subprocess.run(["git", "add", "placeholder"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                   cwd=repo, capture_output=True, timeout=30)
    return repo


PASSING_OUTPUT = textwrap.dedent("""\
    ========================= test session starts ==========================
    platform darwin -- Python 3.12.0, pytest-8.0.0
    collected 100 items

    tests/test_a.py ..........                                         [ 10%]
    tests/test_b.py ..........                                         [ 20%]

    ========================= 100 passed in 5.00s ==========================
""")


# ── AC-1: parse_collect_count reads pytest's real -q summary forms ────────────

@pytest.mark.xfail(strict=True, reason="parse_collect_count does not exist yet")
@pytest.mark.parametrize("stdout,expected", [
    ("4586 tests collected in 1.11s\n", 4586),
    ("1 test collected in 0.00s\n", 1),
    ("1/4 tests collected (3 deselected) in 0.00s\n", 1),
    ("4 tests collected, 1 error in 0.05s\n", 4),
    ("no tests collected in 0.00s\n", 0),
    # No-(-q) output: header + footer.
    ("collected 4 items\n\n==== 4 tests collected in 0.00s ====\n", 4),
], ids=[
    "multi-test", "single-test", "selection", "with-error", "empty-dir",
    "no-q-header",
])
def test_parse_collect_count_reads_real_forms(stdout: str, expected: int) -> None:
    """AC-1: parse_collect_count returns the correct count for every form
    pytest prints with --collect-only -q."""
    from verification_record import parse_collect_count
    assert parse_collect_count(stdout) == expected


# ── AC-2: parse_collect_count returns None for unparseable output ─────────────

@pytest.mark.xfail(strict=True, reason="parse_collect_count does not exist yet")
@pytest.mark.parametrize("stdout", [
    "",
    "test_one.py: 1\ntest_probe.py: 3\n",
    "ERROR: file or directory not found: tests\n",
], ids=["empty", "qq-per-file", "error-message"])
def test_parse_collect_count_returns_none_for_unparseable(stdout: str) -> None:
    """AC-2: parse_collect_count returns None when it cannot read a count."""
    from verification_record import parse_collect_count
    assert parse_collect_count(stdout) is None


# ── AC-3: collected-count floor fires when actual < expected ──────────────────

@pytest.mark.xfail(strict=True, reason="collect-count floor does not read -q form yet")
def test_collected_count_floor_fires(tmp_path: Path) -> None:
    """AC-3: a suite output of 10 passed with --collect-only returning 100
    refuses with 'collected count 10 is below'."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    # Suite output says 10 passed.
    suite_output = textwrap.dedent("""\
        ========================= test session starts ==========================
        collected 10 items

        tests/test_a.py ..........                                         [100%]

        ========================= 10 passed in 1.00s ==========================
    """)
    out_file = tmp_path / "suite-output.txt"
    out_file.write_text(suite_output, encoding="utf-8")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if "--collect-only" in cmd_str:
            # The real -q form: "100 tests collected in 0.50s"
            return MockCompletedProcess(0, "100 tests collected in 0.50s\n", "")
        return MockCompletedProcess(0, "", "")

    def mock_bounded_run(cmd, *args, **kwargs):
        result = mock_run(cmd, *args, **kwargs)
        return result.returncode, result.stdout, result.stderr, False

    mock = MagicMock(side_effect=mock_run)
    mock_bounded = MagicMock(side_effect=mock_bounded_run)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0


# ── AC-4: unparseable collect output refuses ─────────────────────────────────

@pytest.mark.xfail(strict=True, reason="collect-count refusal does not exist yet")
@pytest.mark.parametrize("collect_side_effect,description", [
    (lambda *a, **kw: MockCompletedProcess(0, "", ""), "empty stdout"),
    (lambda *a, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(cmd="collect", timeout=120)), "timeout"),
], ids=["empty-collect", "timeout"])
def test_unparseable_collect_refuses(
    tmp_path: Path, collect_side_effect, description: str
) -> None:
    """AC-4: a collect run that produces unparseable output or times out
    refuses with 'cannot read the --collect-only count' and writes no record."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    out_file = tmp_path / "suite-output.txt"
    out_file.write_text(PASSING_OUTPUT, encoding="utf-8")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if "--collect-only" in cmd_str:
            return collect_side_effect(*args, **kwargs)
        return MockCompletedProcess(0, "", PASSING_OUTPUT)

    def mock_bounded_run(cmd, *args, **kwargs):
        result = mock_run(cmd, *args, **kwargs)
        return result.returncode, result.stdout, result.stderr, False

    mock = MagicMock(side_effect=mock_run)
    mock_bounded = MagicMock(side_effect=mock_bounded_run)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc != 0
    assert not record.exists(), "record file should not be written on refusal"


# ── AC-5: the collect command carries exactly one -q ──────────────────────────

@pytest.mark.xfail(strict=True, reason="collect command construction does not strip -q yet")
def test_collect_command_has_exactly_one_q(tmp_path: Path) -> None:
    """AC-5: for a configured invocation carrying -q, the collect command
    ends with --collect-only -q (exactly one -q)."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    out_file = tmp_path / "suite-output.txt"
    out_file.write_text(PASSING_OUTPUT, encoding="utf-8")

    captured_cmds = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        captured_cmds.append(cmd_str)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if "--collect-only" in cmd_str:
            return MockCompletedProcess(0, "100 tests collected in 0.50s\n", "")
        return MockCompletedProcess(0, "", PASSING_OUTPUT)

    def mock_bounded_run(cmd, *args, **kwargs):
        result = mock_run(cmd, *args, **kwargs)
        return result.returncode, result.stdout, result.stderr, False

    mock = MagicMock(side_effect=mock_run)
    mock_bounded = MagicMock(side_effect=mock_bounded_run)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])

        record = vdir / "test-batch.md"
        # Configured invocation already carries -q.
        invocation_with_q = "python3 -m pytest -q -n 8 --dist loadfile"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", invocation_with_q,
            "--suite-head", HEAD_SHA,
        ])

    # Find the --collect-only command.
    collect_cmds = [c for c in captured_cmds if "--collect-only" in c]
    assert len(collect_cmds) == 1, f"expected one collect command, got {len(collect_cmds)}"
    collect_cmd = collect_cmds[0]

    # Must end with --collect-only -q.
    assert collect_cmd.rstrip().endswith("--collect-only -q"), (
        f"collect command should end with --collect-only -q: {collect_cmd}"
    )

    # Exactly one -q token (not -qq, not two -q).
    tokens = collect_cmd.split()
    q_tokens = [t for t in tokens if t == "-q"]
    assert len(q_tokens) == 1, (
        f"expected exactly one -q token, got {len(q_tokens)}: {collect_cmd}"
    )
    assert "-qq" not in tokens, f"-qq should not appear: {collect_cmd}"


# ── AC-6 (control): passing suite with matching count writes record ──────────

def test_passing_suite_with_matching_count_writes_record(tmp_path: Path) -> None:
    """AC-6 (control): a suite output of 100 passed with --collect-only
    returning 100 writes a record.  This passes today (the floor is skipped)
    and must still pass after the fix."""
    repo = _init_repo(tmp_path)
    vdir = tmp_path / "verification"
    vdir.mkdir()

    out_file = tmp_path / "suite-output.txt"
    out_file.write_text(PASSING_OUTPUT, encoding="utf-8")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if isinstance(cmd, list) and cmd[0] == "git":
            return MockCompletedProcess(0, "", "")
        if "--collect-only" in cmd_str:
            # The real -q form, matching PASSING_OUTPUT's 100.
            return MockCompletedProcess(0, "100 tests collected in 0.50s\n", "")
        return MockCompletedProcess(0, "", PASSING_OUTPUT)

    def mock_bounded_run(cmd, *args, **kwargs):
        result = mock_run(cmd, *args, **kwargs)
        return result.returncode, result.stdout, result.stderr, False

    mock = MagicMock(side_effect=mock_run)
    mock_bounded = MagicMock(side_effect=mock_bounded_run)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(vr.subprocess, "run", mock)
        mp.setattr(vr, "_bounded_run", mock_bounded)
        mp.setattr(vr, "_resolve_project_verification_dir", lambda p: vdir)
        mp.setattr(vr, "read_head_from_git", lambda p: HEAD_SHA)
        mp.setattr(vr, "_git", lambda p, *a: TREE_SHA)
        mp.setattr(vr, "read_baseline_red_at", lambda p, s: [])
        mp.setattr(vr, "read_baseline_red", lambda p: [])

        record = vdir / "test-batch.md"
        rc = vr.main([
            "--project", str(repo),
            "--record", str(record),
            "--base-sha", BASE_SHA,
            "--from-suite-output", str(out_file),
            "--suite-invocation", CONFIGURED_INVOCATION,
            "--suite-head", HEAD_SHA,
        ])

    assert rc == 0
    assert record.exists()
    text = record.read_text()
    assert "verified_head:" in text