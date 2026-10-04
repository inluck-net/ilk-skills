"""Tests: an auto-planned batch stays inside its rails.

Sub-plan: an-auto-planned-batch-stays-inside-its-rails (step 0).
Covers AC-1..AC-6: touches_kernel, rank, screen_candidate, check_master,
mark_candidate, mark_master.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_WATCHDOG_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "autoplan"


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_module():
    """Import autoplan_rails (fails until step 1 ships)."""
    if _LOOP_SCRIPTS not in sys.path:
        sys.path.insert(0, _LOOP_SCRIPTS)
    if _SELF_SCRIPTS not in sys.path:
        sys.path.insert(0, _SELF_SCRIPTS)
    if _WATCHDOG_SCRIPTS not in sys.path:
        sys.path.insert(0, _WATCHDOG_SCRIPTS)
    import autoplan_rails
    return autoplan_rails


def _build_fake_backlog_dir(tmp_path: Path) -> Path:
    """Create an empty backlog directory for testing."""
    backlog_dir = tmp_path / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    return backlog_dir


def _save_candidates(backlog_dir: Path, entries: list[dict]) -> None:
    """Save candidates to the backlog directory."""
    p = backlog_dir / "candidates.json"
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def _load_candidates(backlog_dir: Path) -> list[dict]:
    """Load candidates from the backlog directory."""
    p = backlog_dir / "candidates.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _make_candidate(
    *,
    cid: str = "sig-abc123",
    title: "Missing feature X",
    gap: "No support for X",
    source: str = "triage",
    status: str = "open",
    seen_count: int = 1,
    escalations: int = 0,
    urgent: bool = False,
    autoplan_attempts: int = 0,
    autoplan_blocked: bool = False,
    first_seen: str = "2026-10-03T12:00:00+0800",
) -> dict:
    """Build a minimal backlog entry."""
    return {
        "id": cid,
        "title": title,
        "gap": gap,
        "source": source,
        "status": status,
        "seen_count": seen_count,
        "first_seen": first_seen,
        "relations": {
            "escalations": escalations,
            "urgent": urgent,
            "autoplan_attempts": autoplan_attempts,
            "autoplan_blocked": autoplan_blocked,
        },
        "evidence": [],
        "proposed_fix": "",
        "kind": "toolkit",
        "leverage": "medium",
        "severity": "medium",
    }


def _copy_fixture_batch(tmp_path: Path) -> Path:
    """Copy the fixture batch to a temp plans dir."""
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    for f in FIXTURE_DIR.iterdir():
        if f.suffix == ".md":
            shutil.copy2(f, plans_dir / f.name)
    return plans_dir


# ── AC-1: touches_kernel ─────────────────────────────────────────────────────


class TestAC1:
    """touches_kernel hits kernel entries and misses non-kernel paths."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_plan_lint(self):
        """Exact file match on a kernel path."""
        mod = _load_module()
        result = mod.touches_kernel("skills/ilk-loop/scripts/plan_lint.py")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_directory_containment(self):
        """Directory path (ends with /) hits a kernel file under it."""
        mod = _load_module()
        result = mod.touches_kernel("skills/ilk-loop/scripts/")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_wildcard(self):
        """Glob pattern matches kernel paths."""
        mod = _load_module()
        result = mod.touches_kernel("skills/**")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_conftest_basename(self):
        """Any conftest.py basename hits the kernel."""
        mod = _load_module()
        result = mod.touches_kernel("skills/foo/tests/conftest.py")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_invariants_dir(self):
        """tests/invariants/ directory is in the kernel."""
        mod = _load_module()
        result = mod.touches_kernel("tests/invariants/test_x.py")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_hits_ilk_launch_json(self):
        """".ilk-launch.json is in the kernel."""
        mod = _load_module()
        result = mod.touches_kernel(".ilk-launch.json")
        assert result is not None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_misses_non_kernel_file(self):
        """A file not in the kernel returns None."""
        mod = _load_module()
        result = mod.touches_kernel("skills/ilk-feedback/scripts/collect.py")
        assert result is None

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_misses_non_kernel_file_with_similar_name(self):
        """A file with a similar name but not in kernel returns None."""
        mod = _load_module()
        result = mod.touches_kernel("skills/ilk-loop/scripts/plan_lint_helpers.py")
        assert result is None


# ── AC-2: rank ────────────────────────────────────────────────────────────────


