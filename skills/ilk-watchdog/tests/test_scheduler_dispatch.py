"""Scheduler dispatch test: repo_path resolution → dispatchable / skip-unresolved.

Tests the cross-platform Python core that BOTH scheduler.ps1 and
scheduler.sh consume. Hermetic — no live scheduler, no servers.

Part of sub-plan: scheduler-dispatch-verification (steps 0–1).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"


# ── helpers ─────────────────────────────────────────────────────────


def _write_project_data(
    tmp_path: Path,
    key: str,
    *,
    master_status: str = "active",
    subplan_status: str = "pending",
    last_launch_path: str | None = None,
) -> Path:
    """Scaffold a temp project data dir under ``tmp_path/projects/<key>/``.

    Creates a minimal MASTER with one sub-plan reference and optionally
    a ``runtime/launcher/last-launch.json`` carrying *last_launch_path*.
    """
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Minimal MASTER-*.md
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-06-08T00:00:00+08:00\n"
        f"status: {master_status}\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | pending |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

    # Minimal sub-plan
    subplan = (
        "---\n"
        "plan: 2026-06-08-work\n"
        f"status: {subplan_status}\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-08\n"
        "---\n"
        "\n"
        "# 2026-06-08-work\n"
    )
    (plans_dir / "2026-06-08-work.md").write_text(subplan, encoding="utf-8")

    # Optional last-launch.json
    if last_launch_path is not None:
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        data = {"project_path": last_launch_path}
        (launcher_dir / "last-launch.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    return project_dir


def _read_scan_projects(tmp_home: Path) -> list[dict]:
    """Import and call ``scheduler_scan.scan_projects`` with patched data root."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "plan_slug"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    # Ensure scheduler_scan resolves _SKILL_ROOT from the working copy,
    # not from ILK_SKILL_HOME (which may point at a stale release extract
    # or claude-worker install in the ambient environment).
    os.environ["ILK_SKILL_HOME"] = str(REPO_ROOT / "skills")
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


def _read_resolve_repo_path(
    project_dir: Path,
    key: str,
    *,
    skill_root: Path | None = None,
) -> str | None:
    """Import and call ``scheduler_scan.resolve_repo_path``.

    If *skill_root* is given, patches ``scheduler_scan._SKILL_ROOT`` so
    the registry fallback reads from a temp location.  Also isolates
    ``ILK_DATA_HOME`` so ``ilk_data_root()`` doesn't find the real
    data-home registry.
    """
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "plan_slug"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    # Ensure scheduler_scan resolves _SKILL_ROOT from the working copy,
    # not from ILK_SKILL_HOME (which may point at a stale release extract
    # or claude-worker install in the ambient environment).
    os.environ["ILK_SKILL_HOME"] = str(REPO_ROOT / "skills")
    # Isolate ILK_DATA_HOME so ilk_data_root() doesn't find the real
    # registry — the test's temp skill_root is the intended source.
    if skill_root is not None:
        os.environ["ILK_DATA_HOME"] = str(skill_root / "fake-data-home")
    import scheduler_scan
    if skill_root is not None:
        scheduler_scan._SKILL_ROOT = skill_root
    return scheduler_scan.resolve_repo_path(project_dir, key)


# ── tests ───────────────────────────────────────────────────────────


