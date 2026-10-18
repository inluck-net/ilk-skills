"""Pin commands/ilk2-plan.md as the ilk2-native planner (docs/07 §7.4.2).

The new command is built BESIDE commands/ilk-plan.md (V1's planner, which must
stay V1's) and must speak ilk2's delivery surface: drafts under ~/.ilk2/plans/,
delivery by `ilk2 submit`, and none of V1's paths, scripts or runner.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ILK2_PLAN = REPO_ROOT / "commands" / "ilk2-plan.md"
ILK_PLAN = REPO_ROOT / "commands" / "ilk-plan.md"

# The delivery surface the planner must speak (docs/07 §7.4.2).
REQUIRED = (
    "ilk2 submit --check",
    "ilk2 submit --project",
    "ilk2 project add",
    "~/.ilk2/plans/",
    "~/.ilk2/worker-template",
)

# V1's paths, scripts and runner: none of these may appear in the new planner.
FORBIDDEN = (
    "ilk_paths.py",
    "register_project.py",
    "worker_mcp.py",
    "loop_status.py",
    "plan_preflight.py",
    "launch.ps1",
    "~/.ilk-data",
    "status: queued",
    "current_subplan",
)

# V1's planner opens with this sentence; commands/ilk-plan.md must keep it.
ILK_PLAN_FIRST_LINE_PREFIX = "Plan a new batch of work for the ilk loop"


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def test_ilk2_plan_command_exists() -> None:
    text = _text(ILK2_PLAN)
    assert text.strip(), "commands/ilk2-plan.md is empty"


def test_ilk2_plan_first_lines_say_what_it_is() -> None:
    head = "\n".join(_text(ILK2_PLAN).lstrip().splitlines()[:12])
    assert "ilk2-native" in head, "first lines must name the ilk2-native planner"
    assert "/ilk-plan" in head, "first lines must say it is built beside /ilk-plan"
    assert "docs/07" in head, "first lines must cite the switch (docs/07 §7.4.5)"


def test_ilk2_plan_delivers_through_ilk2_submit() -> None:
    text = _text(ILK2_PLAN)
    for needle in REQUIRED:
        assert needle in text, f"commands/ilk2-plan.md does not contain {needle!r}"


def test_ilk2_plan_states_the_step_size_bounds() -> None:
    """Rule 1 (docs/07 §7.4.2): at most 6 files and 12,000 characters per
    plan, per step — the measures ilk2 reports as files-per-step and
    chars-per-step."""
    text = _text(ILK2_PLAN)
    for needle in ("at most 6", "12,000", "files-per-step", "chars-per-step"):
        assert needle in text, f"commands/ilk2-plan.md does not contain {needle!r}"


def test_ilk2_plan_justifies_dependencies() -> None:
    """Rule 2: depends_on only for shared files (scope_paths overlap) or a
    needed behaviour; serial-chain is the warning for a chain that is not."""
    text = _text(ILK2_PLAN)
    assert "depends_on" in text, "the dependency rule must name depends_on"
    assert "share files" in text, "the dependency rule must require shared files"
    assert "scope_paths" in text, "the dependency rule must name scope_paths"
    assert "serial-chain" in text, "the serial-chain warning must be named"


def test_ilk2_plan_qc_runs_submit_check() -> None:
    """Rule 3: the QC section runs `ilk2 submit --check` and fails on its
    refusals AND its plan-shape warnings before submitting."""
    text = _text(ILK2_PLAN)
    start = text.find("## 7.")
    end = text.find("## 8.")
    assert start != -1 and end != -1 and start < end, "QC section (## 7.) not found"
    qc = text[start:end]
    assert "ilk2 submit --check" in qc, "the QC section must run ilk2 submit --check"


def test_ilk2_plan_verification_subplan_needs_verify_command() -> None:
    """Omission condition 1: without a project `verify_command` there is
    nothing for the sub-plan's single step to run — write none."""
    text = _text(ILK2_PLAN)
    assert "verify_command" in text, "the opt-out must name verify_command"
    assert "the project's instructions omit it" in text, (
        "the MASTER must record which omit reason applied"
    )


def test_ilk2_plan_verification_subplan_follows_project_instructions() -> None:
    """Omission condition 2: the project's own instructions (AGENTS.md /
    CLAUDE.md) may say to omit the verification sub-plan — this repo's
    operator preference (AGENTS.md, 2026-10-11) does."""
    text = _text(ILK2_PLAN)
    assert "its `AGENTS.md` / `CLAUDE.md`" in text, (
        "the opt-out must name the project's own instructions"
    )
    assert "the project's instructions omit it" in text


