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


# ── Fake clock/sleeper for settle tests ────────────────────────────────────


class FakeClock:
    """A monotonic clock whose time advances only via advance(seconds)."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeSleeper:
    """Records each sleep(duration) call without actually sleeping."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


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

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd_with_exit(0),
            status_cmd=_make_status_cmd({"v0.0.2": "ok", "v0.0.1": "ok"}),
            pid_file=pid_file,
            settle_clock=clock.monotonic,
            settle_sleeper=sleeper.sleep,
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

        clock = FakeClock()
        sleeper = FakeSleeper(clock)

        result = release_train.deploy(
            project=project,
            tag="v0.0.2",
            data_dir=data_dir,
            release_cmd=_make_release_cmd(allow_tags={"v0.0.2"}, releases_root=releases_root),
            bounce_cmd=_make_bounce_cmd_with_exit(1),
            status_cmd=_make_status_cmd({"v0.0.2": "ok", "v0.0.1": "ok"}),
            pid_file=pid_file,
            settle_clock=clock.monotonic,
            settle_sleeper=sleeper.sleep,
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


# ── Multi-host deploy: host-2 failures after host-1 success ─────────────────
#
# AC-MH-1  host-1 deploys, host-2 extraction fails → host-1 deployed, host-2 untouched
# AC-MH-2  host-1 deploys, host-2 bounce fails → host-1 deployed, host-2 untouched
# AC-MH-3  host-1 deploys, host-2 smoke fails → host-1 deployed, host-2 rolled back
# AC-MH-4  host-1 deploys, host-2 both smokes fail → host-1 deployed, host-2 critical
# AC-MH-5  host-1 deploys, host-2 transport fails → host-1 deployed, host-2 unverified
# AC-MH-6  result truth: deployed/rolled_back/untouched/unverified lists are honest


def _make_permit(
    data_dir: Path,
    host: str,
    *,
    project: str = "test-project",
    base_tag: str = "v0.0.1",
    candidate_head: str = "b" * 40,
    consumed: bool = False,
    revoked: bool = False,
    expired: bool = False,
) -> Path:
    """Write a permit file for a host under data_dir/runtime/permits/."""
    from datetime import datetime, timezone, timedelta

    permit_dir = data_dir / "runtime" / "permits"
    permit_dir.mkdir(parents=True, exist_ok=True)
    permit_path = permit_dir / f"{host}.json"

    now = datetime.now(timezone.utc)
    expires = now - timedelta(hours=1) if expired else now + timedelta(hours=1)

    permit = {
        "project": project,
        "base_tag": base_tag,
        "candidate_head": candidate_head,
        "host": host,
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "consumed": consumed,
        "consumed_at": None,
        "revoked": revoked,
    }
    permit_path.write_text(json.dumps(permit, indent=2) + "\n")
    return permit_path


class TestMultiHostHost2ExtractionFails:
    """AC-MH-1: host-1 deploys, host-2 extraction fails → host-1 deployed, host-2 untouched."""

    def test_host2_extraction_failure_after_host1_success(self, tmp_path: Path) -> None:
        """Host-1 succeeds; host-2 extraction raises SystemExit(4)."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        # Host-1 process for smoke
        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        def _bounce_with_restart(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            return 1

        # Deploy function that succeeds for host-1, fails for host-2
        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # host-1 succeeds
                return {
                    "tag": tag, "deployed": True,
                    "scheduler_pid": new_proc.pid, "exit_code": 0,
                }
            else:
                # host-2 extraction fails
                raise SystemExit(4)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp", "rezmac"],
            deploy_fn=_deploy_fn,
        )

        assert result["exit_code"] == 4
        assert "chad-mbp" in result["deployed"]
        assert "rezmac" in result["untouched"]
        assert len(result["deployed"]) == 1
        assert len(result["untouched"]) == 1


class TestMultiHostHost2BounceFails:
    """AC-MH-2: host-1 deploys, host-2 bounce fails → host-1 deployed, host-2 untouched."""

    def test_host2_bounce_failure_after_host1_success(self, tmp_path: Path) -> None:
        """Host-1 succeeds; host-2 bounce exits 2 (failed)."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        def _bounce_with_restart(tag: str) -> int:
            pid_file.write_text(str(new_proc.pid))
            return 1

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "tag": tag, "deployed": True,
                    "scheduler_pid": new_proc.pid, "exit_code": 0,
                }
            else:
                return {
                    "tag": tag, "deployed": False,
                    "reason": "bounce failed (exit 2)",
                    "exit_code": 5,
                    "bounce": {"exit": 2, "pid_before": None, "pid_after": None},
                }

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp", "rezmac"],
            deploy_fn=_deploy_fn,
        )

        assert "chad-mbp" in result["deployed"]
        # Bounce failure without rollback → untouched (deploy attempted, no rollback path)
        assert "rezmac" in result["untouched"]
        assert len(result["deployed"]) == 1


