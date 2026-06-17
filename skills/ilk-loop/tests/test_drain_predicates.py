"""RED tests for depends_on-aware drain predicates (AC-1 + AC-2).

The loop currently picks sub-plans by status alone — a blocked sub-plan
with an unmet `depends_on` is invisible, and a master with a single
blocked sub-plan blocks the entire queue.  L4 requires:

  AC-1: ``subplan_is_runnable(fm, sibling_statuses)`` returns False for
        blocked, False when any depends_on sibling is not shipped, True
        for pending/in-progress with all deps shipped.
  AC-2: ``master_is_drainable(master, plans_dir)`` is True iff >= 1
        registered sub-plan is runnable; a master with only blocked /
        dep-on-blocked sub-plans is NOT drainable (= stalled).

These tests build temp plans dirs and in-memory dicts — no subprocess,
no external services.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Will be implemented in step 1 — until then, every test here is RED.
from plan_status import master_is_drainable, subplan_is_runnable  # noqa: E402


# ── AC-1: subplan_is_runnable ────────────────────────────────────────────────


class TestSubplanIsRunnable:
    """subplan_is_runnable(fm: dict, sibling_statuses: dict) -> bool."""

    # --- basic status gating ---

    def test_pending_no_deps_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is True

    def test_in_progress_no_deps_is_runnable(self) -> None:
        fm = {"status": "in-progress", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is True

    def test_shipped_is_not_runnable(self) -> None:
        fm = {"status": "shipped", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is False

    def test_blocked_is_not_runnable(self) -> None:
        fm = {"status": "blocked", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is False

    # --- depends_on gating ---

    def test_pending_with_all_deps_shipped_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "shipped"}
        assert subplan_is_runnable(fm, siblings) is True

    def test_pending_with_unmet_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "pending"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_blocked_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "blocked"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_in_progress_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "in-progress"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_multiple_deps_all_shipped(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha", "beta"]'}
        siblings = {"alpha": "shipped", "beta": "shipped"}
        assert subplan_is_runnable(fm, siblings) is True

    def test_pending_with_multiple_deps_one_unmet(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha", "beta"]'}
        siblings = {"alpha": "shipped", "beta": "blocked"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_blocked_with_deps_shipped_is_still_not_runnable(self) -> None:
        """Blocked status overrides deps — blocked is never runnable."""
        fm = {"status": "blocked", "depends_on": '["alpha"]'}
        siblings = {"alpha": "shipped"}
        assert subplan_is_runnable(fm, siblings) is False

    # --- missing sibling (dep file missing or typo) ---

    def test_dep_not_in_siblings_is_not_runnable(self) -> None:
        """If the dep slug is missing from sibling_statuses, treat as unmet."""
        fm = {"status": "pending", "depends_on": '["nonexistent"]'}
        assert subplan_is_runnable(fm, {}) is False

    # --- empty / absent depends_on ---

    def test_empty_depends_on_string_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": ""}
        assert subplan_is_runnable(fm, {}) is True

    def test_missing_depends_on_key_is_runnable(self) -> None:
        fm = {"status": "pending"}
        assert subplan_is_runnable(fm, {}) is True


# ── AC-2: master_is_drainable ────────────────────────────────────────────────


def _write_plan(path: Path, body: str) -> None:
    """Write a plan file with minimal frontmatter."""
    path.write_text(textwrap.dedent(body), encoding="utf-8")


class TestMasterIsDrainable:
    """master_is_drainable(master_path, plans_dir) -> bool."""

    def test_all_shipped_is_not_drainable(self, tmp_path: Path) -> None:
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: shipped
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is False

    def test_one_runnable_pending_is_drainable(self, tmp_path: Path) -> None:
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_blocked_and_dep_on_blocked_is_stalled(self, tmp_path: Path) -> None:
        """[shipped, blocked, pending-dep-on-blocked] → stalled."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
            - [gamma](2026-01-01-gamma.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: blocked
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-gamma.md", """\
            ---
            status: pending
            depends_on: ["beta"]
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is False

    def test_pending_with_unmet_dep_plus_runnable_sibling(self, tmp_path: Path) -> None:
        """[shipped, pending-dep-on-blocked, pending-no-deps] → drainable."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
            - [gamma](2026-01-01-gamma.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: blocked
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-gamma.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_missing_subplan_file_is_runnable(self, tmp_path: Path) -> None:
        """A registered sub-plan whose file is missing counts as outstanding
        work (matching master_has_nonshipped semantics) — and since its
        status can't be read, treat as pending (runnable)."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
        """)
        # alpha.md does NOT exist on disk
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_no_registered_subplans_is_drainable(self, tmp_path: Path) -> None:
        """A master with no sub-plan references is drainable (nothing blocks
        it — legacy / empty masters should still be promotable)."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            No sub-plan references here.
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is True


# ── AC-3: loop_status integration (resolve_status) ───────────────────────────

# resolve_status discovers plans via ilk_paths, which needs cwd to be inside
# a project with docs/plans/.  We monkeypatch _resolve_plans_dir to point at
# our tmp dirs.  We also need an .ilk-meta.json or .git marker for
# find_project_root — but resolve_status only needs _resolve_plans_dir, not
# find_project_root (that's used for meta-project detection in text mode).
# Since resolve_status calls _resolve_plans_dir directly, we patch it.

from unittest.mock import patch  # noqa: E402

from loop_status import resolve_status  # noqa: E402


class TestLoopStatusDrain:
    """AC-3: loop_status picks runnable sub-plans and flags stalled masters."""

    def _setup_plans(self, tmp_path: Path, sub_plans: dict[str, str]) -> Path:
        """Create a plans dir with a master and the given sub-plans.

        sub_plans maps filename → frontmatter body (without --- delimiters).
        """
        plans = tmp_path / "plans"
        plans.mkdir()
        # Build master registry from sub_plan filenames.
        registry_lines = "\n".join(
            f"  - [{fname}](./{fname})" for fname in sub_plans
        )
        _write_plan(plans / "MASTER-2026-01-01-test.md", f"""\
            ---
            status: active
            ---
            {registry_lines}
        """)
        for fname, fm_body in sub_plans.items():
            _write_plan(plans / fname, f"---\n{fm_body}\n---\n")
        return plans

    def test_picks_pending_not_blocked(self, tmp_path: Path) -> None:
        """[shipped, blocked, pending-no-deps] → picks the pending one."""
        plans = self._setup_plans(tmp_path, {
            "2026-01-01-alpha.md": "status: shipped",
            "2026-01-01-beta.md": "status: blocked",
            "2026-01-01-gamma.md": "status: pending\ndepends_on: []",
        })
        with patch("loop_status._resolve_plans_dir", return_value=(plans, "test")):
            result = resolve_status(tmp_path)
        assert result["next"] is not None
        assert result["next"]["fname"] == "2026-01-01-gamma.md"
        assert result["stalled"] is False

    def test_stalled_when_all_blocked_or_dep_blocked(self, tmp_path: Path) -> None:
        """[blocked, pending-depends-on-blocked] → stalled, next=None."""
        plans = self._setup_plans(tmp_path, {
            "2026-01-01-alpha.md": "status: blocked",
            "2026-01-01-beta.md": "status: pending\ndepends_on: [\"alpha\"]",
        })
        with patch("loop_status._resolve_plans_dir", return_value=(plans, "test")):
            result = resolve_status(tmp_path)
        assert result["next"] is None
        assert result["stalled"] is True

    def test_not_stalled_when_all_shipped(self, tmp_path: Path) -> None:
        """[shipped, shipped] → not stalled, next=None (genuinely done)."""
        plans = self._setup_plans(tmp_path, {
            "2026-01-01-alpha.md": "status: shipped",
            "2026-01-01-beta.md": "status: shipped",
        })
        with patch("loop_status._resolve_plans_dir", return_value=(plans, "test")):
            result = resolve_status(tmp_path)
        assert result["next"] is None
        assert result["stalled"] is False

    def test_picks_runnable_skip_unmet_dep(self, tmp_path: Path) -> None:
        """[shipped, pending-dep-on-shipped, pending-dep-on-blocked] → picks first pending."""
        plans = self._setup_plans(tmp_path, {
            "2026-01-01-alpha.md": "status: shipped",
            "2026-01-01-beta.md": "status: blocked",
            "2026-01-01-gamma.md": "status: pending\ndepends_on: [\"alpha\"]",
            "2026-01-01-delta.md": "status: pending\ndepends_on: [\"beta\"]",
        })
        with patch("loop_status._resolve_plans_dir", return_value=(plans, "test")):
            result = resolve_status(tmp_path)
        assert result["next"] is not None
        assert result["next"]["fname"] == "2026-01-01-gamma.md"
        assert result["stalled"] is False


# ── AC-4: promote_next_master advances past stalled ──────────────────────────

from promote_next_master import main as promote_main  # noqa: E402


class TestPromotePastStalled:
    """AC-4: stalled active master is parked blocked, not shipped."""

    def _write_master(self, plans: Path, name: str, status: str,
                      sub_plan_refs: list[str]) -> None:
        registry = "\n".join(f"  - [r](./{f})" for f in sub_plan_refs)
        content = f"---\nstatus: {status}\npriority: 1\ncreated: 2026-01-01\n---\n{registry}\n"
        (plans / name).write_text(content, encoding="utf-8")

    def test_stalled_active_parked_blocked_not_shipped(self, tmp_path: Path, capsys) -> None:
        """Stalled active → blocked; next queued drainable → active."""
        plans = tmp_path / "plans"
        plans.mkdir()

        # Active master: stalled (blocked sub-plan, pending dep-on-blocked)
        self._write_master(plans, "MASTER-active.md", "active",
                           ["2026-01-01-alpha.md", "2026-01-01-beta.md"])
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: blocked
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: pending
            depends_on: ["alpha"]
            ---
        """)

        # Queued master: drainable (has a pending sub-plan with no deps)
        self._write_master(plans, "MASTER-queued.md", "queued",
                           ["2026-01-02-gamma.md"])
        _write_plan(plans / "2026-01-02-gamma.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)

        exit_code = promote_main(["--plans-dir", str(plans)])
        import json as _json
        plan = _json.loads(capsys.readouterr().out)

        # The stalled master should be demoted to blocked, not shipped.
        assert plan["demoted"] == "MASTER-active.md"
        assert plan["demote_status"] == "blocked"
        assert plan["promoted"] == "MASTER-queued.md"

        # Verify on disk: active master is blocked, queued master is active.
        active_fm = _parse_fm(plans / "MASTER-active.md")
        queued_fm = _parse_fm(plans / "MASTER-queued.md")
        assert active_fm["status"] == "blocked"
        assert queued_fm["status"] == "active"

    def test_all_shipped_active_no_demotion_needed(self, tmp_path: Path, capsys) -> None:
        """An all-shipped active master is already reconciled — no demotion,
        but the queued master is still promoted to active."""
        plans = tmp_path / "plans"
        plans.mkdir()

        self._write_master(plans, "MASTER-active.md", "active",
                           ["2026-01-01-alpha.md"])
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            ---
        """)

        self._write_master(plans, "MASTER-queued.md", "queued",
                           ["2026-01-02-beta.md"])
        _write_plan(plans / "2026-01-02-beta.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)

        exit_code = promote_main(["--plans-dir", str(plans)])
        import json as _json
        plan = _json.loads(capsys.readouterr().out)

        # All-shipped active → filtered out by master_has_nonshipped → no demotion.
        # reconcile_master_status handles the shipped flip.
        assert plan["demoted"] is None
        assert plan["promoted"] == "MASTER-queued.md"


def _parse_fm(path: Path) -> dict[str, str]:
    """Minimal frontmatter parser for test verification."""
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for raw in text[3:end].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


# ── AC-5: quarantine_subplan ─────────────────────────────────────────────────

from quarantine_subplan import quarantine_subplan  # noqa: E402


class TestQuarantineSubplan:
    """AC-5: auto-quarantine after N consecutive local_checks failures."""

    def _make_subplan(self, plans: Path, slug: str = "alpha",
                      status: str = "pending") -> Path:
        fname = f"2026-01-01-{slug}.md"
        _write_plan(plans / fname, f"""\
            ---
            status: {status}
            depends_on: []
            ---
        """)
        return plans / fname

    def test_first_failure_below_threshold(self, tmp_path: Path) -> None:
        """First failure → counter=1, below threshold=2 → not blocked."""
        plans = tmp_path / "plans"
        plans.mkdir()
        self._make_subplan(plans)
        result = quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        assert result["blocked"] is False
        assert result["fails"] == 1

    def test_second_failure_blocks(self, tmp_path: Path) -> None:
        """Second failure → counter=2, at threshold → blocked."""
        plans = tmp_path / "plans"
        plans.mkdir()
        self._make_subplan(plans)
        quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        result = quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        assert result["blocked"] is True
        assert result["fails"] == 2

    def test_blocked_subplan_has_findings_note(self, tmp_path: Path) -> None:
        """Blocked sub-plan gets a Findings note naming the failing check."""
        plans = tmp_path / "plans"
        plans.mkdir()
        sub_path = self._make_subplan(plans)
        quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        text = sub_path.read_text(encoding="utf-8-sig")
        assert "auto-quarantined" in text
        assert "pytest -q" in text

    def test_already_blocked_returns_immediately(self, tmp_path: Path) -> None:
        """A sub-plan already blocked → returns blocked=True without error."""
        plans = tmp_path / "plans"
        plans.mkdir()
        self._make_subplan(plans, status="blocked")
        result = quarantine_subplan(plans, "alpha", "pytest -q", threshold=2)
        assert result["blocked"] is True
        assert result.get("already_blocked") is True

    def test_missing_subplan_returns_error(self, tmp_path: Path) -> None:
        """Missing sub-plan file → returns error, not blocked."""
        plans = tmp_path / "plans"
        plans.mkdir()
        result = quarantine_subplan(plans, "nonexistent", "pytest -q", threshold=2)
        assert result["blocked"] is False
        assert "error" in result

    def test_counter_persists_across_calls(self, tmp_path: Path) -> None:
        """Counter is persisted in frontmatter and survives re-reads."""
        plans = tmp_path / "plans"
        plans.mkdir()
        self._make_subplan(plans)
        quarantine_subplan(plans, "alpha", "pytest -q", threshold=3)
        quarantine_subplan(plans, "alpha", "pytest -q", threshold=3)
        # Third call should block.
        result = quarantine_subplan(plans, "alpha", "pytest -q", threshold=3)
        assert result["blocked"] is True
        assert result["fails"] == 3
