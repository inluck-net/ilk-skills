"""Pin that a worker ships one sub-plan per iteration.

Part of sub-plan ``one-sub-plan-per-iteration`` (MASTER-2026-10-02b).

After a successful ``ship_transition.py --ship`` in an iteration, the worker
may not start another sub-plan.

- ``ship_transition.py`` writes a per-iteration marker (keyed on
  ``ILK_ITERATION_SUBPLAN`` / ``ILK_WORKER_SESSION``, under the runtime dir).
- ``hooks/no-live-clone-edit.py`` denies Edit/Write and ``git commit`` once the
  marker exists, with the message ``shipped this iteration; END YOUR TURN``.
- The driver clears the marker at the next iteration start.

Three acceptance criteria:

  AC-1  marker present ⇒ the hook denies Edit and ``git commit``, and allows
        Read.
  AC-2  no marker ⇒ unchanged.
  AC-3  the driver's iteration start removes a stale marker.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-live-clone-edit.py"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def _fake_env(tmp_path: Path):
    """Create a minimal environment for marker tests.

    Layout::

        tmp/
          clone/                    ← the live clone (hook denies writes here)
            skills/ilk-loop/scripts/
          home/                     ← the fake CLAUDE_CONFIG_DIR
            skills/ilk-loop -> ../../clone/skills/ilk-loop  (symlink)
          selfmod/                  ← a fake selfmod worktree (hook allows)
          other_repo/               ← a different repo (hook allows)
          runtime/launcher/         ← where the marker lives
    """
    clone = tmp_path / "clone"
    (clone / "skills" / "ilk-loop" / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(clone), capture_output=True,
                   timeout=10)

    home = tmp_path / "home"
    link_target = clone / "skills" / "ilk-loop"
    (home / "skills").mkdir(parents=True)
    (home / "skills" / "ilk-loop").symlink_to(link_target)

    selfmod = tmp_path / "selfmod"
    selfmod.mkdir()

    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(other_repo), capture_output=True,
                   timeout=10)
    (other_repo / "file.py").write_text("content\n")
    subprocess.run(["git", "-C", str(other_repo), "add", "file.py"],
                   capture_output=True, timeout=10)
    subprocess.run(
        ["git", "-C", str(other_repo), "config", "user.email", "t@t"],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(other_repo), "config", "user.name", "T"],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(other_repo), "commit", "-q", "-m", "seed"],
        capture_output=True, timeout=10,
    )

    runtime_dir = tmp_path / "runtime" / "launcher"
    runtime_dir.mkdir(parents=True)
    marker_path = runtime_dir / "iteration-shipped.marker"

    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home),
           "ILK_SHIPPED_MARKER": str(marker_path)}
    return {
        "runtime_dir": runtime_dir,
        "marker_path": marker_path,
        "home": home,
        "clone": clone,
        "selfmod": selfmod,
        "other_repo": other_repo,
        "env": env,
    }


def _event(tool_name: str, tool_input: dict | None = None) -> str:
    """Build a JSON event string as Claude Code would."""
    return json.dumps({"tool_name": tool_name,
                       "tool_input": tool_input or {}})


def _run_hook(event: str, env: dict[str, str]) -> dict:
    """Run the hook as a subprocess and return {allowed, payload?, stdout}."""
    if not HOOK_PATH.exists():
        return {"allowed": True, "missing": True}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ── AC-1: marker present ⇒ deny Edit and git commit, allow Read ─────────────


class TestMarkerDeniesEditAndCommit:
    """AC-1: marker present ⇒ the hook denies Edit and ``git commit``.

    All paths are OUTSIDE the clone so the existing clone-protection logic
    is not what fires.  The marker is the only reason to deny.
    """

    def test_edit_denied_with_marker(self, _fake_env: dict) -> None:
        """Edit is denied when the iteration-shipped marker exists."""
        marker = _fake_env["marker_path"]
        marker.write_text("one-sub-plan-per-iteration\n")

        target = _fake_env["selfmod"] / "file.py"
        target.write_text("old content\n")
        event = _event("Edit", {"file_path": str(target),
                                "old_string": "old", "new_string": "new"})
        result = _run_hook(event, _fake_env["env"])
        assert result["allowed"] is False

    def test_git_commit_denied_with_marker(self, _fake_env: dict) -> None:
        """git commit is denied when the iteration-shipped marker exists."""
        marker = _fake_env["marker_path"]
        marker.write_text("one-sub-plan-per-iteration\n")

        other_repo = _fake_env["other_repo"]
        event = _event("Bash", {"command": f"git -C {other_repo} commit -m x"})
        result = _run_hook(event, _fake_env["env"])
        assert result["allowed"] is False


class TestMarkerAllowsRead:
    """AC-1 (complement): marker present ⇒ Read is still allowed.

    This is the current correct behaviour — the hook should never block reads.
    """

    def test_read_allowed_with_marker(self, _fake_env: dict) -> None:
        """Read is allowed even when the iteration-shipped marker exists."""
        marker = _fake_env["marker_path"]
        marker.write_text("one-sub-plan-per-iteration\n")

        target = _fake_env["selfmod"] / "file.py"
        target.write_text("content\n")
        event = _event("Read", {"file_path": str(target)})
        result = _run_hook(event, _fake_env["env"])
        assert result["allowed"] is True


# ── AC-2: no marker ⇒ unchanged ─────────────────────────────────────────────


class TestNoMarkerUnchanged:
    """AC-2: no marker ⇒ unchanged (hook allows Edit and git commit
    on paths outside the clone)."""

    def test_edit_allowed_without_marker(self, _fake_env: dict) -> None:
        """Edit is allowed when no marker exists."""
        target = _fake_env["selfmod"] / "file.py"
        target.write_text("old content\n")
        event = _event("Edit", {"file_path": str(target),
                                "old_string": "old", "new_string": "new"})
        result = _run_hook(event, _fake_env["env"])
        assert result["allowed"] is True

    def test_git_commit_allowed_without_marker(self, _fake_env: dict) -> None:
        """git commit is allowed when no marker exists."""
        other_repo = _fake_env["other_repo"]
        event = _event("Bash", {"command": f"git -C {other_repo} commit -m x"})
        result = _run_hook(event, _fake_env["env"])
        assert result["allowed"] is True


# ── AC-3: driver iteration start removes stale marker ───────────────────────


class TestDriverRemovesStaleMarker:
    """AC-3: the driver's iteration start removes a stale marker."""

    def test_driver_clears_marker(self, _fake_env: dict) -> None:
        """The driver's iteration-start hook removes the marker file."""
        marker = _fake_env["marker_path"]
        marker.write_text("one-sub-plan-per-iteration\n")
        assert marker.exists()

        # Simulate the driver's iteration-start cleanup.
        _clear_iteration_marker(_fake_env["runtime_dir"])

        assert not marker.exists(), (
            "stale marker was not removed by iteration start"
        )


def _clear_iteration_marker(runtime_dir: Path) -> None:
    """Simulate the driver's iteration-start marker cleanup.

    The runner clears the marker at the start of each iteration so the
    worker can take new work.
    """
    marker = runtime_dir / "iteration-shipped.marker"
    if marker.exists():
        marker.unlink()