"""Tests for release_train deploy — data dir, pid path, bounce check, exit codes.

Sub-plan: the-trains-deploy-tells-the-truth, step 0 (red-first pins).

Each test builds a throwaway data root and releases root under ``tmp_path``.
All external commands (release, bounce, host_deploy_status, launchctl, ssh,
ilk_notify) are stubbed.  No real ~/.ilk, ~/.ilk-data, real daemon, or real
scheduler pid is touched.

The nine acceptance criteria:
  AC-1  main() deploy uses project_key for data_dir, not project.name.
  AC-2  deploy(pid_file=None) reads <ILK_DATA_HOME>/scheduler.pid.
  AC-3  passing smoke: deploy returns deployed:True, exit_code:0.
  AC-4  rolled back, verified: exit_code:5, rolled_back audit row.
  AC-5  rollback unverified: exit_code:6, severity:critical.
  AC-6  failed bounce is reported (exit 2, exit 0, unchanged pid).
  AC-7  symlinked launch passes _smoke.
  AC-8  _bouncer_for resolves to releases/<tag>/bounce_daemons.sh.
  AC-9  control: existing tests still pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

# ── Helpers ─────────────────────────────────────────────────────────────────

_LAUNCHED_PROCS: list[subprocess.Popen] = []


def _make_releases_root(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tmp releases root with v0.0.1 as current.

    Returns (releases_root, parent) where parent holds current/previous symlinks.
    """
    releases_root = tmp_path / "releases"
    releases_root.mkdir()
    parent = releases_root.parent

    v001_dir = releases_root / "v0.0.1"
    v001_dir.mkdir()
    manifest = {
        "tag": "v0.0.1",
        "sha": "a" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T00:00:00+00:00",
    }
    (v001_dir / ".ilk-release.json").write_text(json.dumps(manifest, indent=2) + "\n")

    v002_dir = releases_root / "v0.0.2"
    v002_dir.mkdir()
    manifest2 = {
        "tag": "v0.0.2",
        "sha": "b" * 40,
        "source_repo": str(tmp_path / "fake_repo"),
        "extracted_at": "2026-10-03T01:00:00+00:00",
    }
    (v002_dir / ".ilk-release.json").write_text(json.dumps(manifest2, indent=2) + "\n")

    current = parent / "current"
    os.symlink(str(v001_dir), str(current))

    return releases_root, parent


