r"""Tests for _stamp_reentry_note -- the re-entry note stamper.

When an iteration is killed (gtimeout or plan-amended), the runner stamps a
re-entry note into the active sub-plan's Findings section so the next session
knows which step was in flight and why it stopped.

AC-1: a sub-plan with an inline `## Findings` mention in "What changes", a real
      `## Findings` heading and one existing finding.  After stamping, the text
      above the real heading is byte-identical, and the note is the last block
      in the file, after the existing finding.  (Red-first.)
AC-2: the note heading starts with `#### Re-entry`.  No new line in the file
      matches `^### `, and the plan's step-heading parse returns the same steps
      before and after.  (Red-first.)
AC-3: with `## Findings` followed by another `## ` section, the note goes
      before that section.  (Red-first.)
AC-4: with reason `plan-amended`, the heading says `ended by a plan amendment`;
      with no reason, it says `killed at its bound`.  (Red-first.)
AC-5: a step whose first bullet is `- Write \`x.py\``: the note carries that
      bullet (the `\Q` defect is gone).  (Red-first.)
AC-6: a sub-plan with no `## Findings` gains one at EOF holding the note
      (control; that path exists today, apart from the level).

Tests dot-source the runner and call _stamp_reentry_note directly, the way
test_runner_timeout_dirty_tree.py does (sourced runner with ILK_DOTSOURCE_ONLY=1).
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


def _source_runner() -> dict:
    """Return env dict after dot-sourcing the runner with ILK_DOTSOURCE_ONLY=1."""
    result = subprocess.run(
        ["bash", "-c", (
            "export ILK_DOTSOURCE_ONLY=1; "
            f"source '{RUNNER}' 2>/dev/null; "
            "env"
        )],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, f"Failed to source runner: {result.stderr}"
    env = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    return env


def _init_repo(path: Path) -> None:
    """Create a minimal git repo with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)


def _setup_plans_dir(project: Path, plan_body: str) -> Path:
    """Create a docs/plans/ dir with one master and one sub-plan."""
    plans_dir = project / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = plans_dir / "MASTER-test.md"
    master.write_text(textwrap.dedent("""\
        ---
        title: test
        slug: test
        created: 2026-10-03T17:08:00+0800
        status: active
        priority: null
        pause_after_ship: false
        supervised_only: false
        base_branch: main
        branch: null
        goal: test
        out_of_scope: []
        cross_cutting_invariants: []
        ---

        # MASTER plan: test

        ## Sub-plan registry

        | # | Sub-plan | File | Status |
        |---|---|---|---|
        | 1 | sub-a | 2026-10-03-sub-a.md | in-progress |
    """), encoding="utf-8")
    sub = plans_dir / "2026-10-03-sub-a.md"
    sub.write_text(textwrap.dedent(plan_body), encoding="utf-8")
    return plans_dir


# ── Fixture sub-plan bodies ─────────────────────────────────────────────────

# AC-1: inline `## Findings` mention + real heading + one existing finding
PLAN_WITH_INLINE_MENTION = """\
    ---
    plan: sub-a
    status: in-progress
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 3
    last_updated: 2026-10-03
    ---

    # Sub-plan: fix the mocks

    ## What changes

    - Edit `foo.py` to add the `## Findings` section back.
    - Write `bar.py`.

    ## Steps

    ### Step 0 — classify failures
    - Run the failing tests and classify every failure.

    ### Step 1 — fix the mock
    - Write `mock.py`.

    ## Findings

    - One existing finding.
"""

# AC-3: `## Findings` followed by another `## ` section
PLAN_WITH_SECTION_AFTER = """\
    ---
    plan: sub-a
    status: in-progress
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-10-03
    ---

    # Sub-plan: fix the mocks

    ## Steps

    ### Step 0 — classify failures
    - Run the failing tests.

    ## Findings

    - Existing finding.

    ## Out of scope

    - Something else.
"""

# AC-5: step whose first bullet contains backslash-escaped content
PLAN_WITH_ESCAPED_BULLET = """\
    ---
    plan: sub-a
    status: in-progress
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-10-03
    ---

    # Sub-plan: fix the mocks

    ## Steps

    ### Step 0 — write the file
    - Write `x.py`.

    ## Findings
"""

