"""Red-first: a ship-integrity violation names the gate's own error.

rezmac run 20260923-150625: the violation reason was ``gate record unreadable
(no results field, all_passed=false)`` when the actual gate record said
``error: timeout after 120s``.  The scalar ``--gate-passed false`` path
discarded the error field.

AC-1  (xfail) ``--gate-results-file F --slug s`` on a JSONL with a failing
      record ⇒ reason names ``<command> (<error>)``, not ``unreadable``.
AC-2  (unmarked) ``--gate-passed false`` alone ⇒ output unchanged from today.
AC-3  (unmarked) existing ``test_ship_integrity.py`` stays green.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = _REPO / "skills" / "ilk-loop" / "scripts" / "ship_integrity.py"
SCRIPTS_DIR = _REPO / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


def _make_gated_subplan(tmp_path: Path) -> Path:
    """Create a shipped sub-plan with a declared gate."""
    sp = tmp_path / "2026-09-23-test-subplan.md"
    sp.write_text(
        "---\n"
        "plan: test-subplan\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        "---\n\n"
        "# test\n\n"
        "### Step 0\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"bun run x\"\n"
        "    timeout: 120\n"
        "```\n",
        encoding="utf-8",
    )
    return sp


def _run_integrity(
    subplan: Path,
    *,
    gate_passed: str | None = None,
    gate_results_file: Path | None = None,
    slug: str | None = None,
) -> tuple[int, str]:
    args = [sys.executable, str(SCRIPT), "--subplan", str(subplan)]
    if gate_passed is not None:
        args += ["--gate-passed", gate_passed]
    if gate_results_file is not None:
        args += ["--gate-results-file", str(gate_results_file)]
    if slug is not None:
        args += ["--slug", slug]
    r = subprocess.run(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, r.stdout + r.stderr


@pytest.mark.xfail(strict=True, reason="red-first: --gate-results-file not implemented")
def test_violation_names_the_gate_error(tmp_path: Path) -> None:
    """AC-1: the violation reason names the command and error, not 'unreadable'."""
    sp = _make_gated_subplan(tmp_path)
    lc = tmp_path / "gate.jsonl"
    lc.write_text(
        json.dumps({
            "slug": "test-subplan", "step": 0,
            "outcome": "fail", "exit_code": 1,
            "command": "bun run x",
            "error": "timeout after 120s",
        }) + "\n",
        encoding="utf-8",
    )
    rc, out = _run_integrity(
        sp, gate_passed="false",
        gate_results_file=lc, slug="test-subplan",
    )
    assert rc != 0, f"expected violation but got exit {rc}: {out}"
    assert "bun run x" in out, f"command not in output: {out}"
    assert "timeout after 120s" in out, f"error not in output: {out}"
    assert "unreadable" not in out, f"'unreadable' leaked through: {out}"


def test_gate_passed_false_alone_unchanged(tmp_path: Path) -> None:
    """AC-2: --gate-passed false alone ⇒ today's 'unreadable' message."""
    sp = _make_gated_subplan(tmp_path)
    rc, out = _run_integrity(sp, gate_passed="false")
    assert rc != 0, f"expected violation but got exit {rc}: {out}"
    assert "unreadable" in out, f"expected 'unreadable' in back-compat path: {out}"
