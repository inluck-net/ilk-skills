#!/usr/bin/env python3
"""Contract tests for the ilk-ship SKILL.md five-phase documentation.

Sub-plan: ilk-ship-runs-the-five-phases, step 0.
Asserts the doc's phase table matches the scripts' actual entry points —
a doc-vs-code drift check.  Also asserts AC-2 (Phase 0 hard stop) and
AC-8 (shipped ≠ verified).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

SKILL_ROOT = Path(__file__).resolve().parents[3]  # skills/ilk-ship/../../..
SHIP_SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"
SHIP_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
COMMAND_FILE = SKILL_ROOT / "commands" / "ilk-ship.md"
INSTALL_SH = SKILL_ROOT / "install.sh"


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    return SHIP_SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def phase_table(skill_md_text: str) -> str:
    """Extract the markdown phase table from SKILL.md."""
    # The table starts with "| # | Phase |" and ends at the next blank line
    m = re.search(
        r"(\| # \| Phase \|.*?)(?:\n\n|\n#|\Z)",
        skill_md_text,
        re.DOTALL,
    )
    assert m, "Phase table not found in SKILL.md"
    return m.group(1)


# ── AC-1: doc documents each phase's entry/exit from the scripts ─────────────

class TestPhaseTablePresent:
    """SKILL.md contains a five-phase table with the expected phases."""

    def test_five_phases_listed(self, phase_table: str):
        lines = [l.strip() for l in phase_table.splitlines() if l.strip().startswith("|")]
        # header + separator + 5 data rows
        data_rows = [l for l in lines if not l.startswith("| #") and "---" not in l]
        assert len(data_rows) == 5, f"Expected 5 phase rows, got {len(data_rows)}"

    def test_phase_names(self, phase_table: str):
        expected = ["Audit", "Verify", "Fix", "Release", "Deploy"]
        for name in expected:
            assert name in phase_table, f"Phase '{name}' not found in phase table"

    def test_phase_0_engine_is_ship_audit(self, phase_table: str):
        assert "ship_audit.py" in phase_table, "Phase 0 engine (ship_audit.py) not in table"

    def test_phase_1_engines(self, phase_table: str):
        assert "gate_scope.py" in phase_table, "Phase 1 engine (gate_scope.py) not in table"
        assert "baseline_diff.py" in phase_table, "Phase 1 engine (baseline_diff.py) not in table"


class TestEngineScriptsExist:
    """The scripts referenced in the phase table actually exist on disk."""

    def test_ship_audit_exists(self):
        p = SKILL_ROOT / "skills" / "ilk-loop" / "scripts" / "ship_audit.py"
        assert p.exists(), f"ship_audit.py not found at {p}"

    def test_gate_scope_exists(self):
        p = SHIP_SCRIPTS_DIR / "gate_scope.py"
        assert p.exists(), f"gate_scope.py not found at {p}"

    def test_baseline_diff_exists(self):
        p = SHIP_SCRIPTS_DIR / "baseline_diff.py"
        assert p.exists(), f"baseline_diff.py not found at {p}"

    def test_ship_config_exists(self):
        p = SHIP_SCRIPTS_DIR / "ship_config.py"
        assert p.exists(), f"ship_config.py not found at {p}"


# ── AC-2: Phase 0 is a hard stop ────────────────────────────────────────────

class TestPhase0HardStop:
    """Phase 0 refuses to advance if any shipped sub-plan is unproven."""

    def test_hard_stop_documented(self, skill_md_text: str):
        """The doc explicitly says Phase 0 is a hard stop."""
        # Look in the Phase 0 section
        phase0_section = _extract_section(skill_md_text, "Phase 0")
        assert "hard stop" in phase0_section.lower(), (
            "Phase 0 section does not contain 'hard stop'"
        )

    def test_refuses_to_advance(self, skill_md_text: str):
        """The doc says Phase 0 refuses to advance on unproven sub-plans."""
        phase0_section = _extract_section(skill_md_text, "Phase 0")
        assert "refuses to advance" in phase0_section.lower(), (
            "Phase 0 section does not say it 'refuses to advance'"
        )

    def test_unproven_triggers_stop(self, skill_md_text: str):
        """The doc explicitly ties 'unproven' to the hard stop."""
        phase0_section = _extract_section(skill_md_text, "Phase 0")
        assert "unproven" in phase0_section.lower(), (
            "Phase 0 section does not mention 'unproven'"
        )


class TestCommandPhase0HardStop:
    """The /ilk-ship command enforces Phase 0 as a hard stop."""

    @pytest.fixture(scope="class")
    def command_text(self) -> str:
        assert COMMAND_FILE.exists(), f"Command file not found at {COMMAND_FILE}"
        return COMMAND_FILE.read_text(encoding="utf-8")

    def test_command_exists(self, command_text: str):
        """commands/ilk-ship.md exists."""
        pass  # fixture assertion is enough

    def test_command_refuses_to_advance(self, command_text: str):
        """The command says to refuse to advance on unproven sub-plans."""
        assert "refuse to advance" in command_text.lower() or "refuse to proceed" in command_text.lower(), (
            "Command does not say 'refuse to advance/proceed' on unproven sub-plans"
        )

    def test_command_phase_3_blocked(self, command_text: str):
        """The command ties the refusal to Phase 3."""
        assert "phase 3" in command_text.lower(), (
            "Command does not reference Phase 3 as the blocked destination"
        )

    def test_command_hard_stop_label(self, command_text: str):
        """The command labels Phase 0 as a hard stop."""
        assert "hard stop" in command_text.lower(), (
            "Command does not label Phase 0 as a 'hard stop'"
        )

    def test_command_2026_08_14_reference(self, command_text: str):
        """The command references the 2026-08-14 failure that motivated it."""
        assert "2026-08-14" in command_text, (
            "Command does not reference the 2026-08-14 failure"
        )


# ── AC-3: ILK_ALLOW_FULL_SUITE=1 escape documented ─────────────────────────

class TestEscapeHatchDocumented:
    """The ILK_ALLOW_FULL_SUITE=1 escape is documented."""

    def test_escape_mentioned(self, skill_md_text: str):
        assert "ILK_ALLOW_FULL_SUITE=1" in skill_md_text, (
            "ILK_ALLOW_FULL_SUITE=1 not documented in SKILL.md"
        )

    def test_escape_reason(self, skill_md_text: str):
        """The doc explains why the escape exists."""
        # Should mention the hook or "no-full-suite"
        assert "no-full-suite" in skill_md_text.lower() or "hook" in skill_md_text.lower(), (
            "SKILL.md does not explain why ILK_ALLOW_FULL_SUITE=1 exists"
        )


# ── AC-4: missing ship: block degrades to documented default ────────────────

class TestMissingShipBlockDefault:
    """A missing ship: block degrades to a documented default."""

    def test_default_section_exists(self, skill_md_text: str):
        assert "missing" in skill_md_text.lower() and "ship" in skill_md_text.lower(), (
            "No section about missing ship: block"
        )

    def test_default_suite_command(self, skill_md_text: str):
        """The default suite command is documented."""
        assert "python3 -m pytest" in skill_md_text, (
            "Default suite command not documented"
        )

    def test_default_timeout(self, skill_md_text: str):
        """The default timeout is documented."""
        assert "300" in skill_md_text, "Default timeout (300) not documented"


# ── AC-6: Phase 4 reports per host ─────────────────────────────────────────

class TestPhase4PerHost:
    """Phase 4 reports per host and never fakes success."""

    def test_per_host_documented(self, skill_md_text: str):
        phase4_section = _extract_section(skill_md_text, "Phase 4")
        assert "per host" in phase4_section.lower(), (
            "Phase 4 does not document per-host reporting"
        )

    def test_unreachable_not_ok(self, skill_md_text: str):
        phase4_section = _extract_section(skill_md_text, "Phase 4")
        assert "unreachable" in phase4_section.lower(), (
            "Phase 4 does not document 'unreachable' status"
        )


# ── AC-7: no phase claims a skipped step ────────────────────────────────────

class TestNoSkippedSteps:
    """Each phase states what it ran and what it did not."""

    def test_no_skipped_steps_documented(self, skill_md_text: str):
        assert "no phase claims" in skill_md_text.lower() or "skipped" in skill_md_text.lower(), (
            "AC-7: no-skipped-steps discipline not documented"
        )


# ── AC-8: shipped ≠ verified ───────────────────────────────────────────────

class TestShippedNotVerified:
    """SKILL.md states plainly that shipped is commit-only and local."""

    def test_shipped_commit_only(self, skill_md_text: str):
        assert "commit-only" in skill_md_text.lower() or "commit only" in skill_md_text.lower(), (
            "SKILL.md does not state 'shipped is commit-only'"
        )

    def test_shipped_local(self, skill_md_text: str):
        assert "local" in skill_md_text.lower(), (
            "SKILL.md does not state 'shipped is local'"
        )

    def test_shipped_not_ci(self, skill_md_text: str):
        assert "not pushed" in skill_md_text.lower() or "not ci" in skill_md_text.lower(), (
            "SKILL.md does not clarify shipped is not pushed/CI-verified"
        )


# ── AC-5: install.sh discovers the skill and command ────────────────────────

class TestInstallDiscovery:
    """ilk-ship is discovered by install.sh's glob mechanism."""

    def test_install_sh_exists(self):
        assert INSTALL_SH.exists(), f"install.sh not found at {INSTALL_SH}"

    def test_discovery_documented(self, skill_md_text: str):
        assert "install.sh" in skill_md_text, (
            "SKILL.md does not mention install.sh discovery"
        )

    def test_glob_mechanism_documented(self, skill_md_text: str):
        """The doc explains the glob-based discovery (no registration list)."""
        assert "glob" in skill_md_text.lower() or "find" in skill_md_text.lower(), (
            "SKILL.md does not document the glob-based discovery mechanism"
        )

    def test_no_registration_list(self, skill_md_text: str):
        """The doc says there is no registration list to edit."""
        assert "no registration list" in skill_md_text.lower(), (
            "SKILL.md does not state there is no registration list"
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_section(text: str, heading: str) -> str:
    """Extract text from a ### heading to the next ### or ## heading."""
    pattern = rf"### {re.escape(heading)}\b.*?(?=\n### |\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(0)
    # Fallback: try without ###
    pattern = rf"{re.escape(heading)}.*?(?=\n### |\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(0) if m else ""
