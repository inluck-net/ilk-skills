"""Tests for release_train deploy — flip, bounce, smoke, and automatic rollback.

Sub-plan: a-deploy-that-fails-its-smoke-rolls-back, step 0 (pins) + step 1 (impl).

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
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train import deploy  # noqa: E402


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


def _make_release_cmd(
    allow_tags: set[str] | None = None,
    releases_root: Path | None = None,
):
    """Create a stub release_cmd callable.

    *allow_tags* is the set of tags that extraction succeeds for.
    None means all tags succeed.  Tags not in the set raise SystemExit(4).

    When *releases_root* is set, ``--status`` returns the real status JSON
    and ``--rollback`` swaps current/previous (matching ilk_release.py's
    CLI contract so the deploy function can discover the previous tag).
    """
    def _release_cmd(arg: str, repo: Path) -> tuple[int, str]:
        # --status: return release status JSON
        if arg == "--status" and releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            previous = parent / "previous"
            current_val = os.readlink(current) if current.is_symlink() else None
            previous_val = os.readlink(previous) if previous.is_symlink() else None
            return (0, json.dumps({
                "current": current_val,
                "previous": previous_val,
            }))

        # --rollback: swap current and previous
        if arg == "--rollback" and releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            previous = parent / "previous"
            if current.is_symlink() and previous.is_symlink():
                curr_target = os.readlink(current)
                prev_target = os.readlink(previous)
                os.remove(current)
                os.symlink(prev_target, str(current))
                os.remove(previous)
                os.symlink(curr_target, str(previous))
            return (0, "rolled back")

        # Extraction: check allow_tags and flip current
        if allow_tags is not None and arg not in allow_tags:
            print(f"refused: no such tag: {arg}", file=sys.stderr)
            raise SystemExit(4)
        # Simulate ilk_release's flip: current → tag dir
        if releases_root is not None:
            parent = releases_root.parent
            current = parent / "current"
            tag_dir = releases_root / arg
            if tag_dir.is_dir():
                old_target = os.readlink(current) if current.is_symlink() else None
                if old_target:
                    previous = parent / "previous"
                    os.symlink(old_target, str(previous))
                os.remove(current)
                os.symlink(str(tag_dir), str(current))
        return (0, f"extracted {arg}")
    return _release_cmd


def _make_bounce_cmd(bouncer_path: Path) -> callable:
    """Create a stub bounce_cmd that runs the fake bouncer script."""
    def _bounce_cmd() -> int:
        r = subprocess.run(
            [str(bouncer_path)],
            capture_output=True, text=True,
            timeout=10,
        )
        return r.returncode
    return _bounce_cmd


def _make_status_cmd(mapping: dict[str, str]) -> callable:
    """Create a stub status_cmd that returns values from a tag→status mapping."""
    def _status_cmd(tag: str) -> str:
        return mapping.get(tag, "unreachable")
    return _status_cmd


def _write_stub_bouncer(tmp_path: Path) -> Path:
    """Write a fake bounce_daemons.sh that records its argv and prints fresh output."""
    bouncer = tmp_path / "bounce_daemons.sh"
    bouncer.write_text("#!/bin/bash\necho \"$@\" >> \"$(dirname \"$0\")/bouncer_argv\"\n")
    bouncer.chmod(0o755)
    return bouncer


def _make_pid_file(tmp_path: Path, alive: bool = True) -> Path:
    """Create a fake scheduler.pid file.  Returns pid_file."""
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    if alive:
        proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(proc)
        pid_file.write_text(str(proc.pid))
    else:
        pid_file.write_text("99999999")

    return pid_file


def _make_pid_file_for_tag(tmp_path: Path, tag: str) -> Path:
    """Create a fake scheduler.pid whose process command line mentions releases/<tag>/.

    The script is placed in a directory named ``releases/<tag>/`` so that
    ``ps -o command=`` shows the path pattern the deploy smoke checks for.
    """
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    # Create the script under releases/<tag>/ so ps shows the path
    release_dir = tmp_path / "releases" / tag
    release_dir.mkdir(parents=True, exist_ok=True)
    script = release_dir / "scheduler.sh"
    script.write_text("#!/bin/bash\nsleep 60\n")
    script.chmod(0o755)

    proc = subprocess.Popen(["bash", str(script)])
    _LAUNCHED_PROCS.append(proc)
    pid_file.write_text(str(proc.pid))

    return pid_file


# ── AC-1: deploy succeeds when smoke passes ─────────────────────────────────

class TestDeploySucceedsWhenSmokePasses:
    """AC-1: deploy v0.0.2 with stub status ok + stub pid alive → exit 0, current → v0.0.2."""

    def test_deploy_exits_zero_and_flips_current(self, tmp_path: Path) -> None:
        """Smoke passes: exit 0, current points to v0.0.2."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd(bouncer),
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
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
    """AC-2: smoke fails for v0.0.2, ok for v0.0.1 → rolled_back_to: v0.0.1."""

    def test_deploy_rolls_back_on_smoke_failure(self, tmp_path: Path) -> None:
        """Smoke fails for new tag, passes for previous → rollback."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        # The pid file's process must mention releases/v0.0.1/ for the rollback smoke
        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.1")

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_make_bounce_cmd(bouncer),
            status_cmd=_make_status_cmd({"v0.0.2": "tag-mismatch", "v0.0.1": "ok"}),
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
    """AC-3: both smokes fail → current back to v0.0.1, rollback_smoke: failed."""

    def test_deploy_both_smokes_fail(self, tmp_path: Path) -> None:
        """Both smokes fail → current back to v0.0.1, rollback_smoke: failed."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        pid_file = _make_pid_file(tmp_path, alive=True)

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_make_bounce_cmd(bouncer),
            status_cmd=_make_status_cmd({"v0.0.2": "unreachable", "v0.0.1": "unreachable"}),
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

    def test_deploy_exits_four_when_extraction_fails(self, tmp_path: Path) -> None:
        """No such tag in repo → exit 4, current unchanged, no bounce called."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        pid_file = _make_pid_file(tmp_path, alive=True)

        # Record the current target before deploy
        current_before = _read_current_target(parent)

        with pytest.raises(SystemExit) as exc_info:
            deploy(
                project=project,
                tag="v99.99.99",  # tag that doesn't exist
                data_dir=data_dir,
                release_cmd=_make_release_cmd(allow_tags=set(), releases_root=releases_root),  # nothing allowed
                bounce_cmd=_make_bounce_cmd(bouncer),
                status_cmd=_make_status_cmd({"v99.99.99": "ok"}),
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
    """AC-5 (control): deploy uses only injected commands, no real launchctl."""

    def test_no_real_launchctl_in_test_env(self, tmp_path: Path) -> None:
        """The conftest host guard ensures no real launchctl is called.

        This test exists as a control: if launchctl ran, the guard would
        have already failed the test session.  The assertion is that the
        deploy verb uses only injected commands.
        """
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        bouncer = _write_stub_bouncer(tmp_path)
        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        result = deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd(bouncer),
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
            pid_file=pid_file,
        )

        # If we got here without the host guard failing, no real launchctl ran.
        assert result["deployed"] is True


# ── Cleanup ─────────────────────────────────────────────────────────────────

_LAUNCHED_PROCS: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _cleanup():
    """Clean up any sleep processes started by test helpers."""
    _LAUNCHED_PROCS.clear()
    yield
    for proc in _LAUNCHED_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass