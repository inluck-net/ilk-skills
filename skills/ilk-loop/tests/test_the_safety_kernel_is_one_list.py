"""Red-first pins: one machine-readable safety kernel with tiers and range check.

Part of sub-plan ``the-safety-kernel-is-one-list`` (MASTER-2026-10-03k).

Guard 1 of the RSI safety case: a single ``safety-kernel.json`` holds two
tiers — ``rules`` (no loop build may change) and ``kernel`` (an unattended
build may not change) — read only through ``safety_kernel.py``.  The release
train refuses on a kernel-range violation judged by the list at the range's
base commit.

Seven acceptance criteria:

  AC-1  load() returns both tiers; missing file, bad JSON, and entry without
        ``why`` each raise KernelListError.  Red-first.
  AC-2  touches_kernel parity with h's PROTECTED_KERNEL — hits and misses.
        Red-first.
  AC-3  tier_of rules-wins-over-kernel semantics.  Red-first.
  AC-4  every non-may_be_absent entry exists on disk.  Red-first.
  AC-5  check_range violations: unattended kernel edit, session rules edit,
        hand commit, unresolved master, points.jsonl range.  Red-first.
  AC-6  judged at base: list at base, not HEAD; no list at base → no
        violations.  Red-first.
  AC-7  control: check-range exits 0 on hand commits, 2 on corrupt list.
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


# ── Expected PROTECTED_KERNEL from h ──────────────────────────────────────
# Source: 2026-10-03h-an-auto-planned-batch-stays-inside-its-rails.md,
# "What changes" item 1.  Every path here must be a kernel hit.

_PROTECTED_KERNEL: tuple[str, ...] = (
    "skills/ilk-loop/scripts/run_local_checks.py",
    "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
    "skills/ilk-loop/scripts/run_ilk_loop_claude.ps1",
    "skills/ilk-loop/scripts/ship_audit.py",
    "skills/ilk-loop/scripts/verification_record.py",
    "skills/ilk-loop/scripts/verify_attribution.py",
    "skills/ilk-loop/scripts/batch_gate.py",
    "skills/ilk-loop/scripts/ship_transition.py",
    "skills/ilk-loop/scripts/plan_lint.py",
    "skills/ilk-loop/scripts/plan_preflight.py",
    "skills/ilk-loop/scripts/ilk_audit.py",
    "skills/ilk-watchdog/scripts/triage_apply.py",
    "skills/ilk-ship/scripts/release_train.py",
    "skills/ilk-upgrade/scripts/ilk_release.py",
    "skills/ilk-self-improve/scripts/autoplan.py",
    "skills/ilk-self-improve/scripts/autoplan_rails.py",
    ".ilk-launch.json",
    "tests/invariants/",
    # Basename — conftest.py always hits kernel.
    "conftest.py",
)


# ── Helpers ───────────────────────────────────────────────────────────────

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


def _make_auto_planned_master(tmp_repo: Path) -> Path:
    """Create a plans dir with an auto_planned: true master."""
    plans = tmp_repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        auto_planned: true
        ---
        # auto-planned batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "add plans"], tmp_repo)
    return plans


def _make_session_master(tmp_repo: Path) -> Path:
    """Create a plans dir with a session master (no auto_planned)."""
    plans = tmp_repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        ---
        # session batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "add plans"], tmp_repo)
    return plans


def _trailered_commit(tmp_repo: Path, file: Path, content: str,
                      slug: str = "the-safety-kernel-is-one-list") -> str:
    """Commit with a [plan:<slug>#step-N] trailer, return short sha."""
    file.write_text(content, encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m",
          f"feat(x): change [plan:{slug}#step-0]"], tmp_repo)
    return _git_output(["rev-parse", "--short", "HEAD"], tmp_repo)


def _hand_commit(tmp_repo: Path, file: Path, content: str) -> str:
    """Commit without a plan trailer, return short sha."""
    file.write_text(content, encoding="utf-8")
    _git(["add", "."], tmp_repo)
    _git(["commit", "-m", "manual edit"], tmp_repo)
    return _git_output(["rev-parse", "--short", "HEAD"], tmp_repo)


