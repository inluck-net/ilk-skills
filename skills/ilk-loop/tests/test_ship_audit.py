"""Tests verifying ship-integrity and ship-audit.

Part 1 (step 0/1): drives ``test_ship_integrity`` (dot-sourced from the driver)
against a shipped sub-plan with a red gate and asserts the three formerly-dead
defects are fixed:

Defect 1 — ``|| true`` masks exit code → fixed: uses ``|| si_exit=$?`` capture
Defect 2 — ``grep -oP`` is GNU-only   → fixed: Python extracts slug
Defect 3 — ``sed -i`` fails on BSD    → fixed: Python reverts status

Part 2 (step 2): tests ``ship_audit.py`` — the pure predicate that checks
step-commit presence AND gate outcome (AC-5 through AC-8).
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_runner_and_call(func_call: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Dot-source the driver and execute *func_call* in the same shell."""
    env = {"ILK_DOTSOURCE_ONLY": "1"}
    if env_extra:
        env.update(env_extra)
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER}' 2>/dev/null; "
        f"set +e; "  # driver sets -e; the function handles errors internally
        f"{func_call}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _make_plans_dir(tmp: Path) -> tuple[Path, Path]:
    """Create a minimal plans dir with a MASTER and a shipped sub-plan.

    Uses ``docs/plans/`` layout so ``get_plans_dir`` (legacy walk-up) finds it.
    """
    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-14-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-14-test
        status: active
        ---
        # test
    """))
    subplan = plans / "2026-08-14-test-slug.md"
    subplan.write_text(textwrap.dedent("""\
        ---
        plan: test-slug
        status: shipped
        current_step: 3
        local_checks:
          - command: echo ok
            timeout: 10
        ---
        ### Step 0
        ### Step 1
        ### Step 2
    """))
    return plans, subplan


# ── Defect 1: || true masks exit code ────────────────────────────────────────

def test_defect1_ship_integrity_detects_violation(tmp_path: Path) -> None:
    """``test_ship_integrity`` must return non-zero when a red gate is recorded.

    The ``|| true`` antipattern (now fixed) made ``si_exit`` always 0, so the
    violation branch was unreachable.  After the fix the function returns 1.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 2: grep -oP is GNU-only ──────────────────────────────────────────

def test_defect2_slug_extraction_portable(tmp_path: Path) -> None:
    """Slug extraction uses Python, so BSD ``grep`` cannot defeat it.

    The old ``grep -oP`` failed on BSD (exit 2) → slug empty → gate stays
    ``"null"`` → ``ship_integrity.py`` saw declared checks with no result but
    the exit was masked by ``|| true``.  After the fix, Python extracts the
    slug and the gate lookup works.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 3: sed -i without backup suffix ──────────────────────────────────

def test_defect3_status_revert_works(tmp_path: Path) -> None:
    """Status revert uses Python, so BSD ``sed -i`` cannot defeat it.

    The old ``sed -i 's/.../…/'`` (no backup suffix) fails on BSD with
    ``invalid command code f``.  After the fix, Python performs the revert
    and the file reads ``status: in-progress``.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    content = subplan.read_text()
    assert "status: in-progress" in content, (
        f"Expected status revert to in-progress, but file still contains:\n{content}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: ship_audit.py — the step-commit half (AC-5 through AC-8)
# ═══════════════════════════════════════════════════════════════════════════════

import ship_audit


# ── git helpers ───────────────────────────────────────────────────────────────

def _init_repo(path: Path) -> None:
    """Create a git repo with an initial commit so ``git log`` works."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path,
        capture_output=True, check=True,
    )
    (path / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "add", ".gitkeep"], cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True,
    )


def _commit_with_message(path: Path, subject: str, body: str = "") -> None:
    """Create a commit with a specific subject and optional body."""
    (path / "marker.txt").write_text(subject)
    subprocess.run(
        ["git", "add", "marker.txt"], cwd=path, capture_output=True, check=True,
    )
    msg = subject if not body else f"{subject}\n\n{body}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True, check=True,
    )


# ── AC-5: audit_ship is pure, returns correct shape ──────────────────────────

def test_audit_ship_returns_correct_shape_for_proven(tmp_path: Path) -> None:
    """A shipped sub-plan with all steps committed and green gate is proven."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): step 0 [plan:test-slug#step-0]",
    )
    _commit_with_message(
        tmp_path,
        "feat(foo): step 1 [plan:test-slug#step-1]",
    )
    _commit_with_message(
        tmp_path,
        "feat(foo): step 2 [plan:test-slug#step-2]",
    )
    result = ship_audit.audit_ship(
        status="shipped",
        body="### Step 0\n### Step 1\n### Step 2\n",
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="true",
        slug="test-slug",
        cwd=tmp_path,
    )
    assert result["proven"] is True
    assert result["missing_steps"] == []
    assert result["final_gate"] == "pass"
    assert result["reasons"] == []


