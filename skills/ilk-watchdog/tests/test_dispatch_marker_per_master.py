"""Per-master dispatch marker tests.

The idempotency guard in ``scheduler_scan._dispatch_verification_on_drain``
must compare the marker's stored ``master`` to the master being considered,
not just check whether the marker file exists.

Decisive test: a marker naming master A must NOT suppress dispatch for
master B (AC-per-master-1).
Idempotency: the same master twice dispatches once (AC-1 preserved).

Red-first — these tests are expected to FAIL against the current guard
(``marker_path.exists()``) and PASS once the guard reads the stored field.

Hermetic — no live scheduler, no launcher, no servers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"


# ── helpers ─────────────────────────────────────────────────────────


def _import_scheduler_scan():
    """Import ``scheduler_scan`` with a clean module state."""
    for mod_name in (
        "scheduler_scan", "ilk_paths", "plan_status", "blacklist_status",
    ):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    import scheduler_scan
    return scheduler_scan


def _write_marker(project_dir: Path, master_name: str) -> None:
    """Write an idempotency marker naming *master_name*."""
    marker_path = (
        project_dir / "runtime" / "verification-dispatched.json"
    )
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "master": master_name,
        "dispatched_at": "2026-09-08T12:00:00",
        "project_key": project_dir.name,
    }
    tmp = marker_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os_replace = __import__("os").replace
    os_replace(tmp, marker_path)


def _write_master(
    plans_dir: Path,
    name: str,
    *,
    status: str = "active",
    subplans: list[str] | None = None,
) -> None:
    """Write a minimal MASTER-*.md (from test_dispatch_on_drain pattern)."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        "created: 2026-07-29T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines += [
            "## Sub-plan registry",
            "",
            "| # | Sub-plan | Status |",
            "|---|---|---|",
        ]
        for sp in subplans:
            lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
        lines.append("")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _setup_project(
    tmp_path: Path,
    key: str = "test-proj",
    *,
    master_name: str = "MASTER-test.md",
    subplan_name: str = "2026-07-29-work.md",
) -> Path:
    """Scaffold a project data dir with master + subplan + launcher."""
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    _write_master(
        plans_dir,
        master_name,
        status="active",
        subplans=[subplan_name],
    )
    # Minimal sub-plan.
    subplan_path = plans_dir / subplan_name
    subplan_path.write_text(
        "---\n"
        f"plan: {subplan_name.replace('.md', '')}\n"
        "status: shipped\n"
        "current_step: 3\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-07-29\n"
        "---\n"
        f"\n# {subplan_name}\n",
        encoding="utf-8",
    )

    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": "/some/repo"}),
        encoding="utf-8",
    )

    return project_dir


def _call_dispatch(
    project_dir: Path,
    master_path: Path,
    plans_dir: Path,
    *,
    launch_fn=None,
) -> None:
    """Call ``_dispatch_verification_on_drain`` directly."""
    ss = _import_scheduler_scan()
    ss._dispatch_verification_on_drain(
        project_dir, master_path, plans_dir,
        _launch_fn=launch_fn,
    )


# ── tests ───────────────────────────────────────────────────────────