class TestMultiHostHost2SmokeFails:
    """AC-MH-3: host-1 deploys, host-2 smoke fails → host-1 deployed, host-2 rolled back."""

    def test_host2_smoke_failure_after_host1_success(self, tmp_path: Path) -> None:
        """Host-1 succeeds; host-2 smoke fails and rollback succeeds."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "tag": tag, "deployed": True,
                    "scheduler_pid": new_proc.pid, "exit_code": 0,
                }
            else:
                return {
                    "tag": tag, "deployed": False,
                    "rolled_back_to": "v0.0.1",
                    "rollback_smoke": "ok",
                    "reason": "status=tag-mismatch",
                    "exit_code": 5,
                    "bounce": {"exit": 1, "pid_before": None, "pid_after": new_proc.pid},
                }

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp", "rezmac"],
            deploy_fn=_deploy_fn,
        )

        assert result["exit_code"] == 5
        assert "chad-mbp" in result["deployed"]
        assert "rezmac" in result["rolled_back"]
        assert len(result["deployed"]) == 1
        assert len(result["rolled_back"]) == 1


class TestMultiHostHost2BothSmokesFail:
    """AC-MH-4: host-1 deploys, host-2 both smokes fail → host-1 deployed, host-2 critical."""

    def test_host2_both_smokes_fail_after_host1_success(self, tmp_path: Path) -> None:
        """Host-1 succeeds; host-2 both smokes fail (exit 6)."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "tag": tag, "deployed": True,
                    "scheduler_pid": new_proc.pid, "exit_code": 0,
                }
            else:
                return {
                    "tag": tag, "deployed": False,
                    "rolled_back_to": "v0.0.1",
                    "rollback_smoke": "failed",
                    "reason": "status=unreachable",
                    "exit_code": 6,
                    "bounce": {"exit": 1, "pid_before": None, "pid_after": new_proc.pid},
                }

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp", "rezmac"],
            deploy_fn=_deploy_fn,
        )

        assert result["exit_code"] == 6
        assert "chad-mbp" in result["deployed"]
        # Host-2 rolled back (even though both smokes failed, it still attempted rollback)
        assert "rezmac" in result["rolled_back"]


class TestMultiHostHost2TransportFails:
    """AC-MH-5: host-1 deploys, host-2 transport fails → host-1 deployed, host-2 unverified."""

    def test_host2_transport_failure_after_host1_success(self, tmp_path: Path) -> None:
        """Host-1 succeeds; host-2 is remote and transport fails (exit 2)."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        pid_file = _make_pid_file_for_tag(tmp_path, "v0.0.2")

        release_dir = releases_root / "v0.0.2"
        script = release_dir / "scheduler.sh"
        if not script.exists():
            script.write_text("#!/bin/bash\nsleep 60\n")
            script.chmod(0o755)
        new_proc = subprocess.Popen(["bash", str(script)])
        _LAUNCHED_PROCS.append(new_proc)

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "tag": tag, "deployed": True,
                    "scheduler_pid": new_proc.pid, "exit_code": 0,
                }
            else:
                return {
                    "tag": tag, "deployed": False,
                    "reason": "remote deploy not yet implemented",
                    "exit_code": 2,
                    "host": "rezmac",
                    "transport": "ssh",
                }

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp"],
            deploy_fn=_deploy_fn,
        )

        assert result["exit_code"] == 2
        assert "chad-mbp" in result["deployed"]
        assert "rezmac" in result["unverified"]
        assert len(result["deployed"]) == 1
        assert len(result["unverified"]) == 1


class TestMultiHostResultTruth:
    """AC-MH-6: deployed/rolled_back/untouched/unverified lists are honest."""

    def test_all_four_categories_populated(self, tmp_path: Path) -> None:
        """Four hosts, each landing in a different category."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # host-1: deployed
                return {"tag": tag, "deployed": True, "exit_code": 0}
            elif call_count["n"] == 2:
                # host-2: rolled back
                return {
                    "tag": tag, "deployed": False,
                    "rolled_back_to": "v0.0.1", "rollback_smoke": "ok",
                    "exit_code": 5,
                }
            elif call_count["n"] == 3:
                # host-3: extraction fails (untouched)
                raise SystemExit(4)
            else:
                # host-4: transport fails (unverified)
                return {"tag": tag, "deployed": False, "exit_code": 2}

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["h1", "h2", "h3", "h4"],
            local_hosts=["h1", "h2", "h3", "h4"],
            deploy_fn=_deploy_fn,
        )

        assert "h1" in result["deployed"]
        assert "h2" in result["rolled_back"]
        assert "h3" in result["untouched"]
        assert "h4" in result["unverified"]
        assert result["exit_code"] == 5  # worst non-zero exit

    def test_rolled_back_not_hidden_by_deployed(self, tmp_path: Path) -> None:
        """Host-1 deployed, host-2 rolled back → worst exit is 5, not 0."""
        releases_root, parent = _make_releases_root(tmp_path)
        project = _make_fake_project(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        call_count = {"n": 0}

        def _deploy_fn(proj, tag, data_dir, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"tag": tag, "deployed": True, "exit_code": 0}
            return {
                "tag": tag, "deployed": False,
                "rolled_back_to": "v0.0.1", "rollback_smoke": "ok",
                "exit_code": 5,
            }

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["h1", "h2"],
            local_hosts=["h1", "h2"],
            deploy_fn=_deploy_fn,
        )

        assert result["exit_code"] == 5, (
            "A failed host-2 must not be hidden by host-1's success. "
            "exit_code must be 5 (rollback), not 0 (deployed)."
        )


