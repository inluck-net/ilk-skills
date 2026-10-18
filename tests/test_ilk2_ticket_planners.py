"""Pin the ilk2-native planner family built beside V1's (docs/07 §7.4.2).

commands/ilk2-inbox.md and commands/ilk2-lark.md are the ticket planners,
built BESIDE commands/ilk-inbox.md and commands/ilk-lark.md (which must stay
V1's) and keeping their ticket adapters — skills/ilk-inbox-tickets/scripts/
cli.py and skills/ilk-lark-tickets/scripts/cli.py. commands/ilk2-spec.md is
the spec pre-stage, built beside commands/ilk-spec.md. What they all change
is the planning and delivery: work is handed to /ilk2-plan, drafts go under
~/.ilk2/plans/, delivery is `ilk2 submit`, and a ticket's status update
names the ilk2 batch id.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ILK2_INBOX = REPO_ROOT / "commands" / "ilk2-inbox.md"
ILK2_LARK = REPO_ROOT / "commands" / "ilk2-lark.md"
ILK2_SPEC = REPO_ROOT / "commands" / "ilk2-spec.md"
ILK_INBOX = REPO_ROOT / "commands" / "ilk-inbox.md"
ILK_LARK = REPO_ROOT / "commands" / "ilk-lark.md"
ILK_SPEC = REPO_ROOT / "commands" / "ilk-spec.md"

# Every command this family adds must speak /ilk2-plan instead of /ilk-plan.
BASE_REQUIRED = ("/ilk2-plan",)

# The planning/delivery surface the ticket planners must speak (docs/07 §7.4.2).
REQUIRED = BASE_REQUIRED + (
    "ilk2 submit",
    "~/.ilk2/plans/",
)

# V1's planning and delivery leftovers: none may appear in any new command.
FORBIDDEN = (
    "docs/plans/",
    "ilk_paths.py",
    "loop_status.py",
    "~/.ilk-data/projects",
)

# The spec pre-stage additionally must not reach for V1's scripts: its lint is
# the ilk2 checker (skills/ilk2-plan/scripts/plan_check.py), not plan_lint.py.
SPEC_FORBIDDEN = FORBIDDEN + ("ilk-loop/scripts",)

# V1's run/launch surface — it answers questions about an engine that is off.
LAUNCH_TEXT = (
    "type `/ilk`",
    "/ilk-schedule",
    "/ilk-status",
    "/ilk-doctor",
)

# Each ticket planner keeps its own ticket adapter's cli.py.
TICKET_CLI = {
    ILK2_INBOX: "ilk-inbox-tickets/scripts/cli.py",
    ILK2_LARK: "ilk-lark-tickets/scripts/cli.py",
}

# V1's commands open with these sentences; they must keep them.
ILK_INBOX_FIRST_LINE_PREFIX = (
    "Plan a new batch of work for the ilk loop, sourced from eligible"
)
ILK_LARK_FIRST_LINE_PREFIX = (
    "Plan a new batch of work for the ilk loop, sourced from `可执行` tickets"
)
ILK_SPEC_FIRST_LINE_PREFIX = (
    "Elaborate a thin, under-specified task into a detailed-enough design spec"
)


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _ticket_planners() -> dict[Path, str]:
    return {p: _text(p) for p in (ILK2_INBOX, ILK2_LARK)}


def _all() -> dict[Path, str]:
    return {p: _text(p) for p in (ILK2_INBOX, ILK2_LARK, ILK2_SPEC)}


def test_both_ticket_planner_commands_exist() -> None:
    for path, text in _ticket_planners().items():
        assert text.strip(), f"{path.name} is empty"


def test_ticket_planners_hand_off_to_ilk2_plan_and_submit() -> None:
    """Both name /ilk2-plan and `ilk2 submit` — the planner they delegate
    to, and the delivery that replaces V1's 'commit and push'."""
    for path, text in _ticket_planners().items():
        for needle in REQUIRED:
            assert needle in text, f"{path.name} does not contain {needle!r}"


