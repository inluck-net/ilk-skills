"""plan_lint catches two-halves-disagree shapes at plan time.

AC-4  frontmatter plan: differs from filename-derived slug → HARD finding.
AC-6  batch_verification + full-suite body + --scope auto gate → HARD finding.

Both are mechanically detectable and prevent the class of silent
disagreement that parked pv-5611 for ~19 hours.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _patch_plan_lint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure plan_lint's imports resolve from the scripts dir."""
    import sys
    sys.path.insert(0, str(SCRIPTS))
    yield  # type: ignore[misc]
    sys.path.remove(str(SCRIPTS))


class TestSlugIdentityAgreement:
    """AC-4: frontmatter plan: must agree with filename-derived slug."""

    def test_mismatch_emits_hard_finding(self) -> None:
        from plan_lint import lint_slug_identity_agreement

        # Filename: 2026-09-21-pv5-verify.md → slug "pv5-verify"
        # Frontmatter: plan: pv5-round5-verify
        text = textwrap.dedent("""\
            ---
            plan: pv5-round5-verify
            status: shipped
            current_step: 2
            estimated_steps: 2
            local_checks: []
            ---

            # pv5-round5-verify
        """)
        findings = lint_slug_identity_agreement(text, "2026-09-21-pv5-verify")
        assert len(findings) == 1
        assert "HARD" in findings[0]
        assert "pv5-round5-verify" in findings[0]
        assert "pv5-verify" in findings[0]

    def test_match_emits_no_finding(self) -> None:
        from plan_lint import lint_slug_identity_agreement

        text = textwrap.dedent("""\
            ---
            plan: my-slug
            status: shipped
            current_step: 2
            estimated_steps: 2
            local_checks: []
            ---

            # my-slug
        """)
        findings = lint_slug_identity_agreement(text, "2026-09-21-my-slug")
        assert findings == []

    def test_no_plan_field_emits_no_finding(self) -> None:
        from plan_lint import lint_slug_identity_agreement

        text = textwrap.dedent("""\
            ---
            status: shipped
            current_step: 2
            estimated_steps: 2
            local_checks: []
            ---

            # some-slug
        """)
        findings = lint_slug_identity_agreement(text, "2026-09-21-some-slug")
        assert findings == []

    def test_no_date_prefix_still_works(self) -> None:
        from plan_lint import lint_slug_identity_agreement

        text = textwrap.dedent("""\
            ---
            plan: other-slug
            status: shipped
            current_step: 1
            estimated_steps: 1
            local_checks: []
            ---

            # other-slug
        """)
        findings = lint_slug_identity_agreement(text, "my-slug")
        assert len(findings) == 1
        assert "my-slug" in findings[0]
        assert "other-slug" in findings[0]


class TestBatchVerificationScopeMismatch:
    """AC-6: batch_verification + full-suite body + --scope auto → HARD."""

    def test_all_three_conditions_emit_hard_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        text = textwrap.dedent("""\
            ---
            plan: verify-batch
            status: in-progress
            current_step: 0
            estimated_steps: 2
            batch_verification: true
            local_checks: []
            ---

            # verify-batch

            Run the full test suite to verify.

            ### Step 0 — verify

            ```yaml
            local_checks:
              - command: "python3 -m pytest --scope auto"
                timeout: 300
            ```
        """)
        findings = lint_batch_verification_scope_mismatch(text, "verify-batch")
        assert len(findings) == 1
        assert "HARD" in findings[0]
        assert "--scope auto" in findings[0]
        assert "full suite" in findings[0].lower()

    def test_batch_verification_with_full_scope_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        text = textwrap.dedent("""\
            ---
            plan: verify-batch
            status: in-progress
            current_step: 0
            estimated_steps: 2
            batch_verification: true
            local_checks: []
            ---

            # verify-batch

            Run the full test suite to verify.

            ### Step 0 — verify

            ```yaml
            local_checks:
              - command: "python3 -m pytest --scope full"
                timeout: 300
            ```
        """)
        findings = lint_batch_verification_scope_mismatch(text, "verify-batch")
        assert findings == []

    def test_no_batch_verification_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        text = textwrap.dedent("""\
            ---
            plan: verify-batch
            status: in-progress
            current_step: 0
            estimated_steps: 2
            local_checks: []
            ---

            # verify-batch

            Run the full test suite.

            ### Step 0 — verify

            ```yaml
            local_checks:
              - command: "python3 -m pytest --scope auto"
                timeout: 300
            ```
        """)
        findings = lint_batch_verification_scope_mismatch(text, "verify-batch")
        assert findings == []

    def test_batch_verification_no_full_suite_mandate_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        text = textwrap.dedent("""\
            ---
            plan: verify-batch
            status: in-progress
            current_step: 0
            estimated_steps: 2
            batch_verification: true
            local_checks: []
            ---

            # verify-batch

            Run the tests.

            ### Step 0 — verify

            ```yaml
            local_checks:
              - command: "python3 -m pytest --scope auto"
                timeout: 300
            ```
        """)
        findings = lint_batch_verification_scope_mismatch(text, "verify-batch")
        assert findings == []