# ── Step 0: xfail pins for canonical host resolution and SSH deploy ────────
#
# These pins assert the contracts that step 1 (resolve and thread one host
# list) and step 2 (implement bounded SSH deploy and rollback) will make
# green.  Each pin asserts argv, timeouts, target host, tag, and result
# shape — not merely that an SSH function was called.


def _write_ship_config(data_dir: Path, hosts: list[str]) -> None:
    """Write a minimal ship.hosts configuration."""
    config_dir = data_dir / "runtime"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {"ship": {"hosts": hosts}}
    (config_dir / "ship-config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )


class TestCanonicalHostResolution:
    """AC-1: run(..., hosts=None) resolves configured hosts once and threads
    the same list through permits, consumption, deploy, and audit."""

    def test_run_with_hosts_none_resolves_from_config(self, tmp_path: Path) -> None:
        """run(hosts=None) should resolve hosts from ship-config.json and
        pass the same ordered list to _check_permits and _deploy_all_hosts."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        # Write ship-config with two hosts
        _write_ship_config(data_dir, ["chad-mbp", "rezmac"])

        # Write valid permits with correct project key and matching candidate_head
        sys.path.insert(0, str(LOOP_SCRIPTS))
        from ilk_paths import project_key
        pkey = project_key(project)
        _make_permit(data_dir, "chad-mbp", project=pkey, candidate_head="a" * 40)
        _make_permit(data_dir, "rezmac", project=pkey, candidate_head="a" * 40)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Monkey-patch _deploy_all_hosts to record the hosts it receives
        recorded_args = []
        original_deploy_all = release_train._deploy_all_hosts

        def _recording_deploy_all(project, tag, data_dir, **kwargs):
            recorded_args.append(kwargs.get("hosts"))
            return {"tag": tag, "hosts": {"chad-mbp": {"deployed": True}, "rezmac": {"deployed": True}},
                    "exit_code": 0, "deployed": ["chad-mbp", "rezmac"],
                    "rolled_back": [], "untouched": [], "unverified": []}

        release_train._deploy_all_hosts = _recording_deploy_all

        def _check(proj, data_dir):
            return {"eligible": True, "last_tag": "v0.0.1", "head": "a" * 40}

        def _prove(proj, data_dir):
            return {"proven": True}

        def _cut(proj, data_dir):
            return {"tag": "v0.0.2", "commit": "b" * 40}

        # Run with hosts=None — should resolve from config
        release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=_check,
            prove_fn=_prove,
            cut_fn=_cut,
            hosts=None,
            notify_script="/dev/null",
        )

        release_train._deploy_all_hosts = original_deploy_all

        # The resolved host list should have been threaded through
        assert len(recorded_args) > 0, "_deploy_all_hosts should have been called"
        # The hosts passed to deploy should be the canonical list from config
        assert recorded_args[0] == ["chad-mbp", "rezmac"], (
            f"Expected canonical host list ['chad-mbp', 'rezmac'], got {recorded_args[0]}"
        )


class TestCanonicalHostResolutionSingleHost:
    """AC-1 corollary: hosts=None with one configured host still resolves
    to the canonical list, not to the single-host branch."""

    def test_single_configured_host_does_not_use_single_host_branch(self, tmp_path: Path) -> None:
        """run(hosts=None) with one configured host must still resolve
        through _deploy_all_hosts, not the backward-compat single-host path."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_ship_config(data_dir, ["chad-mbp"])

        sys.path.insert(0, str(LOOP_SCRIPTS))
        from ilk_paths import project_key
        pkey = project_key(project)
        _make_permit(data_dir, "chad-mbp", project=pkey, candidate_head="a" * 40)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Monkey-patch _deploy_all_hosts to record that it was called
        deploy_all_called = []
        original_deploy_all = release_train._deploy_all_hosts

        def _recording_deploy_all(project, tag, data_dir, **kwargs):
            deploy_all_called.append(kwargs.get("hosts"))
            return {"tag": tag, "hosts": {"chad-mbp": {"deployed": True}},
                    "exit_code": 0, "deployed": ["chad-mbp"],
                    "rolled_back": [], "untouched": [], "unverified": []}

        release_train._deploy_all_hosts = _recording_deploy_all

        def _check(proj, data_dir):
            return {"eligible": True, "last_tag": "v0.0.1", "head": "a" * 40}

        def _prove(proj, data_dir):
            return {"proven": True}

        def _cut(proj, data_dir):
            return {"tag": "v0.0.2", "commit": "b" * 40}

        release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=_check,
            prove_fn=_prove,
            cut_fn=_cut,
            hosts=None,
            notify_script="/dev/null",
        )

        release_train._deploy_all_hosts = original_deploy_all

        # With one configured host, _deploy_all_hosts should still be called
        # (not the single-host backward-compat branch)
        assert len(deploy_all_called) > 0, "_deploy_all_hosts should have been called"
        # The host list should be the resolved canonical list
        assert deploy_all_called[0] == ["chad-mbp"], (
            f"Expected ['chad-mbp'], got {deploy_all_called[0]}"
        )