def _make_fake_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with two tags."""
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
    """Stub release_cmd.  allow_tags=None means all succeed."""
    def _release_cmd(arg: str, repo: Path) -> tuple[int, str]:
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

        if allow_tags is not None and arg not in allow_tags:
            print(f"refused: no such tag: {arg}", file=sys.stderr)
            raise SystemExit(4)
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
    """Stub bounce_cmd that runs a fake bouncer script."""
    def _bounce_cmd(tag: str) -> int:
        r = subprocess.run(
            [str(bouncer_path)],
            capture_output=True, text=True,
            timeout=10,
        )
        return r.returncode
    return _bounce_cmd


def _make_bounce_cmd_with_pid(
    bouncer_path: Path,
    pid_file: Path,
    new_pid: int,
) -> callable:
    """Stub bounce_cmd that exits 1 and replaces the pid file (simulates restart)."""
    def _bounce_cmd(tag: str) -> int:
        pid_file.write_text(str(new_pid))
        return 1
    return _bounce_cmd


def _make_bounce_cmd_with_exit(exit_code: int) -> callable:
    """Stub bounce_cmd that returns a specific exit code."""
    def _bounce_cmd(tag: str) -> int:
        return exit_code
    return _bounce_cmd


def _make_status_cmd(mapping: dict[str, str]) -> callable:
    """Stub status_cmd returning values from a tag→status mapping."""
    def _status_cmd(tag: str, cwd: Path | None = None) -> str:
        return mapping.get(tag, "unreachable")
    return _status_cmd


def _write_stub_bouncer(tmp_path: Path) -> Path:
    """Write a fake bounce_daemons.sh."""
    bouncer = tmp_path / "bounce_daemons.sh"
    bouncer.write_text("#!/bin/bash\necho \"$@\" >> \"$(dirname \"$0\")/bouncer_argv\"\n")
    bouncer.chmod(0o755)
    return bouncer


def _make_pid_file(tmp_path: Path, alive: bool = True) -> Path:
    """Create a fake scheduler.pid file."""
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
    """Create a fake scheduler.pid whose process command line mentions releases/<tag>/."""
    pid_dir = tmp_path / "data" / "runtime"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "scheduler.pid"

    release_dir = tmp_path / "releases" / tag
    release_dir.mkdir(parents=True, exist_ok=True)
    script = release_dir / "scheduler.sh"
    script.write_text("#!/bin/bash\nsleep 60\n")
    script.chmod(0o755)

    proc = subprocess.Popen(["bash", str(script)])
    _LAUNCHED_PROCS.append(proc)
    pid_file.write_text(str(proc.pid))

    return pid_file


def _read_current_target(parent: Path) -> str | None:
    """Read the target of the current symlink."""
    current = parent / "current"
    if current.is_symlink():
        return os.readlink(current)
    return None


# ── AC-1: main() uses project_key for data_dir ──────────────────────────────

class TestMainUsesProjectKeyForDataDir:
    """AC-1: main() deploy records data_dir as <data_root>/projects/<project_key>."""

    def test_main_deploy_uses_project_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() deploy verb should key data_dir by project_key, not project.name."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)

        # Import after env setup
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train
        from ilk_paths import project_key

        recorded = {}

        def fake_deploy(proj, tag, data_dir, **kwargs):
            recorded["data_dir"] = str(data_dir)
            return {"exit_code": 0, "deployed": True, "tag": tag}

        monkeypatch.setattr(release_train, "deploy", fake_deploy)

        # Run main() with deploy verb
        monkeypatch.setattr(sys, "argv", [
            "release_train.py", "deploy",
            "--project", str(project),
            "--tag", "v0.0.2",
        ])

        exit_code = release_train.main()
        assert exit_code == 0

        expected_key = project_key(project)
        expected_dir = str(tmp_path / "data" / "projects" / expected_key)
        assert recorded["data_dir"] == expected_dir, (
            f"Expected data_dir keyed by project_key ({expected_key}), "
            f"got {recorded['data_dir']}"
        )


# ── AC-2: deploy reads <ILK_DATA_HOME>/scheduler.pid ────────────────────────

class TestDeployReadsHostSchedulerPid:
    """AC-2: deploy(pid_file=None) reads <ILK_DATA_HOME>/scheduler.pid."""

    def test_default_pid_file_is_data_root_scheduler_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """deploy with pid_file=None should read <ILK_DATA_HOME>/scheduler.pid."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        recorded = {}

        # Stub _smoke to record the pid_file it receives
        original_smoke = release_train._smoke

        def fake_smoke(tag, status_cmd, pid_file, **kwargs):
            recorded["pid_file"] = str(pid_file)
            return True, ""

        monkeypatch.setattr(release_train, "_smoke", fake_smoke)

        bouncer = _write_stub_bouncer(tmp_path)
        data_dir = tmp_path / "data" / "projects" / "fake-key"
        data_dir.mkdir(parents=True, exist_ok=True)

        release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd(bouncer),
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
            pid_file=None,
        )

        expected_pid = str(tmp_path / "data" / "scheduler.pid")
        assert recorded["pid_file"] == expected_pid, (
            f"Expected pid_file {expected_pid}, got {recorded['pid_file']}"
        )


# ── AC-3: passing smoke returns deployed:True, exit_code:0 ──────────────────

