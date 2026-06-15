"""Test: frontmatter parser strips inline # comments from scalar values.

Verifies AC-1 through AC-4 of the 2026-06-15-frontmatter-strip-inline-comments
sub-plan:
  - parse_frontmatter strips trailing ``# comment`` from unquoted scalars.
  - Quoted values containing ``#`` are preserved.
  - Values with no space before ``#`` are preserved.
  - normalize_master_status tolerates a trailing comment (defensive layer).
  - scheduler_scan INCLUDES a master whose status has an inline comment.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"

# Ensure plan_status is importable for unit tests.
if str(SCRIPTS_ILK_LOOP) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))

# Clear cached modules so edited plan_status.py is picked up.
for _mod in ("plan_status", "scheduler_scan", "ilk_paths"):
    sys.modules.pop(_mod, None)


def _import_plan_status():
    """Import (or reimport) plan_status from the scripts dir."""
    for mod_name in ("plan_status",):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import plan_status
    return plan_status


# ── helpers ─────────────────────────────────────────────────────────

def _write_master(plans_dir: Path, name: str, *, status: str,
                  subplans: list[str]) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body_lines = [
        "---",
        f"title: {name}",
        "created: 2026-06-15T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
        "## Sub-plan registry",
        "",
        "| # | Sub-plan | Status |",
        "|---|---|---|",
    ]
    for sp in subplans:
        body_lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
    body_lines.append("")
    (plans_dir / name).write_text("\n".join(body_lines), encoding="utf-8")


def _write_subplan(plans_dir: Path, name: str) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"plan: {name.replace('.md', '')}\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-15\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _read_scan_projects(tmp_home: Path) -> list[dict]:
    """Import and call ``scheduler_scan.scan_projects`` with patched data root."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


# ── unit tests: parse_frontmatter ───────────────────────────────────

class TestParseFrontmatterInlineComments:
    """parse_frontmatter strips inline # comments from unquoted scalars."""

    def test_unquoted_comment_stripped(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\nstatus: queued   # note\n---\n")
        assert fm["status"] == "queued"

    def test_quoted_hash_preserved(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter('---\nname: "a#b"\n---\n')
        assert fm["name"] == "a#b"

    def test_no_space_before_hash_preserved(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\nurl: http://host#frag\n---\n")
        assert fm["url"] == "http://host#frag"

    def test_single_quoted_hash_preserved(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\nname: 'x#y'\n---\n")
        assert fm["name"] == "x#y"

    def test_no_comment_value_unchanged(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\nstatus: active\n---\n")
        assert fm["status"] == "active"

    def test_comment_with_multiple_spaces(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\nstatus: queued     # many spaces\n---\n")
        assert fm["status"] == "queued"

    def test_bare_hash_not_stripped(self):
        ps = _import_plan_status()
        fm = ps.parse_frontmatter("---\ntag: #hashtag\n---\n")
        assert fm["tag"] == "#hashtag"


# ── unit tests: normalize_master_status ─────────────────────────────

class TestNormalizeMasterStatusInlineComments:
    """normalize_master_status tolerates trailing inline comments."""

    def test_comment_stripped_before_normalize(self):
        ps = _import_plan_status()
        assert ps.normalize_master_status("queued   # note") == "queued"

    def test_pending_with_comment_maps_to_queued(self):
        ps = _import_plan_status()
        assert ps.normalize_master_status("pending   # legacy") == "queued"

    def test_no_comment_passes_through(self):
        ps = _import_plan_status()
        assert ps.normalize_master_status("active") == "active"

    def test_active_with_comment(self):
        ps = _import_plan_status()
        assert ps.normalize_master_status("active  # running") == "active"


# ── integration: scheduler_scan sees commented-status master ────────

class TestSchedulerScanCommentedStatus:
    """scheduler_scan INCLUDES a master whose status has an inline comment."""

    def test_commented_queued_master_is_dispatch_visible(self, tmp_path):
        """AC-3: a master with ``status: queued  # comment`` and a pending
        sub-plan IS returned by scan_projects (the exact kira failure)."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-test.md",
                      status="queued   # UNPARKED — ready for next batch",
                      subplans=["2026-06-15-work.md"])
        _write_subplan(plans, "2026-06-15-work.md")

        # Add last-launch.json so repo_path resolves
        project_dir = tmp_path / "projects" / "test-proj"
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        import json
        (launcher_dir / "last-launch.json").write_text(
            json.dumps({"project_path": "/some/repo"}), encoding="utf-8",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert any(p["key"] == "test-proj" for p in scan), (
            "scheduler_scan should include project with commented status"
        )

    def test_commented_active_master_is_dispatch_visible(self, tmp_path):
        """Variant: ``status: active  # running`` is also visible."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-test.md",
                      status="active  # currently running",
                      subplans=["2026-06-15-work.md"])
        _write_subplan(plans, "2026-06-15-work.md")

        project_dir = tmp_path / "projects" / "test-proj"
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        import json
        (launcher_dir / "last-launch.json").write_text(
            json.dumps({"project_path": "/some/repo"}), encoding="utf-8",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert any(p["key"] == "test-proj" for p in scan), (
            "scheduler_scan should include project with active+comment status"
        )

    def test_commented_draft_still_excluded(self, tmp_path):
        """A draft master with a comment is still excluded (draft gate)."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-test.md",
                      status="draft   # not ready yet",
                      subplans=["2026-06-15-work.md"])
        _write_subplan(plans, "2026-06-15-work.md")

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 0, "draft master must be excluded even with comment"