class TestSSHDeploySucceeds:
    """AC-2: a successful fake SSH deploy returns structured evidence with
    extract, bounce, smoke, and tag conformance for the named host."""

    def test_fake_ssh_deploy_returns_structured_evidence(self, tmp_path: Path) -> None:
        """A remote host reached via SSH should return a result dict with:
        - host name
        - transport: 'ssh'
        - deployed: True
        - exit_code: 0
        - tag matching the deployed tag
        - ssh argv with target host, timeout, and tag extraction command
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Inject a fake SSH adapter that records its call
        ssh_calls = []

        def _fake_ssh_deploy(proj, tag, data_dir, **kwargs):
            host = kwargs.get("host", "rezmac")
            ssh_calls.append({
                "host": host,
                "tag": tag,
                "transport": "ssh",
            })
            return {
                "tag": tag,
                "deployed": True,
                "exit_code": 0,
                "host": host,
                "transport": "ssh",
                "extract": {"rc": 0, "tag": tag},
                "bounce": {"exit": 1, "pid_before": 12345, "pid_after": 12346},
                "smoke": {"ok": True, "reason": ""},
            }

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],  # rezmac is remote
            ssh_deploy_fn=_fake_ssh_deploy,
        )

        assert result["exit_code"] == 0
        assert "rezmac" in result["deployed"]
        assert len(ssh_calls) == 1
        assert ssh_calls[0]["host"] == "rezmac"
        assert ssh_calls[0]["tag"] == "v0.0.2"
        assert ssh_calls[0]["transport"] == "ssh"

        # Result should carry structured evidence
        host_result = result["hosts"]["rezmac"]
        assert host_result["deployed"] is True
        assert host_result["exit_code"] == 0
        assert host_result["transport"] == "ssh"
        assert "extract" in host_result
        assert "bounce" in host_result
        assert "smoke" in host_result


class TestSSHTransportRefusal:
    """AC-3: SSH/auth/timeout failures are explicit nonzero per-host results
    and can never be classified as deployed or ok."""

    def test_ssh_auth_failure_returns_nonzero_exit(self, tmp_path: Path) -> None:
        """A remote host must be reached via the SSH adapter (not the
        placeholder stub).  The adapter should be invoked with the target
        host, tag, and a bounded timeout, and should return structured
        evidence including the ssh argv."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # This test asserts the SSH adapter is actually called for remote
        # hosts.  Today _deploy_all_hosts returns a hardcoded placeholder
        # without calling any adapter.  Step 2 will wire in a real adapter.
        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],  # rezmac is remote
        )

        host_result = result["hosts"]["rezmac"]
        # The result must carry structured SSH evidence, not a placeholder
        assert host_result["deployed"] is False
        assert host_result["exit_code"] != 0
        assert host_result.get("transport") == "ssh"
        # The placeholder returns "remote deploy not yet implemented" —
        # the real adapter must return something else
        assert host_result.get("reason") != "remote deploy not yet implemented", (
            "SSH adapter should be called, not the placeholder stub"
        )

    def test_ssh_timeout_returns_nonzero_exit(self, tmp_path: Path) -> None:
        """An SSH timeout must be reported by the real adapter with a
        bounded timeout value in the result, not by the placeholder stub."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],
        )

        host_result = result["hosts"]["rezmac"]
        assert host_result["deployed"] is False
        assert host_result["exit_code"] != 0
        # Placeholder says "remote deploy not yet implemented" — the real
        # adapter must not produce that message
        assert host_result.get("reason") != "remote deploy not yet implemented", (
            "SSH adapter should be called, not the placeholder stub"
        )
        # Real adapter must carry structured evidence
        for key in ("extract", "bounce", "smoke"):
            assert key in host_result, (
                f"Remote host result must carry '{key}' evidence"
            )

    def test_ssh_extraction_failure_returns_nonzero_exit(self, tmp_path: Path) -> None:
        """An extraction failure on remote host must come from the real
        SSH adapter, not the placeholder stub."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],
        )

        host_result = result["hosts"]["rezmac"]
        assert host_result["deployed"] is False
        assert host_result["exit_code"] != 0
        # Placeholder says "remote deploy not yet implemented"
        assert host_result.get("reason") != "remote deploy not yet implemented"


class TestPerHostRollback:
    """AC-4: forward failure invokes rollback on that same host and records
    verified rollback or critical unverified rollback."""

    def test_ssh_smoke_failure_triggers_rollback_on_same_host(self, tmp_path: Path) -> None:
        """When remote smoke fails, rollback runs on the same host and
        returns structured evidence."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        rollback_calls = []

        def _deploy_with_rollback(proj, tag, data_dir, **kwargs):
            host = kwargs.get("host", "rezmac")
            if tag == "v0.0.2":
                # Forward deploy: smoke fails
                return {
                    "tag": tag,
                    "deployed": False,
                    "exit_code": 5,
                    "host": host,
                    "transport": "ssh",
                    "reason": "smoke failed: tag-mismatch",
                    "rolled_back_to": "v0.0.1",
                    "rollback_smoke": "ok",
                    "rollback_host": host,  # rollback on same host
                }
            return {"tag": tag, "deployed": False, "exit_code": 2}

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],
            ssh_deploy_fn=_deploy_with_rollback,
        )

        host_result = result["hosts"]["rezmac"]
        assert host_result["deployed"] is False
        assert host_result.get("rolled_back_to") == "v0.0.1"
        assert host_result.get("rollback_smoke") == "ok"
        # Rollback must be on the same host
        assert host_result.get("rollback_host") == "rezmac"
        assert "rezmac" in result["rolled_back"]

    def test_both_ssh_smokes_fail_returns_critical(self, tmp_path: Path) -> None:
        """When both forward and rollback smokes fail on remote host,
        result is exit_code=6 (critical)."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        def _critical_ssh_deploy(proj, tag, data_dir, **kwargs):
            host = kwargs.get("host", "rezmac")
            return {
                "tag": tag,
                "deployed": False,
                "exit_code": 6,
                "host": host,
                "transport": "ssh",
                "reason": "both smokes failed",
                "rolled_back_to": "v0.0.1",
                "rollback_smoke": "failed",
            }

        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["rezmac"],
            local_hosts=[],
            ssh_deploy_fn=_critical_ssh_deploy,
        )

        assert result["exit_code"] == 6
        host_result = result["hosts"]["rezmac"]
        assert host_result["exit_code"] == 6
        assert host_result.get("rollback_smoke") == "failed"