def _run_cli(args: list[str], cwd: Path,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run safety_kernel.py CLI."""
    cmd = [sys.executable, str(_SCRIPTS / "safety_kernel.py"), *args]
    merged = {**os.environ}
    if env:
        merged.update(env)
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, env=merged,
    )


def _write_points_jsonl(repo: Path, entries: list[dict]) -> Path:
    """Write a points.jsonl file with given entries."""
    ledger = repo / ".ilk-data" / "runtime"
    ledger.mkdir(parents=True, exist_ok=True)
    p = ledger / "points.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return p


# ── xfail markers ─────────────────────────────────────────────────────────
# These tests xfail(strict=True) until safety_kernel.py and
# safety-kernel.json exist.  Each imports the module inside the test body
# so the xfail is on the import, not on pytest collection.

_xfail_no_module = pytest.mark.xfail(
    reason="safety_kernel.py does not exist yet (step 1 delivers it)",
    strict=True,
)


# ── AC-1: load() semantics ───────────────────────────────────────────────

@_xfail_no_module
def test_load_returns_both_tiers(tmp_path: Path) -> None:
    """load() on the shipped file returns a dict with 'rules' and 'kernel'."""
    from safety_kernel import load  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    result = load(repo)
    assert "rules" in result, f"load() missing 'rules' key: {result.keys()}"
    assert "kernel" in result, f"load() missing 'kernel' key: {result.keys()}"
    assert isinstance(result["rules"], list) and len(result["rules"]) > 0
    assert isinstance(result["kernel"], list) and len(result["kernel"]) > 0


@_xfail_no_module
def test_load_missing_file_raises(tmp_path: Path) -> None:
    """Missing safety-kernel.json raises KernelListError."""
    from safety_kernel import KernelListError, load  # type: ignore[import-untyped]
    with pytest.raises(KernelListError, match="safety-kernel"):
        load(tmp_path)


@_xfail_no_module
def test_load_invalid_json_raises(tmp_path: Path) -> None:
    """Corrupt JSON raises KernelListError."""
    from safety_kernel import KernelListError, load  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    kdir = repo / "skills" / "ilk-loop"
    kdir.mkdir(parents=True)
    (kdir / "safety-kernel.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(KernelListError):
        load(repo)


@_xfail_no_module
def test_load_entry_without_why_raises(tmp_path: Path) -> None:
    """An entry missing 'why' raises KernelListError."""
    from safety_kernel import KernelListError, load  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    kdir = repo / "skills" / "ilk-loop"
    kdir.mkdir(parents=True)
    (kdir / "safety-kernel.json").write_text(
        json.dumps({"schema": 1, "rules": [{"path": "x.py"}], "kernel": []}),
        encoding="utf-8",
    )
    with pytest.raises(KernelListError, match="why"):
        load(repo)


# ── AC-2: touches_kernel parity with h ───────────────────────────────────

@_xfail_no_module
def test_touches_kernel_hits(tmp_path: Path) -> None:
    """touches_kernel hits paths that are in or under the kernel."""
    from safety_kernel import load, touches_kernel  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)

    hits = [
        "skills/ilk-loop/scripts/plan_lint.py",       # exact file
        "./skills/ilk-loop/scripts/",                  # directory with dot-prefix
        "skills/foo/tests/conftest.py",                # basename conftest.py
        "tests/invariants/test_x.py",                  # under rules dir
        ".ilk-launch.json",                            # exact file
    ]
    for p in hits:
        assert touches_kernel(p, kernel=kernel) is not None, (
            f"touches_kernel({p!r}) should hit, got None"
        )


@_xfail_no_module
def test_touches_kernel_misses(tmp_path: Path) -> None:
    """touches_kernel misses paths outside the kernel."""
    from safety_kernel import load, touches_kernel  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)

    misses = [
        "skills/ilk-feedback/scripts/collect.py",     # different skill
        "skills/ilk-loop/scripts/plan_lint_helpers.py",  # not in list
    ]
    for p in misses:
        assert touches_kernel(p, kernel=kernel) is None, (
            f"touches_kernel({p!r}) should miss, got a hit"
        )


@_xfail_no_module
def test_touches_kernel_covers_all_protected_kernel(tmp_path: Path) -> None:
    """Every path in h's PROTECTED_KERNEL is a kernel hit."""
    from safety_kernel import load, touches_kernel  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)

    for p in _PROTECTED_KERNEL:
        assert touches_kernel(p, kernel=kernel) is not None, (
            f"PROTECTED_KERNEL entry {p!r} is not a kernel hit — "
            f"the safety-kernel.json must cover every entry in h's tuple"
        )


# ── AC-3: tier_of rules-wins-over-kernel ─────────────────────────────────

