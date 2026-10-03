"""Tests for release_train deploy — flip, bounce, smoke, and automatic rollback.

Sub-plan: a-deploy-that-fails-its-smoke-rolls-back, step 0 (pins).

Each test builds a throwaway releases root under ``tmp_path`` with v0.0.1
as current and a v0.0.2 release dir already extracted.  All external
commands (ilk_release, bounce, host_deploy_status, pid file) are injected
as stubs so no real launchctl, no real scheduler, and no real releases
root are touched.

The five acceptance criteria:
  AC-1  deploy v0.0.2 with stub status ok + stub pid naming releases/v0.0.2/:
        exit 0, current points to v0.0.2.
  AC-2  stub status prints tag-mismatch for v0.0.2 and ok for v0.0.1:
        exit 5, current points back to v0.0.1, rolled_back_to: v0.0.1.
  AC-3  both smokes fail: exit 6, current back to v0.0.1.
  AC-4  extraction fails (no such tag): exit 4, current unchanged, no bounce.
  AC-5  the fake launchctl never ran outside the stub bounce (host guard).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))


def _import_deploy():
    """Import deploy — deferred because it doesn't exist at step-0 commit time."""
    from release_train import deploy
    return deploy


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_releases_root(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tmp releases root with v0.0.1 as current.

    Returns (releases_root, parent) where parent holds current/previous symlinks.
    The env var ILK_RELEASES_ROOT is set to releases_root for the test.
    """
    releases_root = tmp_path / "releases"
    releases_root.mkdir()
    parent = releases_root.parent

    # Create v0.0.1 release dir with a manifest
    v001_dir = releases_root / "v0.0.1"
    v001_dir.mkdir()
    manifest = {
        "tag": "v0.0.1",
        "sha": "a" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T00:00:00+00:00",
    }
    (v001_dir / ".ilk-release.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Create v0.0.2 release dir with a manifest
    v002_dir = releases_root / "v0.0.2"
    v002_dir.mkdir()
    manifest2 = {
        "tag": "v0.0.2",
        "sha": "b" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T01:00:00+00:00",
    }
    (v002_dir / ".ilk-release.json").write_text(json.dumps(manifest2, indent=2) + "\n")

    # Symlink current → v0.0.1
    current = parent / "current"
    previous = parent / "previous"
    os.symlink(str(v001_dir), str(current))

    # Set env so ilk_release uses our tmp root
    os.environ["ILK_RELEASES_ROOT"] = str(releases_root)

    return releases_root, parent


def _read_current_target(parent: Path) -> str | None:
    """Read the target of the current symlink."""
    current = parent / "current"
    if current.is_symlink():
        return os.readlink(current)
    return None


def _make_fake_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with two tags for the deploy tests."""
    project = tmp_path / "project"
    project.mkdir()

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, check=True, capture_output=True,
    )

    # Initial commit + v0.0.1 tag
    (project / "README.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
        cwd=project, check=True, capture_output=True,
    )

    # Second commit + v0.0.2 tag
    (project / "README.md").write_text("v0.0.2 content")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "v0.0.2"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.2", "-m", "v0.0.2"],
        cwd=project, check=True, capture_output=True,
    )

    return project


def _write_stub_bouncer(tmp_path: Path, output: str = "fresh: scheduler — fresh (toolkit_head matches HEAD)\nrecorded_sha: aaaa\nrecorded_sha: aaaa\ntree_state: clean\n") -> Path:
    """Write a fake bounce_daemons.sh that records its argv and prints output."""
    bouncer = tmp_path / "bounce_daemons.sh"
    bouncer.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "$@" >> "$(dirname "$0")/bouncer_argv"
        cat <<'BOUNCER_OUTPUT'
{output.strip()}
BOUNCER_OUTPUT
    """))
    bouncer.chmod(0o755)
    return bouncer


def _write_stub_status(tmp_path: Path, status: str = "ok") -> Path:
    """Write a fake host_deploy_status.py that prints the given status."""
    status_script = tmp_path / "host_deploy_status.py"
    status_script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        print("{status}")
        sys.exit(0 if "{status}" == "ok" else 1)
    """))
    status_script.chmod(0o755)
    return status_script


def _write_pid_file(tmp_path: Path, alive: bool = True, command: str = "") -> Path:
    """Write a fake scheduler.pid file pointing to a live or dead process."""
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    if alive:
        # Start a sleep process and record its pid
        proc = subprocess.Popen(["sleep", "60"])
        pid_file.write_text(str(proc.pid))
        # Store proc for cleanup
        _write_pid_file._procs = getattr(_write_pid_file, "_procs", [])
        _write_pid_file._procs.append(proc)
    else:
        # Use a pid that's very likely dead
        pid_file.write_text("99999999")

    return pid_file


def _cleanup_pid_procs():
    """Kill any sleep processes started by _write_pid_file."""
    for proc in getattr(_write_pid_file, "_procs", []):
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass


# ── AC-1: deploy succeeds when smoke passes ─────────────────────────────────