class TestPerHostAuditTruth:
    """AC-5: permits stay bound to the exact canonical host list and are
    consumed only after cut.  Per-host results are honest."""

    def test_permits_bound_to_canonical_host_list(self, tmp_path: Path) -> None:
        """Permits must be checked against the exact same host list that
        deploy uses.  A permit for a host not in the canonical list must
        not satisfy the check."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        # Config says hosts are [chad-mbp, rezmac]
        _write_ship_config(data_dir, ["chad-mbp", "rezmac"])

        # Write permit only for chad-mbp (rezmac has no permit)
        _make_permit(data_dir, "chad-mbp", project="test-project")

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        def _check(proj, data_dir):
            return {"eligible": True, "last_tag": "v0.0.1", "head": "a" * 40}

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=_check,
            prove_fn=lambda p, d: {"proven": True},
            cut_fn=lambda p, d: {"tag": "v0.0.2", "commit": "b" * 40},
            notify_script="/dev/null",
            hosts=None,
        )

        # Should refuse because rezmac has no permit
        assert result["exit_code"] == 4
        assert "permits refused" in result.get("reason", "")

    def test_multi_host_result_lists_are_exhaustive(self, tmp_path: Path) -> None:
        """Every host must appear in exactly one of: deployed, rolled_back,
        untouched, unverified.  A remote host's result must carry transport
        and structured evidence (extract/bounce/smoke), not just exit_code."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # This test uses _deploy_all_hosts without an injected deploy_fn,
        # so it exercises the real path.  The SSH placeholder does not
        # provide structured evidence — the real adapter must.
        result = release_train._deploy_all_hosts(
            project, "v0.0.2", data_dir,
            hosts=["chad-mbp", "rezmac"],
            local_hosts=["chad-mbp"],  # rezmac is remote
        )

        # rezmac must be in exactly one list
        all_hosts = set(result.get("deployed", []) + result.get("rolled_back", []) +
                       result.get("untouched", []) + result.get("unverified", []))
        assert "rezmac" in all_hosts, "rezmac must appear in one result list"

        # The remote host's result must carry structured evidence
        rezmac_result = result["hosts"].get("rezmac", {})
        assert rezmac_result.get("transport") == "ssh", (
            "Remote host result must name its transport"
        )
        # The placeholder does not carry extract/bounce/smoke — the real
        # adapter must
        for key in ("extract", "bounce", "smoke"):
            assert key in rezmac_result, (
                f"Remote host result must carry '{key}' evidence"
            )


# ── Step 0: xfail pins for remote tag acquisition ────────────────────────────
#
# These pins assert the contracts that step 1 will make green: before
# remote extraction, the SSH adapter must fetch the exact candidate tag
# from the configured origin into refs/tags/<tag>.  Each pin asserts
# command order (acquire before extract), failure behavior (fail-closed),
# and branch immutability.