class TestAC2:
    """rank orders eligible candidates correctly and drops ineligible ones."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_escalated_before_urgent_before_high_seen(self):
        """Escalation > urgency > seen_count ordering."""
        mod = _load_module()
        entries = [
            _make_candidate(cid="high-seen", title="High seen", seen_count=10),
            _make_candidate(cid="urgent", title="Urgent item", urgent=True),
            _make_candidate(cid="escalated", title="Escalated item", escalations=1),
        ]
        ranked = mod.rank(entries)
        ids = [e["id"] for e in ranked]
        assert ids.index("escalated") < ids.index("urgent") < ids.index("high-seen")

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_drops_feedback_source(self):
        """Non-triage source entries are dropped."""
        mod = _load_module()
        entries = [
            _make_candidate(cid="triage-ok", source="triage"),
            _make_candidate(cid="feedback-bad", source="feedback",
                            title="Feedback entry", gap="gap"),
        ]
        ranked = mod.rank(entries)
        ids = [e["id"] for e in ranked]
        assert "feedback-bad" not in ids

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_drops_planned_status(self):
        """Non-open status entries are dropped."""
        mod = _load_module()
        entries = [
            _make_candidate(cid="open-ok", status="open"),
            _make_candidate(cid="planned-bad", status="planned",
                            title="Planned entry", gap="gap"),
        ]
        ranked = mod.rank(entries)
        ids = [e["id"] for e in ranked]
        assert "planned-bad" not in ids

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_drops_max_attempts(self):
        """Entries with autoplan_attempts >= 2 are dropped."""
        mod = _load_module()
        entries = [
            _make_candidate(cid="fresh", autoplan_attempts=0),
            _make_candidate(cid="exhausted", autoplan_attempts=2,
                            title="Exhausted entry", gap="gap"),
        ]
        ranked = mod.rank(entries)
        ids = [e["id"] for e in ranked]
        assert "exhausted" not in ids

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_drops_blocked(self):
        """Entries with autoplan_blocked are dropped."""
        mod = _load_module()
        entries = [
            _make_candidate(cid="ok", autoplan_blocked=False),
            _make_candidate(cid="blocked", autoplan_blocked=True,
                            title="Blocked entry", gap="gap"),
        ]
        ranked = mod.rank(entries)
        ids = [e["id"] for e in ranked]
        assert "blocked" not in ids


# ── AC-3: screen_candidate ────────────────────────────────────────────────────


class TestAC3:
    """screen_candidate catches kernel references in candidate text."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_returns_kernel_entry_for_basename_mention(self):
        """When gap mentions a kernel file by basename, returns it."""
        mod = _load_module()
        entry = _make_candidate(
            gap="ship_audit.py produces wrong output",
        )
        result = mod.screen_candidate(entry)
        assert result is not None
        assert "ship_audit.py" in result

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_returns_none_for_clean_candidate(self):
        """When no kernel path is mentioned, returns None."""
        mod = _load_module()
        entry = _make_candidate(
            gap="collect.py misses some metrics",
            title="Fix metrics collection",
        )
        result = mod.screen_candidate(entry)
        assert result is None


# ── AC-4: check_master ────────────────────────────────────────────────────────


class TestAC4:
    """check_master validates batch structure against rails."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_clean_batch_passes(self):
        """A well-formed batch with verify-last returns empty problems."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        problems = mod.check_master(master_path, plans_dir)
        assert problems == []

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_verify_not_last_fails(self):
        """Verify sub-plan not being last is caught."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        # Swap the order in the master
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        text = master_path.read_text(encoding="utf-8")
        text = text.replace(
            "| 0 | [2026-10-03-fixture-feature.md]",
            "| 0 | [2026-10-03-fixture-verify.md]",
        ).replace(
            "| 1 | [2026-10-03-fixture-verify.md]",
            "| 1 | [2026-10-03-fixture-feature.md]",
        )
        master_path.write_text(text, encoding="utf-8")
        problems = mod.check_master(master_path, plans_dir)
        assert len(problems) >= 1

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_kernel_scope_path_fails(self):
        """A sub-plan with a kernel scope_path is caught."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        # Add a kernel path to the feature sub-plan's scope_paths
        feature_path = plans_dir / "2026-10-03-fixture-feature.md"
        text = feature_path.read_text(encoding="utf-8")
        text = text.replace(
            '- "skills/ilk-feedback/scripts/helper.py"',
            '- "skills/ilk-loop/scripts/"',
        )
        feature_path.write_text(text, encoding="utf-8")
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        problems = mod.check_master(master_path, plans_dir)
        assert len(problems) >= 1

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_conftest_edit_bullet_fails(self):
        """An Edit bullet mentioning conftest.py is caught."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        feature_path = plans_dir / "2026-10-03-fixture-feature.md"
        text = feature_path.read_text(encoding="utf-8")
        text += "\n\nEdit `conftest.py` to add a fixture.\n"
        feature_path.write_text(text, encoding="utf-8")
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        problems = mod.check_master(master_path, plans_dir)
        assert len(problems) >= 1

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_second_auto_planned_queued_fails(self):
        """A second auto-planned master already queued is caught."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        # Create a second auto-planned master
        second_master = plans_dir / "MASTER-2026-10-03-other-auto-execution-plan.md"
        second_master.write_text(
            "---\nmaster_plan: 2026-10-03-other-auto\nstatus: queued\nauto_planned: true\n---\n\n# Other\n",
            encoding="utf-8",
        )
        # Mark the fixture master as auto_planned
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        text = master_path.read_text(encoding="utf-8")
        text = text.replace("status: queued", "status: queued\nauto_planned: true")
        master_path.write_text(text, encoding="utf-8")
        problems = mod.check_master(master_path, plans_dir)
        assert len(problems) >= 1


