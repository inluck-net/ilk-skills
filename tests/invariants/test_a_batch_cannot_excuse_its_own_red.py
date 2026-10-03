"""I6: a batch cannot excuse its own red.

On a tmp repo, a test that passes at base and fails at head is
attributed: ``verification_record.py --run-suite`` then
``verify_attribution.py`` exits non-zero naming it.

Rail: the attribution rule (``verify_attribution.py:277-294``).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verification_record as vr  # noqa: E402
import verify_attribution as va    # noqa: E402

pytestmark = pytest.mark.timeout(120)


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "test")
    return repo


def _write_ship_config(repo: Path) -> None:
    cfg = {
        "ship": {
            "suite": {"command": "python3 -m pytest -x"},
            "baseline_red": [],
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8")


def _write_test(repo: Path, name: str, body: str) -> Path:
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    p = tests / name
    p.write_text(body, encoding="utf-8")
    return p


def _base(repo: Path, ref: str = "HEAD~1") -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _run_main(module, argv: list[str]) -> int:
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            return module.main(argv)
    except SystemExit as exc:
        return exc.code


# ── test ─────────────────────────────────────────────────────────────────────

def test_batch_cannot_excuse_its_own_red(
    tmp_path: Path, monkeypatch
) -> None:
    """A test that passes at base and fails at head is attributed as owned."""
    repo = _init_repo(tmp_path)
    _write_test(repo, "test_x.py", "def test_a(): assert True\n")
    _write_ship_config(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: passing test")

    # Break the test at HEAD.
    _write_test(repo, "test_x.py", "def test_a(): assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "break test_a")

    vdir = tmp_path / "verification"
    vdir.mkdir()

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    # Create a verification record (suite at base passes).
    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b1",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])

    # The record should exist even if the suite run had issues.
    record_path = vdir / "b1-batch.md"

    # Run verify_attribution — it should detect the regression.
    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b1",
        "--no-write-gate-record"])

    assert gate_rc == 1, (
        f"verify_attribution should exit 1 for an attributed regression, "
        f"got exit {gate_rc}."
    )