class TestSchedulerDispatch:
    """repo_path resolution → dispatchable / skip-unresolved."""

    def test_dispatchable_with_last_launch(self, tmp_path):
        """AC-1: active master + non-shipped sub-plan + valid last-launch.json
        → dispatchable (non-null repo_path)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )

        # Via scan_projects (the full scheduler path)
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] == "/some/real/repo"

        # Via resolve_repo_path directly
        repo_path = _read_resolve_repo_path(project_dir, "test-proj")
        assert repo_path == "/some/real/repo"

    def test_skip_unresolved_without_last_launch(self, tmp_path):
        """AC-2: active master + non-shipped sub-plan + no last-launch.json
        + not in registry → skip-unresolved (null repo_path)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
        )

        # Via scan_projects
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] is None

        # Via resolve_repo_path directly
        repo_path = _read_resolve_repo_path(project_dir, "test-proj")
        assert repo_path is None

    def test_dispatchable_queued_master(self, tmp_path):
        """AC-1 variant: queued master + valid last-launch.json → dispatchable."""
        _write_project_data(
            tmp_path, "test-proj",
            master_status="queued",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] == "/some/real/repo"

    def test_register_flips_skip_unresolved_to_dispatchable(self, tmp_path):
        """AC-3: register_project into a temp registry flips repo_path
        from None to the registered path."""
        from register_project import register_project

        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
            # No last-launch.json → skip-unresolved initially
        )

        # Create a temp skill root with an empty registry
        skill_root = tmp_path / "skill-root"
        registry = skill_root / "ilk-launcher" / "projects.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text('{"projects": []}', encoding="utf-8")

        # Verify initial state: skip-unresolved
        repo_path = _read_resolve_repo_path(
            project_dir, "test-proj", skill_root=skill_root
        )
        assert repo_path is None

        # Register the project into the temp registry
        # Use a real path that exists on disk so register_project accepts it
        real_repo = tmp_path / "real-repo"
        real_repo.mkdir()
        result = register_project(real_repo, projects_json=registry)
        assert result["added"] is True

        # Now resolve_repo_path should find it via the registry
        # But we need to use the project_key of the registered path
        sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
        for mod_name in ("ilk_paths",):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        from ilk_paths import project_key
        registered_key = project_key(real_repo)

        # Create a project dir matching the registered key
        registered_project = _write_project_data(
            tmp_path, registered_key,
            master_status="active",
            subplan_status="pending",
        )

        repo_path = _read_resolve_repo_path(
            registered_project, registered_key, skill_root=skill_root
        )
        assert repo_path == str(real_repo)

    def test_draft_master_excluded(self, tmp_path):
        """AC-4a: draft master is NOT returned by scan_projects even with
        a resolvable path."""
        _write_project_data(
            tmp_path, "test-proj",
            master_status="draft",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 0, "draft master must be excluded from dispatch"

    def test_supervised_only_master_not_excluded(self, tmp_path):
        """supervised_only is tolerated-and-ignored: master IS returned by
        scan_projects with a resolvable path (retired 2026-09-20)."""
        project_dir = tmp_path / "projects" / "test-proj"
        plans_dir = project_dir / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Write a supervised_only MASTER
        master = (
            "---\n"
            "title: MASTER-test\n"
            "created: 2026-06-08T00:00:00+08:00\n"
            "status: active\n"
            "priority: 0\n"
            "pause_after_ship: false\n"
            "supervised_only: true\n"
            "---\n"
            "\n"
            "# MASTER-test\n"
            "\n"
            "## Sub-plan registry\n"
            "\n"
            "| # | Sub-plan | Status |\n"
            "|---|---|---|\n"
            "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | pending |\n"
        )
        (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

        # Write a minimal sub-plan
        subplan = (
            "---\n"
            "plan: 2026-06-08-work\n"
            "status: pending\n"
            "current_step: 0\n"
            "estimated_steps: 3\n"
            "last_updated: 2026-06-08\n"
            "---\n"
            "\n"
            "# 2026-06-08-work\n"
        )
        (plans_dir / "2026-06-08-work.md").write_text(subplan, encoding="utf-8")

        # Write last-launch.json so repo_path would resolve
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        (launcher_dir / "last-launch.json").write_text(
            json.dumps({"project_path": "/some/real/repo"}), encoding="utf-8"
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1, "supervised_only master must be dispatched — flag is tolerated"


# ── permit check tests ───────────────────────────────────────────────


def _write_permit(
    data_dir: Path,
    host: str,
    *,
    project: str = "test-proj",
    base_tag: str = "v0.0.1",
    candidate_head: str = "b" * 40,
    consumed: bool = False,
    revoked: bool = False,
    expired: bool = False,
) -> Path:
    """Write a permit file for a host."""
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


def _write_ship_config(data_dir: Path, hosts: list[str]) -> None:
    """Write a ship-config.json with the given hosts."""
    config_dir = data_dir / "runtime"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {"ship": {"hosts": hosts}}
    (config_dir / "ship-config.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )


def _read_check_permits(data_dir: Path, project: Path) -> dict:
    """Import and call check_permits_for_dispatch."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("release_train_dispatch", "ilk_paths"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    from release_train_dispatch import check_permits_for_dispatch
    return check_permits_for_dispatch(data_dir, project)


def _project_key_for(project: Path) -> str:
    """Compute the project_key for a repo path."""
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("ilk_paths",):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    from ilk_paths import project_key
    return project_key(project)


class TestPermitCheck:
    """Permit pre-flight check before release train dispatch."""

    def test_no_hosts_configured_permits_ok(self, tmp_path):
        """No hosts configured → permits ok (no permits needed)."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"
        result = _read_check_permits(data_dir, tmp_path / "fake-repo")
        assert result["ok"] is True

    def test_all_permits_valid_permits_ok(self, tmp_path):
        """All hosts have valid permits → ok."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"

        # Create a real git repo to resolve base_tag and candidate_head
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
        (repo / "f").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"], cwd=repo, check=True, capture_output=True)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        pk = _project_key_for(repo)
        _write_ship_config(data_dir, ["h1", "h2"])
        _write_permit(data_dir, "h1", project=pk, base_tag="v0.0.1", candidate_head=head)
        _write_permit(data_dir, "h2", project=pk, base_tag="v0.0.1", candidate_head=head)

        result = _read_check_permits(data_dir, repo)
        assert result["ok"] is True

    def test_missing_permit_refuses(self, tmp_path):
        """Missing permit for one host → refused."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        pk = _project_key_for(repo)

        _write_ship_config(data_dir, ["h1", "h2"])
        _write_permit(data_dir, "h1", project=pk)

        result = _read_check_permits(data_dir, repo)
        assert result["ok"] is False
        assert "missing permit" in result["reason"]
        assert "h2" in result["reason"]

    def test_consumed_permit_refuses(self, tmp_path):
        """Consumed permit → refused."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        pk = _project_key_for(repo)

        _write_ship_config(data_dir, ["h1"])
        _write_permit(data_dir, "h1", project=pk, consumed=True)

        result = _read_check_permits(data_dir, repo)
        assert result["ok"] is False
        assert "consumed" in result["reason"]

    def test_revoked_permit_refuses(self, tmp_path):
        """Revoked permit → refused."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        pk = _project_key_for(repo)

        _write_ship_config(data_dir, ["h1"])
        _write_permit(data_dir, "h1", project=pk, revoked=True)

        result = _read_check_permits(data_dir, repo)
        assert result["ok"] is False
        assert "revoked" in result["reason"]

    def test_stale_permit_refuses(self, tmp_path):
        """Expired permit → refused."""
        project_dir = _write_project_data(tmp_path, "test-proj")
        data_dir = tmp_path / "projects" / "test-proj"
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        pk = _project_key_for(repo)

        _write_ship_config(data_dir, ["h1"])
        _write_permit(data_dir, "h1", project=pk, expired=True)

        result = _read_check_permits(data_dir, repo)
        assert result["ok"] is False
        assert "stale" in result["reason"]


class TestRapidTerminal:
    """Scan returns projects correctly when last-exit.json exists (rapid-terminal guard)."""

    def test_scan_with_terminal_sentinel(self, tmp_path):
        """A project with a terminal last-exit.json is still returned by scan
        (the scheduler decides whether to back off, not the scanner)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )
        # Write a terminal last-exit.json
        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "last-exit.json").write_text(
            json.dumps({
                "state": "local_checks_failed",
                "run_id": "test-run-001",
                "pid": 12345,
                "iterations": 1,
            }),
            encoding="utf-8",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] == "/some/real/repo"

    def test_scan_with_running_sentinel(self, tmp_path):
        """A project with state=running in last-exit.json is still returned
        by scan (the scheduler's Test-RunningPid handles busy detection)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )
        runtime_dir = project_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "last-exit.json").write_text(
            json.dumps({
                "state": "running",
                "run_id": "test-run-002",
                "pid": 99999,
                "iterations": 0,
            }),
            encoding="utf-8",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
