"""Pin commands/ilk2-inbox.md and commands/ilk2-lark.md as the ilk2-native
ticket planners (docs/07 §7.4.2).

Both are built BESIDE the V1 commands (commands/ilk-inbox.md and
commands/ilk-lark.md, which must stay V1's) and keep their ticket adapters —
skills/ilk-inbox-tickets/scripts/cli.py and
skills/ilk-lark-tickets/scripts/cli.py. What changes is the planning and
delivery: the selected tickets are handed to /ilk2-plan, drafts go under
~/.ilk2/plans/, delivery is `ilk2 submit`, and a ticket's status update
names the ilk2 batch id.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ILK2_INBOX = REPO_ROOT / "commands" / "ilk2-inbox.md"
ILK2_LARK = REPO_ROOT / "commands" / "ilk2-lark.md"
ILK_INBOX = REPO_ROOT / "commands" / "ilk-inbox.md"
ILK_LARK = REPO_ROOT / "commands" / "ilk-lark.md"

# The planning/delivery surface each new command must speak (docs/07 §7.4.2).
REQUIRED = (
    "/ilk2-plan",
    "ilk2 submit",
    "~/.ilk2/plans/",
)

# V1's planning and delivery leftovers: none may appear in either new command.
FORBIDDEN = (
    "docs/plans/",
    "ilk_paths.py",
    "loop_status.py",
    "~/.ilk-data/projects",
)

# V1's run/launch surface — it answers questions about an engine that is off.
LAUNCH_TEXT = (
    "type `/ilk`",
    "/ilk-schedule",
    "/ilk-status",
    "/ilk-doctor",
)

# Each command keeps its own ticket adapter's cli.py.
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


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _both() -> dict[Path, str]:
    return {p: _text(p) for p in (ILK2_INBOX, ILK2_LARK)}


def test_both_ticket_planner_commands_exist() -> None:
    for path, text in _both().items():
        assert text.strip(), f"{path.name} is empty"


def test_ticket_planners_hand_off_to_ilk2_plan_and_submit() -> None:
    """Both name /ilk2-plan and `ilk2 submit` — the planner they delegate
    to, and the delivery that replaces V1's 'commit and push'."""
    for path, text in _both().items():
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
    for path, text in _both().items():
        assert "batch id" in text, f"{path.name} never names the ilk2 batch id"
        assert "ilk2 batch" in text, (
            f"{path.name} does not write 'ilk2 batch <batch-id>' into a ticket"
        )


def test_ticket_planners_have_no_v1_planning_or_delivery_paths() -> None:
    for path, text in _both().items():
        for needle in FORBIDDEN:
            assert needle not in text, f"{path.name} still contains V1's {needle!r}"


def test_ticket_planners_have_no_v1_launch_text() -> None:
    for path, text in _both().items():
        for needle in LAUNCH_TEXT:
            assert needle not in text, f"{path.name} still says to run {needle!r}"


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
