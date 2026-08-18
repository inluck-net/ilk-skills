"""Tests for gate_scope.py — tier selection from the consumer set.

Step 1 focuses on:
- AC-5: oracle failed → tier 3, not tier 1 (distinguishing zero from unknown)
- ConsumerResult distinction between zero and unknown
- resolve_consumers integration (uses real grep)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gate_scope import (
    CONTRACT_GOVERNED_FILES,
    ConsumerResult,
    OracleStatus,
    _is_contract_governed,
    _is_path_or_schema_change,
    _is_test_path,
    select_tier,
)



# ── ConsumerResult: the zero/unknown distinction (AC-5) ────────────────────

class TestConsumerResultDistinction:
    """AC-5: 'oracle could not run' must be distinct from 'zero consumers'."""

    def test_zero_is_not_unknown(self) -> None:
        """Zero consumers (oracle ran, found nothing) is NOT unknown."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        assert zero.is_zero is True
        assert zero.is_unknown is False

    def test_unknown_is_not_zero(self) -> None:
        """Oracle failed is NOT zero — they must be distinguishable."""
        failed = ConsumerResult(status=OracleStatus.FAILED, importers=())
        assert failed.is_unknown is True
        assert failed.is_zero is False

    def test_with_importers_is_neither(self) -> None:
        """Oracle found consumers — neither zero nor unknown."""
        with_imports = ConsumerResult(status=OracleStatus.OK, importers=("a.py", "b.py"))
        assert with_imports.is_zero is False
        assert with_imports.is_unknown is False
        assert with_imports.count == 2


# ── Tier selection: AC-5 (oracle failed → tier 3) ──────────────────────────

class TestOracleFailedIsTier3:
    """AC-5: when the consumer oracle cannot run, the result is tier 3."""

    def test_failed_oracle_with_code_change(self) -> None:
        """Oracle failed + code change → tier 3, not tier 1."""
        failed = ConsumerResult(status=OracleStatus.FAILED, importers=())
        decision = select_tier(["skills/ilk-loop/scripts/emit_jsonl_record.py"], failed)
        assert decision.tier == 3
        assert "oracle" in decision.reason.lower() or "could not run" in decision.reason.lower()

    def test_failed_oracle_with_no_changes(self) -> None:
        """Oracle failed + no changes → still tier 3 (safety first)."""
        failed = ConsumerResult(status=OracleStatus.FAILED, importers=())
        decision = select_tier([], failed)
        assert decision.tier == 3

    def test_zero_consumers_is_tier1_not_tier3(self) -> None:
        """Zero consumers (oracle ran successfully) → tier 1, not tier 3."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["skills/ilk-loop/scripts/emit_jsonl_record.py"], zero)
        assert decision.tier == 1
        assert "zero" in decision.reason.lower()


# ── Tier selection: AC-4 (path/schema → tier 3) ────────────────────────────

class TestPathChangeIsTier3:
    """AC-4: a path or schema change selects tier 3, not tier 1."""

    def test_json_path_change(self) -> None:
        """A .json file change → tier 3 (no import graph)."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["config/settings.json"], zero)
        assert decision.tier == 3

    def test_yaml_path_change(self) -> None:
        """A .yaml file change → tier 3."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["config/settings.yaml"], zero)
        assert decision.tier == 3

    def test_ilk_launch_json(self) -> None:
        """".ilk-launch.json → tier 3 (path/config file)."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier([".ilk-launch.json"], zero)
        assert decision.tier == 3

    def test_sentinel_fixture_selects_tier3(self) -> None:
        """THE SENTINEL CASE: a path/schema change that broke 12 fixtures
        across 7 files, despite the consumer oracle returning zero.

        From the MASTER: "The sentinel move was one identifier at 2 call sites
        and broke 12 fixtures in 3 skills." A path has no import graph, so the
        consumer oracle returns zero — but the change is high-risk. If this
        selects tier 1, the whole design has failed.

        The sentinel was a JSONL schema change (a writer path). We model it
        as a .json config file change with zero consumers.
        """
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        # Sentinel: schema/config change that has no import graph
        sentinel_paths = [
            "skills/ilk-loop/scripts/.ilk-loop-schema.json",
        ]
        decision = select_tier(sentinel_paths, zero)
        assert decision.tier == 3, (
            f"Sentinel path change selected tier {decision.tier}, expected 3. "
            f"The oracle returned zero consumers, but a path change has no "
            f"import graph — zero is not a real zero. Reason: {decision.reason}"
        )


