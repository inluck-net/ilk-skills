"""Tests for route-specific postmortem text and #52 run-id-scoped classification.

Covers:
  AC-1  ship_integrity_violation ⇒ "gate was red" + "3 of 12"
  AC-2  shipped-unproven / work_tree_invalid get their own text
  AC-3  genuine tier case keeps today's text, no "(none listed)"
  AC-4  label + watchdog routing unchanged (control)
  AC-5  #52: classify run A from its own run_exit when sentinel names run B
  AC-6  #52: sentinel running + live pid ⇒ exit 3, no postmortem

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
Both HOME and ILK_DATA_HOME are pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
_LOOP_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_LOOP_SCRIPTS_DIR))

import collect  # noqa: E402
from ilk_paths import external_launcher_dir, project_key  # noqa: E402


# -- helpers ------------------------------------------------------------------


def _write_sentinel(
    project_path: Path,
    *,
    state: str,
    run_id: str,
    pid: int = 0,
    iteration: int = 0,
) -> None:
    """Write a minimal sentinel file in the external launcher dir."""
    launcher_dir = external_launcher_dir(project_key(project_path))
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "pid": pid,
        "run_id": run_id,
        "iteration": iteration,
        "exit_code": None,
        "generated_at": "2026-09-25T12:00:00+08:00",
    }
    (launcher_dir / "last-exit.json").write_text(json.dumps(sentinel))


def _write_subplan(
    plans_dir: Path,
    slug: str,
    *,
    status: str = "shipped",
    tier: str | None = None,
) -> Path:
    """Create a minimal sub-plan .md with the given frontmatter."""
    lines = ["---"]
    lines.append(f"plan: {slug}")
    lines.append(f"status: {status}")
    if tier is not None:
        lines.append(f"verification_tier: {tier}")
    lines.append("current_step: 3")
    lines.append("estimated_steps: 3")
    lines.append("---")
    lines.append(f"# {slug}\n")
    p = plans_dir / f"2026-09-25-{slug}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    """Create a temp plans dir with a MASTER file so _has_master() passes."""
    d = tmp_path / "plans"
    d.mkdir()
    (d / "MASTER-2026-09-25-test.md").write_text(
        "---\nmaster_plan: 2026-09-25-test\n---\n# test\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def isolated_env(tmp_path: Path):
    """Pin HOME and ILK_DATA_HOME to tmp_path for the test duration."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    old_home = os.environ.get("HOME")
    old_data = os.environ.get("ILK_DATA_HOME")
    try:
        os.environ["HOME"] = str(fake_home)
        os.environ["ILK_DATA_HOME"] = str(data_home)
        yield tmp_path
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_data is not None:
            os.environ["ILK_DATA_HOME"] = old_data
        else:
            os.environ.pop("ILK_DATA_HOME", None)


# -- AC-1: ship_integrity_violation ⇒ "gate was red" + "3 of 12" --------------


