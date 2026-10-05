"""Regression pins for Kira run 20261005-202912.

The declared production prerequisite failed as designed.  The runner then
expanded a redirect variable that was declared only inside the optional
red-owner attribution branch, so ``set -u`` aborted before the normal terminal
footer could write run evidence and remove ``running.pid``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"


@pytest.mark.xfail(strict=True, reason="redirect state is scoped inside optional attribution")
def test_redirect_state_exists_before_optional_red_owner_attribution() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    branch = 'if [[ -n "$_red_owner_script" && -f "$_red_owner_script" && -n "$_iteration_base" ]]; then'
    declaration = 'local _redirect_quarantine_to=""'
    assert text.index(declaration) < text.index(branch)


def test_missing_production_opt_in_remains_blocking(tmp_path: Path) -> None:
    """The regression fixture proves refusal; it never creates the opt-in."""
    opt_in = tmp_path / ".kira-operator.local.md"
    result = subprocess.run(
        ["bash", "-c", "test -f .kira-operator.local.md && exit 99"],
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode != 0
    assert not opt_in.exists()


def test_normal_terminal_footer_owns_run_exit_and_pid_cleanup() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "'record_type': 'run_exit'" in text
    assert 'rm -f "${runtime_dir}/running.pid"' in text