# ── Tier selection: AC-9 (code file → never tier 0) ────────────────────────

class TestCodeNeverTier0:
    """AC-9: a change touching any .py/.sh/.ps1 can never be tier 0."""

    def test_py_file_not_tier0(self) -> None:
        """A .py file → not tier 0."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["src/module.py"], zero)
        assert decision.tier != 0

    def test_sh_file_not_tier0(self) -> None:
        """A .sh file → not tier 0."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["scripts/run.sh"], zero)
        assert decision.tier != 0

    def test_docs_only_is_tier0(self) -> None:
        """Docs-only change → tier 0."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["README.md", "CHANGELOG.md"], zero)
        assert decision.tier == 0


# ── Tier selection: contract-governed → tier 3 ─────────────────────────────

class TestContractGovernedIsTier3:
    """A contract-governed file change → tier 3."""

    def test_collect_py(self) -> None:
        """collect.py is contract-governed → tier 3."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["skills/ilk-feedback/scripts/collect.py"], zero)
        assert decision.tier == 3
        assert "contract-governed" in decision.reason.lower()


# ── Tier selection: consumers present → tier 2 ─────────────────────────────

class TestConsumersPresentIsTier2:
    """N resolved consumers → tier 2."""

    def test_two_consumers(self) -> None:
        """Two consumers → tier 2."""
        result = ConsumerResult(status=OracleStatus.OK, importers=("a.py", "b.py"))
        decision = select_tier(["module.py"], result)
        assert decision.tier == 2
        assert decision.consumer_count == 2


# ── _is_test_path mirrors plan_lint exactly ────────────────────────────────

class TestIsTestPath:
    """_is_test_path must agree with plan_lint._is_test_path."""

    def test_test_dir(self) -> None:
        assert _is_test_path("skills/ilk-loop/tests/test_foo.py") is True

    def test_test_file_prefix(self) -> None:
        assert _is_test_path("src/test_module.py") is True

    def test_non_test_file(self) -> None:
        assert _is_test_path("src/module.py") is False

    def test_testing_utils_not_test(self) -> None:
        """testing_utils.py is NOT a test file (no substring match)."""
        assert _is_test_path("src/testing_utils.py") is False


# ── _is_path_or_schema_change ──────────────────────────────────────────────

class TestPathOrSchemaDetection:
    """Path/schema files have no import graph."""

    def test_json_file(self) -> None:
        assert _is_path_or_schema_change("config/data.json") is True

    def test_yaml_file(self) -> None:
        assert _is_path_or_schema_change("config/data.yaml") is True

    def test_toml_file(self) -> None:
        assert _is_path_or_schema_change("pyproject.toml") is True

    def test_ilk_launch_json(self) -> None:
        assert _is_path_or_schema_change(".ilk-launch.json") is True

    def test_py_file_not_path(self) -> None:
        assert _is_path_or_schema_change("src/module.py") is False


# ── _is_contract_governed ──────────────────────────────────────────────────

class TestContractGoverned:
    """Contract-governed file detection."""

    def test_collect_py(self) -> None:
        assert _is_contract_governed("skills/ilk-feedback/scripts/collect.py", CONTRACT_GOVERNED_FILES) is True

    def test_run_ilk_loop(self) -> None:
        assert _is_contract_governed("skills/ilk-loop/scripts/run_ilk_loop_claude.sh", CONTRACT_GOVERNED_FILES) is True

    def test_regular_module(self) -> None:
        assert _is_contract_governed("src/module.py", CONTRACT_GOVERNED_FILES) is False
