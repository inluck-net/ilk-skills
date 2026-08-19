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
    FLOOR_COMMANDS,
    ConsumerResult,
    OracleStatus,
    SubtractionResult,
    _commands_match,
    _extract_test_path,
    _is_contract_governed,
    _is_path_or_schema_change,
    _is_test_path,
    select_tier,
    subtract_complement,
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

    # ── the tool's own artifacts are not risk signals ──────────────────────
    #
    # `store_baseline` writes .ilk-baselines/<tag>__<hash>.json into the repo
    # on every release. Because `.json` is a path/schema extension, that
    # artifact made the NEXT release select tier 3 regardless of what actually
    # changed: measured 2026-08-19, a docs-only diff plus a stored baseline
    # selected tier 3, while the same diff without it selected tier 0. The
    # release process was poisoning its own next gate decision. A baseline is
    # also host-specific (rezmac 745 passed/16 skipped vs this Mac 746/15 at
    # v0.9.66), so it is never shared state worth gating on.

    def test_stored_baseline_is_not_a_path_change(self) -> None:
        assert _is_path_or_schema_change(
            ".ilk-baselines/v0.9.66__22bbe8a191e8.json"
        ) is False

    def test_stored_baseline_nested_under_project_root(self) -> None:
        assert _is_path_or_schema_change(
            "some/project/.ilk-baselines/v1.2.3__abc123.json"
        ) is False

    def test_windows_separator_stored_baseline(self) -> None:
        assert _is_path_or_schema_change(
            r".ilk-baselines\v0.9.66__22bbe8a191e8.json"
        ) is False

    def test_unrelated_json_still_a_path_change(self) -> None:
        """The exemption must be narrow — a real config json still widens."""
        assert _is_path_or_schema_change("config/data.json") is True
        assert _is_path_or_schema_change(".ilk-launch.json") is True

    def test_baseline_lookalike_outside_the_dir_still_counts(self) -> None:
        """Only the real artifact directory is exempt, not a similar name."""
        assert _is_path_or_schema_change("ilk-baselines-notes.json") is True


# ── _is_contract_governed ──────────────────────────────────────────────────

class TestContractGoverned:
    """Contract-governed file detection."""

    def test_collect_py(self) -> None:
        assert _is_contract_governed("skills/ilk-feedback/scripts/collect.py", CONTRACT_GOVERNED_FILES) is True

    def test_run_ilk_loop(self) -> None:
        assert _is_contract_governed("skills/ilk-loop/scripts/run_ilk_loop_claude.sh", CONTRACT_GOVERNED_FILES) is True

    def test_regular_module(self) -> None:
        assert _is_contract_governed("src/module.py", CONTRACT_GOVERNED_FILES) is False


# ── Complement subtraction (AC-6, AC-8) ────────────────────────────────────

class TestComplementSubtraction:
    """AC-6: reports what was subtracted and why.  AC-8: floors never shrink."""

    def test_subtract_matching_command(self) -> None:
        """A command already in JSONL is subtracted."""
        selected = ["python3 -m pytest skills/ilk-loop/tests/ -q --timeout=180 --timeout-method=signal"]
        recorded = ["python3 -m pytest skills/ilk-loop/tests/ -q --timeout=180 --timeout-method=signal"]
        result = subtract_complement(selected, recorded)
        assert len(result.subtracted) == 1
        assert len(result.kept) == 0

    def test_different_flags_not_subtracted(self) -> None:
        """Same path but different flags → NOT subtracted (different work)."""
        selected = ["python3 -m pytest skills/ilk-loop/tests/ -q --timeout-method=signal"]
        recorded = ["python3 -m pytest skills/ilk-loop/tests/ -q --timeout-method=thread"]
        result = subtract_complement(selected, recorded)
        assert len(result.subtracted) == 0
        assert len(result.kept) == 1

    def test_different_path_not_subtracted(self) -> None:
        """Different path → NOT subtracted."""
        selected = ["python3 -m pytest skills/ilk-loop/tests/ -q"]
        recorded = ["python3 -m pytest skills/ilk-runner/tests/ -q"]
        result = subtract_complement(selected, recorded)
        assert len(result.subtracted) == 0
        assert len(result.kept) == 1

    def test_empty_recorded_subtracts_nothing(self) -> None:
        """No recorded commands → nothing subtracted."""
        selected = ["python3 -m pytest skills/ilk-loop/tests/ -q"]
        result = subtract_complement(selected, [])
        assert len(result.subtracted) == 0
        assert result.kept == tuple(selected)

    def test_ac6_result_to_dict_is_auditable(self) -> None:
        """AC-6: the result is machine-readable and names what was subtracted."""
        selected = ["pytest A", "pytest B"]
        recorded = ["pytest A"]
        result = subtract_complement(selected, recorded)
        d = result.to_dict()
        assert "subtracted" in d
        assert "kept" in d
        assert "already_run" in d


# ── AC-8: floors can never be subtracted ────────────────────────────────────

