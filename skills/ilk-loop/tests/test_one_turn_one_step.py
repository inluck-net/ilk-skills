"""One turn, one step: the worker contract never licenses a multi-step turn.

Retro 2026-10-09 (docs/retros/retro-2026-10-09-a-worker-ran-the-whole-batch-in-one-turn.md):
SKILL.md said "or a few consecutive ones" and commands/ilk.md "You MAY execute
several consecutive steps"; MiMo workers followed that, ran 3 sub-plans in one
40-min iteration, and every counter read 0/N until the turn ended.  One worker
also wrote `status: complete`, which is not an ilk state.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "ilk-loop" / "SKILL.md"
ILK_CMD = REPO / "commands" / "ilk.md"
RUNNER = REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"

_MULTI = re.compile(r"(few|several) consecutive", re.IGNORECASE)


def test_skill_md_has_no_multi_step_license() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert not _MULTI.search(text), _MULTI.search(text)
    assert "Execute exactly ONE step" in text


def test_skill_md_bump_is_exactly_one_and_status_is_the_drivers() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "by exactly one" in text
    assert "no `complete` state" in text
    assert "Do not start step N+1" in text


def test_ilk_command_has_no_multi_step_license() -> None:
    text = ILK_CMD.read_text(encoding="utf-8")
    assert not _MULTI.search(text), _MULTI.search(text)
    assert "then end your turn" in text


def _worker_gate_notice_block() -> str:
    text = RUNNER.read_text(encoding="utf-8")
    start = text.index('_worker_gate_notice="AFTER your step commit')
    end = text.index('Findings section.', start)
    end = text.index('"', end)
    return text[start:end]


def test_worker_gate_notice_ends_the_turn_after_one_step() -> None:
    block = _worker_gate_notice_block()
    assert "END YOUR TURN" in block
    assert "Do not start the next step in this turn" in block
    assert "${_wgn_step}" in block  # names the step, expanded at prompt time
    assert "Never write the sub-plan's status: field" in block
