"""plan_lint catches a sub-plan whose two slug identities disagree.

A sub-plan has two derivable identities:
  1. ``_slug_from_filename(fname)`` — strips date prefix + .md extension
  2. ``frontmatter plan:`` — the explicit field

When they disagree, readers that pick different identities silently diverge.
This parked the pv-5611 batch for ~19 hours (measured 2026-09-22).

AC-4  plan_lint emits a HARD finding when frontmatter plan: differs from the
      filename-derived slug, naming both values.
AC-6  plan_lint emits a HARD finding when a sub-plan has
      ``batch_verification: true``, a body that mandates a **full suite**,
      and a gate passing ``--scope auto``.  (Same family — two halves of the
      plan disagree about what it is.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _make_subplan_text(
    plan_field: str,
    *,
    body: str = "",
    batch_verification: bool = False,
) -> str:
    """Return sub-plan text with a given frontmatter plan: field."""
    bv = "\nbatch_verification: true" if batch_verification else ""
    return (
        f"---\nplan: {plan_field}\nstatus: pending\ncurrent_step: 0\n"
        f"estimated_steps: 3{bv}\nlocal_checks: []\n---\n\n"
        f"# {plan_field}\n\n{body}"
    )


class TestSlugIdentityMismatch:
    """AC-4: HARD finding when plan: differs from filename-derived slug."""

    def test_mismatched_slug_fires_hard(self) -> None:
        from plan_lint import lint_slug_identity_mismatch

        # Filename: 2026-09-21-pv5-verify.md -> slug "pv5-verify"
        # Frontmatter: plan: pv5-round5-verify
        text = _make_subplan_text("pv5-round5-verify")
        findings = lint_slug_identity_mismatch(text, slug="pv5-verify")
        assert len(findings) == 1, (
            f"expected 1 HARD finding for mismatched slug, got {len(findings)}"
        )
        assert "HARD" in findings[0]
        assert "pv5-verify" in findings[0]
        assert "pv5-round5-verify" in findings[0]

    def test_matching_slug_no_finding(self) -> None:
        from plan_lint import lint_slug_identity_mismatch

        text = _make_subplan_text("my-slug")
        findings = lint_slug_identity_mismatch(text, slug="my-slug")
        assert findings == [], (
            f"expected no finding when slugs match, got {findings}"
        )


class TestBatchVerificationScopeAuto:
    """AC-6: HARD finding when batch_verification + full suite body + --scope auto."""

    def test_full_suite_body_with_scope_auto_fires_hard(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        body = (
            "Run the **full suite** to verify.\n\n"
            "```yaml\nlocal_checks:\n"
            "  - command: pytest --scope auto -q\n    timeout: 180\n```\n"
        )
        text = _make_subplan_text("verify-slug", body=body, batch_verification=True)
        findings = lint_batch_verification_scope_mismatch(text, slug="verify-slug")
        assert len(findings) == 1, (
            f"expected 1 HARD finding for batch_verification + auto scope, "
            f"got {len(findings)}"
        )
        assert "HARD" in findings[0]
        assert "--scope auto" in findings[0] or "scope auto" in findings[0]
        assert "full suite" in findings[0].lower() or "full-suite" in findings[0].lower()

    def test_no_batch_verification_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        body = (
            "Run the **full suite** to verify.\n\n"
            "```yaml\nlocal_checks:\n"
            "  - command: pytest --scope auto -q\n    timeout: 180\n```\n"
        )
        text = _make_subplan_text("verify-slug", body=body, batch_verification=False)
        findings = lint_batch_verification_scope_mismatch(text, slug="verify-slug")
        assert findings == [], (
            f"expected no finding without batch_verification, got {findings}"
        )

    def test_scope_full_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        body = (
            "Run the **full suite** to verify.\n\n"
            "```yaml\nlocal_checks:\n"
            "  - command: pytest --scope full -q\n    timeout: 180\n```\n"
        )
        text = _make_subplan_text("verify-slug", body=body, batch_verification=True)
        findings = lint_batch_verification_scope_mismatch(text, slug="verify-slug")
        assert findings == [], (
            f"expected no finding with --scope full, got {findings}"
        )

    def test_no_full_suite_mention_no_finding(self) -> None:
        from plan_lint import lint_batch_verification_scope_mismatch

        body = (
            "Run targeted tests.\n\n"
            "```yaml\nlocal_checks:\n"
            "  - command: pytest --scope auto -q\n    timeout: 180\n```\n"
        )
        text = _make_subplan_text("verify-slug", body=body, batch_verification=True)
        findings = lint_batch_verification_scope_mismatch(text, slug="verify-slug")
        assert findings == [], (
            f"expected no finding without full-suite mention, got {findings}"
        )