"""Test: block-style YAML front-matter lists are read, not silently dropped.

The defect this closes produced a WRONG RESULT and REPORTED CLEAN. Written in
block form::

    depends_on:
      - alpha

every reader skipped the bullet line and kept the key's empty inline value, so
``_parse_depends_on("")`` returned ``[]`` and the sub-plan read as
dependency-free. The loop then executed sub-plans in the wrong order, every
step committed, and nothing warned (measured 2026-09-03 on
MASTER-2026-09-03-gate-identity-and-driver-bookkeeping: all three sub-plans
parsed as dependency-free and sub-plan 2 started while sub-plan 1 was
``blocked``).

The fix has two halves and they must land together:

  1. the readers — ``loop_status.parse_frontmatter`` and
     ``plan_status.parse_frontmatter`` — normalise a block sequence to YAML
     flow form (``"[alpha, beta]"``), keeping their ``dict[str, str]``
     contract;
  2. ``plan_status._parse_depends_on`` strips the ``- `` bullet from each
     item, so it is correct on raw block text whichever reader hands it over.

``TestBulletStripHalf`` is the half-landing guard: it exercises half 2
directly, so fixing only the readers leaves it RED. Without it, a reader that
captured bullets verbatim would parse ``['- alpha']`` — a slug that can never
match a sibling — converting a silently-ignored dependency into a permanently
wedged sub-plan.

Note on imports: this file deliberately does NOT pop entries out of
``sys.modules``. Replacing a live module object is the leading hypothesis for
the scoped gate's order-dependence (a test's already-bound function closes over
the old module while ``patch`` targets the new one), so a new file should not
add another instance of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from loop_status import parse_frontmatter as loop_parse_frontmatter  # noqa: E402
from plan_status import (  # noqa: E402
    _parse_depends_on,
    parse_frontmatter as plan_parse_frontmatter,
    subplan_is_runnable,
)

# Both readers sit on the same live path — loop_status.py:332 builds the
# front-matter dict that loop_status.py:359 hands to subplan_is_runnable, and
# plan_status.py:330/345 does the same for master_is_drainable — so every
# reader test runs against both.
PARSERS = pytest.mark.parametrize(
    "parse",
    [loop_parse_frontmatter, plan_parse_frontmatter],
    ids=["loop_status", "plan_status"],
)


def _plan(body: str) -> str:
    return f"---\n{body}\n---\n\n# a sub-plan\n"


# ── half 1: the readers ──────────────────────────────────────────────────────


class TestReaderHalf:
    @PARSERS
    def test_block_list_becomes_flow_form(self, parse) -> None:
        fm = parse(_plan("status: pending\ndepends_on:\n  - alpha\n  - beta-gamma"))
        assert fm["depends_on"] == "[alpha, beta-gamma]"

    @PARSERS
    def test_block_list_parses_to_the_same_slugs_as_inline(self, parse) -> None:
        block = parse(_plan("depends_on:\n  - alpha\n  - beta-gamma"))
        inline = parse(_plan("depends_on: [alpha, beta-gamma]"))
        assert _parse_depends_on(block["depends_on"]) == ["alpha", "beta-gamma"]
        assert _parse_depends_on(block["depends_on"]) == _parse_depends_on(
            inline["depends_on"]
        )

    @PARSERS
    def test_single_item_block_list(self, parse) -> None:
        fm = parse(_plan("depends_on:\n  - queue-drain-past-blocked"))
        assert _parse_depends_on(fm["depends_on"]) == ["queue-drain-past-blocked"]

    @PARSERS
    def test_quoted_items_are_unquoted(self, parse) -> None:
        fm = parse(_plan('scope_paths:\n  - "portal/**"\n  - \'apps/**\''))
        assert fm["scope_paths"] == "[portal/**, apps/**]"

    @PARSERS
    def test_a_blank_or_comment_line_does_not_truncate_the_list(self, parse) -> None:
        # Ending the sequence on a blank line would silently drop the rest,
        # which is the class of bug this parser is fixing.
        fm = parse(_plan("depends_on:\n  - alpha\n\n  # a note\n  - beta"))
        assert _parse_depends_on(fm["depends_on"]) == ["alpha", "beta"]

    @PARSERS
    def test_a_following_key_ends_the_list(self, parse) -> None:
        fm = parse(_plan("depends_on:\n  - alpha\nstatus: pending\nrepo: acme"))
        assert _parse_depends_on(fm["depends_on"]) == ["alpha"]
        assert fm["status"] == "pending"
        assert fm["repo"] == "acme"

    @PARSERS
    def test_key_with_no_value_and_no_bullets_still_reads_empty(self, parse) -> None:
        # Callers treat a bare key as absent; that must not change.
        fm = parse(_plan("depends_on:\nstatus: pending"))
        assert fm["depends_on"] == ""
        assert _parse_depends_on(fm["depends_on"]) == []

    @PARSERS
    def test_bullets_with_no_preceding_key_are_ignored(self, parse) -> None:
        fm = parse(_plan("  - stray\nstatus: pending"))
        assert fm == {"status": "pending"}

    @PARSERS
    def test_scalars_and_inline_lists_are_unchanged(self, parse) -> None:
        fm = parse(
            _plan(
                "status: in-progress\n"
                "current_step: 3\n"
                "depends_on: [alpha, beta]\n"
                "verification_tier: loop-verified"
            )
        )
        assert fm["status"] == "in-progress"
        assert fm["current_step"] == "3"
        assert fm["depends_on"] == "[alpha, beta]"
        assert fm["verification_tier"] == "loop-verified"

    def test_plan_status_still_strips_inline_comments_on_items(self) -> None:
        # plan_status is the parser that strips inline comments from scalars;
        # its block items follow the same rule. loop_status strips neither.
        fm = plan_parse_frontmatter(_plan("depends_on:\n  - alpha  # the first one"))
        assert _parse_depends_on(fm["depends_on"]) == ["alpha"]


# ── half 2: the bullet strip (half-landing guard) ────────────────────────────


class TestBulletStripHalf:
    """RED if only the readers were fixed.

    These call ``_parse_depends_on`` on raw block text, so they do not depend
    on any reader's normalisation.
    """

    def test_raw_block_text_multi_item(self) -> None:
        assert _parse_depends_on("- alpha\n- beta") == ["alpha", "beta"]

    def test_raw_block_text_indented(self) -> None:
        assert _parse_depends_on("  - alpha\n  - beta-gamma") == ["alpha", "beta-gamma"]

    def test_raw_block_text_single_item(self) -> None:
        assert _parse_depends_on("- alpha") == ["alpha"]

    def test_bulleted_item_inside_flow_brackets(self) -> None:
        assert _parse_depends_on("[- alpha]") == ["alpha"]

    def test_a_bulleted_slug_never_reads_as_a_literal_dash_slug(self) -> None:
        # The wedge: ['- alpha'] can never match a sibling slug, so the
        # dependent would read as blocked forever.
        assert "- alpha" not in _parse_depends_on("- alpha")

    def test_previous_shapes_still_parse(self) -> None:
        assert _parse_depends_on("[alpha, beta-gamma]") == ["alpha", "beta-gamma"]
        assert _parse_depends_on('["alpha", "beta"]') == ["alpha", "beta"]
        assert _parse_depends_on("alpha, beta") == ["alpha", "beta"]
        assert _parse_depends_on("alpha") == ["alpha"]
        assert _parse_depends_on("[]") == []
        assert _parse_depends_on("") == []


# ── both halves: the wrong-result-reports-clean case ─────────────────────────


class TestBlockFormDependencyIsHonoured:
    """A block-form dependency must gate runnability exactly like an inline one.

    Pre-fix, the first assertion of each test failed: ``depends_on`` read as
    ``[]``, the sub-plan was runnable, and the loop ran it out of order.
    """

    @PARSERS
    def test_pending_dependency_blocks(self, parse) -> None:
        fm = parse(_plan("status: pending\ndepends_on:\n  - alpha"))
        assert subplan_is_runnable(fm, {"alpha": "pending"}) is False

    @PARSERS
    def test_blocked_dependency_blocks(self, parse) -> None:
        fm = parse(_plan("status: pending\ndepends_on:\n  - alpha"))
        assert subplan_is_runnable(fm, {"alpha": "blocked"}) is False

    @PARSERS
    def test_shipped_dependency_releases(self, parse) -> None:
        fm = parse(_plan("status: pending\ndepends_on:\n  - alpha"))
        assert subplan_is_runnable(fm, {"alpha": "shipped"}) is True

    @PARSERS
    def test_one_unshipped_of_several_blocks(self, parse) -> None:
        fm = parse(_plan("status: pending\ndepends_on:\n  - alpha\n  - beta"))
        assert subplan_is_runnable(fm, {"alpha": "shipped", "beta": "pending"}) is False
        assert subplan_is_runnable(fm, {"alpha": "shipped", "beta": "shipped"}) is True

    @PARSERS
    def test_block_and_inline_agree_on_runnability(self, parse) -> None:
        block = parse(_plan("status: pending\ndepends_on:\n  - alpha"))
        inline = parse(_plan("status: pending\ndepends_on: [alpha]"))
        for siblings in ({"alpha": "pending"}, {"alpha": "shipped"}):
            assert subplan_is_runnable(block, siblings) == subplan_is_runnable(
                inline, siblings
            )