def test_ticket_planners_keep_their_ticket_cli_calls() -> None:
    """The ticket adapters stay: they read and write the inbox / the Bitable."""
    for path, cli in TICKET_CLI.items():
        text = _text(path)
        assert cli in text, f"{path.name} no longer calls {cli}"
        assert "cli.py update" in text, (
            f"{path.name} no longer writes ticket status back via cli.py update"
        )


def test_ticket_planners_name_the_ilk2_batch_id_on_status_updates() -> None:
    """A status update written back to a ticket names the batch id — there is
    no repo path to link to once drafts live outside every repo."""
    for path, text in _ticket_planners().items():
        assert "batch id" in text, f"{path.name} never names the ilk2 batch id"
        assert "ilk2 batch" in text, (
            f"{path.name} does not write 'ilk2 batch <batch-id>' into a ticket"
        )


def test_new_commands_have_no_v1_planning_or_delivery_paths() -> None:
    for path, text in _all().items():
        for needle in FORBIDDEN:
            assert needle not in text, f"{path.name} still contains V1's {needle!r}"


def test_new_commands_have_no_v1_launch_text() -> None:
    for path, text in _all().items():
        for needle in LAUNCH_TEXT:
            assert needle not in text, f"{path.name} still says to run {needle!r}"


# --- commands/ilk2-spec.md: the spec pre-stage ----------------------------

def test_ilk2_spec_command_exists() -> None:
    text = _text(ILK2_SPEC)
    assert text.strip(), "commands/ilk2-spec.md is empty"


def test_ilk2_spec_hands_off_to_ilk2_plan() -> None:
    """The spec pre-stage emits a /ilk2-plan-ready task description, never a
    plan and never a submission."""
    text = _text(ILK2_SPEC)
    for needle in BASE_REQUIRED:
        assert needle in text, f"commands/ilk2-spec.md does not contain {needle!r}"


def test_ilk2_spec_has_no_v1_scripts() -> None:
    """Its lint is skills/ilk2-plan/scripts/plan_check.py, not V1's
    plan_lint.py under the V1 kernel's scripts folder."""
    text = _text(ILK2_SPEC)
    for needle in SPEC_FORBIDDEN:
        assert needle not in text, f"commands/ilk2-spec.md still contains {needle!r}"


def test_ilk2_spec_lints_with_plan_check_when_installed() -> None:
    """The pillar lint runs the ilk2 checker if it exists, and skips with a
    note when it does not."""
    text = _text(ILK2_SPEC)
    assert "plan_check.py" in text, "the spec lint must name plan_check.py"
    assert "--spec" in text, "the spec lint must pass --spec"
    assert "skip" in text.lower(), (
        "a missing plan_check.py must be skipped with a note, not worked around"
    )


def test_v1_ticket_planners_are_unchanged() -> None:
    """Built beside, never replace: commands/ilk-inbox.md and
    commands/ilk-lark.md stay V1's."""
    inbox_first = _text(ILK_INBOX).lstrip().splitlines()[0]
    assert inbox_first.startswith(ILK_INBOX_FIRST_LINE_PREFIX), (
        "commands/ilk-inbox.md is no longer V1's inbox planner: "
        f"first line is {inbox_first!r}"
    )
    lark_first = _text(ILK_LARK).lstrip().splitlines()[0]
    assert lark_first.startswith(ILK_LARK_FIRST_LINE_PREFIX), (
        "commands/ilk-lark.md is no longer V1's Lark planner: "
        f"first line is {lark_first!r}"
    )


def test_v1_spec_command_is_unchanged() -> None:
    """Built beside, never replace: commands/ilk-spec.md stays V1's."""
    first = _text(ILK_SPEC).lstrip().splitlines()[0]
    assert first.startswith(ILK_SPEC_FIRST_LINE_PREFIX), (
        "commands/ilk-spec.md is no longer V1's spec pre-stage: "
        f"first line is {first!r}"
    )
