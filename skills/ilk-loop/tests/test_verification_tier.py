"""Tests for verification_tier surfacing in loop_status.py.

Builds a temp plans dir with mixed/absent verification_tier values and
asserts resolve_status carries the correct tier on each subplan dict.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

# Import the module under test — adjust path so it works whether pytest
# is invoked from repo root or from the tests/ directory itself.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from loop_status import resolve_status  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MASTER_BODY = textwrap.dedent("""\
    ---
    master_plan: 2026-06-08-test
    batch_date: 2026-06-08
    status: active
    total_tickets: 3
    ---

    ## Sub-plan registry

    | # | Slug | Status |
    |---|---|---|
    | 1 | 2026-06-08-alpha.md | pending |
    | 2 | 2026-06-08-beta.md | shipped |
    | 3 | 2026-06-08-gamma.md | pending |
""")

SUBPLAN_ALPHA = textwrap.dedent("""\
    ---
    plan: alpha
    status: pending
    current_step: 0
    estimated_steps: 3
    verification_tier: compile-only
    ---

    # Alpha
""")

SUBPLAN_BETA = textwrap.dedent("""\
    ---
    plan: beta
    status: shipped
    current_step: 3
    estimated_steps: 3
    verification_tier: device-manual
    ---

    # Beta
""")

SUBPLAN_GAMMA = textwrap.dedent("""\
    ---
    plan: gamma
    status: pending
    current_step: 0
    estimated_steps: 2
    ---

    # Gamma  (no verification_tier — should default to loop-verified)
""")


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    """Create a temporary plans dir with one MASTER and three sub-plans."""
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-06-08-test.md").write_text(MASTER_BODY, encoding="utf-8")
    (d / "2026-06-08-alpha.md").write_text(SUBPLAN_ALPHA, encoding="utf-8")
    (d / "2026-06-08-beta.md").write_text(SUBPLAN_BETA, encoding="utf-8")
    (d / "2026-06-08-gamma.md").write_text(SUBPLAN_GAMMA, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerificationTierInResolveStatus:
    """resolve_status subplan dicts carry verification_tier."""

    def test_explicit_tier_present(self, plans_dir: Path) -> None:
        data = resolve_status(plans_dir)
        by_slug = {sp["slug"]: sp for sp in data["subplans"]}
        assert by_slug["alpha"]["verification_tier"] == "compile-only"
        assert by_slug["beta"]["verification_tier"] == "device-manual"

    def test_absent_tier_defaults_to_loop_verified(self, plans_dir: Path) -> None:
        data = resolve_status(plans_dir)
        by_slug = {sp["slug"]: sp for sp in data["subplans"]}
        assert by_slug["gamma"]["verification_tier"] == "loop-verified"

    def test_json_payload_includes_tier(self, plans_dir: Path) -> None:
        data = resolve_status(plans_dir)
        # Simulate --json serialisation round-trip
        payload = json.loads(json.dumps(data, ensure_ascii=False))
        by_slug = {sp["slug"]: sp for sp in payload["subplans"]}
        assert "verification_tier" in by_slug["alpha"]
        assert "verification_tier" in by_slug["gamma"]
        assert by_slug["gamma"]["verification_tier"] == "loop-verified"