class TestShipIntegrityViolationLabel:
    """Sentinel ship_integrity_violation ⇒ shipped-unverified label (already works)."""

    def test_label_is_shipped_unverified(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="ship_integrity_violation", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "shipped-unverified", f"expected shipped-unverified, got {label}"


@pytest.mark.xfail(strict=True, reason="red-first")
class TestShipIntegrityViolationNamesTheGate:
    """Sentinel ship_integrity_violation body contains 'gate was red' and '3 of 12',
    does not contain 'All sub-plans shipped'."""

    def test_body_contains_gate_was_red(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="ship_integrity_violation", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "gate was red" in narrative, (
            f"narrative should contain 'gate was red', got: {narrative}"
        )

    def test_body_contains_shipped_count(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="ship_integrity_violation", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "3 of 12" in narrative, (
            f"narrative should contain shipped count like '3 of 12', got: {narrative}"
        )

    def test_body_does_not_say_all_shipped(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="ship_integrity_violation", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "All sub-plans shipped" not in narrative, (
            f"a ship_integrity_violation must NOT say 'All sub-plans shipped', got: {narrative}"
        )


# -- AC-2: shipped-unproven / work_tree_invalid get their own text -------------


class TestShippedUnprovenLabel:
    """Sentinel shipped-unproven ⇒ shipped-unverified label (already works)."""

    def test_shipped_unproven_label(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="shipped-unproven", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "shipped-unverified", f"expected shipped-unverified, got {label}"


@pytest.mark.xfail(strict=True, reason="red-first")
class TestShippedUnprovenDistinctText:
    """Sentinel shipped-unproven ⇒ route-specific narrative text."""

    def test_shipped_unproven_mentions_no_ship_proof(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="shipped-unproven", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "ship-proof" in narrative.lower() or "no ship" in narrative.lower(), (
            f"shipped-unproven narrative should mention missing ship-proof, got: {narrative}"
        )


class TestWorkTreeInvalidLabel:
    """Sentinel work_tree_invalid ⇒ shipped-unverified label (already works)."""

    def test_work_tree_invalid_label(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="work_tree_invalid", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 1,
            "exit_code": 0,
            "duration_sec": 30,
            "new_commits_total": 0,
            "stop_reason": None,
        }]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "shipped-unverified", f"expected shipped-unverified, got {label}"


@pytest.mark.xfail(strict=True, reason="red-first")
class TestWorkTreeInvalidDistinctText:
    """Sentinel work_tree_invalid ⇒ route-specific narrative text."""

    def test_work_tree_invalid_mentions_work_tree(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="work_tree_invalid", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 1,
            "exit_code": 0,
            "duration_sec": 30,
            "new_commits_total": 0,
            "stop_reason": None,
        }]
        label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "work_tree" in narrative.lower() or "work tree" in narrative.lower(), (
            f"work_tree_invalid narrative should mention work_tree, got: {narrative}"
        )


# -- AC-3: genuine tier case keeps text, no "(none listed)" --------------------


class TestGenuineTierLabel:
    """A compile-only sub-plan shipped ⇒ shipped-unverified label (already works)."""

    def test_compile_only_label(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_subplan(plans_dir, "alpha", tier="compile-only")
        _write_subplan(plans_dir, "beta", tier="loop-verified")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
            with patch.object(collect, "_loop_status", None):
                with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                    label, facts = collect.classify(iters, None, project_path)
        assert label == "shipped-unverified", f"expected shipped-unverified, got {label}"


class TestGenuineTierNoNoneListed:
    """A compile-only sub-plan shipped ⇒ narrative lists the tier,
    and the words '(none listed)' never appear. Already works."""

    def test_compile_only_lists_tier(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_subplan(plans_dir, "alpha", tier="compile-only")
        _write_subplan(plans_dir, "beta", tier="loop-verified")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "all-shipped",
        }]
        with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
            with patch.object(collect, "_loop_status", None):
                with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                    label, facts = collect.classify(iters, None, project_path)
        narrative = collect._label_narrative(label, facts)
        assert "(none listed)" not in narrative, (
            f"'(none listed)' must never appear, got: {narrative}"
        )
        assert "compile-only" in narrative, (
            f"narrative should list the compile-only tier, got: {narrative}"
        )


# -- AC-4: label + watchdog routing unchanged (control) -----------------------


class TestExistingClassificationsUnchanged:
    """Regression guard: existing collect tests must stay green.
    This is a control — no xfail. If this fails, the change broke something."""

    def test_clean_success_still_works(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_subplan(plans_dir, "alpha", tier="loop-verified")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 3,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 5,
            "stop_reason": "already-shipped",
        }]
        with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, project_path)
        assert label == "clean-success", f"expected clean-success, got {label}"

    def test_sentinel_budget_exhausted_unchanged(self, isolated_env, plans_dir):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        _write_sentinel(project_path, state="budget_exhausted", run_id="20260925-181210")

        iters = [{
            "run_id": "20260925-181210",
            "iteration": 5,
            "exit_code": 1,
            "duration_sec": 300,
            "new_commits_total": 2,
        }]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "budget-exhausted", f"expected budget-exhausted, got {label}"


# -- AC-5: #52 — classify run A from its own run_exit when sentinel names run B --


@pytest.mark.xfail(strict=True, reason="red-first")
class TestClassifiesFromOwnRunExit:
    """JSONL has run A (run_exit selfmod_live_clone_touched), then run B running.
    Sentinel names run B. classify(run A's iters) ⇒ merge-conflict, not interrupted."""

    def test_run_a_classified_from_own_run_exit(self, isolated_env):
        project_path = isolated_env / "repo"
        project_path.mkdir()
        # Sentinel names run B (which is running)
        _write_sentinel(project_path, state="running", run_id="20260925-200000", pid=99999)

        # Run A's records: has a run_exit with selfmod_live_clone_touched
        iters = [
            {
                "run_id": "20260925-181210",
                "iteration": 1,
                "exit_code": 0,
                "duration_sec": 120,
                "new_commits_total": 3,
            },
            {
                "run_id": "20260925-181210",
                "iteration": 999999,
                "record_type": "run_exit",
                "stop_reason": "selfmod_live_clone_touched",
            },
        ]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "merge-conflict", (
            f"run A should be classified from its own run_exit (merge-conflict), "
            f"got {label}: {facts}"
        )

    def test_run_a_not_interrupted_by_other_sentinel(self, isolated_env):
        """The sentinel for run B (interrupted) must NOT override run A's own stop."""
        project_path = isolated_env / "repo"
        project_path.mkdir()
        # Sentinel names run B with state "interrupted"
        _write_sentinel(project_path, state="interrupted", run_id="20260925-200000")

        # Run A: ship_integrity_violation
        iters = [
            {
                "run_id": "20260925-181210",
                "iteration": 3,
                "exit_code": 0,
                "duration_sec": 120,
                "new_commits_total": 5,
            },
            {
                "run_id": "20260925-181210",
                "iteration": 999999,
                "record_type": "run_exit",
                "stop_reason": "ship_integrity_violation",
            },
        ]
        label, facts = collect.classify(iters, None, project_path)
        assert label == "shipped-unverified", (
            f"run A should be classified from its own run_exit (shipped-unverified), "
            f"got {label}: {facts}"
        )


# -- AC-6: #52 — sentinel running + live pid ⇒ exit 3, no postmortem ----------


@pytest.mark.xfail(strict=True, reason="red-first")
class TestLiveRunRefusesPostmortem:
    """Sentinel running for the SAME run_id with a live pid ⇒ collect.py exits 3,
    writes no postmortem file."""

    def test_live_pid_exits_3(self, isolated_env):
        """A sentinel running + live pid (the test's own sleep child) ⇒ exit 3."""
        project_path = isolated_env / "repo"
        project_path.mkdir()

        # Start a live child process to use as the "running" pid
        child = subprocess.Popen(["sleep", "30"])
        try:
            _write_sentinel(
                project_path,
                state="running",
                run_id="20260925-200000",
                pid=child.pid,
            )

            iters = [{
                "run_id": "20260925-200000",
                "iteration": 1,
                "exit_code": 0,
                "duration_sec": 60,
                "new_commits_total": 1,
            }]

            # The classify function should refuse to classify a live run.
            # Currently it doesn't — this xfail proves the gap.
            # After the fix, classify should raise SystemExit(3) or return
            # a special signal that main() maps to exit 3.
            label, facts = collect.classify(iters, None, project_path)
            # If we get here without raising, the test fails (xfail expected)
            pytest.fail(
                f"classify should refuse a live run (exit 3), but returned "
                f"label={label}, facts={facts}"
            )
        finally:
            child.kill()
            child.wait()

    def test_live_run_writes_no_postmortem(self, isolated_env):
        """A live run must not produce a postmortem file."""
        project_path = isolated_env / "repo"
        project_path.mkdir()

        child = subprocess.Popen(["sleep", "30"])
        try:
            _write_sentinel(
                project_path,
                state="running",
                run_id="20260925-200000",
                pid=child.pid,
            )

            iters = [{
                "run_id": "20260925-200000",
                "iteration": 1,
                "exit_code": 0,
                "duration_sec": 60,
                "new_commits_total": 1,
            }]

            # After fix: classify or main should refuse and not write.
            # This xfail proves the gap exists.
            label, facts = collect.classify(iters, None, project_path)
            pytest.fail(
                "classify should refuse a live run, but it classified and "
                "would have written a postmortem"
            )
        finally:
            child.kill()
            child.wait()