@_xfail_no_module
def test_tier_of_rules_path(tmp_path: Path) -> None:
    """A path under tests/invariants/ is 'rules' tier."""
    from safety_kernel import load, tier_of  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)
    result = tier_of("tests/invariants/x.py", kernel=kernel)
    assert result is not None, "tier_of should hit tests/invariants/x.py"
    assert result[0] == "rules", f"expected 'rules', got {result[0]!r}"


@_xfail_no_module
def test_tier_of_kernel_path(tmp_path: Path) -> None:
    """A kernel-only path is 'kernel' tier."""
    from safety_kernel import load, tier_of  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)
    result = tier_of("skills/ilk-loop/scripts/batch_gate.py", kernel=kernel)
    assert result is not None, "tier_of should hit batch_gate.py"
    assert result[0] == "kernel", f"expected 'kernel', got {result[0]!r}"


@_xfail_no_module
def test_tier_of_directory_with_rules_file_wins(tmp_path: Path) -> None:
    """A directory containing a rules file returns 'rules' (rules wins)."""
    from safety_kernel import load, tier_of  # type: ignore[import-untyped]
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    kernel = load(repo)
    # skills/ilk-loop/scripts/ contains both rules (safety_kernel.py) and
    # kernel (batch_gate.py) entries; rules must win.
    result = tier_of("skills/ilk-loop/scripts/", kernel=kernel)
    assert result is not None, "tier_of should hit the scripts directory"
    assert result[0] == "rules", (
        f"directory with a rules file must return 'rules', got {result[0]!r}"
    )


# ── AC-4: every non-may_be_absent entry exists ───────────────────────────

@_xfail_no_module
def test_all_required_entries_exist_on_disk() -> None:
    """Every non-may_be_absent entry in safety-kernel.json exists in this repo."""
    from safety_kernel import load  # type: ignore[import-untyped]
    kernel = load(_REPO_ROOT)
    missing = []
    for tier_name in ("rules", "kernel"):
        for entry in kernel[tier_name]:
            if entry.get("may_be_absent", False):
                continue
            p = _REPO_ROOT / entry["path"]
            if not p.exists():
                # Directory entries end with / — check as directory
                if entry["path"].endswith("/"):
                    if not p.is_dir():
                        missing.append(entry["path"])
                else:
                    missing.append(entry["path"])
    assert missing == [], (
        f"Non-may_be_absent entries missing on disk: {missing}"
    )


# ── AC-5: check_range violations ─────────────────────────────────────────

@_xfail_no_module
def test_check_range_unattended_kernel_edit(tmp_path: Path) -> None:
    """auto_planned build editing a kernel file → kernel-edit-by-unattended-build."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 1, f"expected violations (exit 1), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    violations = data if isinstance(data, list) else data.get("violations", data.get("result", []))
    assert any(v.get("reason") == "kernel-edit-by-unattended-build" for v in violations), (
        f"expected kernel-edit-by-unattended-build in {violations}"
    )


@_xfail_no_module
def test_check_range_session_rules_edit(tmp_path: Path) -> None:
    """Session master (no auto_planned) editing rules → rules-edit-by-loop-build."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_session_master(repo)
    target = repo / "tests" / "invariants" / "a.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 1, f"expected violations (exit 1), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    violations = data if isinstance(data, list) else data.get("violations", data.get("result", []))
    assert any(v.get("reason") == "rules-edit-by-loop-build" for v in violations), (
        f"expected rules-edit-by-loop-build in {violations}"
    )


@_xfail_no_module
def test_check_range_hand_commit_no_violation(tmp_path: Path) -> None:
    """A hand commit (no trailer) editing rules → no violation."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    target = repo / "tests" / "invariants" / "a.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _hand_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 0, f"expected clean (exit 0), got {r.returncode}\n{r.stdout}\n{r.stderr}"


@_xfail_no_module
def test_check_range_unresolved_master(tmp_path: Path) -> None:
    """Trailered commit whose slug is in no master → kernel-edit-unresolved-master."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n", slug="nonexistent-slug")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 1, f"expected violations (exit 1), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    violations = data if isinstance(data, list) else data.get("violations", data.get("result", []))
    assert any(v.get("reason") == "kernel-edit-unresolved-master" for v in violations), (
        f"expected kernel-edit-unresolved-master in {violations}"
    )


