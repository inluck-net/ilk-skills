"""Tests for postmortem integrity-violation awareness — AC-3, AC-4.

Verifies that:
- _label_narrative and recommend_params name a gate edit as what it is
  (not "marked shipped while its gate was red") when integrity_violations
  holds a gates-edited entry and no red-ship entry.
- The existing red-ship text is unchanged when violations are absent or
  hold a red-ship entry.
- _integrity_violations reads the launcher log and returns structured data.

Import idiom: same as test_collect_narrative.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400


# -- base text for ship_integrity_violation (current behavior) ----------------

_LABEL_NARRATIVE_BASE = (
    "stopped: a sub-plan was marked shipped while its gate was red; "
    "the ship was reverted and the master parked."
)
_RECOMMEND_PARAMS_BASE = (
    "a sub-plan was marked shipped while its gate was red; "
    "the ship was reverted. Fix the gate or the sub-plan, then relaunch."
)


# -- AC-3: _label_narrative kind-aware text -----------------------------------

class TestLabelNarrativeKindAware:
    """_label_narrative names the violation kind for gates-edited."""

    def test_gates_edited_names_the_gate(self):
        """gates-edited → text contains 'gates' and the slug, not 'marked shipped'."""
        facts = {
            "route": "ship_integrity_violation",
            "integrity_violations": [{"kind": "gates-edited", "slug": "my-slug"}],
        }
        text = collect._label_narrative("shipped-unverified", facts)
        assert "gates" in text, f"expected 'gates' in: {text}"
        assert "my-slug" in text, f"expected 'my-slug' in: {text}"
        assert "marked shipped" not in text, f"unexpected 'marked shipped' in: {text}"
        assert "gate was red" not in text, f"unexpected 'gate was red' in: {text}"

    def test_red_ship_keeps_base_text(self):
        """red-ship → same text as base (unchanged behavior)."""
        facts = {
            "route": "ship_integrity_violation",
            "integrity_violations": [{"kind": "red-ship", "slug": "s", "reason": "x"}],
        }
        text = collect._label_narrative("shipped-unverified", facts)
        assert text == _LABEL_NARRATIVE_BASE

    def test_absent_violations_keeps_base_text(self):
        """No integrity_violations → same text as base."""
        facts = {"route": "ship_integrity_violation"}
        text = collect._label_narrative("shipped-unverified", facts)
        assert text == _LABEL_NARRATIVE_BASE

    def test_empty_violations_keeps_base_text(self):
        """Empty violations list → same text as base."""
        facts = {
            "route": "ship_integrity_violation",
            "integrity_violations": [],
        }
        text = collect._label_narrative("shipped-unverified", facts)
        assert text == _LABEL_NARRATIVE_BASE


# -- AC-3: recommend_params kind-aware text -----------------------------------

class TestRecommendParamsKindAware:
    """recommend_params names the violation kind for gates-edited."""

    def test_gates_edited_names_the_gate(self):
        """gates-edited → rationale contains 'gates' and the slug, not 'marked shipped'."""
        facts = {
            "route": "ship_integrity_violation",
            "integrity_violations": [{"kind": "gates-edited", "slug": "s"}],
        }
        _, _, rationale = collect.recommend_params(
            "shipped-unverified", [], None, facts,
        )
        assert "gates" in rationale, f"expected 'gates' in: {rationale}"
        assert "s" in rationale, f"expected 's' in: {rationale}"
        assert "marked shipped" not in rationale, f"unexpected 'marked shipped' in: {rationale}"
        assert "gate was red" not in rationale, f"unexpected 'gate was red' in: {rationale}"

    def test_red_ship_keeps_base_text(self):
        """red-ship → same rationale as base."""
        facts = {
            "route": "ship_integrity_violation",
            "integrity_violations": [{"kind": "red-ship", "slug": "s", "reason": "x"}],
        }
        _, _, rationale = collect.recommend_params(
            "shipped-unverified", [], None, facts,
        )
        assert rationale == _RECOMMEND_PARAMS_BASE

    def test_absent_violations_keeps_base_text(self):
        """No integrity_violations → same rationale as base."""
        facts = {"route": "ship_integrity_violation"}
        _, _, rationale = collect.recommend_params(
            "shipped-unverified", [], None, facts,
        )
        assert rationale == _RECOMMEND_PARAMS_BASE


# -- AC-4: _integrity_violations wiring ---------------------------------------

class TestIntegrityViolationsWiring:
    """_integrity_violations reads the launcher log and returns structured data."""

    def test_finds_log_and_returns_gates_edited(self, tmp_path: Path):
        """_integrity_violations finds a log under external_logs_dir and returns
        the gates-edited entry."""
        # Set up a launcher log with the incident's VIOLATION line
        log_dir = tmp_path / "logs" / "launcher"
        log_dir.mkdir(parents=True)
        log = log_dir / "users-chad-projects-keyreply-kira-cloudflare-1851f78-20261006-040012.log"
        log.write_text(
            "  ! [ship-integrity VIOLATION] the worker changed gates for ticket-attachment-projector; restored to the pre-dispatch snapshot (rc=3):\n"
            "! [gates] ticket-attachment-projector: gates changed; restored to snapshot\n"
            "=== Loop ended: ship_integrity_violation ===\n",
            encoding="utf-8",
        )

        # Monkeypatch external_logs_dir to return our tmp_path / "logs"
        with patch("collect.external_logs_dir", return_value=tmp_path / "logs"):
            result = collect._integrity_violations(tmp_path, "20261006-040012")

        assert result == [{"kind": "gates-edited", "slug": "ticket-attachment-projector"}]

    def test_run_id_none_returns_empty(self, tmp_path: Path):
        """With run_id=None, returns []."""
        result = collect._integrity_violations(tmp_path, None)
        assert result == []

    def test_no_log_returns_empty(self, tmp_path: Path):
        """When no launcher log exists, returns []."""
        with patch("collect.external_logs_dir", return_value=tmp_path / "logs"):
            result = collect._integrity_violations(tmp_path, "nonexistent-run")
        assert result == []