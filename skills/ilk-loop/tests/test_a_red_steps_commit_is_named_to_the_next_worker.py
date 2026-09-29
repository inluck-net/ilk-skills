"""Pin that a red step's commit is named in the revert notice.

Part of sub-plan ``a-red-steps-commit-is-named-to-the-next-worker``
(MASTER-2026-09-29g).

Four acceptance criteria, verified against the revert_notice helper
and the runner's ship-integrity revert site.  The tests use synthetic
revert rows and prompt assembly helpers — no loop runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_revert_row(
    slug: str,
    ship_commit: str | None = None,
    reason: str = "ship_integrity",
    from_status: str = "shipped",
    to_status: str = "in-progress",
    from_step: int | None = None,
    to_step: int | None = None,
    run_id: str = "test-run",
    iteration: int = 1,
    timestamp: str = "2026-09-29T10:00:00+0800",
    site: str = "integrity",
    red_step: int | None = None,
    red_step_commits: list[str] | None = None,
) -> dict:
    """Build a synthetic revert row, optionally with red-step fields."""
    row: dict = {
        "slug": slug,
        "ship_commit": ship_commit,
        "reason": reason,
        "from_status": from_status,
        "to_status": to_status,
        "from_step": from_step,
        "to_step": to_step,
        "run_id": run_id,
        "iteration": iteration,
        "timestamp": timestamp,
        "site": site,
    }
    if red_step is not None:
        row["red_step"] = red_step
    if red_step_commits is not None:
        row["red_step_commits"] = red_step_commits
    return row


# ── AC-1: revert row records red_step and red_step_commits ──────────────────


class TestRevertRowRecordsRedStep:
    """AC-1: when ship-integrity reverts because a step's gate was red,
    the revert row also records ``red_step`` (int) and ``red_step_commits``:
    the SHAs in the iteration's range carrying
    ``[plan:<slug>#step-<red_step>]``, or on a shared remote every commit
    in the iteration's range, marked
    ``"attribution": "unfiltered-no-trailers"``."""

    def test_row_carrying_red_step_has_both_fields(self, tmp_path):
        """A revert row from a red-step gate must carry red_step and
        red_step_commits."""
        import revert_notice as rn

        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        rn.append_revert_row(
            reverts_file,
            slug="my-slug",
            ship_commit="abc1234567890",
            reason="ship_integrity",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=None,
            run_id="test-run",
            iteration=1,
            timestamp="2026-09-29T10:00:00+0800",
            site="integrity",
            red_step=1,
            red_step_commits=["abc1234", "def5678"],
        )

        rows = rn.read_revert_rows(reverts_file)
        assert len(rows) == 1
        row = rows[0]
        assert row["red_step"] == 1
        assert row["red_step_commits"] == ["abc1234", "def5678"]

    def test_row_without_red_step_has_no_fields(self, tmp_path):
        """A revert row from a non-red-step revert (e.g. one-ship) must
        NOT carry red_step or red_step_commits."""
        import revert_notice as rn

        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        rn.append_revert_row(
            reverts_file,
            slug="my-slug",
            ship_commit="abc1234567890",
            reason="one_ship_enforcement",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=2,
            run_id="test-run",
            iteration=1,
            timestamp="2026-09-29T10:00:00+0800",
            site="one-ship",
        )

        rows = rn.read_revert_rows(reverts_file)
        assert len(rows) == 1
        row = rows[0]
        assert "red_step" not in row
        assert "red_step_commits" not in row

    def test_red_step_commits_with_attribution_on_shared_remote(self, tmp_path):
        """On a shared remote (no trailers), the row carries every commit
        in the iteration's range, marked
        ``"attribution": "unfiltered-no-trailers"``."""
        import revert_notice as rn

        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        rn.append_revert_row(
            reverts_file,
            slug="my-slug",
            ship_commit="abc1234567890",
            reason="ship_integrity",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=None,
            run_id="test-run",
            iteration=1,
            timestamp="2026-09-29T10:00:00+0800",
            site="integrity",
            red_step=1,
            red_step_commits=["aaa1111", "bbb2222", "ccc3333"],
            attribution="unfiltered-no-trailers",
        )

        rows = rn.read_revert_rows(reverts_file)
        assert len(rows) == 1
        row = rows[0]
        assert row["red_step_commits"] == ["aaa1111", "bbb2222", "ccc3333"]
        assert row["attribution"] == "unfiltered-no-trailers"


# ── AC-2: notice names the red step's commit ────────────────────────────────


class TestNoticeNamesRedStepCommit:
    """AC-2: ``assemble_revert_notice`` adds, per such row::

        commit <sha7> claims <slug> step <N>, but that step's gate was
        red — its message is not a verdict; re-run the gate before
        relying on it.
    """

    def test_notice_includes_red_step_line(self):
        """A revert row with red_step and red_step_commits produces a
        notice line naming the commit and the red step."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "my-slug",
                ship_commit="abc1234567890",
                red_step=1,
                red_step_commits=["deadbee1234"],
            ),
        ]
        shipped_slugs: set[str] = set()

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        assert "deadbee" in notice, "notice should name the red step's commit (sha7)"
        assert "step 1" in notice, "notice should name the red step number"
        assert "gate was red" in notice, "notice should say the gate was red"

    def test_notice_includes_multiple_red_step_commits(self):
        """When multiple commits claim the red step, all sha7s appear."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "my-slug",
                ship_commit="abc1234567890",
                red_step=2,
                red_step_commits=["aaa1111aaa", "bbb2222bbb"],
            ),
        ]
        shipped_slugs: set[str] = set()

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        assert "aaa1111" in notice
        assert "bbb2222" in notice

    def test_notice_without_red_step_uses_original_template(self):
        """A revert row without red_step (e.g. one-ship) uses the
        original notice template unchanged."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "my-slug",
                ship_commit="abc1234567890",
                reason="one_ship_enforcement",
                site="one-ship",
            ),
        ]
        shipped_slugs: set[str] = set()

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        assert "REVERTED BY THE RUNNER" in notice
        assert "abc1234" in notice
        # The red-step specific line must NOT appear.
        assert "gate was red" not in notice


