"""Pins for teeth.py — a removed rail must turn its gate red.

AC-1..AC-5 are xfail(strict=True) until the teeth harness exists.
Each test builds a toy git repo + catalog so the harness can be exercised
in isolation without touching the real repo.

Sub-plan: a-removed-rail-turns-its-gate-red
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _make_toy_repo(tmp_path: Path, *, guard: str = "x > 0", test_passes: bool = True) -> Path:
    """Create a minimal git repo with a guard function and a test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # guard module
    (repo / "calc.py").write_text(textwrap.dedent(f"""\
        def check(x: int) -> bool:
            return {guard}
    """))
    # test that pins the guard
    assertion = "assert check(1)" if test_passes else "assert check(-1)"
    (repo / "test_calc.py").write_text(textwrap.dedent(f"""\
        from calc import check
        def test_guard():
            {assertion}
    """))
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


def _make_catalog(
    tmp_path: Path,
    *,
    pattern: str = r"^def check\(x: int\) -> bool:\n    return x > 0$",
    replacement: str = "def check(x: int) -> bool:\n    return True",
    kind: str = "replace",
    min_matches: int = 1,
    test_passes: bool = True,
) -> Path:
    """Write a mutations.json catalog with one mutation."""
    catalog = tmp_path / "mutations.json"
    catcher_arg = "test_calc.py" if test_passes else "test_calc_will_fail.py"
    catalog.write_text(json.dumps({
        "schema": 1,
        "mutations": [
            {
                "id": "guard-removed",
                "incident": "toy",
                "rail": "check rejects x<=0",
                "file": "calc.py",
                "kind": kind,
                "pattern": pattern,
                "replacement": replacement,
                "min_matches": min_matches,
                "catcher": {
                    "argv": ["python3", "-m", "pytest", catcher_arg, "-q"],
                    "expect_in_output": ["FAILED", "test_guard"],
                },
            }
        ],
    }))
    return catalog


# ── AC-1: mutation deletes the guard → catcher goes red → killed → pass ──────

@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac1_guard_removed_kills(tmp_path: Path) -> None:
    """A mutation that removes the guard must make the catcher fail (killed)."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")  # will fail until step 1
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    catalog = _make_catalog(tmp_path, test_passes=True)
    result = teeth.run(repo, catalog)
    assert result["verdict"] == "pass"
    assert len(result["results"]) == 1
    assert result["results"][0]["outcome"] == "killed"


# ── AC-2: catcher still passes after mutation → survived → fail ──────────────

@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac2_catcher_still_passes_survived(tmp_path: Path) -> None:
    """A mutation the catcher doesn't pin must survive (fail)."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    # guard always returns True, so the test passes even without the guard
    repo = _make_toy_repo(tmp_path, guard="True", test_passes=True)
    # mutation changes "True" to "False" — but the test asserts check(1),
    # and True is the return value, not the guard logic the test pins
    catalog = _make_catalog(
        tmp_path,
        pattern=r"return True$",
        replacement="return False",
        test_passes=True,
    )
    result = teeth.run(repo, catalog)
    assert result["verdict"] == "fail"
    assert result["results"][0]["outcome"] == "survived"


# ── AC-3: stale / control-red / red-for-another-reason → each is fail ────────

@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac3a_pattern_matches_nothing_stale(tmp_path: Path) -> None:
    """A pattern that matches nothing in the file is stale (fail)."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    catalog = _make_catalog(
        tmp_path,
        pattern=r"^THIS_LINE_DOES_NOT_EXIST_ANYWHERE$",
        replacement="pass",
    )
    result = teeth.run(repo, catalog)
    assert result["verdict"] == "fail"
    assert result["results"][0]["outcome"] == "stale"


@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac3b_catcher_red_unmutated_control_red(tmp_path: Path) -> None:
    """A catcher that fails on the unmutated code is control-red (fail)."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    # test passes, but we point the catcher at a non-existent file
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    catalog = _make_catalog(tmp_path, test_passes=False)  # test_calc_will_fail.py doesn't exist
    result = teeth.run(repo, catalog)
    assert result["verdict"] == "fail"
    assert result["results"][0]["outcome"] == "control-red"


@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac3c_fails_without_expected_id_red_for_another_reason(tmp_path: Path) -> None:
    """A mutant that fails but without the expected output is red-for-another-reason."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    # expect_in_output looks for a test name that won't appear
    catalog = _make_catalog(tmp_path, test_passes=True)
    # corrupt expect_in_output to look for something absent
    cat_data = json.loads(catalog.read_text())
    cat_data["mutations"][0]["catcher"]["expect_in_output"] = ["THIS_ID_WILL_NEVER_APPEAR"]
    catalog.write_text(json.dumps(cat_data))
    result = teeth.run(repo, catalog)
    assert result["verdict"] == "fail"
    assert result["results"][0]["outcome"] == "red-for-another-reason"


# ── AC-4: repo untouched after run; cwd outside repo; ILK_SKILL_HOME set ────

@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac4_repo_unchanged_after_run(tmp_path: Path) -> None:
    """The toy repo's working tree, HEAD, and git state must be identical after run."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    catalog = _make_catalog(tmp_path, test_passes=True)

    # snapshot before
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    ).stdout
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    before_wt = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True,
    ).stdout

    teeth.run(repo, catalog)

    after_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    ).stdout
    after_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    after_wt = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True,
    ).stdout

    assert before_status == after_status, "working tree dirty after run"
    assert before_head == after_head, "HEAD changed after run"
    assert before_wt == after_wt, "worktree list changed after run"


# ── AC-5: only=[] → fail with no-mutations-selected ─────────────────────────

@pytest.mark.xfail(strict=True, reason="teeth.py does not exist yet")
def test_ac5_empty_only_is_fail(tmp_path: Path) -> None:
    """run with only=[] must be fail, never pass."""
    from importlib import import_module
    teeth = import_module("skills.scripts.teeth")
    repo = _make_toy_repo(tmp_path, guard="x > 0", test_passes=True)
    catalog = _make_catalog(tmp_path, test_passes=True)
    result = teeth.run(repo, catalog, only=[])
    assert result["verdict"] == "fail"