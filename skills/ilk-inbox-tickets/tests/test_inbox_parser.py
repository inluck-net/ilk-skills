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
import project_registry as r

FIXTURE = Path(__file__).parent / "fixtures" / "_inbox.md"

# A test registry matching the fixture entries
_TEST_REGISTRY = {
    "projects": {
        "acme/example-app": {"path": "/tmp/acme-example"},
        "~/.ilk-data templates": {"not_plannable": True},
    }
}


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


# ── AC-4: project registry resolution ───────────────────────────────────


class TestProjectRegistry:
    def test_resolve_registered_returns_path(self):
        result = r.resolve("acme/example-app", _TEST_REGISTRY)
        assert result == "/tmp/acme-example"

    def test_resolve_not_plannable(self):
        result = r.resolve("~/.ilk-data templates", _TEST_REGISTRY)
        assert result is r.NOT_PLANNABLE

    def test_resolve_unmapped(self):
        result = r.resolve("unknown-org/new-repo", _TEST_REGISTRY)
        assert result is r.UNRESOLVED

    def test_resolve_slug_with_path_suffix(self):
        """Strings like 'slug (path)' try the leading slug token."""
        reg = {"projects": {"my-org/repo": {"path": "/tmp/repo"}}}
        assert r.resolve("my-org/repo (/extra/path)", reg) == "/tmp/repo"

    def test_needs_mapping_returns_unresolved(self):
        es = list(_entries().values())
        unmapped = r.needs_mapping(es, _TEST_REGISTRY)
        slugs = [e.slug for e in unmapped]
        assert "unmapped-project-entry" in slugs
        # registered and not-plannable entries should NOT appear
        assert "plain-pending-entry" not in slugs
        assert "not-plannable-entry" not in slugs


# ── AC-5: eligibility predicate ──────────────────────────────────────────


class TestEligibility:
    def test_plain_entry_is_eligible(self):
        e = _entries()["plain-pending-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is True

    def test_proposal_entry_is_ineligible(self):
        e = _entries()["proposal-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is False

    def test_research_entry_is_ineligible(self):
        e = _entries()["research-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is False

    def test_unmapped_project_is_ineligible(self):
        e = _entries()["unmapped-project-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is False

    def test_not_plannable_project_is_ineligible(self):
        e = _entries()["not-plannable-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is False

    def test_mid_flight_entry_is_eligible(self):
        """Mid-flight entries with remaining scope are still eligible."""
        e = _entries()["mid-flight-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is True

    def test_tier2_entry_is_eligible(self):
        """Tier-2 entries with a handoff doc are eligible if project resolves."""
        e = _entries()["tier-two-entry"]
        assert p.is_ilk_eligible(e, _TEST_REGISTRY) is True


# ── AC-6: cross-project grouping ─────────────────────────────────────────


class TestGrouping:
    def test_groups_by_resolved_project(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        assert "/tmp/acme-example" in grouped

    def test_includes_only_pending_by_default(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        for proj, entries in grouped.items():
            for e in entries:
                assert e.status.get("state") == "pending"

    def test_excludes_proposal(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        for entries in grouped.values():
            slugs = [e.slug for e in entries]
            assert "proposal-entry" not in slugs

    def test_excludes_research(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        for entries in grouped.values():
            slugs = [e.slug for e in entries]
            assert "research-entry" not in slugs

    def test_excludes_unmapped(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        for entries in grouped.values():
            slugs = [e.slug for e in entries]
            assert "unmapped-project-entry" not in slugs

    def test_excludes_not_plannable(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY)
        for entries in grouped.values():
            slugs = [e.slug for e in entries]
            assert "not-plannable-entry" not in slugs

    def test_status_none_includes_all_states(self):
        es = list(_entries().values())
        grouped = p.group_by_project(es, _TEST_REGISTRY, status=None)
        all_slugs = [e.slug for entries in grouped.values() for e in entries]
        # mid-flight and tier-2 are now included
        assert "mid-flight-entry" in all_slugs
        assert "tier-two-entry" in all_slugs