@_xfail_no_module
def test_check_range_points_jsonl_range(tmp_path: Path) -> None:
    """Untrailered commit inside a points.jsonl range → attributed by ledger."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)

    # Commit BEFORE so we can record its sha in points.jsonl.
    before_file = repo / "marker.txt"
    _hand_commit(repo, before_file, "before\n")
    before_sha = _git_output(["rev-parse", "HEAD"], repo)

    # Now make an untrailered commit editing a kernel file.
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _hand_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)

    # Write points.jsonl attributing the range to the auto-planned master.
    _write_points_jsonl(repo, [{
        "slug": "sub",
        "master": "MASTER-2026-10-03k",
        "before": before_sha,
        "after": head,
        "writer": "driver",
    }])

    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 1, f"expected violations (exit 1), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    violations = data if isinstance(data, list) else data.get("violations", data.get("result", []))
    assert any(v.get("reason") == "kernel-edit-by-unattended-build" for v in violations), (
        f"expected kernel-edit-by-unattended-build via points.jsonl in {violations}"
    )


# ── AC-6: judged at base ─────────────────────────────────────────────────

@_xfail_no_module
def test_check_range_judged_at_base_not_head(tmp_path: Path) -> None:
    """A range that deletes the list at base and then edits kernel still
    produces the violation (judged by the list at base, not HEAD)."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)

    # Commit 1: delete batch_gate.py from the kernel list.
    kfile = repo / "skills" / "ilk-loop" / "safety-kernel.json"
    data = json.loads(kfile.read_text(encoding="utf-8"))
    data["kernel"] = [e for e in data["kernel"]
                      if e.get("path") != "skills/ilk-loop/scripts/batch_gate.py"]
    kfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "remove batch_gate from list [plan:sub#step-0]"], repo)

    # Commit 2: edit batch_gate.py (auto-planned).
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)

    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 1, f"expected violations (exit 1), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    violations = data if isinstance(data, list) else data.get("violations", data.get("result", []))
    assert any(v.get("reason") == "kernel-edit-by-unattended-build" for v in violations), (
        f"judged-at-base: violation must appear even though HEAD's list "
        f"no longer contains batch_gate.py: {violations}"
    )


@_xfail_no_module
def test_check_range_no_list_at_base_gives_no_violations(tmp_path: Path) -> None:
    """A range whose base has no list → no violations, CLI prints judged_by: null."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    # Add the list AFTER base.
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    target = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(repo, target, "# edited\n")
    head = _git_output(["rev-parse", "HEAD"], repo)

    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans), "--json"],
        repo,
    )
    assert r.returncode == 0, f"expected clean (exit 0), got {r.returncode}\n{r.stdout}\n{r.stderr}"
    data = json.loads(r.stdout)
    # judged_by should be null when no list at base.
    judged = data.get("judged_by") if isinstance(data, dict) else None
    assert judged is None, (
        f"judged_by should be null when no list at base, got {judged}"
    )


# ── AC-7: control — exit codes ───────────────────────────────────────────

@_xfail_no_module
def test_check_range_exits_0_on_hand_commits(tmp_path: Path) -> None:
    """CLI check-range exits 0 on a range of hand commits only."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    _copy_kernel_json(repo)
    plans = _make_auto_planned_master(repo)
    f1 = repo / "a.txt"
    _hand_commit(repo, f1, "a\n")
    f2 = repo / "b.txt"
    _hand_commit(repo, f2, "b\n")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans)],
        repo,
    )
    assert r.returncode == 0, (
        f"hand commits should exit 0, got {r.returncode}\n{r.stdout}\n{r.stderr}"
    )


@_xfail_no_module
def test_check_range_exits_2_on_corrupt_list(tmp_path: Path) -> None:
    """CLI check-range exits 2 when the list at base is corrupt."""
    import safety_kernel  # noqa: F401 — xfail guard
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # Write a corrupt list and commit it as base.
    kdir = repo / "skills" / "ilk-loop"
    kdir.mkdir(parents=True)
    (kdir / "safety-kernel.json").write_text("{not json", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "corrupt list"], repo)
    base = _git_output(["rev-parse", "HEAD"], repo)
    plans = _make_session_master(repo)
    f1 = repo / "a.txt"
    _hand_commit(repo, f1, "a\n")
    head = _git_output(["rev-parse", "HEAD"], repo)
    r = _run_cli(
        ["check-range", "--repo", str(repo), "--base", base, "--head", head,
         "--plans-dir", str(plans)],
        repo,
    )
    assert r.returncode == 2, (
        f"corrupt list should exit 2, got {r.returncode}\n{r.stdout}\n{r.stderr}"
    )