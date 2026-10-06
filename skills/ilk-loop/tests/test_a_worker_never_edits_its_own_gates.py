"""Pin the worker's writable plan regions.

AC-1 (no licence): commands/ilk.md contains no line that tells the worker to
    edit local_checks (unless the line carries its own negation), and no line
    says "under 'Out of scope'" (the old instruction to write below Out of
    scope instead of below Findings).
AC-2 (rule present): §5 contains the keywords that name the new contract.
AC-3 (pinned to the runner): plan_fingerprint.surface is unchanged by edits
    to current_step / last_updated / status / text under ## Findings, and
    changed by edits to local_checks and ## Out of scope body.
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
COMMANDS_ILK = REPO_ROOT / "commands" / "ilk.md"
SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"


# ── AC-1: no licence to edit local_checks or Out of scope ────────────────────


class TestAC1NoLicence:
    """commands/ilk.md must never tell the worker to edit local_checks
    or to write under 'Out of scope'."""

    def test_no_local_checks_edit_instruction(self) -> None:
        """AC-1a: no line says to edit/change/fix local_checks without a negation."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        # Match any line that has an edit-verb near local_checks.
        edit_re = re.compile(
            r"\b(fix|edit|update|change|rewrite|re-scope|adjust)\b"
            r"[^\n]*local_checks",
            re.IGNORECASE,
        )
        negation_re = re.compile(
            r"\b(never|not|don't)\b"
            r"[^\n]*"
            r"\b(fix|edit|update|change|rewrite|re-scope|adjust)\b"
            r"[^\n]*local_checks",
            re.IGNORECASE,
        )
        for i, line in enumerate(text.splitlines(), 1):
            if edit_re.search(line) and not negation_re.search(line):
                pytest.fail(
                    f"ilk.md line {i} tells worker to edit local_checks: {line!r}"
                )

    def test_no_out_of_scope_write_instruction(self) -> None:
        """AC-1b: no line says to write under 'Out of scope'."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        pattern = re.compile(r"""under\s+['"]Out of scope['"]""", re.IGNORECASE)
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                pytest.fail(
                    f"ilk.md line {i} tells worker to write under Out of scope: {line!r}"
                )


# ── AC-2: the new rule is present in §5 ──────────────────────────────────────


class TestAC2RulePresent:
    """§5 must contain the key phrases naming the new contract."""

    def test_section5_names_contract(self) -> None:
        """AC-2: §5 contains Findings, plan-amended, ship_integrity_violation,
        and local_checks."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        section_start = text.find("## 5.")
        assert section_start != -1, "§5 not found in commands/ilk.md"
        section_end = text.find("\n## 6.", section_start)
        section = text[section_start:section_end] if section_end != -1 else text[section_start:]

        required = ["## Findings", "plan-amended", "ship_integrity_violation", "local_checks"]
        missing = [kw for kw in required if kw not in section]
        assert not missing, f"§5 missing required keywords: {missing}"


# ── AC-3: plan_fingerprint surface is pinned to the runner ───────────────────


class TestAC3PinnedToRunner:
    """Characterisation: plan_fingerprint.surface behaves as the runner expects."""

    def _fixture_plan(self) -> str:
        """A minimal sub-plan with all the regions we need to test."""
        return textwrap.dedent("""\
            ---
            plan: test-slug
            status: pending
            current_step: 0
            last_updated: 2026-10-01
            local_checks:
              - command: "pytest -q"
                timeout: 300
            ---

            # Sub-plan: test

            ## Before you start

            Read the docs.

            ## Out of scope

            - Item A
            - Item B

            ## Steps

            ### Step 0

            Do work.

            ```yaml
            local_checks:
              - command: "pytest tests/test_foo.py -q"
                timeout: 120
            ```

            ## Findings

            Nothing yet.
        """)

    def test_surface_unchanged_by_runner_keys(self) -> None:
        """AC-3a: editing current_step, last_updated, status does not change surface."""
        sys.path.insert(0, str(SCRIPTS))
        from plan_fingerprint import surface

        original = self._fixture_plan()
        baseline = surface(original)

        modified = original
        modified = modified.replace("current_step: 0", "current_step: 2")
        modified = modified.replace("last_updated: 2026-10-01", "last_updated: 2026-10-07")
        modified = modified.replace("status: pending", "status: in-progress")

        assert surface(modified) == baseline, (
            "surface changed after editing runner-owned frontmatter keys"
        )

    def test_surface_unchanged_by_findings_edit(self) -> None:
        """AC-3b: editing text under ## Findings does not change surface."""
        sys.path.insert(0, str(SCRIPTS))
        from plan_fingerprint import surface

        original = self._fixture_plan()
        baseline = surface(original)

        modified = original.replace(
            "Nothing yet.", "Step 0 produced no issues. All tests passed."
        )

        assert surface(modified) == baseline, (
            "surface changed after editing text under ## Findings"
        )

    def test_surface_changed_by_frontmatter_local_checks(self) -> None:
        """AC-3c: editing frontmatter local_checks changes surface."""
        sys.path.insert(0, str(SCRIPTS))
        from plan_fingerprint import surface

        original = self._fixture_plan()
        baseline = surface(original)

        modified = original.replace(
            '  - command: "pytest -q"\n    timeout: 300',
            '  - command: "pytest tests/ -q"\n    timeout: 600',
        )

        assert surface(modified) != baseline, (
            "surface unchanged after editing frontmatter local_checks"
        )

    def test_surface_changed_by_step_local_checks(self) -> None:
        """AC-3d: editing per-step local_checks changes surface."""
        sys.path.insert(0, str(SCRIPTS))
        from plan_fingerprint import surface

        original = self._fixture_plan()
        baseline = surface(original)

        modified = original.replace(
            'command: "pytest tests/test_foo.py -q"',
            'command: "pytest tests/test_bar.py -q"',
        )

        assert surface(modified) != baseline, (
            "surface unchanged after editing per-step local_checks"
        )

    def test_surface_changed_by_out_of_scope_edit(self) -> None:
        """AC-3e: editing the ## Out of scope body changes surface."""
        sys.path.insert(0, str(SCRIPTS))
        from plan_fingerprint import surface

        original = self._fixture_plan()
        baseline = surface(original)

        modified = original.replace("- Item A", "- Item A\n- Item C")

        assert surface(modified) != baseline, (
            "surface unchanged after editing ## Out of scope body"
        )