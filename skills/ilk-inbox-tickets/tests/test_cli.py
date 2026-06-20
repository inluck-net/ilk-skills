"""Tests for the ilk-inbox-tickets CLI (cli.py).

Covers AC-1…AC-6 from sub-plan 2026-06-20-inbox-cli.
All mutating tests operate on tmp-copied fixtures, never the real inbox.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cli  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INBOX_FIXTURE = FIXTURES / "_inbox.md"
REGISTRY_FIXTURE = FIXTURES / "inbox-projects.json"
HANDOFF_FIXTURE = FIXTURES / "some-thing-handoff.md"

# The fixture registry maps "acme/example-app" to /tmp/acme-example-app
# and "~/.ilk-data templates" as not_plannable.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_inbox(tmp_path):
    """Copy the fixture inbox into a tmp dir; return the path."""
    dest = tmp_path / "_inbox.md"
    shutil.copy(INBOX_FIXTURE, dest)
    return dest


@pytest.fixture()
def tmp_archive(tmp_path):
    """Return a tmp path for the archive file (starts empty)."""
    return tmp_path / "_inbox-archive.md"


def _run_cli(capsys, argv):
    """Run cli.main and return captured stdout."""
    cli.main(argv)
    return capsys.readouterr().out


def _run_cli_json(capsys, argv):
    """Run cli.main and return parsed JSON from stdout."""
    out = _run_cli(capsys, argv)
    return json.loads(out)


# ---------------------------------------------------------------------------
# AC-1: list --all --json groups by resolved project, excludes ineligible
# ---------------------------------------------------------------------------

class TestListAll:
    def test_groups_by_project(self, capsys):
        out = _run_cli_json(capsys, [
            "list", "--all", "--json",
            "--inbox", str(INBOX_FIXTURE),
            "--registry", str(REGISTRY_FIXTURE),
        ])
        assert "/tmp/acme-example-app" in out
        slugs = [e["slug"] for e in out["/tmp/acme-example-app"]]
        assert "plain-pending-entry" in slugs
        # Proposal / research entries excluded
        assert "proposal-entry" not in slugs
        assert "research-entry" not in slugs

    def test_excludes_unmapped_and_not_plannable(self, capsys):
        out = _run_cli_json(capsys, [
            "list", "--all", "--json",
            "--inbox", str(INBOX_FIXTURE),
            "--registry", str(REGISTRY_FIXTURE),
        ])
        all_slugs = []
        for entries in out.values():
            all_slugs.extend(e["slug"] for e in entries)
        assert "unmapped-project-entry" not in all_slugs
        assert "not-plannable-entry" not in all_slugs

    def test_excludes_non_pending_by_default(self, capsys):
        out = _run_cli_json(capsys, [
            "list", "--all", "--json",
            "--inbox", str(INBOX_FIXTURE),
            "--registry", str(REGISTRY_FIXTURE),
        ])
        all_slugs = []
        for entries in out.values():
            all_slugs.extend(e["slug"] for e in entries)
        # mid-flight is shipped, tier-two is in-progress
        assert "mid-flight-entry" not in all_slugs
        assert "tier-two-entry" not in all_slugs


# ---------------------------------------------------------------------------
# AC-2: list --project <slug> prints only that project's eligible entries
# ---------------------------------------------------------------------------

class TestListProject:
    def test_filters_to_project(self, capsys):
        out = _run_cli_json(capsys, [
            "list", "--project", "acme/example-app", "--json",
            "--inbox", str(INBOX_FIXTURE),
            "--registry", str(REGISTRY_FIXTURE),
        ])
        slugs = [e["slug"] for e in out]
        assert "plain-pending-entry" in slugs
        assert "proposal-entry" not in slugs

    def test_unknown_project_exits_error(self):
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "list", "--project", "unknown/x", "--json",
                "--inbox", str(INBOX_FIXTURE),
                "--registry", str(REGISTRY_FIXTURE),
            ])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# AC-3: show <slug> prints fields/body, Tier-2 inlines handoff
# ---------------------------------------------------------------------------

class TestShow:
    def test_show_plain_entry(self, capsys):
        out = _run_cli_json(capsys, [
            "show", "plain-pending-entry",
            "--inbox", str(INBOX_FIXTURE),
        ])
        assert out["slug"] == "plain-pending-entry"
        assert out["fields"]["Project"] == "acme/example-app"
        assert "related_handoff" not in out

    def test_show_tier_two_inlines_handoff(self, capsys):
        out = _run_cli_json(capsys, [
            "show", "tier-two-entry",
            "--inbox", str(INBOX_FIXTURE),
        ])
        assert out["slug"] == "tier-two-entry"
        assert "related_handoff" in out
        assert "related_handoff_content" in out
        assert "some-thing handoff" in out["related_handoff_content"]

    def test_show_unknown_slug_exits_error(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["show", "nonexistent", "--inbox", str(INBOX_FIXTURE)])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# AC-4: update rewrites Status + adds Plan line, leaves others untouched
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_status_and_plan(self, capsys, tmp_inbox):
        cli.main([
            "update", "plain-pending-entry",
            "--status", "in-progress",
            "--plan", "/tmp/plan.md",
            "--inbox", str(tmp_inbox),
        ])
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["ok"] is True

        # Re-parse to confirm
        text = tmp_inbox.read_text()
        assert "**Status**: in-progress" in text
        assert "**Plan**: /tmp/plan.md" in text

    def test_update_leaves_other_entries_unchanged(self, tmp_inbox):
        orig = INBOX_FIXTURE.read_text()
        cli.main([
            "update", "plain-pending-entry",
            "--status", "blocked: waiting on design",
            "--inbox", str(tmp_inbox),
        ])
        new_text = tmp_inbox.read_text()

        # proposal-entry block should be byte-identical
        import re
        orig_proposal = re.search(
            r"## 2026-06-19 — proposal-entry.*?(?=## |\Z)",
            orig, re.DOTALL,
        ).group()
        new_proposal = re.search(
            r"## 2026-06-19 — proposal-entry.*?(?=## |\Z)",
            new_text, re.DOTALL,
        ).group()
        assert orig_proposal == new_proposal

    def test_update_refreshes_existing_plan_line(self, tmp_inbox):
        # First update adds Plan
        cli.main([
            "update", "plain-pending-entry",
            "--status", "in-progress",
            "--plan", "/tmp/old-plan.md",
            "--inbox", str(tmp_inbox),
        ])
        # Second update refreshes Plan
        cli.main([
            "update", "plain-pending-entry",
            "--status", "in-progress",
            "--plan", "/tmp/new-plan.md",
            "--inbox", str(tmp_inbox),
        ])
        text = tmp_inbox.read_text()
        assert "**Plan**: /tmp/new-plan.md" in text
        assert "**Plan**: /tmp/old-plan.md" not in text

    def test_update_unknown_slug_exits_error(self, tmp_inbox):
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "update", "nonexistent",
                "--status", "in-progress",
                "--inbox", str(tmp_inbox),
            ])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# AC-5: archive moves entry from inbox to archive
# ---------------------------------------------------------------------------

class TestArchive:
    def test_archive_removes_from_inbox(self, tmp_inbox, tmp_archive):
        cli.main([
            "archive", "plain-pending-entry",
            "--inbox", str(tmp_inbox),
            "--archive", str(tmp_archive),
        ])
        text = tmp_inbox.read_text()
        assert "plain-pending-entry" not in text
        # Other entries still present
        assert "proposal-entry" in text

    def test_archive_appends_to_archive(self, tmp_inbox, tmp_archive):
        cli.main([
            "archive", "plain-pending-entry",
            "--inbox", str(tmp_inbox),
            "--archive", str(tmp_archive),
        ])
        archive_text = tmp_archive.read_text()
        assert "plain-pending-entry" in archive_text
        assert "Fix the login form validation" in archive_text

    def test_archive_unknown_slug_exits_error(self, tmp_inbox, tmp_archive):
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "archive", "nonexistent",
                "--inbox", str(tmp_inbox),
                "--archive", str(tmp_archive),
            ])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# AC-6: resolve lists unmapped entries, exit 1 when non-empty
# ---------------------------------------------------------------------------

class TestResolve:
    def test_resolve_finds_unmapped(self):
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "resolve", "--json",
                "--inbox", str(INBOX_FIXTURE),
                "--registry", str(REGISTRY_FIXTURE),
            ])
        assert exc.value.code == 1

    def test_resolve_json_lists_unmapped_slugs(self, capsys):
        out = None
        try:
            cli.main([
                "resolve", "--json",
                "--inbox", str(INBOX_FIXTURE),
                "--registry", str(REGISTRY_FIXTURE),
            ])
        except SystemExit:
            pass
        out = capsys.readouterr().out
        result = json.loads(out)
        slugs = [e["slug"] for e in result]
        assert "unmapped-project-entry" in slugs

    def test_resolve_clean_inbox_exits_zero(self, capsys, tmp_inbox, tmp_path):
        """An inbox with only resolved projects should exit 0."""
        # Remove the unmapped entry by archiving it first
        archive = tmp_path / "_archive.md"
        cli.main([
            "archive", "unmapped-project-entry",
            "--inbox", str(tmp_inbox),
            "--archive", str(archive),
        ])
        cli.main([
            "archive", "not-plannable-entry",
            "--inbox", str(tmp_inbox),
            "--archive", str(archive),
        ])
        # Now resolve should exit 0
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "resolve",
                "--inbox", str(tmp_inbox),
                "--registry", str(REGISTRY_FIXTURE),
            ])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "All entries have resolved projects" in out
