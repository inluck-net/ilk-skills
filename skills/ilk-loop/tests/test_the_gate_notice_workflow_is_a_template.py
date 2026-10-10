"""Pin that the worker-gate notice's workflow is a template, and its invariants are not.

Backlog 8db6cca8: a worker ran no test until tool call 131 of 206 in a 90-min red
iteration (gh-resolve run 20261010-012459 iter 6).  The fix is prompt text, and
the text lived inside the kernel runner, so no improvement batch could change it.
build_worker_gate_notice now reads the workflow part from
templates/worker-gate-notice.md (not kernel) and always appends the invariants
itself, so a template edit can tune the workflow but never drop a safety rule.

AC-1: the shipped template renders the same lines as the old inline notice.
AC-2: a template that drops every rule still yields all three invariants.
AC-3: a missing template falls back to the built-in workflow, plus invariants.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_ilk_loop_claude.sh"
TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "worker-gate-notice.md"
CMDS = "python3 -m pytest tests/test_a.py -q\npython3 -m pytest tests/test_b.py -q"

INVARIANTS = [
    "- Never weaken or delete an assertion. Never add a deselect or baseline_red entry.",
    "- When green: bump current_step from 2 to the next step (exactly +1), commit that bump, and END YOUR TURN.",
    "- Do not start the next step in this turn. Never write the sub-plan's status: field.",
]

OLD_NOTICE = """AFTER your step commit, run the declared gate and iterate until green:
  python3 -m pytest tests/test_a.py -q
  python3 -m pytest tests/test_b.py -q

Rules:
- Run every command above after your commit.
- While any is red: fix the code (or fix a test-infra fault), commit with 'test-infra:' in the body if fixing a test, then rerun.
- End your turn ONLY when all are green.
- Never weaken or delete an assertion. Never add a deselect or baseline_red entry.
- If green is not reachable, end your turn with the failing ids written to the sub-plan's Findings section.
- When green: bump current_step from 2 to the next step (exactly +1), commit that bump, and END YOUR TURN.
- Do not start the next step in this turn. Never write the sub-plan's status: field."""


def _notice(tmp_path: Path, template: str | None) -> str:
    root = tmp_path / "skills"
    tdir = root / "ilk-loop" / "templates"
    tdir.mkdir(parents=True)
    if template is not None:
        (tdir / "worker-gate-notice.md").write_text(template, encoding="utf-8")
    script = (f"export ILK_DOTSOURCE_ONLY=1; source '{RUNNER}'; _SKILL_ROOT='{root}'; "
              f"build_worker_gate_notice \"$1\" 2")
    r = subprocess.run(["bash", "-c", script, "x", CMDS], capture_output=True, text=True,
                       timeout=60, env={"ILK_DOTSOURCE_ONLY": "1", "HOME": str(tmp_path),
                                        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"})
    assert r.returncode == 0, r.stderr
    return r.stdout.rstrip("\n")


def test_the_shipped_template_keeps_the_old_lines(tmp_path: Path) -> None:
    out = _notice(tmp_path, TEMPLATE.read_text(encoding="utf-8"))
    assert sorted(out.splitlines()) == sorted(OLD_NOTICE.splitlines()), out
    assert out.startswith("AFTER your step commit, run the declared gate")


def test_a_template_cannot_drop_an_invariant(tmp_path: Path) -> None:
    out = _notice(tmp_path, "Run this first:\n{gate_cmds}\n")
    assert "  python3 -m pytest tests/test_a.py -q" in out
    for line in INVARIANTS:
        assert line in out.splitlines(), out


def test_a_missing_template_falls_back(tmp_path: Path) -> None:
    out = _notice(tmp_path, None)
    assert sorted(out.splitlines()) == sorted(OLD_NOTICE.splitlines()), out