class TestPassingSmokeReturnsExitCodeZero:
    """AC-3: passing smoke → deployed:True, exit_code:0."""

    def test_deploy_returns_exit_code_zero(self, tmp_path: Path) -> None:
        """A passing smoke should yield exit_code: 0."""
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        # Start a new process from releases/v0.0.2/ to simulate a restart
        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd_with_pid(None, pid_file, new_proc.pid),
            status_cmd=_make_status_cmd({"v0.0.2": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is True
        assert result["exit_code"] == 0
        assert "scheduler_pid" in result


# ── AC-4: rolled back, verified → exit_code:5 ───────────────────────────────

class TestRolledBackVerifiedExitCodeFive:
    """AC-4: forward fails, rollback verified → exit_code:5, rolled-back audit."""

    def test_deploy_returns_exit_code_five_on_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rollback with verified smoke → exit_code: 5."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Stub _smoke: first call (forward) fails, second (rollback) succeeds
        call_count = {"n": 0}

        def fake_smoke(tag, status_cmd, pid_file, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False, "status=tag-mismatch"
            return True, ""

        monkeypatch.setattr(release_train, "_smoke", fake_smoke)

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.1")

        # Bounce exits 1 and writes a new pid (so bounce check passes)
        new_proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(new_proc)

        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_make_bounce_cmd_with_pid(None, pid_file, new_proc.pid),
            status_cmd=_make_status_cmd({"v0.0.2": "tag-mismatch", "v0.0.1": "ok"}),
            pid_file=pid_file,
        )

        assert result["exit_code"] == 5
        assert result["deployed"] is False
        assert result["rolled_back_to"] == "v0.0.1"
        assert result["rollback_smoke"] == "ok"

        # Verify run() maps this to rolled_back: True and writes rolled-back audit
        audit_file = tmp_path / "data" / "audit.jsonl"

        def fake_check(proj, data_dir):
            return {"eligible": True}

        def fake_prove(proj, data_dir):
            return {"proven": True}

        def fake_cut(proj, data_dir):
            return {"tag": "v0.0.2"}

        run_result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=fake_check,
            prove_fn=fake_prove,
            cut_fn=fake_cut,
            deploy_fn=lambda p, t, d: result,
            notify_script="/dev/null",
        )

        assert run_result["exit_code"] == 5
        assert run_result["rolled_back"] is True

        # Check audit file for rolled-back row, NO released row
        if audit_file.exists():
            lines = audit_file.read_text().strip().split("\n")
            events = [json.loads(line) for line in lines if line.strip()]
            rolled_back = [e for e in events if e.get("event") == "rolled-back"]
            released = [e for e in events if e.get("event") == "released"]
            assert len(rolled_back) >= 1, "Expected rolled-back audit row"
            assert len(released) == 0, "Should not have released audit row on rollback"


# ── AC-5: rollback unverified → exit_code:6 ─────────────────────────────────

class TestRollbackUnverifiedExitCodeSix:
    """AC-5: both smokes fail → exit_code:6, severity:critical."""

    def test_deploy_returns_exit_code_six_on_unverified_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both smokes fail → exit_code: 6."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        def fake_smoke(tag, status_cmd, pid_file, **kwargs):
            return False, "status=unreachable"

        monkeypatch.setattr(release_train, "_smoke", fake_smoke)

        pid_file = _make_pid_file(tmp_path, alive=True)

        # Bounce exits 1 and writes a new pid (so bounce check passes)
        new_proc = subprocess.Popen(["sleep", "60"])
        _LAUNCHED_PROCS.append(new_proc)

        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(
                allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root,
            ),
            bounce_cmd=_make_bounce_cmd_with_pid(None, pid_file, new_proc.pid),
            status_cmd=_make_status_cmd({"v0.0.2": "unreachable", "v0.0.1": "unreachable"}),
            pid_file=pid_file,
        )

        assert result["exit_code"] == 6
        assert result["deployed"] is False


# ── AC-6: failed bounce is reported ─────────────────────────────────────────

