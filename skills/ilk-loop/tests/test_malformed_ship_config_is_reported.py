"""Red-first: a malformed ship config is reported, not crashed on.

gh-resolve v0.75.0: ``ship_audit._resolve_expected_invocation`` crashed with
``AttributeError: 'MalformedConfig' object has no attribute 'ship'`` because
it tested ``NotConfigured`` only and fell through to ``config.ship[...]``.

AC-1  (xfail) ``_resolve_expected_invocation`` raises ``ValueError`` whose
      message names the problem (``node_id``).
AC-2  (xfail) ``batch_gate`` returns a record with
      ``verdict == "malformed_config"``, no exception.
AC-3  (xfail) ``plan_lint`` emits a finding containing the detail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
SHIP_SCRIPTS = _REPO / "skills" / "ilk-ship" / "scripts"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SHIP_SCRIPTS))


def _make_launch_json(tmp_path: Path, *, missing_node_id: bool = False) -> Path:
    """Create a .ilk-launch.json with a baseline_red entry."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(exist_ok=True)
    launch = tmp_path / ".ilk-launch.json"
    entry: dict = {"file": "t.py", "name": "x", "reason": "known red"}
    if not missing_node_id:
        entry["node_id"] = "t.py::x"
    payload = {
        "ship": {
            "suite": {"command": "pytest"},
            "baseline_red": [entry],
        }
    }
    launch.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return launch


@pytest.mark.xfail(strict=True, reason="red-first: MalformedConfig unhandled")
def test_resolve_expected_invocation_raises_for_malformed(tmp_path: Path) -> None:
    """AC-1: raises ValueError naming the problem."""
    from ship_config import load_ship_config
    from ship_audit import _resolve_expected_invocation

    _make_launch_json(tmp_path, missing_node_id=True)
    config = load_ship_config(tmp_path)
    with pytest.raises(ValueError, match="node_id"):
        _resolve_expected_invocation(config, tmp_path)


@pytest.mark.xfail(strict=True, reason="red-first: MalformedConfig unhandled")
def test_batch_gate_returns_malformed_config_verdict(tmp_path: Path) -> None:
    """AC-2: verdict == 'malformed_config', no exception."""
    from batch_gate import run_batch_gate

    _make_launch_json(tmp_path, missing_node_id=True)
    # run_batch_gate should not raise on malformed config.
    result = run_batch_gate(tmp_path)
    assert result is not None
    assert hasattr(result, "verdict"), f"no verdict on result: {result}"
    assert result.verdict == "malformed_config", (
        f"expected malformed_config, got {result.verdict}"
    )


@pytest.mark.xfail(strict=True, reason="red-first: MalformedConfig unhandled")
def test_plan_lint_emits_finding_for_malformed(tmp_path: Path) -> None:
    """AC-3: plan_lint emits a finding containing the detail."""
    from plan_lint import lint_project

    _make_launch_json(tmp_path, missing_node_id=True)
    findings = lint_project(tmp_path)
    mal = [f for f in findings if "node_id" in str(getattr(f, "message", ""))]
    assert mal, f"no finding about node_id in {findings}"