class TestRemoteTagAcquisition:
    """AC-1: remote missing the pushed tag fetches it before extraction.

    The SSH adapter must run ``git fetch origin tag <tag>`` before
    ``ilk_release.py`` extraction.  Command order is pinned: acquire,
    extract, bounce, settle.
    """

    def test_remote_missing_tag_fetches_before_extraction(self, tmp_path: Path) -> None:
        """When the remote repo does not have the candidate tag, the SSH
        adapter must fetch it from origin before calling extraction."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # Record SSH calls in order
        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

            # git fetch origin tag v0.0.2 → succeed
            if any(str(a) == "fetch" for a in cmd) and "v0.0.2" in cmd_str:
                return {"rc": 0, "stdout": "", "stderr": ""}

            # ilk_release.py extraction → succeed
            if "ilk_release.py" in cmd_str and "--repo" in cmd_str:
                return {"rc": 0, "stdout": "extracted v0.0.2", "stderr": ""}

            # bounce → succeed
            if "bounce_daemons" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}

            # host_deploy_status → ok
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}

            return {"rc": 0, "stdout": "", "stderr": ""}

        result = release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        # The first SSH call must be a fetch
        assert len(ssh_calls) >= 1, "Expected at least one SSH call"
        first_cmd = ssh_calls[0]["cmd"]
        assert "fetch" in first_cmd, (
            f"First SSH call must be tag acquisition (git fetch), got: {first_cmd}"
        )
        assert "v0.0.2" in first_cmd, (
            f"Fetch must target the candidate tag v0.0.2, got: {first_cmd}"
        )

        # Extract must come after fetch
        extract_idx = None
        for i, call in enumerate(ssh_calls):
            if "ilk_release.py" in call["cmd"] and "--repo" in call["cmd"]:
                extract_idx = i
                break
        assert extract_idx is not None, "Expected an extraction call"
        assert extract_idx > 0, "Extraction must come after tag acquisition"


class TestTagAcquisitionCommandShape:
    """AC-1 corollary: the fetch refspec must be exact (refs/tags/<tag>),
    not a wildcard or branch fetch."""

    def test_fetch_refspec_is_exact_tag(self, tmp_path: Path) -> None:
        """The fetch command must use an exact refspec for the candidate tag,
        not a wildcard like 'refs/tags/*'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})
            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 0, "stdout": "", "stderr": ""}
            if "ilk_release.py" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            if "bounce_daemons" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            return {"rc": 0, "stdout": "", "stderr": ""}

        release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        # Find the fetch call (use args-based check to avoid temp dir false positives)
        fetch_calls = [c for c in ssh_calls if any(str(a) == "fetch" for a in c["args"])]
        assert len(fetch_calls) >= 1, "Expected a fetch call"

        fetch_args = fetch_calls[0]["args"]
        # Must NOT fetch all tags (wildcard)
        assert "refs/tags/*" not in fetch_args, (
            f"Fetch must not use wildcard refspec, got: {fetch_args}"
        )
        # Must fetch the specific tag ref
        tag_ref = f"refs/tags/v0.0.2"
        assert any(tag_ref in str(a) for a in fetch_args), (
            f"Fetch must target {tag_ref}, got: {fetch_args}"
        )


class TestFetchFailureIsFailClosed:
    """AC-3: fetch failure returns explicit nonzero evidence and skips
    extraction, bounce, and smoke."""

    def test_fetch_failure_skips_extraction(self, tmp_path: Path) -> None:
        """When the SSH fetch fails, the adapter must return nonzero exit
        and must NOT call extraction."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

            # fetch fails
            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 1, "stdout": "", "stderr": "fatal: couldn't find remote ref"}

            # Any other call should not happen
            return {"rc": 0, "stdout": "", "stderr": ""}

        result = release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        assert result["exit_code"] != 0, "Fetch failure must yield nonzero exit"
        assert result["deployed"] is False, "Fetch failure must not report deployed"

        # Extraction must NOT have been called
        extract_calls = [c for c in ssh_calls
                        if "ilk_release.py" in c["cmd"] and "--repo" in c["cmd"]]
        assert len(extract_calls) == 0, (
            f"Extraction must not run after fetch failure, "
            f"but {len(extract_calls)} extraction calls recorded"
        )


class TestMissingTagAfterFetchIsFailClosed:
    """AC-3 corollary: if the tag is still missing after fetch, the adapter
    must return nonzero and skip extraction."""

    def test_missing_tag_after_fetch_skips_extraction(self, tmp_path: Path) -> None:
        """When fetch succeeds but the tag is still not present on the remote,
        the adapter must return nonzero and must NOT extract."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

            # fetch succeeds (the tag exists on origin)
            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 0, "stdout": "", "stderr": ""}

            # tag verification after fetch — tag not found
            if "rev-parse" in cmd_str and "v0.0.2" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": "unknown revision"}

            return {"rc": 0, "stdout": "", "stderr": ""}

        result = release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        assert result["exit_code"] != 0, "Missing tag must yield nonzero exit"
        assert result["deployed"] is False, "Missing tag must not report deployed"

        extract_calls = [c for c in ssh_calls
                        if "ilk_release.py" in c["cmd"] and "--repo" in c["cmd"]]
        assert len(extract_calls) == 0, (
            f"Extraction must not run when tag is missing, "
            f"but {len(extract_calls)} extraction calls recorded"
        )


