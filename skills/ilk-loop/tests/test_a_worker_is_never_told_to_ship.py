"""Pin that no worker-facing instruction tells the worker to ship.

Regression for gh-resolve run 20261006-191934: a worker followed ilk.md §7
and its sub-plan's "ship with ship_transition.py", exonerated a red gate
with a filtered rerun, and shipped in-run-yield-stops-the-worker via a
/private/tmp/golden-batch copy.

  AC-1  no line of commands/ilk.md contains ``ship_transition.py --ship``.
  AC-2  the refusal raised in a worker session appears verbatim in §7.
  AC-3  no line of commands/ilk.md pairs ``status`` with
        ``through `ship_transition.py` ``.
  AC-4  the subplan-template Step N section contains no ``--ship``, no
        ``by hand``, and does contain ``driver``.
  AC-5  commands/ilk-plan.md "Sequenced steps" bullet contains ``No step``
        and ``ship_transition.py``.
  AC-6  §5 of commands/ilk.md contains ``narrower``, ``earlier step``,
        ``pre-existing``, ``git worktree add --detach``, and ``## Findings``.
  AC-7  (control) the refusal is raised and its message is non-empty after
        stripping ``refused: ``.  Proves AC-2 cannot pass vacuously.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
COMMANDS_ILK = REPO_ROOT / "commands" / "ilk.md"
COMMANDS_ILK_PLAN = REPO_ROOT / "commands" / "ilk-plan.md"
SUBPLAN_TEMPLATE = REPO_ROOT / "skills" / "ilk-loop" / "templates" / "subplan-template.md"
SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ship_transition  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _section7(text: str) -> str:
    """Extract §7 ("## 7. Boundary:") from commands/ilk.md."""
    start = text.find("## 7. Boundary:")
    assert start != -1, "§7 not found in commands/ilk.md"
    end = text.find("\n## 8.", start)
    return text[start:end] if end != -1 else text[start:]


def _section5(text: str) -> str:
    """Extract §5 ("## 5. Execute") from commands/ilk.md."""
    start = text.find("## 5.")
    assert start != -1, "§5 not found in commands/ilk.md"
    end = text.find("\n## 6.", start)
    return text[start:end] if end != -1 else text[start:]


# ── AC-1: no line of commands/ilk.md contains ship_transition.py --ship ─────


@pytest.mark.xfail(
    strict=True,
    reason="base commands/ilk.md:207 still has 'ship_transition.py --ship <slug>'",
)
class TestAC1NoShipCommandInIlkMd:
    """commands/ilk.md must not contain a ``ship_transition.py --ship`` command."""

    def test_no_ship_flag(self) -> None:
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            assert "ship_transition.py --ship" not in line, (
                f"ilk.md line {i} still has 'ship_transition.py --ship': {line!r}"
            )


# ── AC-2: the refusal appears verbatim in §7 ───────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="base §7 does not quote the worker-session refusal message",
)
class TestAC2RefusalQuotedInIlkMd:
    """§7 must contain the exact text ``ship_transition.py`` emits when it
    refuses a worker session."""

    def test_refusal_in_section7(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Trigger the refusal
        monkeypatch.setenv("ILK_WORKER_SESSION", "1")
        monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "x")
        with pytest.raises(ship_transition.ShipTransitionError) as exc_info:
            ship_transition.ship(tmp_path, tmp_path, "x")
        msg = str(exc_info.value)
        assert msg.startswith("refused: "), f"unexpected prefix: {msg!r}"
        refusal = msg[len("refused: "):]
        assert refusal, "refusal message is empty after stripping prefix"

        # Check §7 contains it verbatim
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        section = _section7(text)
        assert refusal in section, (
            f"§7 does not contain the refusal text {refusal!r}"
        )


# ── AC-3: no line pairs status with through `ship_transition.py` ────────────


@pytest.mark.xfail(
    strict=True,
    reason="base commands/ilk.md:82-83 still pairs 'status' with 'through ship_transition.py'",
)
class TestAC3NoStatusThroughShipTransition:
    """No line of commands/ilk.md contains both ``status`` and
    ``through `ship_transition.py` ``."""

    def test_no_status_through_ship(self) -> None:
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "status" in line and "through `ship_transition.py`" in line:
                pytest.fail(
                    f"ilk.md line {i} pairs 'status' with "
                    f"'through ship_transition.py': {line!r}"
                )


# ── AC-4: subplan-template Step N has no --ship, no by hand, has driver ──────


@pytest.mark.xfail(
    strict=True,
    reason="base subplan-template Step N has ship command block and by-hand fallback",
)
class TestAC4SubplanTemplateNoShip:
    """subplan-template.md Step N section must not contain ``--ship`` or
    ``by hand``, and must contain ``driver``."""

    def test_step_n_no_ship_no_by_hand_has_driver(self) -> None:
        text = SUBPLAN_TEMPLATE.read_text(encoding="utf-8")
        # Step N section: from "### Step N" to the next "<!--"
        start = text.find("### Step N")
        assert start != -1, "### Step N not found in subplan-template.md"
        end = text.find("<!--", start)
        section = text[start:end] if end != -1 else text[start:]

        assert "--ship" not in section, (
            f"subplan-template Step N still has '--ship': {section!r}"
        )
        assert "by hand" not in section, (
            f"subplan-template Step N still has 'by hand': {section!r}"
        )
        assert "driver" in section, (
            "subplan-template Step N does not mention 'driver'"
        )


# ── AC-5: ilk-plan.md "Sequenced steps" bullet ──────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="base ilk-plan.md Sequenced steps bullet does not forbid ship steps",
)
class TestAC5IlkPlanForbidsShipSteps:
    """commands/ilk-plan.md "Sequenced steps" bullet must contain ``No step``
    and ``ship_transition.py``."""

    def test_sequenced_steps_forbids_ship(self) -> None:
        text = COMMANDS_ILK_PLAN.read_text(encoding="utf-8")
        start = text.find("- Sequenced steps")
        assert start != -1, "'Sequenced steps' not found in commands/ilk-plan.md"
        end = text.find("\n  - ", start + 1)
        bullet = text[start:end] if end != -1 else text[start:]

        assert "No step" in bullet, (
            "ilk-plan.md Sequenced steps bullet missing 'No step'"
        )
        assert "ship_transition.py" in bullet, (
            "ilk-plan.md Sequenced steps bullet missing 'ship_transition.py'"
        )


# ── AC-6: §5 red-gate ownership rule ─────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="base §5 does not contain the red-gate ownership rule",
)
class TestAC6RedGateOwnership:
    """§5 of commands/ilk.md must contain the key phrases from the red-gate
    ownership rule: ``narrower``, ``earlier step``, ``pre-existing``,
    ``git worktree add --detach``, and ``## Findings``."""

    def test_section5_has_red_gate_rule(self) -> None:
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        section = _section5(text)
        required = [
            "narrower",
            "earlier step",
            "pre-existing",
            "git worktree add --detach",
            "## Findings",
        ]
        missing = [kw for kw in required if kw not in section]
        assert not missing, f"§5 missing red-gate keywords: {missing}"


# ── AC-7: control — refusal is raised and non-empty ──────────────────────────


class TestAC7ControlRefusalNonEmpty:
    """The refusal is raised and its message is non-empty after stripping
    ``refused: ``.  This proves AC-2 cannot pass vacuously."""

    def test_refusal_is_nonempty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ILK_WORKER_SESSION", "1")
        monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "x")
        with pytest.raises(ship_transition.ShipTransitionError) as exc_info:
            ship_transition.ship(tmp_path, tmp_path, "x")
        msg = str(exc_info.value)
        assert msg.startswith("refused: "), f"unexpected prefix: {msg!r}"
        refusal = msg[len("refused: "):]
        assert refusal, "refusal message is empty after stripping 'refused: '"