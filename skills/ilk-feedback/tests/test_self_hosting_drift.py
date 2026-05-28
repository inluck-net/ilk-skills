"""Tests for self-hosting-drift classification in collect.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# -- _classify_self_hosting_drift -----------------------------------------------


def test_drift_not_self_hosting() -> None:
    """No override when is_self_hosting is False."""
    sh_facts = {"is_self_hosting": False, "log_path_drifted": True}
    result = collect._classify_self_hosting_drift("interrupted", {}, sh_facts)
    assert result is None


def test_drift_no_path_drift() -> None:
    """No override when paths haven't drifted."""
    sh_facts = {"is_self_hosting": True, "log_path_drifted": False, "launch_log_exists": True}
    result = collect._classify_self_hosting_drift("interrupted", {}, sh_facts)
    assert result is None


def test_drift_overrides_interrupted() -> None:
    """Overrides interrupted label when self-hosting + drift."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": True,
        "launch_log_exists": False,
        "preserved_archive_exists": True,
    }
    result = collect._classify_self_hosting_drift("interrupted", {"iters": 5}, sh_facts)
    assert result is not None
    label, facts = result
    assert label == "self-hosting-drift"
    assert facts["original_label"] == "interrupted"
    assert facts["iters"] == 5
    assert "drift_reason" in facts


def test_drift_overrides_stuck_no_progress() -> None:
    """Overrides stuck-no-progress when self-hosting + drift."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": True,
        "launch_log_exists": False,
        "preserved_archive_exists": False,
    }
    result = collect._classify_self_hosting_drift("stuck-no-progress", {}, sh_facts)
    assert result is not None
    assert result[0] == "self-hosting-drift"


def test_drift_overrides_api_blocked() -> None:
    """Overrides api-blocked when self-hosting + drift."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": True,
        "launch_log_exists": False,
        "preserved_archive_exists": True,
    }
    result = collect._classify_self_hosting_drift("api-blocked", {}, sh_facts)
    assert result is not None
    assert result[0] == "self-hosting-drift"


def test_drift_preserves_clean_success() -> None:
    """Does NOT override clean-success (intact evidence)."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": True,
        "launch_log_exists": False,
        "preserved_archive_exists": True,
    }
    result = collect._classify_self_hosting_drift("clean-success", {}, sh_facts)
    assert result is None


def test_drift_preserves_local_checks_stuck() -> None:
    """Does NOT override local-checks-stuck (intact evidence)."""
    sh_facts = {"is_self_hosting": True, "log_path_drifted": True}
    result = collect._classify_self_hosting_drift("local-checks-stuck", {}, sh_facts)
    assert result is None


def test_drift_preserves_budget_exhausted() -> None:
    """Does NOT override budget-exhausted (intact evidence)."""
    sh_facts = {"is_self_hosting": True, "log_path_drifted": True}
    result = collect._classify_self_hosting_drift("budget-exhausted", {}, sh_facts)
    assert result is None


def test_drift_preserves_timeout_bound() -> None:
    """Does NOT override timeout-bound (intact evidence)."""
    sh_facts = {"is_self_hosting": True, "log_path_drifted": True}
    result = collect._classify_self_hosting_drift("timeout-bound", {}, sh_facts)
    assert result is None


def test_drift_launch_log_missing_no_archive() -> None:
    """Fires when launch_log_exists is False even without preserved archive."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": False,
        "launch_log_exists": False,
        "preserved_archive_exists": False,
    }
    result = collect._classify_self_hosting_drift("interrupted", {}, sh_facts)
    assert result is not None
    assert result[0] == "self-hosting-drift"


def test_drift_preserved_archive_but_log_exists() -> None:
    """No override when log still exists even with preserved archive."""
    sh_facts = {
        "is_self_hosting": True,
        "log_path_drifted": False,
        "launch_log_exists": True,
        "preserved_archive_exists": True,
    }
    result = collect._classify_self_hosting_drift("interrupted", {}, sh_facts)
    assert result is None


# -- recommend_params for self-hosting-drift ------------------------------------


def test_recommend_params_self_hosting_drift() -> None:
    """Recommendation includes preserve + clean + relaunch guidance."""
    max_iter, timeout, rationale = collect.recommend_params(
        "self-hosting-drift", [], {"max_iterations": 20, "iteration_timeout_min": 30}
    )
    assert max_iter == 20
    assert timeout == 30
    assert "preserve" in rationale.lower() or "stable" in rationale.lower()


# -- _label_narrative for self-hosting-drift ------------------------------------


def test_label_narrative_self_hosting_drift() -> None:
    """Narrative mentions self-hosting and path drift."""
    facts = {
        "original_label": "interrupted",
        "launch_log_exists": False,
        "preserved_archive_exists": True,
        "log_path_drifted": True,
    }
    narrative = collect._label_narrative("self-hosting-drift", facts)
    assert "self-hosting" in narrative.lower()
    assert "drift" in narrative.lower()
    assert "interrupted" in narrative
