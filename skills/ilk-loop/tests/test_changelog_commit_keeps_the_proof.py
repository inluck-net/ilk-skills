"""Red-first: a CHANGELOG-only commit must not invalidate the gate proof.

The batch gate certifies CODE. A commit that touches only `CHANGELOG.md`
(plus empty markers) must not break the proven-unproven status of a batch
whose gate ran on an earlier tree.

This is the same problem as empty-marker (test_empty_marker_keeps_the_gate_fresh),
but for a *file-bearing* commit whose only real change is release bookkeeping.
The allowlist is exactly `CHANGELOG.md` — tests read Markdown, and no test
or script reads CHANGELOG.md as content (it appears only as a path string
in test_gate_scope.py:154).

AC-1   record carries tree_sha, HEAD moved by CHANGELOG-only commit -> fresh
       (xfail until step 1 implements bookkeeping-only freshness)
AC-1b  same fixture through CLI loop_status.py --json -> proven + proof_freshness
       (xfail until step 1)
AC-2   commit touches CHANGELOG.md AND README.md -> stale_head (control)
AC-3   commit touches a .md under skills/ -> stale_head (control)
AC-4   record without tree_sha + CHANGELOG-only commit -> stale_head (legacy)
AC-5   empty marker commit -> fresh, basis tree-equal (control)
AC-6   git diff fails (bogus tree_sha) -> not fresh (control)
AC-7   guard test: no test or script file reads a BOOKKEEPING_PATHS entry
       (already green — no xfail needed)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

INVOCATION = "python3 -m pytest -q"


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write(tmp_path: Path, **fields) -> Path:
    p = tmp_path / "batch-gate.json"
    p.write_text(json.dumps(fields), encoding="utf-8")
    return p


# ── AC-1 ─────────────────────────────────────────────────────────────────────

def test_changelog_only_commit_keeps_the_record_fresh(tmp_path: Path) -> None:
    """A commit touching only CHANGELOG.md must not invalidate the gate proof."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Simulate a release: bump only CHANGELOG.md
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1.0\n\n- released\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(changelog): release v0.1.0")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    assert new_tree != gated_tree, "a CHANGELOG edit must change the tree"

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00",
               tree_sha=gated_tree, writer="batch_gate.py")

    result = validate_record(p, new_sha, INVOCATION, expected_tree_sha=new_tree, repo=repo)
    assert result == "fresh", (
        "a commit touching only CHANGELOG.md must not invalidate a statement "
        f"about the code; got {result!r}"
    )


# ── AC-1b ────────────────────────────────────────────────────────────────────

def test_loop_status_json_shows_proven_and_freshness_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through resolve_status, a CHANGELOG-only commit shows proven + bookkeeping-only."""
    import importlib

    import batch_gate as _bg_mod  # type: ignore[import-untyped]
    import loop_status as _ls_mod  # type: ignore[import-untyped]
    importlib.reload(_bg_mod)
    importlib.reload(_ls_mod)
    from loop_status import resolve_status  # type: ignore[import-untyped]
    from ilk_paths import resolve_project_key  # type: ignore[import-untyped]

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1.0\n\n- released\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(changelog): release v0.1.0")

    # Resolve the real project key from the repo path
    data_home = tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))
    key = resolve_project_key(repo)

    # Write a batch-gate record under the resolved project key
    runtime = data_home / "projects" / key / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "batch-gate.json").write_text(json.dumps({
        "verdict": "pass",
        "head_sha": gated_sha,
        "tree_sha": gated_tree,
        "writer": "batch_gate.py",
        "invocation": INVOCATION,
        "timestamp": "2026-09-28T10:00:00+08:00",
    }), encoding="utf-8")

    # Write a minimal plans structure so loop_status finds something
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-test.md").write_text(
        "---\nmaster_plan: test\nbatch_date: 2026-09-28\nstatus: active\n"
        "total_tickets: 1\n---\n\n# Test\n\n## Sub-plan registry\n\n"
        "| # | File | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|\n"
        "| 1 | 2026-09-28-test.md | test | 1 | shipped |\n",
        encoding="utf-8",
    )
    (plans / "2026-09-28-test.md").write_text(
        "---\nplan: test\nstatus: shipped\ncurrent_step: 1\ntickets: []\n"
        "priority: P1\nestimated_steps: 1\nlast_updated: 2026-09-28\n"
        "verification_tier: loop-verified\n---\n\n# Test sub-plan\n",
        encoding="utf-8",
    )

    data = resolve_status(repo, json_mode=True)
    # Find the test sub-plan
    sub_data = None
    for s in data.get("subplans", []):
        if s.get("slug") == "test":
            sub_data = s
            break
    assert sub_data is not None, f"test sub-plan not found in {data}"
    assert sub_data.get("proven") is True, f"expected proven true, got {sub_data}"
    assert sub_data.get("proof_freshness") == "bookkeeping-only", (
        f"expected bookkeeping-only, got {sub_data}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_changelog_and_readme_goes_stale(tmp_path: Path) -> None:
    """A commit touching CHANGELOG.md AND README.md must go stale."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1.0\n\n- released\n", encoding="utf-8")
    (repo / "README.md").write_text("# Project\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: release + readme update")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00",
               tree_sha=gated_tree, writer="batch_gate.py")

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "stale_head", "a commit touching non-bookkeeping files must invalidate the record"


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_md_under_skills_goes_stale(tmp_path: Path) -> None:
    """A commit touching a .md under skills/ must go stale."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    skills_dir = repo / "skills" / "ilk-loop"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# updated skill\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: update skill")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00",
               tree_sha=gated_tree, writer="batch_gate.py")

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "stale_head", "a commit touching skills/*.md must invalidate the record"


# ── AC-4 ─────────────────────────────────────────────────────────────────────

def test_legacy_record_without_tree_sha_stays_stale(tmp_path: Path) -> None:
    """A record without tree_sha (legacy/foreign) stays strict SHA."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1.0\n\n- released\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(changelog): release")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00")

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "stale_head", "a legacy record without tree_sha must use strict SHA comparison"