# AC-6: no `## Findings` section at all
PLAN_WITHOUT_FINDINGS = """\
    ---
    plan: sub-a
    status: in-progress
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-10-03
    ---

    # Sub-plan: fix the mocks

    ## Steps

    ### Step 0 — classify failures
    - Run the failing tests.
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_stamp(
    project: Path, env: dict, slug: str = "sub-a", step: str = "0", reason: str = ""
) -> tuple[str, str]:
    """Call _stamp_reentry_note and return (plan_body, stderr)."""
    env_copy = dict(env)
    env_copy["ILK_DOTSOURCE_ONLY"] = "1"
    env_copy["PROJECT_PATH"] = str(project)

    reason_arg = f" '{reason}'" if reason else ""
    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        PROJECT_PATH='{project}'
        _stamp_reentry_note '{slug}' '{step}'{reason_arg}
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env_copy,
    )
    sub = project / "docs" / "plans" / "2026-10-03-sub-a.md"
    body = sub.read_text(encoding="utf-8") if sub.exists() else ""
    return body, result.stderr


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def env() -> dict:
    """Env dict from sourcing the runner."""
    return _source_runner()


# ── AC-1: inline mention → note lands after the real Findings ────────────────

@pytest.mark.xfail(strict=True, reason="stamper uses text.find('## Findings') which matches inline mentions")
class TestAC1InlineMention:
    """The note lands after the real ## Findings heading, not after an inline mention."""

    def test_note_after_real_heading_not_after_inline(self, tmp_path: Path, env: dict) -> None:
        """Text above the real heading is byte-identical; note is last block."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_INLINE_MENTION)
        sub = project / "docs" / "plans" / "2026-10-03-sub-a.md"

        before = sub.read_text(encoding="utf-8")
        # The real ## Findings heading is after the inline mention.
        real_findings_pos = before.index("## Findings\n\n- One existing finding.")
        inline_pos = before.index("`## Findings`")
        assert inline_pos < real_findings_pos, "inline mention must come before real heading"

        body, _stderr = _run_stamp(project, env)

        # The text before the real heading must be unchanged.
        real_pos_after = body.index("## Findings\n\n- One existing finding.")
        assert body[:real_pos_after] == before[:real_findings_pos], (
            "text before the real ## Findings heading must be byte-identical"
        )

        # The note must be the last block (after the existing finding).
        assert "#### Re-entry" in body, "note heading must be present"
        note_pos = body.index("#### Re-entry")
        finding_pos = body.index("- One existing finding.")
        assert note_pos > finding_pos, (
            "note must be after the existing finding, not between heading and finding"
        )


# ── AC-2: note heading is #### not ### ──────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="stamper writes ### Re-entry instead of #### Re-entry")
class TestAC2HeadingLevel:
    """The note heading starts with ####, not ###."""

    def test_no_new_h3_headings(self, tmp_path: Path, env: dict) -> None:
        """No new line matching ^###  is added by the stamp."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_INLINE_MENTION)
        sub = project / "docs" / "plans" / "2026-10-03-sub-a.md"

        before = sub.read_text(encoding="utf-8")
        import re
        h3_before = set(re.findall(r"^###\s+.+$", before, re.M))

        body, _stderr = _run_stamp(project, env)

        h3_after = set(re.findall(r"^###\s+.+$", body, re.M))
        new_h3 = h3_after - h3_before
        assert not new_h3, (
            f"stamp must not add new ### headings; new ones: {new_h3}"
        )

    def test_note_is_h4(self, tmp_path: Path, env: dict) -> None:
        """The re-entry note heading is #### Re-entry."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_INLINE_MENTION)

        body, _stderr = _run_stamp(project, env)

        assert "#### Re-entry" in body, (
            "note heading must start with #### Re-entry"
        )


# ── AC-3: note goes before the next ## section ──────────────────────────────

@pytest.mark.xfail(strict=True, reason="stamper inserts after heading instead of appending at end of section")
class TestAC3BeforeNextSection:
    """With ## Findings followed by ## Out of scope, the note goes before that section."""

    def test_note_between_findings_and_next_section(self, tmp_path: Path, env: dict) -> None:
        """The note is between the last finding and the next ## heading."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_SECTION_AFTER)

        body, _stderr = _run_stamp(project, env)

        note_pos = body.index("#### Re-entry")
        out_of_scope_pos = body.index("## Out of scope")
        existing_finding_pos = body.index("- Existing finding.")
        assert existing_finding_pos < note_pos < out_of_scope_pos, (
            "note must be between the existing finding and the next ## section"
        )


# ── AC-4: reason text ───────────────────────────────────────────────────────

class TestAC4ReasonText:
    """The note heading carries the correct reason text."""

    def test_default_reason_killed_at_bound(self, tmp_path: Path, env: dict) -> None:
        """With no reason arg, the heading says 'killed at its bound'."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_INLINE_MENTION)

        body, _stderr = _run_stamp(project, env)

        assert "killed at its bound" in body, (
            "default reason must be 'killed at its bound'"
        )

    @pytest.mark.xfail(strict=True, reason="stamper passes reason arg but the function ignores it")
    def test_plan_amended_reason(self, tmp_path: Path, env: dict) -> None:
        """With reason 'plan-amended', the heading says 'ended by a plan amendment'."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_INLINE_MENTION)

        body, _stderr = _run_stamp(project, env, reason="plan-amended")

        assert "ended by a plan amendment" in body, (
            "plan-amended reason must produce 'ended by a plan amendment'"
        )


# ── AC-5: backslash-escaped bullet carried ──────────────────────────────────

class TestAC5EscapedBullet:
    """A step whose first bullet is `- Write \\`x.py\\``: the note carries that bullet."""

    def test_step_bullet_in_note(self, tmp_path: Path, env: dict) -> None:
        """The note includes the step's first bullet text."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITH_ESCAPED_BULLET)

        body, _stderr = _run_stamp(project, env)

        assert "`x.py`" in body, (
            "note must carry the step's first bullet including backtick-escaped content"
        )


# ── AC-6: no Findings → creates one at EOF (control) ────────────────────────

class TestAC6NoFindings:
    """A sub-plan with no ## Findings gains one at EOF holding the note.

    This is a control test: it verifies that the existing "no Findings" path
    creates the section and places the note under it.  It does NOT check the
    note's heading level (that is AC-2's job).
    """

    def test_findings_created_at_eof(self, tmp_path: Path, env: dict) -> None:
        """## Findings is created and the note is under it."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, PLAN_WITHOUT_FINDINGS)
        sub = project / "docs" / "plans" / "2026-10-03-sub-a.md"

        before = sub.read_text(encoding="utf-8")
        assert "## Findings" not in before, "fixture must have no ## Findings"

        body, _stderr = _run_stamp(project, env)

        assert "## Findings" in body, "## Findings must be created"
        assert "Re-entry" in body, "re-entry note must be present (any heading level)"
        # The note must be after the new ## Findings heading.
        findings_pos = body.index("## Findings")
        note_pos = body.index("Re-entry")
        assert note_pos > findings_pos, (
            "note must be after the ## Findings heading"
        )