def test_audit_ship_returns_correct_shape_for_unproven_red_gate(tmp_path: Path) -> None:
    """A shipped sub-plan with all steps committed but red gate is unproven."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): step 0 [plan:test-slug#step-0]",
    )
    _commit_with_message(
        tmp_path,
        "feat(foo): step 1 [plan:test-slug#step-1]",
    )
    _commit_with_message(
        tmp_path,
        "feat(foo): step 2 [plan:test-slug#step-2]",
    )
    result = ship_audit.audit_ship(
        status="shipped",
        body="### Step 0\n### Step 1\n### Step 2\n",
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="false",
        slug="test-slug",
        cwd=tmp_path,
    )
    assert result["proven"] is False
    assert result["missing_steps"] == []
    assert result["final_gate"] == "fail"
    assert len(result["reasons"]) == 1
    assert "gate" in result["reasons"][0].lower()


def test_audit_ship_returns_correct_shape_for_missing_steps(tmp_path: Path) -> None:
    """A shipped sub-plan with missing commits is unproven even with green gate."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): step 0 [plan:test-slug#step-0]",
    )
    # step 1 and 2 not committed
    result = ship_audit.audit_ship(
        status="shipped",
        body="### Step 0\n### Step 1\n### Step 2\n",
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="true",
        slug="test-slug",
        cwd=tmp_path,
    )
    assert result["proven"] is False
    assert result["missing_steps"] == [1, 2]
    assert result["final_gate"] == "pass"
    assert len(result["reasons"]) == 1
    assert "step" in result["reasons"][0].lower()


def test_audit_ship_non_shipped_is_always_proven() -> None:
    """Non-shipped sub-plans are always proven (nothing to audit)."""
    for status in ("pending", "in-progress", "blocked"):
        result = ship_audit.audit_ship(
            status=status,
            body="### Step 0\n",
            declared_checks=[],
            gate_passed="unknown",
            slug="test",
        )
        assert result["proven"] is True, f"status={status}"


# ── AC-6: full-message search (body-placed trailers count) ───────────────────

def test_step_heading_count() -> None:
    """``count_authored_steps`` extracts step numbers from headings."""
    body = "### Step 0\nfoo\n### Step 1\nbar\n### Step 2\nbaz\n"
    assert ship_audit.count_authored_steps(body) == [0, 1, 2]


def test_step_heading_count_with_gaps() -> None:
    """Non-contiguous step numbers are preserved."""
    body = "### Step 0\n### Step 3\n### Step 7\n"
    assert ship_audit.count_authored_steps(body) == [0, 3, 7]


def test_step_heading_count_empty() -> None:
    """No step headings → empty list."""
    assert ship_audit.count_authored_steps("just some text\n") == []


def test_check_step_commits_finds_subject_trailer(tmp_path: Path) -> None:
    """Trailers in the subject line are found."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): do step 0 [plan:my-slug#step-0]",
    )
    present, missing = ship_audit.check_step_commits("my-slug", [0, 1], cwd=tmp_path)
    assert present == [0]
    assert missing == [1]


def test_check_step_commits_finds_body_placed_trailer(tmp_path: Path) -> None:
    """Trailers in the commit body are found (AC-6: full-message search).

    This is the critical test — 1.3% of real commits place the trailer in the
    body.  A subject-only predicate would report these as missing and revert
    correct work.
    """
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(plan-lint): resolve whether a changed module has importers",
        body="[plan:a-shared-module-change-gates-on-its-callers#step-1]",
    )
    present, missing = ship_audit.check_step_commits(
        "a-shared-module-change-gates-on-its-callers", [1], cwd=tmp_path,
    )
    assert present == [1]
    assert missing == []


def test_check_step_commits_finds_comma_separated_trailers(tmp_path: Path) -> None:
    """Comma-separated step numbers in a single trailer are parsed."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): combined step [plan:my-slug#step-0,step-1]",
    )
    present, missing = ship_audit.check_step_commits("my-slug", [0, 1, 2], cwd=tmp_path)
    assert sorted(present) == [0, 1]
    assert missing == [2]


def test_check_step_commits_empty_steps() -> None:
    """No expected steps → empty results, no git call."""
    present, missing = ship_audit.check_step_commits("any-slug", [])
    assert present == []
    assert missing == []


# ── AC-8: no-gate sub-plan is exempt from gate half only ─────────────────────

