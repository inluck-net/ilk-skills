"""Tests for inbox_parser — AC-1 … AC-6 coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow imports from scripts/
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import inbox_parser as p

FIXTURE = Path(__file__).parent / "fixtures" / "_inbox.md"


# ── helpers ──────────────────────────────────────────────────────────────


def _entries() -> dict[str, p.Entry]:
    return {e.slug: e for e in p.parse_inbox(FIXTURE)}


# ── AC-1: field parsing ─────────────────────────────────────────────────


class TestFieldParsing:
    def test_all_entries_parsed(self):
        es = _entries()
        assert len(es) >= 6, f"expected ≥6 entries, got {len(es)}"

    def test_plain_entry_has_project(self):
        e = _entries()["plain-pending-entry"]
        assert e.fields["Project"] == "acme/example-app"

    def test_plain_entry_has_scope(self):
        e = _entries()["plain-pending-entry"]
        assert "login form" in e.fields["Scope"]

    def test_date_parsed(self):
        e = _entries()["plain-pending-entry"]
        assert e.date == "2026-06-20"

    def test_body_not_empty(self):
        e = _entries()["plain-pending-entry"]
        assert len(e.body) > 0


# ── AC-2: Tier-2 related follow ─────────────────────────────────────────


class TestRelatedFollow:
    def test_tier2_has_related_handoff(self):
        e = _entries()["tier-two-entry"]
        assert e.related_handoff is not None

    def test_tier2_handoff_resolves_to_fixture(self):
        e = _entries()["tier-two-entry"]
        assert e.related_handoff.name == "some-thing-handoff.md"

    def test_tier1_has_no_related(self):
        e = _entries()["plain-pending-entry"]
        assert e.related_handoff is None


# ── AC-3: prose-status partial-scope ────────────────────────────────────


class TestProseStatus:
    def test_pending_state(self):
        s = p.parse_status("pending")
        assert s["state"] == "pending"
        assert s["remaining"] == ""

    def test_shipped_state(self):
        s = p.parse_status("shipped: PR #123 (merged)")
        assert s["state"] == "shipped"

    def test_in_progress_state(self):
        s = p.parse_status("in-progress")
        assert s["state"] == "in-progress"

    def test_blocked_state(self):
        s = p.parse_status("blocked: waiting on API key")
        assert s["state"] == "blocked"

    def test_remaining_extracted(self):
        text = "shipped: PR #123 (merged). REMAINING: P1 add refresh-token rotation, P2 update docs"
        s = p.parse_status(text)
        assert s["state"] == "shipped"
        assert "P1" in s["remaining"]
        assert "refresh-token" in s["remaining"]

    def test_mid_flight_entry_remaining(self):
        e = _entries()["mid-flight-entry"]
        assert e.status["state"] == "shipped"
        assert "P1" in e.status["remaining"]
