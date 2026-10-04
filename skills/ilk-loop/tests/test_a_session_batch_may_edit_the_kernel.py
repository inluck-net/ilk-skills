"""Red-first pins: a session batch's kernel edit takes the slow path, not a refusal.

Part of sub-plan ``a-session-batch-may-edit-the-kernel`` (MASTER-2026-10-04m).

The design (docs/architecture/unattended-unblocking-design.md:306-309) says a
tier-0 edit "takes the slow path", and only the rules tier is a refusal.  But
``safety_kernel.check_range`` at 4e5ec01a labels every resolved, non-auto-
planned master ``kernel-edit-unresolved-master`` — a false refusal.

Five acceptance criteria:

  AC-1  session master editing kernel → no violation.  Red-first (xfail).
  AC-2  auto_planned master editing kernel → kernel-edit-by-unattended-build.
  AC-3  slug in no master → kernel-edit-unresolved-master.
  AC-4  session master editing rules → rules-edit-by-loop-build.
  AC-5  every test in test_the_safety_kernel_is_one_list.py and
        test_an_auto_planned_batch_stays_inside_its_rails.py passes.
        (This is a step-1 gate, not a test here.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO_ROOT = _HERE.parents[3]

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers (copied from test_the_safety_kernel_is_one_list.py) ───────────

def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )


def _git_output(args: list[str], cwd: Path) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "test@test"], path)
    _git(["config", "user.name", "Test"], path)
    (path / ".gitkeep").write_text("")
    _git(["add", ".gitkeep"], path)
    _git(["commit", "-m", "init"], path)


def _copy_kernel_json(tmp_repo: Path) -> Path:
    """Copy safety-kernel.json into the tmp repo and commit."""
    src = _REPO_ROOT / "skills" / "ilk-loop" / "safety-kernel.json"
    dst_dir = tmp_repo / "skills" / "ilk-loop"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "safety-kernel.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "add kernel list"], tmp_repo)
    return dst


def _make_session_master(tmp_repo: Path, slug: str = "2026-10-03k-sub") -> Path:
    """Create a plans dir with a session master (no auto_planned)."""
    plans = tmp_repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent(f"""\
        ---
        master_plan: 2026-10-03k
        status: active
        ---
        # session batch

        | # | Slug |
        |---|---|
        | 0 | {slug}.md |
    """), encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "add plans"], tmp_repo)
    return plans


def _make_auto_planned_master(
    tmp_repo: Path, slug: str = "2026-10-03k-sub",
) -> Path:
    """Create a plans dir with an auto_planned: true master."""
    plans = tmp_repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent(f"""\
        ---
        master_plan: 2026-10-03k
        status: active
        auto_planned: true
        ---
        # auto-planned batch

        | # | Slug |
        |---|---|
        | 0 | {slug}.md |
    """), encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "add plans"], tmp_repo)
    return plans


def _trailered_commit(
    tmp_repo: Path, file: Path, content: str, slug: str = "2026-10-03k-sub",
) -> str:
    """Commit with a [plan:<slug>#step-N] trailer, return short sha."""
    file.write_text(content, encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(
        ["commit", "-m", f"feat(x): change [plan:{slug}#step-0]"],
        tmp_repo,
    )
    return _git_output(["rev-parse", "--short", "HEAD"], tmp_repo)


def _run_cli(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run safety_kernel.py CLI."""
    cmd = [sys.executable, str(_SCRIPTS / "safety_kernel.py"), *args]
    merged = {**os.environ}
    if env:
        merged.update(env)
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, env=merged,
    )


# ── AC-1: session master editing kernel → no violation (xfail) ───────────

@pytest.mark.xfail(
    strict=True,
    reason=(
        "check_range at 4e5ec01a labels session-master kernel edits as "
        "kernel-edit-unresolved-master; the fix lets them pass."
    ),
)
def test_session_master_kernel_edit_no_violation(tmp_path: Path) -> None:
    """A session master (no auto_planned) editing a kernel path should
    produce NO violation — the slow path governs, not a refusal."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    plans = _make_session_master(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n", slug="2026-10-03k-sub")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        [
            "check-range", "--repo", str(repo), "--base", base,
            "--head", head, "--plans-dir", str(plans), "--json",
        ],
        repo,
    )
    assert r.returncode == 0, (
        f"session master kernel edit should be clean (exit 0), "
        f"got {r.returncode}\n{r.stdout}\n{r.stderr}"
    )
    data = json.loads(r.stdout) if r.stdout.strip() else []
    violations = (
        data if isinstance(data, list)
        else data.get("violations", data.get("result", []))
    )
    assert violations == [], (
        f"expected no violations for session master kernel edit, got {violations}"
    )


# ── AC-2: auto_planned master editing kernel → unattended-build ──────────

def test_auto_planned_kernel_edit_is_violation(tmp_path: Path) -> None:
    """An auto_planned build editing a kernel file →
    kernel-edit-by-unattended-build."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n", slug="2026-10-03k-sub")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        [
            "check-range", "--repo", str(repo), "--base", base,
            "--head", head, "--plans-dir", str(plans), "--json",
        ],
        repo,
    )
    assert r.returncode == 1, (
        f"expected violations (exit 1), got {r.returncode}\n"
        f"{r.stdout}\n{r.stderr}"
    )
    data = json.loads(r.stdout)
    violations = (
        data if isinstance(data, list)
        else data.get("violations", data.get("result", []))
    )
    assert any(
        v.get("reason") == "kernel-edit-by-unattended-build" for v in violations
    ), f"expected kernel-edit-by-unattended-build in {violations}"


# ── AC-3: slug in no master → unresolved-master ──────────────────────────

def test_unresolved_master_kernel_edit(tmp_path: Path) -> None:
    """A trailered commit whose slug is in no master →
    kernel-edit-unresolved-master."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n", slug="nonexistent-slug")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        [
            "check-range", "--repo", str(repo), "--base", base,
            "--head", head, "--plans-dir", str(plans), "--json",
        ],
        repo,
    )
    assert r.returncode == 1, (
        f"expected violations (exit 1), got {r.returncode}\n"
        f"{r.stdout}\n{r.stderr}"
    )
    data = json.loads(r.stdout)
    violations = (
        data if isinstance(data, list)
        else data.get("violations", data.get("result", []))
    )
    assert any(
        v.get("reason") == "kernel-edit-unresolved-master" for v in violations
    ), f"expected kernel-edit-unresolved-master in {violations}"


# ── AC-4: session master editing rules → rules-edit-by-loop-build ────────

def test_session_rules_edit_is_violation(tmp_path: Path) -> None:
    """A session master editing a rules-tier path →
    rules-edit-by-loop-build."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    plans = _make_session_master(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    target = repo / "tests" / "invariants" / "a.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n", slug="2026-10-03k-sub")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        [
            "check-range", "--repo", str(repo), "--base", base,
            "--head", head, "--plans-dir", str(plans), "--json",
        ],
        repo,
    )
    assert r.returncode == 1, (
        f"expected violations (exit 1), got {r.returncode}\n"
        f"{r.stdout}\n{r.stderr}"
    )
    data = json.loads(r.stdout)
    violations = (
        data if isinstance(data, list)
        else data.get("violations", data.get("result", []))
    )
    assert any(
        v.get("reason") == "rules-edit-by-loop-build" for v in violations
    ), f"expected rules-edit-by-loop-build in {violations}"