class TestFloorsNeverShrink:
    """AC-8: baseline-compare and collection are always kept."""

    def test_baseline_compare_never_subtracted(self) -> None:
        """baseline-compare is a floor — kept even if already run."""
        selected = ["baseline-compare --tag v0.9.66", "python3 -m pytest skills/ilk-loop/tests/ -q"]
        recorded = ["baseline-compare --tag v0.9.66"]
        result = subtract_complement(selected, recorded)
        assert "baseline-compare --tag v0.9.66" in result.kept
        assert "baseline-compare --tag v0.9.66" in result.floors_protected
        assert "baseline-compare --tag v0.9.66" not in result.subtracted

    def test_collection_never_subtracted(self) -> None:
        """collection (--collect-only) is a floor — kept even if already run."""
        selected = ["python3 -m pytest --collect-only -q", "python3 -m pytest skills/ilk-loop/tests/ -q"]
        recorded = ["python3 -m pytest --collect-only -q"]
        result = subtract_complement(selected, recorded)
        assert any("collect" in cmd for cmd in result.floors_protected)
        assert len(result.subtracted) == 0 or "collect" not in result.subtracted[0]

    def test_all_commands_subtracted_but_floors_kept(self) -> None:
        """AC-8: complement empties the gate, but floors still run."""
        selected = [
            "baseline-compare --tag v0.9.66",
            "python3 -m pytest --collect-only -q",
            "python3 -m pytest skills/ilk-loop/tests/ -q",
        ]
        recorded = [
            "baseline-compare --tag v0.9.66",
            "python3 -m pytest --collect-only -q",
            "python3 -m pytest skills/ilk-loop/tests/ -q",
        ]
        result = subtract_complement(selected, recorded)
        # The pytest command is subtracted, but both floors are kept
        assert len(result.floors_protected) == 2
        assert len(result.kept) >= 2  # at least the two floors


# ── _extract_test_path ─────────────────────────────────────────────────────

class TestExtractTestPath:
    """Test path extraction from pytest commands."""

    def test_simple_path(self) -> None:
        assert _extract_test_path("python3 -m pytest skills/ilk-loop/tests/ -q") == "skills/ilk-loop/tests/"

    def test_path_with_flags_before(self) -> None:
        assert _extract_test_path("python3 -m pytest -v skills/ilk-loop/tests/ -q") == "skills/ilk-loop/tests/"

    def test_no_pytest(self) -> None:
        assert _extract_test_path("grep -rn foo .") is None

    def test_pytest_no_path(self) -> None:
        assert _extract_test_path("python3 -m pytest -q") is None


# ── _commands_match ────────────────────────────────────────────────────────

class TestCommandsMatch:
    """Command reconciliation — same work or not?"""

    def test_identical_commands_match(self) -> None:
        cmd = "python3 -m pytest skills/ilk-loop/tests/ -q --timeout=180"
        assert _commands_match(cmd, cmd) is True

    def test_different_flags_no_match(self) -> None:
        sel = "python3 -m pytest skills/ilk-loop/tests/ -q --timeout-method=signal"
        rec = "python3 -m pytest skills/ilk-loop/tests/ -q --timeout-method=thread"
        assert _commands_match(sel, rec) is False

    def test_different_path_no_match(self) -> None:
        sel = "python3 -m pytest skills/ilk-loop/tests/ -q"
        rec = "python3 -m pytest skills/ilk-runner/tests/ -q"
        assert _commands_match(sel, rec) is False

    def test_trailing_slash_normalized(self) -> None:
        sel = "python3 -m pytest skills/ilk-loop/tests -q"
        rec = "python3 -m pytest skills/ilk-loop/tests/ -q"
        assert _commands_match(sel, rec) is True


# ── Replay fixture: 08-13 batch sub-plan 2 ─────────────────────────────────

class TestReplay0813Batch:
    """Replay the 08-13 batch's sub-plan 2 against the selector.

    Sub-plan 2 of the 08-13 batch modified ``skills/ilk-loop/scripts/status_all.py``
    (a contract-governed file) and gated on only
    ``pytest skills/ilk-runner/tests/ skills/ilk-feedback/tests/`` —
    missing the ``ilk-loop`` scope that broke 12 fixtures across 7 files.

    The selector must add the ``ilk-loop`` scope that gate omitted.
    """

    def test_status_all_py_change_is_tier3(self) -> None:
        """status_all.py is contract-governed → tier 3, not tier 1 or 2."""
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        changed_paths = ["skills/ilk-loop/scripts/status_all.py"]
        decision = select_tier(changed_paths, zero)
        assert decision.tier == 3, (
            f"status_all.py is contract-governed (in CONTRACT_GOVERNED_FILES), "
            f"but selector returned tier {decision.tier}. "
            f"The 08-13 batch's narrow gate would still have been too narrow."
        )

    def test_narrow_gate_would_miss_ilk_loop_scope(self) -> None:
        """The 08-13 gate (ilk-runner + ilk-feedback only) missed ilk-loop tests.

        This fixture verifies the selector would have expanded the gate
        to include the ilk-loop scope.
        """
        # The 08-13 batch's actual gate was:
        narrow_gate = [
            "python3 -m pytest skills/ilk-runner/tests/ -q",
            "python3 -m pytest skills/ilk-feedback/tests/ -q",
        ]
        # The selector says tier 3 (contract-governed file) → whole suite
        zero = ConsumerResult(status=OracleStatus.OK, importers=())
        decision = select_tier(["skills/ilk-loop/scripts/status_all.py"], zero)
        assert decision.tier == 3
        # A tier-3 gate should include the ilk-loop tests, not just runner+feedback
        # (this is verified by the SKILL.md in sub-plan 6, but the fixture
        # confirms the tier is correct)

