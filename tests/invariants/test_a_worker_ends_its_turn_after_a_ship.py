"""I5: a worker ends its turn after a ship.

The runner gives the worker an ``ILK_SHIPPED_MARKER`` path.  The hook
``no-live-clone-edit.py`` denies Edit/Write after the marker file exists,
naming END YOUR TURN.

Rail: the runner's call to ``export_iteration_marker`` and the marker
check in ``hooks/no-live-clone-edit.py:299-309, :324-330``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
HOOK = _REPO / "hooks" / "no-live-clone-edit.py"

pytestmark = pytest.mark.timeout(120)


def _run_hook(
    event: dict,
    env_extra: dict[str, str],
    root: Path,
) -> tuple[int, str, str]:
    """Run the hook with a given event and env.  Returns (exit, stdout, stderr)."""
    env = {**os.environ, **env_extra}
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env, cwd=str(root),
    )
    return r.returncode, r.stdout, r.stderr


def test_hook_denies_edit_after_ship(tmp_path: Path) -> None:
    """With ILK_SHIPPED_MARKER pointing to an existing file, Edit is denied."""
    marker_file = tmp_path / "shipped.marker"
    marker_file.write_text("shipped\n", encoding="utf-8")

    exit_code, stdout, stderr = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.py"}},
        {"ILK_SHIPPED_MARKER": str(marker_file)},
        tmp_path,
    )

    assert exit_code == 0, f"hook should exit 0 (deny is via stdout), got {exit_code}"
    assert "END YOUR TURN" in stdout, (
        f"hook should deny with 'END YOUR TURN', got:\nstdout={stdout[-300:]}\n"
        f"stderr={stderr[-300:]}"
    )


def test_hook_denies_write_after_ship(tmp_path: Path) -> None:
    """Write is also denied after ship."""
    marker_file = tmp_path / "shipped.marker"
    marker_file.write_text("shipped\n", encoding="utf-8")

    exit_code, stdout, stderr = _run_hook(
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.py"}},
        {"ILK_SHIPPED_MARKER": str(marker_file)},
        tmp_path,
    )

    assert exit_code == 0
    assert "END YOUR TURN" in stdout, (
        f"hook should deny Write with 'END YOUR TURN', got:\nstdout={stdout[-300:]}"
    )


def test_hook_allows_read_after_ship(tmp_path: Path) -> None:
    """Read is allowed even after ship (the hook only denies writes)."""
    marker_file = tmp_path / "shipped.marker"
    marker_file.write_text("shipped\n", encoding="utf-8")

    exit_code, stdout, stderr = _run_hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x.py"}},
        {"ILK_SHIPPED_MARKER": str(marker_file)},
        tmp_path,
    )

    assert exit_code == 0
    # Read should produce no denial.
    assert "END YOUR TURN" not in stdout, (
        f"hook should allow Read after ship, but denied:\nstdout={stdout[-300:]}"
    )


def test_hook_allows_edit_without_marker(tmp_path: Path) -> None:
    """Without ILK_SHIPPED_MARKER, Edit is allowed."""
    exit_code, stdout, stderr = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.py"}},
        {},  # no ILK_SHIPPED_MARKER
        tmp_path,
    )

    assert exit_code == 0
    assert "END YOUR TURN" not in stdout, (
        f"hook should allow Edit without marker, but denied:\nstdout={stdout[-300:]}"
    )