def test_no_gate_sub_plan_exempt_from_gate_check(tmp_path: Path) -> None:
    """A sub-plan with no declared local_checks is NOT reported unproven for
    the gate half.  Missing-step commits still count.
    """
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): step 0 [plan:test-slug#step-0]",
    )
    result = ship_audit.audit_ship(
        status="shipped",
        body="### Step 0\n### Step 1\n",
        declared_checks=[],
        gate_passed="unknown",
        slug="test-slug",
        cwd=tmp_path,
    )
    # Gate is exempt (None), but step 1 is missing.
    assert result["proven"] is False
    assert result["missing_steps"] == [1]
    assert result["final_gate"] is None
    assert len(result["reasons"]) == 1
    assert "step 1" in result["reasons"][0]


def test_no_gate_sub_plan_all_steps_present(tmp_path: Path) -> None:
    """A no-gate sub-plan with all steps committed is proven."""
    _init_repo(tmp_path)
    _commit_with_message(
        tmp_path,
        "feat(foo): step 0 [plan:test-slug#step-0]",
    )
    _commit_with_message(
        tmp_path,
        "feat(foo): step 1 [plan:test-slug#step-1]",
    )
    result = ship_audit.audit_ship(
        status="shipped",
        body="### Step 0\n### Step 1\n",
        declared_checks=[],
        gate_passed="unknown",
        slug="test-slug",
        cwd=tmp_path,
    )
    assert result["proven"] is True
    assert result["final_gate"] is None
    assert result["reasons"] == []


# ── AC-7: predicate over 9 sub-plans reproduces 3/6 split ────────────────────

# Fixture: the 9 sub-plans of MASTER-2026-08-13, as they shipped.
# Each entry: (slug, step_headings, has_gate, gate_passed, expected_proven)
# gate_passed: "true"/"false"/"unknown" (no record)
_FIXTURE_08_13 = [
    # 3 proven: all steps committed, gate green
    ("the-sentinel-lands-where-readers-look", [0, 1, 2, 3], True, "true", True),
    ("the-postmortem-names-the-failing-command", [0, 1, 2, 3], True, "true", True),
    ("the-backlog-reader-survives-legacy-records", [0, 1, 2], True, "true", True),
    # 6 unproven: missing commits or red gate
    ("a-one-iteration-gate-failure-is-not-stuck", [0, 1, 2], True, "unknown", False),
    ("the-linter-knows-where-a-path-lives", [0, 1, 2, 3, 4], True, "false", False),
    ("one-batch-one-branch", [0, 1, 2, 3], True, "unknown", False),
    ("a-shared-module-change-gates-on-its-callers", [0, 1, 2, 3], True, "false", False),
    ("the-planner-shows-its-branch-targets", [0, 1, 2], True, "false", False),
    ("plan-lint-validates-with-the-runtime-parser", [0, 1, 2, 3], True, "false", False),
]


def test_audit_08_13_batch_produces_3_proven_6_unproven(tmp_path: Path) -> None:
    """AC-7: running the predicate over the 08-13 batch's 9 sub-plans
    reproduces exactly 3 proven / 6 unproven.

    Uses a committed fixture (not live git) so the test is repeatable.
    We create a git repo with commits for the "proven" sub-plans' steps.
    """
    _init_repo(tmp_path)

    # Create commits for the 3 proven sub-plans (all steps present).
    for slug, steps, _, _, _ in _FIXTURE_08_13:
        if slug in (
            "the-sentinel-lands-where-readers-look",
            "the-postmortem-names-the-failing-command",
            "the-backlog-reader-survives-legacy-records",
        ):
            for step_n in steps:
                _commit_with_message(
                    tmp_path,
                    f"feat({slug}): step {step_n} "
                    f"[plan:{slug}#step-{step_n}]",
                )

    proven_count = 0
    unproven_count = 0
    per_plan: dict[str, bool] = {}

    for slug, steps, has_gate, gate_passed, expected_proven in _FIXTURE_08_13:
        checks = [{"command": "echo ok", "timeout": 10}] if has_gate else []
        body = "\n".join(f"### Step {n}" for n in steps) + "\n"
        result = ship_audit.audit_ship(
            status="shipped",
            body=body,
            declared_checks=checks,
            gate_passed=gate_passed,
            slug=slug,
            cwd=tmp_path,
        )
        per_plan[slug] = result["proven"]
        if result["proven"]:
            proven_count += 1
        else:
            unproven_count += 1

    assert proven_count == 3, (
        f"Expected 3 proven, got {proven_count}. Per-plan: {per_plan}"
    )
    assert unproven_count == 6, (
        f"Expected 6 unproven, got {unproven_count}. Per-plan: {per_plan}"
    )

    # Verify per-sub-plan verdicts match the table.
    for slug, _, _, _, expected_proven in _FIXTURE_08_13:
        assert per_plan[slug] == expected_proven, (
            f"{slug}: expected proven={expected_proven}, got {per_plan[slug]}"
        )


# ── read_subplan_for_audit: file reader ──────────────────────────────────────

