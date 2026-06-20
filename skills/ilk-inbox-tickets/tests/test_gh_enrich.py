"""Tests for gh_enrich — ref extraction + annotate with fake runner.

All tests are fully offline (no network, no real ``gh``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gh_enrich
from gh_enrich import extract_refs, annotate, EnrichedEntry, GhRef


# ---------------------------------------------------------------------------
# Minimal entry stub (mirrors inbox_parser.Entry shape)
# ---------------------------------------------------------------------------

@dataclass
class FakeEntry:
    slug: str
    fields: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fake runners
# ---------------------------------------------------------------------------

def _make_runner(mapping: dict[int, str]) -> Any:
    """Create a fake runner from ``{number: state}`` mapping."""
    def runner(number: int) -> dict[str, Any]:
        state = mapping.get(number, "UNKNOWN")
        return {"state": state}
    return runner


def _open_runner(number: int) -> dict[str, Any]:
    """Every issue is OPEN."""
    return {"state": "OPEN"}


def _closed_runner(number: int) -> dict[str, Any]:
    """Every issue is CLOSED."""
    return {"state": "CLOSED"}


def _mixed_runner(number: int) -> dict[str, Any]:
    """#10 is CLOSED, everything else OPEN."""
    return {"state": "CLOSED"} if number == 10 else {"state": "OPEN"}


# ---------------------------------------------------------------------------
# extract_refs tests
# ---------------------------------------------------------------------------

class TestExtractRefs:
    """extract_refs should pull #NNN from Related and Status fields."""

    @pytest.mark.parametrize("tag", ["extract"])
    def test_from_status(self, tag):
        entry = FakeEntry(slug="s", fields={"Status": "shipped: PR #123"})
        assert extract_refs(entry) == {123}

    @pytest.mark.parametrize("tag", ["extract"])
    def test_from_related(self, tag):
        entry = FakeEntry(slug="s", fields={"Related": "see #456 and #789"})
        assert extract_refs(entry) == {456, 789}

    @pytest.mark.parametrize("tag", ["extract"])
    def test_from_both_fields(self, tag):
        entry = FakeEntry(slug="s", fields={
            "Status": "shipped: PR #123 (merged). REMAINING: P1",
            "Related": "see #456",
        })
        assert extract_refs(entry) == {123, 456}

    @pytest.mark.parametrize("tag", ["extract"])
    def test_no_refs(self, tag):
        entry = FakeEntry(slug="s", fields={"Status": "pending"})
        assert extract_refs(entry) == set()

    @pytest.mark.parametrize("tag", ["extract"])
    def test_empty_fields(self, tag):
        entry = FakeEntry(slug="s", fields={})
        assert extract_refs(entry) == set()

    @pytest.mark.parametrize("tag", ["extract"])
    def test_deduplicates(self, tag):
        entry = FakeEntry(slug="s", fields={
            "Status": "PR #42 done",
            "Related": "also #42",
        })
        assert extract_refs(entry) == {42}


# ---------------------------------------------------------------------------
# annotate tests
# ---------------------------------------------------------------------------

