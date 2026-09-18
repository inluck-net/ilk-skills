"""Pin the role-tier registry design doc's appendix.

The appendix in docs/role-tier-registry-design.md is the verbatim source the
build sub-plan copies into tools/claude-worker/role-registry.json. If the doc
drifts (bad JSON, a tier leaves the enum, a home gains a conflicting tier),
this gate goes red instead of a review comment noticing later.
"""

import json
import re
from pathlib import Path

import pytest

DESIGN_DOC = (
    Path(__file__).resolve().parent.parent / "docs" / "role-tier-registry-design.md"
)
VALID_TIERS = {"worker", "planner", "manager"}
TIER_RANKS = {"worker": 0, "planner": 1, "manager": 2}


def _extract_appendix():
    text = DESIGN_DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", text, re.S)
    assert blocks, "design doc carries no fenced json block"
    return json.loads(blocks[-1])


def test_doc_exists_with_status_block():
    text = DESIGN_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("# "), "plain # Title required"
    # house convention (model-worker-framework.md): title, blank line, then
    # the blockquoted Status line — within the first 4 lines
    assert any(
        "**Status:**" in line for line in lines[:4]
    ), "Status block missing from the doc header"


def test_appendix_parses_and_has_manager_row():
    registry = _extract_appendix()
    assert registry["version"] == 1
    manager = registry["roles"]["manager"]
    assert manager["home"] == "~/.claude-manager"
    assert manager["tier"] == "manager"
    assert manager.get("path_command") == "claude-manager"


def test_all_tiers_in_enum_and_ranks_total():
    registry = _extract_appendix()
    tiers = {r["tier"] for r in registry["roles"].values()}
    assert tiers <= VALID_TIERS, tiers - VALID_TIERS
    # every rank is representable and distinct — guards rely on strict order
    assert TIER_RANKS["worker"] < TIER_RANKS["planner"] < TIER_RANKS["manager"]


def test_no_home_carries_conflicting_tiers():
    registry = _extract_appendix()
    seen = {}
    for role, row in registry["roles"].items():
        home = row["home"]
        if home in seen:
            assert seen[home] == row["tier"], (
                f"home {home} carries both {seen[home]} ({role}) "
                f"and {row['tier']}"
            )
        seen[home] = row["tier"]


def test_read_contract_elements_present():
    text = DESIGN_DOC.read_text(encoding="utf-8")
    for needle in (
        "require_min_tier",
        "ILK_ROLE_REGISTRY",
        "role-registry.json",
        "ancestor-prefix",
        "Fail closed",
    ):
        assert needle in text, f"design doc lost its {needle!r} contract text"