def test_read_subplan_for_audit_extracts_fields(tmp_path: Path) -> None:
    """``read_subplan_for_audit`` extracts status, body, checks, slug."""
    subplan = tmp_path / "test-plan.md"
    subplan.write_text(textwrap.dedent("""\
        ---
        plan: my-slug
        status: shipped
        current_step: 3
        local_checks:
          - command: echo ok
            timeout: 10
        ---
        ### Step 0
        do stuff
        ### Step 1
        more stuff
        ### Step 2
        final stuff
    """))
    info = ship_audit.read_subplan_for_audit(subplan)
    assert info["status"] == "shipped"
    assert info["slug"] == "my-slug"
    assert len(info["declared_checks"]) == 1
    assert "### Step 0" in info["body"]
    assert "### Step 2" in info["body"]


# ── Per-step gates are gates too (final_gate must not be None) ───────────────
#
# A sub-plan may declare `local_checks: []` in frontmatter and carry its real
# gates in per-step ```yaml blocks under `### Step N`.  /ilk-plan writes this
# shape, and `_detect_local_checks.py` already treats it as gated.  Two readers
# disagreed with it, so a gated sub-plan audited as if it had no gate at all:
#
#   ship_audit.read_subplan_for_audit  — parsed frontmatter only, so
#     `declared_checks` came back empty and `audit_ship` set
#     `final_gate: None` (ship_audit.py:177) no matter what the gate did.
#   run_ilk_loop_claude.sh test_ship_integrity — `head -20 | grep -qE
#     '^\s*local_checks:\s*$'` matches only block form in the first 20 lines,
#     so it `continue`d past these sub-plans and never enforced the gate.
#
# Observed 2026-08-21 on MASTER-2026-08-21-loop-execution-speed: all three
# sub-plans audited `final_gate: None`.

_PER_STEP_GATED = """\
---
plan: per-step-slug
status: shipped
current_step: 2
priority: P1
estimated_steps: 2
verification_tier: loop-verified
data_prereqs:
  - description: "a data prereq long enough to push local_checks past line 20"
    verify_cmd: "test -d /"
env_prereqs:
  - description: "an env prereq that also pushes the frontmatter down"
    verify_cmd: "test -d /"
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/thing.py"
---

### Step 0 — first
```yaml
local_checks:
  - command: echo step0
    timeout: 30
```
- do the thing
- Commit: `feat(x): thing [plan:per-step-slug#step-0]`

### Step 1 — second
```yaml
local_checks:
  - command: echo step1
    timeout: 30
```
- do the other thing
- Commit: `feat(x): other [plan:per-step-slug#step-1]`
"""


def test_read_subplan_sees_per_step_gates(tmp_path: Path) -> None:
    """Per-step ``local_checks`` blocks must land in ``declared_checks``."""
    subplan = tmp_path / "2026-08-21-per-step.md"
    subplan.write_text(_PER_STEP_GATED)
    info = ship_audit.read_subplan_for_audit(subplan)
    cmds = [c.get("command") for c in info["declared_checks"]]
    assert info["declared_checks"], (
        "frontmatter says `local_checks: []` but Steps 0 and 1 each declare a "
        f"gate; declared_checks came back empty. cmds={cmds}"
    )
    assert "echo step0" in cmds and "echo step1" in cmds, cmds


def test_final_gate_not_none_for_per_step_gated_subplan(tmp_path: Path) -> None:
    """A per-step-gated sub-plan must report a real verdict, never ``None``."""
    subplan = tmp_path / "2026-08-21-per-step.md"
    subplan.write_text(_PER_STEP_GATED)
    info = ship_audit.read_subplan_for_audit(subplan)
    _init_repo(tmp_path)
    for n in (0, 1):
        _commit_with_message(tmp_path, f"feat(x): s{n} [plan:per-step-slug#step-{n}]")
    result = ship_audit.audit_ship(
        slug=info["slug"],
        status=info["status"],
        body=info["body"],
        declared_checks=info["declared_checks"],
        gate_passed="true",
        cwd=tmp_path,
    )
    assert result["final_gate"] == "pass", (
        f"gate passed, so final_gate must be 'pass', got {result['final_gate']!r}. "
        f"full result={result}"
    )


def test_ship_integrity_does_not_skip_per_step_gated_subplan(tmp_path: Path) -> None:
    """The driver's detector must enforce the gate on a per-step-gated plan.

    A red gate is recorded, so ``test_ship_integrity`` must report a violation.
    Before the fix the narrow frontmatter grep skipped the file and returned 0.
    """
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-21-test.md").write_text(
        "---\nmaster_plan: 2026-08-21-test\nstatus: active\n---\n# test\n"
    )
    (plans / "2026-08-21-per-step.md").write_text(_PER_STEP_GATED)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"per-step-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        "red gate on a per-step-gated shipped sub-plan must be a violation; "
        f"got exit {result.returncode} (detector skipped the file).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