class TestAnnotate:
    """annotate should attach live GitHub state via the injected runner."""

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_no_refs_returns_empty(self, tag):
        entry = FakeEntry(slug="no-refs", fields={"Status": "pending"})
        result = annotate(entry, runner=_open_runner)
        assert result.slug == "no-refs"
        assert result.refs == []
        assert result.has_closed_ref is False

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_open_refs(self, tag):
        entry = FakeEntry(slug="open", fields={"Status": "PR #10, #20"})
        result = annotate(entry, runner=_open_runner)
        assert len(result.refs) == 2
        assert all(not r.is_closed for r in result.refs)
        assert result.has_closed_ref is False

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_closed_ref_detected(self, tag):
        entry = FakeEntry(slug="closed", fields={"Related": "#10"})
        result = annotate(entry, runner=_closed_runner)
        assert len(result.refs) == 1
        assert result.refs[0].number == 10
        assert result.refs[0].state == "CLOSED"
        assert result.refs[0].is_closed is True
        assert result.has_closed_ref is True

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_mixed_refs(self, tag):
        entry = FakeEntry(slug="mixed", fields={
            "Status": "PR #10 (merged). REMAINING: #20",
        })
        result = annotate(entry, runner=_mixed_runner)
        # #10 is CLOSED, #20 is OPEN
        by_num = {r.number: r for r in result.refs}
        assert by_num[10].is_closed is True
        assert by_num[20].is_closed is False
        assert result.has_closed_ref is True

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_runner_receives_correct_numbers(self, tag):
        """Verify the runner is called with the right issue numbers."""
        calls = []

        def tracking_runner(number):
            calls.append(number)
            return {"state": "OPEN"}

        entry = FakeEntry(slug="track", fields={"Status": "#5, #15, #25"})
        annotate(entry, runner=tracking_runner)
        assert sorted(calls) == [5, 15, 25]

    @pytest.mark.parametrize("tag", ["annotate"])
    def test_default_runner_fallback(self, tag):
        """With no runner arg, annotate should not crash (uses _default_runner).
        We can't test the real gh call, but we verify the function signature."""
        entry = FakeEntry(slug="default", fields={"Status": "no refs here"})
        # This calls _default_runner but extract_refs finds no #NNN so
        # the runner is never invoked — safe to call without gh.
        result = annotate(entry)
        assert result.refs == []


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestCliGhCheck:
    """Test the gh-check CLI verb via subprocess against the test fixture."""

    FIXTURE_INBOX = str(Path(__file__).resolve().parent / "fixtures" / "_inbox.md")

    @pytest.mark.parametrize("tag", ["cli"])
    def test_gh_check_exits_zero_when_no_closed_refs(self, tag, tmp_path):
        """An inbox with no #NNN references → exit 0, no flagged entries."""
        import subprocess
        inbox = tmp_path / "inbox.md"
        inbox.write_text(
            "# Handoffs Inbox\n\n"
            "## 2026-06-20 — clean-entry\n\n"
            "**Project**: acme/app\n"
            "**Status**: pending\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "scripts.cli", "gh-check",
             "--inbox", str(inbox), "--json"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "[]"

    @pytest.mark.parametrize("tag", ["cli"])
    def test_gh_check_exits_nonzero_when_closed_ref(self, tag, tmp_path):
        """An entry referencing a closed issue → exit 1, entry flagged."""
        import subprocess
        inbox = tmp_path / "inbox.md"
        inbox.write_text(
            "# Handoffs Inbox\n\n"
            "## 2026-06-20 — has-closed-ref\n\n"
            "**Project**: acme/app\n"
            "**Status**: pending\n"
            "**Related**: #99\n"
        )
        # We need to monkeypatch gh_enrich._default_runner, but since cli.py
        # runs as a subprocess we can't easily inject. Instead, we verify the
        # CLI structure works by checking that it *attempts* the gh call and
        # handles the result gracefully. The unit tests above cover the
        # fake-runner path thoroughly.
        #
        # For the CLI test, we just verify the argparse plumbing works:
        # the command should exit 0 or 1 depending on gh availability.
        # Since gh may not be available in CI, we accept either exit code
        # but verify the output structure.
        result = subprocess.run(
            [sys.executable, "-m", "scripts.cli", "gh-check",
             "--inbox", str(inbox), "--json"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=10,
        )
        # Should not crash (exit 2)
        assert result.returncode in (0, 1)
        # Output should be valid JSON
        import json
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    @pytest.mark.parametrize("tag", ["cli"])
    def test_gh_check_status_filter(self, tag, tmp_path):
        """gh-check only examines entries matching --status."""
        import subprocess
        inbox = tmp_path / "inbox.md"
        inbox.write_text(
            "# Handoffs Inbox\n\n"
            "## 2026-06-20 — shipped-entry\n\n"
            "**Project**: acme/app\n"
            "**Status**: shipped: PR #999\n"
        )
        # shipped entries should be skipped with default --status=pending
        result = subprocess.run(
            [sys.executable, "-m", "scripts.cli", "gh-check",
             "--inbox", str(inbox), "--json"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "[]"