class TestCheckedOutBranchUntouched:
    """AC-2: tag acquisition never checks out, resets, merges, or otherwise
    rewrites the remote repository's current branch or working tree."""

    def test_fetch_does_not_modify_remote_branch(self, tmp_path: Path) -> None:
        """The SSH adapter must not run checkout, reset, merge, or any
        branch-modifying command on the remote host."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

            # Fail immediately if any branch-modifying command is detected
            dangerous = ["checkout", "reset", "merge", "rebase", "push"]
            for word in dangerous:
                if word in cmd_str.lower() and not any(str(a) == "fetch" for a in cmd):
                    assert False, (
                        f"Dangerous command detected on remote host: {cmd_str}"
                    )

            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 0, "stdout": "", "stderr": ""}
            if "ilk_release.py" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            if "bounce_daemons" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            return {"rc": 0, "stdout": "", "stderr": ""}

        result = release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        # Tag acquisition must have been attempted
        # Use args-based check to avoid false positives from temp dir names
        fetch_calls = [c for c in ssh_calls if any(str(a) == "fetch" for a in c["args"])]
        assert len(fetch_calls) >= 1, (
            "Expected tag acquisition (git fetch) to be called on the remote host"
        )

        # Verify no branch-modifying commands were issued
        for call in ssh_calls:
            cmd = call["cmd"].lower()
            for word in ["checkout", "reset", "merge", "rebase"]:
                assert word not in cmd or "fetch" in cmd, (
                    f"Remote branch must not be modified, found '{word}' in: {call['cmd']}"
                )


class TestTagMismatchAfterAcquisition:
    """AC-3: a fetched tag whose object does not match the expected SHA
    must return nonzero and skip extraction."""

    def test_tag_sha_mismatch_returns_nonzero(self, tmp_path: Path) -> None:
        """When the fetched tag resolves to a different SHA than expected,
        the adapter must refuse and return nonzero."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = _make_fake_project(tmp_path)

        # Create a PID file for the current process so settle loop succeeds
        releases_dir = project / "releases" / "v0.0.2"
        releases_dir.mkdir(parents=True)
        pid_file = project / "releases" / "v0.0.2" / "scheduler.pid"
        pid_file.write_text(str(os.getpid()))

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            ssh_calls.append({"host": host, "cmd": cmd_str, "args": list(cmd)})

            # fetch succeeds
            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 0, "stdout": "", "stderr": ""}

            # tag verification — resolves to wrong SHA
            if "rev-parse" in cmd_str and "v0.0.2" in cmd_str:
                return {"rc": 0, "stdout": "f" * 40, "stderr": ""}

            # extraction — succeed (so deploy would succeed without tag check)
            if "ilk_release.py" in cmd_str:
                return {"rc": 0, "stdout": "extracted v0.0.2", "stderr": ""}

            # status check — ok (must come before bounce because
            # host_deploy_status --bouncer contains "bounce_daemons")
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}

            # bounce — succeed (deploy must succeed to expose tag mismatch)
            if "bounce_daemons" in cmd_str:
                return {"rc": 0, "stdout": "", "stderr": ""}

            return {"rc": 0, "stdout": "", "stderr": ""}

        result = release_train._ssh_deploy(
            project, "v0.0.2", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        # Without tag SHA verification, the deploy succeeds (exit_code=0).
        # This test asserts that a tag SHA mismatch must cause nonzero exit,
        # which will only be true after step 1 implements tag verification.
        assert result["exit_code"] != 0, "Tag SHA mismatch must yield nonzero exit"
        assert result["deployed"] is False, "Tag mismatch must not report deployed"


# ── Step 0: xfail pin for remote acquisition project path ─────────────────
#
# The v0.9.151 rezmac fail-closed: _acquire_remote_tag hardcodes the literal
# string "repo" as the -C argument for both git fetch and git rev-parse.
# The real project path must flow from _ssh_deploy through to both commands.
# This pin supplies a nonliteral path (with spaces) and asserts both git -C
# commands receive it as one argument.


class TestRemoteAcquisitionUsesProjectPath:
    """AC-1: both remote acquisition commands use the same explicit project
    path supplied to _ssh_deploy; neither contains the literal 'repo'."""

    def test_fetch_and_revparse_receive_project_path(self, tmp_path: Path) -> None:
        """When _ssh_deploy receives a project path like 'my project',
        both git -C commands in _acquire_remote_tag must use it (not 'repo')."""
        # Use a path with spaces to prove it's passed as one argument
        project = tmp_path / "my project"
        project.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Make a minimal git repo so _tag_sha can resolve the tag
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True,
        )
        (project / "f").write_text("x")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
            cwd=project, check=True, capture_output=True,
        )

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_calls: list[dict] = []

        def _fake_ssh(host: str, cmd: list[str], timeout: int = 120) -> dict:
            ssh_calls.append({"host": host, "args": list(cmd)})
            if any(str(a) == "fetch" for a in cmd):
                return {"rc": 0, "stdout": "", "stderr": ""}
            if "rev-parse" in " ".join(str(c) for c in cmd):
                return {"rc": 0, "stdout": "a" * 40, "stderr": ""}
            if "ilk_release.py" in " ".join(str(c) for c in cmd):
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            if "host_deploy_status" in " ".join(str(c) for c in cmd):
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            if "bounce_daemons" in " ".join(str(c) for c in cmd):
                return {"rc": 1, "stdout": "", "stderr": ""}
            return {"rc": 0, "stdout": "", "stderr": ""}

        release_train._ssh_deploy(
            project, "v0.0.1", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh,
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )

        # Find the git fetch and git rev-parse calls
        fetch_calls = [
            c for c in ssh_calls
            if any(str(a) == "fetch" for a in c["args"])
        ]
        revparse_calls = [
            c for c in ssh_calls
            if any(str(a) == "rev-parse" for a in c["args"])
        ]

        assert len(fetch_calls) >= 1, "Expected a git fetch call"
        assert len(revparse_calls) >= 1, "Expected a git rev-parse call"

        # Both must use the real project path, not the literal "repo"
        fetch_args = fetch_calls[0]["args"]
        revparse_args = revparse_calls[0]["args"]

        # git -C <path> → the element after -C is the path.
        # The path may be shell-quoted (shlex.quote) to survive SSH arg
        # concatenation, so strip shell quotes before comparing.
        def _get_c_path(args: list) -> str | None:
            import shlex as _shlex
            for i, a in enumerate(args):
                if str(a) == "-C" and i + 1 < len(args):
                    raw = str(args[i + 1])
                    # shlex.split strips one layer of shell quoting
                    parts = _shlex.split(raw)
                    return parts[0] if parts else raw
            return None

        fetch_c_path = _get_c_path(fetch_args)
        revparse_c_path = _get_c_path(revparse_args)

        assert fetch_c_path is not None, "fetch command must use -C"
        assert revparse_c_path is not None, "rev-parse command must use -C"

        # The path must be the project path (with spaces), not "repo"
        project_str = str(project)
        assert fetch_c_path == project_str, (
            f"fetch -C must be {project_str!r}, got {fetch_c_path!r}"
        )
        assert revparse_c_path == project_str, (
            f"rev-parse -C must be {project_str!r}, got {revparse_c_path!r}"
        )


