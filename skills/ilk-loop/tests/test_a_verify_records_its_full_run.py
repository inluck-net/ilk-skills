"""A full-suite verify in --ledger prefer mode records its run in the ledger.

Backlog c0b75341e683a079.  On rezmac 2026-10-08, #8007's verify ran the full
kira convex suite (26439 tests, 804 s, ``ledger_mode: prefer``,
``suite_scope: full``) and left no ledger entry: the resolver's ledger dir
held 0 ``<tree>.json`` files.  The only writer was the ``--ledger require``
branch, so a pre-push hook or the next verify could not ask "is this tree
already measured?" and ran the suite again.
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# pytest is often a ``--user`` install (it is wherever ``python3`` is the
# system interpreter).  Pinning HOME moves user-site resolution, so the suite
# subprocess this file drives raises ``ModuleNotFoundError`` and produces no
# pytest summary line.  Captured at import, while HOME is still the real one.
_USER_SITE = site.getusersitepackages()


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
    if _USER_SITE and os.path.isdir(_USER_SITE):
        prior = os.environ.get("PYTHONPATH") or ""
        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join([p for p in (_USER_SITE, prior) if p]),
        )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    import suite_ledger
    suite_ledger._ORIGINAL_HOME = original_home


@pytest.fixture(autouse=True)
def _needs_pytest_in_python3() -> None:
    check = subprocess.run([sys.executable, "-c", "import pytest"],
                           capture_output=True, timeout=10)
    if check.returncode != 0:
        pytest.skip("pytest not available in the system python3")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """Base: one passing test.  HEAD: a test that writes a sentinel per run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / ".ilk-launch.json").write_text(json.dumps({"ship": {"suite": {
        "command": "python3 -m pytest",
        "flags": ["-q", "-p", "no:cacheprovider"]}}}))
    test_file = repo / "test_stuff.py"
    test_file.write_text("def test_pass():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    sentinel = tmp_path / "runs.txt"
    test_file.write_text(textwrap.dedent(f"""\
        import pathlib
        RUNS = pathlib.Path({str(sentinel)!r})

        def test_pass():
            with RUNS.open("a") as f:
                f.write("run\\n")
    """))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "head")
    return repo, sentinel


def _verify(repo: Path, record: Path, scope: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo),
         "--base-sha", _git(repo, "rev-parse", "HEAD~1"),
         "--run-suite", "--ledger", "prefer", "--scope", scope,
         "--record", str(record)],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )


def _runs(sentinel: Path) -> int:
    return len(sentinel.read_text().splitlines()) if sentinel.exists() else 0


def test_full_prefer_run_writes_a_lookup_valid_entry(tmp_path):
    repo, sentinel = _repo(tmp_path)
    r = _verify(repo, tmp_path / "record.md", "full")
    assert r.returncode == 0, r.stderr
    assert _runs(sentinel) == 1

    import suite_ledger
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    entry = suite_ledger.lookup(repo, tree)
    assert entry is not None, "no lookup-valid ledger entry for the verified tree"
    assert entry["scope_mode"] == "full"
    assert entry["counts"]["passed"] == 1


def test_second_verify_of_the_same_tree_runs_no_suite(tmp_path):
    repo, sentinel = _repo(tmp_path)
    assert _verify(repo, tmp_path / "r1.md", "full").returncode == 0
    assert _runs(sentinel) == 1

    r = _verify(repo, tmp_path / "r2.md", "full")
    assert r.returncode == 0, r.stderr
    assert _runs(sentinel) == 1, "the second verify re-ran the suite"
    assert "head_source: ledger" in (tmp_path / "r2.md").read_text()


def test_control_scoped_prefer_run_writes_nothing(tmp_path):
    """A scoped run must never answer "is tree X green?"."""
    repo, sentinel = _repo(tmp_path)
    r = _verify(repo, tmp_path / "record.md", "auto")
    assert r.returncode == 0, r.stderr
    assert "suite_scope: scoped" in (tmp_path / "record.md").read_text()

    import suite_ledger
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    assert not (suite_ledger.ledger_dir(repo) / f"{tree}.json").exists()