def test_ilk2_plan_verification_subplan_is_omittable() -> None:
    """A master does not have to end with a verification sub-plan: the
    task can still ask for one, and an omission says which reason applied."""
    text = _text(ILK2_PLAN)
    assert "the task asks for one" in text, (
        "the task asking for one must still get a verification sub-plan"
    )
    assert "which reason applied" in text, (
        "an omission must say in the MASTER which reason applied"
    )
    for needle in (
        "Every master MUST end with a batch-verification sub-plan",
        "not optional; every batch ends with a verified suite",
        "A master without one is a HARD lint finding",
        "A master without this sub-plan as the last registry entry",
    ):
        assert needle not in text, (
            f"commands/ilk2-plan.md still makes the verification sub-plan "
            f"unconditional: contains {needle!r}"
        )


def test_ilk2_plan_leaves_no_section_for_the_loop_to_fill() -> None:
    """ilk2 never edits a plan file after submission, so a section the
    loop fills during execution is dead weight and the instruction to
    write one misleads the planner (gh-resolve 18b)."""
    text = _text(ILK2_PLAN)
    assert "loop fills" not in text, (
        "commands/ilk2-plan.md still tells the planner to leave a section "
        "for the loop to fill"
    )
    assert 'Empty "Findings"' not in text, (
        "commands/ilk2-plan.md still asks for an empty Findings section"
    )


def test_ilk2_plan_verification_step_names_expected_contract_changes() -> None:
    """The verification step's text lists the batch's EXPECTED contract
    changes (behaviour changed on purpose), one line each, so the worker
    can tell an intended change from a regression in the suite's
    failures (gh-resolve 18b). The worker starts from that step's text —
    the MASTER is readable, but it is not what it starts from."""
    text = _text(ILK2_PLAN)
    assert "expected contract changes" in text, (
        "the verification step must list the batch's expected contract changes"
    )
    for needle in ("renamed verbs", "removed fields", "changed defaults"):
        assert needle in text, (
            f"the expected-contract-changes list must name {needle!r} as an "
            f"example of an intended change"
        )


def test_ilk2_plan_passes_verified_when_the_batch_verifies() -> None:
    """With a verification sub-plan, the checker runs with `--verified`:
    its importer-coverage findings are advisory, because ilk2's
    verification step and attribution cover them. Without one they stay
    hard and the step gates must run the importers' tests."""
    text = _text(ILK2_PLAN)
    assert "--verified" in text, (
        "commands/ilk2-plan.md must run plan_check with --verified when the "
        "batch has a verification sub-plan"
    )
    assert "importers' tests" in text, (
        "without a verification sub-plan the importer-coverage rule stays "
        "hard: the step gates must run the importers' tests"
    )


def test_ilk2_plan_reads_verify_command_from_ilk2_project_show() -> None:
    """The project's `verify_command` comes from `ilk2 project show
    <project>` (its `config.verify_command`), not from the store."""
    text = _text(ILK2_PLAN)
    assert "ilk2 project show" in text, (
        "commands/ilk2-plan.md must read verify_command via ilk2 project show"
    )
    assert "config.verify_command" in text, (
        "the read must name the config.verify_command field"
    )
    assert "not from the store" in text, (
        "the planner must not read verify_command from the store"
    )


def test_ilk2_plan_runs_its_own_checker_not_v1s() -> None:
    """The planner's QC lints must call ilk2's own checker, not V1's.

    `commands/ilk2-plan.md` used to invoke
    `<skill-root>/ilk-loop/scripts/plan_lint.py`, which goes away with V1
    (docs/07 §7.4.2). It now calls `skills/ilk2-plan/scripts/plan_check.py`
    — the standalone authoring checker, resolved through `<skill-root>` like
    the file's other skill paths.
    """
    text = _text(ILK2_PLAN)
    assert "ilk2-plan/scripts/plan_check.py" in text, (
        "commands/ilk2-plan.md must name skills/ilk2-plan/scripts/plan_check.py"
    )
    assert "ilk-loop/scripts" not in text, (
        "commands/ilk2-plan.md must not call into V1's scripts folder"
    )


def test_ilk2_plan_has_no_v1_paths_scripts_or_runner() -> None:
    text = _text(ILK2_PLAN)
    for needle in FORBIDDEN:
        assert needle not in text, f"commands/ilk2-plan.md still contains V1's {needle!r}"


def test_ilk_plan_first_line_unchanged() -> None:
    first = _text(ILK_PLAN).lstrip().splitlines()[0]
    assert first.startswith(ILK_PLAN_FIRST_LINE_PREFIX), (
        "commands/ilk-plan.md is no longer V1's planner: "
        f"first line is {first!r}"
    )
