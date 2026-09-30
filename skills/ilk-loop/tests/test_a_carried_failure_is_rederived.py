"""A failure carried from an earlier attempt is re-derived, not assumed attributed.

R3 (``_check_history``) unions every failing node from earlier attempts at the
same code and refused when any was absent now, "still attributed". It never
asked how the node classified. gh-resolve 29b run 20260930-034122: the 4 nodes
carried were 2 failed-at-base (eval_translate) and 2 flaky 29c-owned (picker)
nodes. None was 29b's, no code change could clear them, and the worker deleted
the history to get past it.

Now a carried node is re-measured (at base, K head reruns, batch touched) and
classified with the same rule as a current row. Old records are never read
for this: their cells can be forged and the files deleted.

  AC-1  carried node, passes at base and now, file untouched -> flaky-owed, no refusal
  AC-2  carried node red K/K at head, passed at base -> still refused (a retry
        cannot erase a real regression)
  AC-3  carried node that fails at base too -> pre-existing, no refusal
  AC-4  in a worker session, re-derivation is refused (the driver re-derives)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_attribution as va  # noqa: E402

INV = f"{sys.executable} -m pytest -q -p no:cacheprovider -p no:randomly"
OK = "tests/test_ok.py::test_ok"
RED = "tests/test_red.py::test_red"
BASERED = "tests/test_basered.py::test_basered"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write(repo, "tests/test_ok.py", "def test_ok():\n    assert True\n")
    _write(repo, "tests/test_red.py", "def test_red():\n    assert True\n")
    _write(repo, "tests/test_basered.py", "def test_basered():\n    assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    # The batch breaks test_red for real; test_ok and test_basered are untouched.
    _write(repo, "tests/test_red.py", "def test_red():\n    assert False\n")
    _git(repo, "commit", "-q", "-am", "fix: breaks red [plan:our-slug#step-0]")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _record(repo: Path, base: str, head: str, carried: list[str]) -> Path:
    rec = repo / "rec.md"
    text = (
        "# Batch verification record — t\n\n"
        "attempt: 2\n"
        f"verified_head: {head}\n"
        f"base_sha: {base}\n"
        f"suite_invocation: {INV}\n"
        "suite_failed: 0\n\n"
        "## At-base rerun\n\n_(no failures)_\n"
    )
    rec.write_text(text, encoding="utf-8")
    hist = [
        {"attempt": 1, "digest": "x" * 64, "failing_nodes": carried, "head": head},
        {"attempt": 2, "digest": va._compute_record_digest(text),
         "failing_nodes": [], "head": head},
    ]
    va._history_path(rec).write_text(
        "".join(json.dumps(h) + "\n" for h in hist), encoding="utf-8")
    return rec


def _check(repo: Path, rec: Path) -> list[str]:
    text = rec.read_text(encoding="utf-8")
    return va._check_history(rec, text, 0, set(), project=repo)


def test_ac1_untouched_node_passing_now_is_flaky_owed(tmp_path, monkeypatch):
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    repo, base, head = _repo(tmp_path)
    assert _check(repo, _record(repo, base, head, [OK])) == [OK]


def test_ac2_a_real_regression_still_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    repo, base, head = _repo(tmp_path)
    with pytest.raises(va.VerificationError, match="still attributed"):
        _check(repo, _record(repo, base, head, [RED]))


def test_ac3_red_at_base_is_pre_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    repo, base, head = _repo(tmp_path)
    assert _check(repo, _record(repo, base, head, [BASERED])) == []


def test_ac4_worker_session_does_not_rederive(tmp_path, monkeypatch):
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    repo, base, head = _repo(tmp_path)
    with pytest.raises(va.VerificationError, match="driver"):
        _check(repo, _record(repo, base, head, [OK]))