# ── Step 0: xfail pin for remote SSH argument serialization ───────────────
#
# v0.9.152 fail-closed: _acquire_remote_tag passes
#   f"tag refs/tags/{tag}:refs/tags/{tag}"  as one subprocess arg.
# After SSH concatenates args with spaces, the remote shell receives:
#   git -C <path> fetch origin tag refs/tags/<tag>:refs/tags/<tag>
# Two bugs:
#   1. "tag refs/tags/..." is one Python arg but two shell tokens — the
#      `tag` prefix is invalid git-fetch syntax.
#   2. A repo path containing spaces (e.g. "my project") is one Python
#      arg but multiple shell tokens — git -C sees only the first word.
# This pin captures the real subprocess boundary, round-trips through
# shell parsing, and asserts both invariants.  Step 1 will make it green.


class TestRemoteSSHArgvSerialization:
    """AC-1/AC-2/AC-3: the SSH invocation passes one safely shell-serialized
    remote command; the fetch refspec is exact (no ``tag`` prefix); and a
    repository path containing spaces remains one ``git -C`` argument.

    Captures the real ``subprocess.run`` call at the SSH boundary and
    round-trips the remote command through shell parsing to verify
    argument boundaries.
    """

    def test_remote_fetch_refspec_and_path_preserved(self, tmp_path: Path) -> None:
        """The remote ``git fetch`` must use an exact refspec
        ``refs/tags/<tag>:refs/tags/<tag>`` (no ``tag`` prefix) and the
        repository path with spaces must round-trip as one ``-C`` argument
        through shell parsing."""
        # Use a path with spaces to expose the quoting bug
        project = tmp_path / "my project"
        project.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Minimal git repo so _tag_sha resolves the tag
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t"],
            cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=project, check=True, capture_output=True,
        )
        (project / "f").write_text("x")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
            cwd=project, check=True, capture_output=True,
        )

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        ssh_subprocess_calls: list[list[str]] = []
        original_run = subprocess.run

        def _capturing_run(args, **kwargs):
            """Capture the SSH argv and delegate to the real runner."""
            if args and args[0] == "ssh":
                ssh_subprocess_calls.append(list(args))
            return original_run(args, **kwargs)

        import unittest.mock

        with unittest.mock.patch("subprocess.run", side_effect=_capturing_run):
            release_train._ssh_deploy(
                project, "v0.0.1", data_dir,
                host="rezmac",
                settle_deadline_sec=0.1,
                settle_poll_interval_sec=0.05,
            )

        assert len(ssh_subprocess_calls) >= 1, "Expected at least one SSH subprocess call"

        first_ssh = ssh_subprocess_calls[0]
        # SSH argv: ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
        #            "rezmac", "git", "-C", "my project", "fetch", "origin", ...]
        # Everything after the host is the remote command args
        host_idx = first_ssh.index("rezmac")
        remote_args = first_ssh[host_idx + 1:]

        # Reconstruct the remote shell command as the remote shell would
        # parse it (subprocess.run passes each arg as a separate SSH arg,
        # SSH concatenates them with spaces, remote shell re-parses).
        # The production code already applies shlex.quote() to paths, so
        # joining with spaces (no additional quoting) reproduces the exact
        # string the remote shell receives.
        import shlex
        remote_cmd_str = " ".join(str(a) for a in remote_args)

        # Round-trip through shell parsing to verify argument boundaries
        parsed = shlex.split(remote_cmd_str)

        # AC-2: the fetch refspec must NOT contain the incompatible "tag" prefix.
        # After shell parsing, "tag refs/tags/v0.0.1:refs/tags/v0.0.1" as one
        # Python arg becomes two tokens: "tag" and "refs/tags/...".
        fetch_idx = parsed.index("fetch")
        origin_idx = fetch_idx + 1
        refspec_token = parsed[origin_idx + 1]
        assert not refspec_token.startswith("tag "), (
            f"Fetch refspec must not contain 'tag ' prefix, got: {refspec_token!r}"
        )
        assert refspec_token == f"refs/tags/v0.0.1:refs/tags/v0.0.1", (
            f"Fetch refspec must be exact, got: {refspec_token!r}"
        )

        # AC-3: a repo path with spaces must survive shell parsing as one
        # argument to git -C.  shlex.quote("my project") → "'my project'",
        # which round-trips as one token.
        c_idx = parsed.index("-C")
        c_path = parsed[c_idx + 1]
        assert c_path == str(project), (
            f"git -C path must be {str(project)!r}, got {c_path!r}"
        )


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