# ── AC-5: mark_candidate ──────────────────────────────────────────────────────


class TestAC5:
    """mark_candidate updates entries under lock, refuses corrupt files."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_corrupt_file_raises(self):
        """A corrupt candidates.json raises and leaves bytes unchanged."""
        mod = _load_module()
        backlog_dir = _build_fake_backlog_dir(tempfile.mkdtemp())
        p = backlog_dir / "candidates.json"
        p.write_text("NOT VALID JSON{{{", encoding="utf-8")
        original = p.read_text(encoding="utf-8")
        with pytest.raises(Exception):
            mod.mark_candidate("sig-abc", status="planned", backlog_dir=backlog_dir)
        assert p.read_text(encoding="utf-8") == original

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_updates_exactly_one_entry(self):
        """mark_candidate changes only the targeted entry."""
        mod = _load_module()
        backlog_dir = _build_fake_backlog_dir(tempfile.mkdtemp())
        _save_candidates(backlog_dir, [
            _make_candidate(cid="sig-aaa", title="AAA"),
            _make_candidate(cid="sig-bbb", title="BBB"),
        ])
        result = mod.mark_candidate("sig-aaa", status="planned",
                                    backlog_dir=backlog_dir)
        assert result["status"] == "planned"
        loaded = _load_candidates(backlog_dir)
        aaa = [e for e in loaded if e["id"] == "sig-aaa"][0]
        bbb = [e for e in loaded if e["id"] == "sig-bbb"][0]
        assert aaa["status"] == "planned"
        assert bbb["status"] == "open"

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_unknown_id_raises(self):
        """An unknown entry id raises KeyError and writes nothing."""
        mod = _load_module()
        backlog_dir = _build_fake_backlog_dir(tempfile.mkdtemp())
        _save_candidates(backlog_dir, [
            _make_candidate(cid="sig-aaa", title="AAA"),
        ])
        with pytest.raises(KeyError):
            mod.mark_candidate("sig-nonexistent", status="planned",
                               backlog_dir=backlog_dir)
        loaded = _load_candidates(backlog_dir)
        assert loaded[0]["status"] == "open"


# ── AC-6: mark_master ─────────────────────────────────────────────────────────


class TestAC6:
    """mark_master inserts auto_planned fields into frontmatter."""

    @pytest.mark.xfail(strict=True, reason="autoplan_rails not yet implemented")
    def test_sets_auto_planned_true(self):
        """After mark_master, parse_frontmatter reads auto_planned: true."""
        mod = _load_module()
        plans_dir = _copy_fixture_batch(tmp_path=tempfile.mkdtemp())
        master_path = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"

        # Need plan_status for parse_frontmatter
        if _LOOP_SCRIPTS not in sys.path:
            sys.path.insert(0, _LOOP_SCRIPTS)
        from plan_status import parse_frontmatter

        mod.mark_master(master_path, candidate_id="sig-abc", run_id="run-001")
        text = master_path.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        assert fm.get("auto_planned") == "true"
        assert fm.get("autoplan_candidate") == "sig-abc"
        assert fm.get("autoplan_run") == "run-001"
        # Original status preserved
        assert fm.get("status") == "queued"