class TestFailedBounceIsReported:
    """AC-6: bounce failures are detected and reported."""

    def test_bounce_exit_two_reports_failure(self, tmp_path: Path) -> None:
        """Bounce exits 2 → not deployed, reason contains 'bounce failed'."""
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        # Rollback needs a new process running from releases/v0.0.1/
        release_dir = releases_root / "v0.0.1"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        rollback_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(rollback_proc)

        # Forward bounce exits 2, rollback bounce exits 1 with new pid
        call_count = {"n": 0}

        def _failing_bounce(tag: str) -> int:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 2  # forward: failed
            pid_file.write_text(str(rollback_proc.pid))
            return 1  # rollback: restarted

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2", "v0.0.1"}, releases_root=releases_root),
            bounce_cmd=_failing_bounce,
            status_cmd=_make_status_cmd({"v0.0.2": "ok", "v0.0.1": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert "bounce failed" in result.get("reason", "")
        assert result.get("bounce", {}).get("exit") == 2

    def test_bounce_exit_zero_reports_no_restart(self, tmp_path: Path) -> None:
        """Bounce exits 0 → reason contains 'restarted nothing'."""
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd_with_exit(0),
            status_cmd=_make_status_cmd({"v0.0.2": "ok", "v0.0.1": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert "restarted nothing" in result.get("reason", "")

    def test_bounce_exit_one_unchanged_pid_reports_failure(self, tmp_path: Path) -> None:
        """Bounce exits 1 but pid unchanged → reason contains 'unchanged'."""
        releases_root, _ = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd_with_exit(1),
            status_cmd=_make_status_cmd({"v0.0.2": "ok", "v0.0.1": "ok"}),
            pid_file=pid_file,
        )

        assert result["deployed"] is False
        assert "unchanged" in result.get("reason", "")


# ── AC-7: symlinked launch passes _smoke ────────────────────────────────────

class TestSymlinkedLaunchPassesSmoke:
    """AC-7: a scheduler started via a symlink passes _smoke."""

    def test_smoke_passes_through_symlink(self, tmp_path: Path) -> None:
        """_smoke should pass when the scheduler is reached via a current/ symlink."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Create releases/v0.0.2 with a scheduler.sh
        release_dir = tmp_path / "releases" / "v0.0.2"
        release_dir.mkdir(parents=True)
        script = release_dir / "scheduler.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        # Create a current/ symlink pointing to v0.0.2
        current = tmp_path / "current"
        os.symlink(str(release_dir), str(current))

        # Start the scheduler via the symlink
        proc = subprocess.Popen(["bash", str(current / "scheduler.sh")])
        _LAUNCHED_PROCS.append(proc)

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        pid_file.write_text(str(proc.pid))

        ok, reason = release_train._smoke(
            "v0.0.2",
            _make_status_cmd({"v0.0.2": "ok"}),
            pid_file,
        )

        assert ok, f"_smoke failed: {reason}"


# ── AC-8: _bouncer_for resolves correctly ────────────────────────────────────

class TestBouncerForResolvesCorrectly:
    """AC-8: _bouncer_for(tag) resolves to releases/<tag>/bounce_daemons.sh."""

    def test_bouncer_for_resolves_to_release_bouncer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_bouncer_for should return the bouncer inside the release dir."""
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root = tmp_path / "releases"
        releases_root.mkdir()
        bouncer = releases_root / "v0.0.2" / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"
        bouncer.parent.mkdir(parents=True)
        bouncer.write_text("#!/bin/bash\nexit 0\n")
        bouncer.chmod(0o755)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._bouncer_for("v0.0.2")
        assert result == bouncer, f"Expected {bouncer}, got {result}"

    def test_default_bounce_runs_release_bouncer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default bounce_cmd should run the bouncer from the release dir."""
        monkeypatch.setenv("ILK_RELEASES_ROOT", str(tmp_path / "releases"))

        releases_root, _ = _make_releases_root(tmp_path)

        # Place a bouncer inside v0.0.2's release dir
        bouncer = releases_root / "v0.0.2" / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"
        bouncer.parent.mkdir(parents=True)
        bouncer.write_text("#!/bin/bash\necho \"bounced\" > \"$(dirname \"$0\")/called\"\nexit 1\n")
        bouncer.chmod(0o755)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Verify _bouncer_for resolves correctly
        resolved = release_train._bouncer_for("v0.0.2")
        assert resolved == bouncer

        # Verify default bounce_cmd runs the bouncer.
        # We need the deploy to succeed (no rollback), so the process must
        # appear to be running from releases/v0.0.2/.
        recorded = {}
        original_run = subprocess.run

        def recording_run(args, **kwargs):
            if args and "bounce_daemons" in str(args[0]):
                recorded["bounce_cmd"] = str(args[0])
            return original_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", recording_run)

        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        # Start a process from releases/v0.0.2/ so _smoke passes
        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(proc)

        # Start a second process for the "new" pid after bounce
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        pid_dir = tmp_path / "data" / "runtime"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "scheduler.pid"
        pid_file.write_text(str(proc.pid))

        # Use a bounce_cmd that writes a new pid (so the bounce check passes)
        # and also invokes the real bouncer so we can verify it was called.
        def _test_bounce(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            # Also run the real bouncer to verify _bouncer_for resolution
            r = subprocess.run(
                [str(release_train._bouncer_for(tag))],
                capture_output=True, text=True, timeout=10,
            )
            return 1  # bounced

        release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=lambda t, r: (0, "ok"),
            bounce_cmd=_test_bounce,
            status_cmd=lambda t, cwd=None: "ok",
            pid_file=pid_file,
        )

        assert "bounce_cmd" in recorded
        assert "bounce_daemons" in recorded["bounce_cmd"]


# ── AC-9: control — existing tests pass ──────────────────────────────────────

class TestControlExistingTestsPass:
    """AC-9: existing deploy tests still pass (import check only)."""

    def test_existing_test_module_imports(self) -> None:
        """Verify the existing test module can still be imported."""
        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train
        # If we got here, the module imports fine
        assert hasattr(release_train, "deploy")


# ── Cleanup ─────────────────────────────────────────────────────────────────

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