# ── AC-3: notice shown only while slug is still not shipped ──────────────────


class TestNoticeShownOnlyWhileNotShipped:
    """AC-3: the notice is shown only while the slug is still not shipped
    (today's rule)."""

    def test_red_step_notice_excluded_when_shipped(self):
        """A revert row with red_step whose slug is now shipped does NOT
        appear in the notice."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "my-slug",
                ship_commit="abc1234567890",
                red_step=1,
                red_step_commits=["deadbee1234"],
            ),
        ]
        shipped_slugs = {"my-slug"}

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        assert "my-slug" not in notice or notice == ""

    def test_red_step_notice_included_when_not_shipped(self):
        """A revert row with red_step whose slug is still not shipped
        appears in the notice."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "my-slug",
                ship_commit="abc1234567890",
                red_step=1,
                red_step_commits=["deadbee1234"],
            ),
        ]
        shipped_slugs: set[str] = set()

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        assert "my-slug" in notice
        assert "deadbee" in notice


# ── AC-4: unchanged rows render as today ─────────────────────────────────────


class TestUnchangedRowsRenderAsToday:
    """AC-4: rows without the new fields render as today, and no commit
    is rewritten or reverted.  Git history is never edited.

    These are green controls — they pass today because the fields are
    optional and the original template is unchanged."""

    def test_original_template_unchanged_for_non_red_step_rows(self):
        """A revert row without red_step produces the original notice."""
        import revert_notice as rn

        revert_rows = [
            _make_revert_row(
                "alpha",
                ship_commit="abc1234567890",
                reason="ship_integrity",
                site="integrity",
            ),
        ]
        shipped_slugs: set[str] = set()

        notice = rn.assemble_revert_notice(revert_rows, shipped_slugs)
        expected = (
            "REVERTED BY THE RUNNER: alpha — "
            "#ship abc1234 was undone (ship_integrity). "
            "This is not a desync: do not set it shipped by hand; "
            "make its final gate pass."
        )
        assert notice == expected

    def test_row_without_red_step_field_is_still_readable(self, tmp_path):
        """A row written by the old writer (no red_step field) is still
        read and rendered without error."""
        import revert_notice as rn

        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        # Write a row in the old format (no red_step fields).
        row = _make_revert_row("old-slug", ship_commit="abc1234567890")
        reverts_file.parent.mkdir(parents=True, exist_ok=True)
        with open(reverts_file, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        rows = rn.read_revert_rows(reverts_file)
        assert len(rows) == 1
        assert "red_step" not in rows[0]
        # Assembly must not raise.
        notice = rn.assemble_revert_notice(rows, set())
        assert "old-slug" in notice

    def test_no_commit_is_rewritten(self):
        """The mechanism is purely additive — no test here touches git
        history.  This pin exists to mark the acceptance criterion as
        verified by inspection: append_revert_row and
        assemble_revert_notice have no git calls."""
        import revert_notice as rn
        import inspect

        # Verify the functions have no subprocess/git calls.
        source = inspect.getsource(rn)
        assert "git " not in source, (
            "revert_notice.py must not call git — "
            "git history is never edited"
        )