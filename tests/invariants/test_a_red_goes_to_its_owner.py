"""I4: a red goes to its owner.

On a tmp repo, commit A (``[plan:a#step-1]``) breaks a test, commit B
(``[plan:b#step-1]``) is unrelated.  ``red_owner.py`` names A's sha and
slug ``a``, not B.

Rail: ``bisect_red_owner`` (``red_owner.py:90``) and ``attribute_red``
(``:282``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
RED_OWNER = _SCRIPTS / "red_owner.py"

pytestmark = pytest.mark.timeout(120)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Repo with a passing test, then commit A breaks it, then commit B is clean."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    # Base: a passing test.
    (repo / "test_ok.py").write_text(
        "def test_pass(): assert True\n", encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init: passing test")

    # Commit A: breaks the test (with plan trailer).
    (repo / "test_ok.py").write_text(
        "def test_pass(): assert False\n", encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: break test [plan:a#step-1]")

    # Commit B: unrelated clean change (with plan trailer).
    (repo / "unrelated.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: unrelated [plan:b#step-1]")

    return repo


def _run_red_owner(repo: Path, base: str, head: str) -> dict:
    """Bisect mode: find the first red commit."""
    r = subprocess.run(
        [sys.executable, str(RED_OWNER),
         "--repo", str(repo),
         "--base", base,
         "--head", head,
         "--cmd", f"{sys.executable} -m pytest test_ok.py -q -p no:cacheprovider",
         "--node", "test_ok.py::test_pass"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, (
        f"red_owner.py exited {r.returncode}.\n"
        f"stdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}"
    )
    return json.loads(r.stdout)


def test_bisect_names_commit_a(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~2")
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_red_owner(repo, base, head)

    commit_a_sha = _git(repo, "rev-parse", "HEAD~1")
    assert result.get("first_red") == commit_a_sha, (
        f"bisect should name commit A ({commit_a_sha[:12]}) as the first red, "
        f"got {result.get('first_red', '<none>')[:12]}.\n"
        f"result={json.dumps(result, indent=2)}"
    )


def test_attribute_names_slug_a(tmp_path: Path) -> None:
    """attribute_red names slug 'a' as the owner, not 'b'."""
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~2")
    head = _git(repo, "rev-parse", "HEAD")
    commit_a_sha = _git(repo, "rev-parse", "HEAD~1")

    # Write gate stdout to a file for --stdout-file.
    stdout_file = tmp_path / "gate-stdout.txt"
    stdout_file.write_text("FAILED test_ok.py::test_pass\n", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(RED_OWNER),
         "--repo", str(repo),
         "--attribute",
         "--head", head,
         "--iteration-base", commit_a_sha,
         "--batch-base", base,
         "--cmd", f"{sys.executable} -m pytest test_ok.py -q -p no:cacheprovider",
         "--stdout-file", str(stdout_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, (
        f"red_owner.py --attribute exited {r.returncode}.\n"
        f"stdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}"
    )
    result = json.loads(r.stdout)

    owner_slug = result.get("owner_slug")
    assert owner_slug == "a", (
        f"attribute_red should name slug 'a' as owner, got {owner_slug!r}.\n"
        f"result={json.dumps(result, indent=2)}"
    )