class TestDispatchMarkerPerMaster:
    """A marker for one master must not suppress another."""

    def test_different_master_still_dispatches(self, tmp_path):
        """A marker naming master-A must NOT suppress dispatch for master-B.

        This is the decisive test for C3.  The current guard
        (``marker_path.exists()``) makes this FAIL — it returns early
        regardless of which master the marker names.
        """
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path,
            master_name="MASTER-new.md",
        )
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-new.md"

        # Pre-existing marker names a DIFFERENT master.
        _write_marker(project_dir, "MASTER-2026-09-03-stale.md")

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 1, (
            "marker naming MASTER-2026-09-03-stale.md must NOT suppress "
            "dispatch for MASTER-new.md"
        )

    def test_same_master_suppresses(self, tmp_path):
        """A marker naming the SAME master suppresses dispatch (idempotency).

        AC-1 from the original design must be preserved: two consecutive
        dispatch calls for the same master produce exactly one dispatch.
        """
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path,
            master_name="MASTER-test.md",
        )
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # Pre-existing marker names the SAME master.
        _write_marker(project_dir, "MASTER-test.md")

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 0, (
            "marker naming the same master must suppress dispatch"
        )

    def test_no_marker_dispatches(self, tmp_path):
        """No marker → dispatch fires (baseline sanity)."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path)
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 1, "no marker → must dispatch"

    def test_malformed_marker_dispatches(self, tmp_path):
        """A marker that cannot be read or has no ``master`` field
        must NOT silently suppress dispatch.

        Fail closed: treat as "unknown" → dispatch (the caller is the
        scheduler, which will write a fresh marker on success).
        """
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path)
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # Write malformed JSON (no "master" field).
        marker_path = (
            project_dir / "runtime" / "verification-dispatched.json"
        )
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"dispatched_at": "2026-09-08T12:00:00"}),
            encoding="utf-8",
        )

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 1, (
            "malformed marker (no 'master' field) must NOT suppress dispatch"
        )

    def test_marker_with_corrupt_json_dispatches(self, tmp_path):
        """A marker with unparseable JSON must NOT suppress dispatch.

        Same fail-closed policy: unreadable → unknown → dispatch.
        """
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path)
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        marker_path = (
            project_dir / "runtime" / "verification-dispatched.json"
        )
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("NOT JSON {{{", encoding="utf-8")

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 1, (
            "corrupt marker JSON must NOT suppress dispatch"
        )


class TestDispatchMarkerLifecycle:
    """Marker lifecycle: written on dispatch, keyed per-master."""

    def test_dispatch_writes_master_name_in_marker(self, tmp_path):
        """After dispatch, the marker names the master that was dispatched."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path,
            master_name="MASTER-alpha.md",
        )
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-alpha.md"

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )
        assert len(dispatches) == 1

        marker_path = (
            project_dir / "runtime" / "verification-dispatched.json"
        )
        assert marker_path.exists()
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert data["master"] == "MASTER-alpha.md", (
            "marker must name the dispatched master"
        )

    def test_successive_masters_overwrite_marker(self, tmp_path):
        """Dispatching master B after master A updates the marker to B.

        This pins the lifecycle: a marker is not permanent — it is
        superseded when a different master dispatches.  The old marker
        for A was already inert (per-master guard), but it must also
        not accumulate stale state on disk.
        """
        dispatches: list[list[str]] = []

        def capture_launch(cmd):
            dispatches.append(cmd)

        # First: dispatch for master A.
        project_dir = _setup_project(
            tmp_path,
            master_name="MASTER-A.md",
        )
        plans_dir = project_dir / "plans"
        master_a = plans_dir / "MASTER-A.md"

        _call_dispatch(
            project_dir, master_a, plans_dir,
            launch_fn=capture_launch,
        )
        assert len(dispatches) == 1

        marker_path = (
            project_dir / "runtime" / "verification-dispatched.json"
        )
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert data["master"] == "MASTER-A.md"

        # Now: dispatch for master B — writes a fresh marker.
        _write_master(
            plans_dir,
            "MASTER-B.md",
            status="active",
            subplans=["2026-07-29-next.md"],
        )
        master_b = plans_dir / "MASTER-B.md"

        _call_dispatch(
            project_dir, master_b, plans_dir,
            launch_fn=capture_launch,
        )
        assert len(dispatches) == 2, (
            "master B must dispatch (marker names A)"
        )

        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert data["master"] == "MASTER-B.md", (
            "marker must be overwritten with the newly dispatched master"
        )

    def test_stale_marker_for_old_master_does_not_block_new(self, tmp_path):
        """A pre-existing marker naming an old master is inert for a new one.

        This is the migration test: three stale markers exist across
        projects (kira-cloudflare, ilk-skills, gh-resolve), each naming
        a master that shipped weeks or months ago.  Under the per-master
        guard, they must NOT suppress dispatch for any current master.
        No explicit migration is needed — the read-side fix handles it.
        """
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path,
            master_name="MASTER-2026-09-08b-state-ownership-execution-plan.md",
        )
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-2026-09-08b-state-ownership-execution-plan.md"

        # Pre-existing marker names the stale master from 2026-09-03.
        _write_marker(
            project_dir,
            "MASTER-2026-09-03-gate-identity-and-driver-bookkeeping-execution-plan.md",
        )

        def capture_launch(cmd):
            dispatches.append(cmd)

        _call_dispatch(
            project_dir, master_path, plans_dir,
            launch_fn=capture_launch,
        )

        assert len(dispatches) == 1, (
            "stale marker naming MASTER-2026-09-03-… must NOT suppress "
            "dispatch for MASTER-2026-09-08b-…"
        )