# ── AC-5 ─────────────────────────────────────────────────────────────────────

def test_empty_marker_commit_keeps_fresh(tmp_path: Path) -> None:
    """An empty marker commit stays fresh (tree-equal basis)."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    _git(repo, "commit", "-q", "--allow-empty", "-m",
         "test(verify): record [plan:x#step-0]")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    assert new_tree == gated_tree, "empty commit must preserve tree"

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00",
               tree_sha=gated_tree, writer="batch_gate.py")

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "fresh", "an empty marker commit must not invalidate the record"


# ── AC-6 ─────────────────────────────────────────────────────────────────────

def test_bogus_tree_sha_means_not_fresh(tmp_path: Path) -> None:
    """When git diff fails (bogus tree_sha), the record must not be fresh."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1.0\n\n- released\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(changelog): release")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # Write record with a bogus tree_sha that won't resolve in git
    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-28T10:00:00+08:00",
               tree_sha="0000000000000000000000000000000000000000",
               writer="batch_gate.py")

    result = validate_record(p, new_sha, INVOCATION, expected_tree_sha=new_tree)
    assert result != "fresh", (
        "a record with a bogus tree_sha must not validate as fresh"
    )


# ── AC-7 ─────────────────────────────────────────────────────────────────────

def test_guard_no_test_or_script_reads_changelog_md() -> None:
    """No test or script file opens or reads a BOOKKEEPING_PATHS entry.

    This guard catches regressions: if a test starts reading CHANGELOG.md
    as content (not just a path string), the allowlist assumption breaks.
    """
    import re

    project_root = Path(__file__).resolve().parent.parent.parent.parent
    test_dirs = [
        project_root / "skills" / "ilk-loop" / "tests",
        project_root / "skills" / "ilk-ship" / "tests",
        project_root / "skills" / "ilk-runner" / "tests",
        project_root / "skills" / "ilk-feedback" / "tests",
    ]
    script_dirs = [
        project_root / "skills" / "ilk-loop" / "scripts",
        project_root / "skills" / "ilk-ship" / "scripts",
        project_root / "skills" / "ilk-runner" / "scripts",
        project_root / "skills" / "ilk-feedback" / "scripts",
    ]

    # Patterns that indicate reading CHANGELOG.md as content
    read_patterns = re.compile(
        r'open\s*\(.*CHANGELOG\.md|read_text.*CHANGELOG\.md|'
        r'Path\s*\(.*["\']CHANGELOG\.md["\']|cat\s+CHANGELOG\.md',
        re.IGNORECASE,
    )
    # String-only uses are OK (e.g. test_gate_scope.py:154)
    known_string_uses = {"test_gate_scope.py"}

    violations = []
    for d in test_dirs + script_dirs:
        if not d.is_dir():
            continue
        for py in d.rglob("*.py"):
            if py.name in known_string_uses:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if read_patterns.search(text):
                violations.append(str(py.relative_to(project_root)))

    assert not violations, (
        "The following files read CHANGELOG.md as content, which breaks "
        "the bookkeeping-only freshness allowlist: "
        + ", ".join(violations)
    )