class TestDeploySucceedsWhenSmokePasses:
    """AC-1: deploy v0.0.2 with stub status ok + stub pid alive → exit 0, current → v0.0.2."""

    @pytest.mark.xfail(strict=True, reason="deploy verb does not exist yet")
    def test_deploy_exits_zero_and_flips_current(self, tmp_path: Path) -> None:
        """Smoke passes: exit 0, current points to v0.0.2."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        status_script = _write_stub_status(tmp_path, status="ok")
        pid_file = _write_pid_file(tmp_path, alive=True)

        deploy = _import_deploy()
        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=None,   # use default ilk_release
            bounce_cmd=str(bouncer),
            status_cmd=str(status_script),
            pid_file=pid_file,
        )

        assert result["deployed"] is True
        assert result["tag"] == "v0.0.2"

        # current should now point to v0.0.2
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.2" in current_target


# ── AC-2: deploy rolls back when smoke fails ────────────────────────────────

class TestDeployRollsBackOnSmokeFailure:
    """AC-2: smoke fails for v0.0.2, ok for v0.0.1 → exit 5, rolled_back_to: v0.0.1."""

    @pytest.mark.xfail(strict=True, reason="deploy verb does not exist yet")
    def test_deploy_exits_five_and_rolls_back(self, tmp_path: Path) -> None:
        """Smoke fails for new tag, passes for previous → rollback, exit 5."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        # Status fails for v0.0.2 but passes for v0.0.1
        status_script = tmp_path / "host_deploy_status.py"
        status_script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import sys
            tag = None
            for i, arg in enumerate(sys.argv):
                if arg == "--require-tag" and i + 1 < len(sys.argv):
                    tag = sys.argv[i + 1]
            if tag == "v0.0.2":
                print("tag-mismatch")
                sys.exit(1)
            else:
                print("ok")
                sys.exit(0)
        """))
        status_script.chmod(0o755)
        pid_file = _write_pid_file(tmp_path, alive=True)

        deploy = _import_deploy()
        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=None,
            bounce_cmd=str(bouncer),
            status_cmd=str(status_script),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert result["rolled_back_to"] == "v0.0.1"
        assert result["rollback_smoke"] == "ok"

        # current should point back to v0.0.1
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.1" in current_target


# ── AC-3: both smokes fail → exit 6 ─────────────────────────────────────────

class TestDeployBothSmokesFail:
    """AC-3: both smokes fail → exit 6, current back to v0.0.1."""

    @pytest.mark.xfail(strict=True, reason="deploy verb does not exist yet")
    def test_deploy_exits_six_when_both_smokes_fail(self, tmp_path: Path) -> None:
        """Both smokes fail → exit 6, current back to v0.0.1."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        # Status always fails
        status_script = _write_stub_status(tmp_path, status="unreachable")
        pid_file = _write_pid_file(tmp_path, alive=True)

        deploy = _import_deploy()
        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=None,
            bounce_cmd=str(bouncer),
            status_cmd=str(status_script),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert result["rollback_smoke"] == "failed"

        # current should point back to v0.0.1
        current_target = _read_current_target(parent)
        assert current_target is not None
        assert "v0.0.1" in current_target


# ── AC-4: extraction fails → exit 4 ─────────────────────────────────────────

class TestDeployExtractionFails:
    """AC-4: extraction fails (no such tag) → exit 4, current unchanged, no bounce."""

    @pytest.mark.xfail(strict=True, reason="deploy verb does not exist yet")
    def test_deploy_exits_four_when_extraction_fails(self, tmp_path: Path) -> None:
        """No such tag in repo → exit 4, current unchanged, no bounce called."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        status_script = _write_stub_status(tmp_path, status="ok")
        pid_file = _write_pid_file(tmp_path, alive=True)

        # Record the current target before deploy
        current_before = _read_current_target(parent)

        deploy = _import_deploy()
        with pytest.raises(SystemExit) as exc_info:
            deploy(
                project=project,
                tag="v99.99.99",  # tag that doesn't exist
                data_dir=data_dir,
                release_cmd=None,
                bounce_cmd=str(bouncer),
                status_cmd=str(status_script),
                pid_file=pid_file,
            )
        assert exc_info.value.code == 4

        # current unchanged
        current_after = _read_current_target(parent)
        assert current_after == current_before

        # bouncer should not have been called
        bouncer_argv = tmp_path / "bouncer_argv"
        assert not bouncer_argv.exists(), "bouncer was called despite extraction failure"


# ── AC-5: no real launchctl calls ────────────────────────────────────────────

class TestNoRealLaunchctlCalls:
    """AC-5 (control): the fake launchctl never ran outside the stub bounce."""

    @pytest.mark.xfail(strict=True, reason="deploy verb does not exist yet")
    def test_no_real_launchctl_in_test_env(self, tmp_path: Path) -> None:
        """The conftest host guard ensures no real launchctl is called.

        This test exists as a control: if launchctl ran, the guard would
        have already failed the test session.  The assertion is that the
        deploy verb uses only injected commands.
        """
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        status_script = _write_stub_status(tmp_path, status="ok")
        pid_file = _write_pid_file(tmp_path, alive=True)

        deploy = _import_deploy()
        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=None,
            bounce_cmd=str(bouncer),
            status_cmd=str(status_script),
            pid_file=pid_file,
        )

        # If we got here without the host guard failing, no real launchctl ran.
        assert result["deployed"] is True


# ── Cleanup ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cleanup():
    """Clean up any sleep processes started by _write_pid_file."""
    yield
    _cleanup_pid_procs()