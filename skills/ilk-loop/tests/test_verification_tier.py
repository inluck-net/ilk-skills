"""Tests for verification_tier surfacing in loop_status.py.

Builds a temp plans dir with mixed/absent verification_tier values and
asserts resolve_status carries the correct tier on each subplan dict.
Also tests text-mode tier marker rendering.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Import the module under test — adjust path so it works whether pytest
# is invoked from repo root or from the tests/ directory itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from loop_status import resolve_status, main as loop_status_main  # noqa: E402


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


class TestTextModeTierMarker:
    """Text-mode output marks non-loop-verified shipped sub-plans."""

    def _run_text_mode(self, plans_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
        """Run main() in text mode with cwd set to plans_dir parent, return stdout."""
        monkeypatch.chdir(plans_dir)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        return capsys.readouterr().out

    def test_tier_marker_present_for_non_loop_verified(self, plans_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        out = self._run_text_mode(plans_dir, monkeypatch, capsys)
        # beta is shipped with device-manual tier
        assert "needs-verify:device-manual" in out

    def test_tier_marker_absent_for_pending_non_loop_verified(self, plans_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """A non-loop-verified tier is only a signal once SHIPPED.

        alpha is pending + compile-only — it must NOT be marked, even though
        its tier is non-loop-verified. (Marking pending rows would dilute the
        'needs human verification' signal that only applies to shipped work.)
        """
        out = self._run_text_mode(plans_dir, monkeypatch, capsys)
        assert "needs-verify:compile-only" not in out
        # ...while the shipped non-loop-verified row is still marked.
        assert "needs-verify:device-manual" in out

    def test_text_output_is_ascii_safe(self, plans_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """loop_status text output must encode on a non-UTF-8 console.

        Regression guard: the runner keys off this script's exit code, so a
        UnicodeEncodeError (e.g. a "⚠" glyph on a zh-CN cp936/GBK console)
        crashed the script → exit 1 → runner read it as pending work → false
        stuck-no-progress (wechat-relay, run 20260608-104937). The marker
        path is exercised here (beta is shipped + device-manual).
        """
        out = self._run_text_mode(plans_dir, monkeypatch, capsys)
        # Must round-trip through GBK (the operator's console) without raising.
        out.encode("gbk")
        out.encode("ascii")

    def test_tier_marker_absent_for_all_loop_verified(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """All-loop-verified batch should have NO tier markers."""
        d = tmp_path / "docs" / "plans"
        d.mkdir(parents=True)
        master = textwrap.dedent("""\
            ---
            master_plan: 2026-06-08-test2
            batch_date: 2026-06-08
            status: active
            total_tickets: 2
            ---

            ## Sub-plan registry

            | # | Slug | Status |
            |---|---|---|
            | 1 | 2026-06-08-x.md | shipped |
            | 2 | 2026-06-08-y.md | shipped |
        """)
        sub_x = textwrap.dedent("""\
            ---
            plan: x
            status: shipped
            current_step: 2
            estimated_steps: 2
            ---

            # X
        """)
        sub_y = textwrap.dedent("""\
            ---
            plan: y
            status: shipped
            current_step: 3
            estimated_steps: 3
            ---

            # Y
        """)
        (d / "MASTER-2026-06-08-test2.md").write_text(master, encoding="utf-8")
        (d / "2026-06-08-x.md").write_text(sub_x, encoding="utf-8")
        (d / "2026-06-08-y.md").write_text(sub_y, encoding="utf-8")
        out = self._run_text_mode(tmp_path, monkeypatch, capsys)
        assert "⚠" not in out


class TestDraftMasterReporting:
    """A draft master is HELD (non-runnable) — must not be reported as 'all shipped'.

    Regression: loop_status nulls `next` for a draft master (correct, so the
    runner won't execute it), but the text branch printed "All N sub-plans
    shipped -- nothing to do" even though the sub-plans were pending, not
    shipped. That false report appeared in a real run log (2026-06-08).
    """

    def _draft_plans(self, tmp_path: Path) -> Path:
        d = tmp_path / "docs" / "plans"
        d.mkdir(parents=True)
        master = textwrap.dedent("""\
            ---
            master_plan: 2026-06-08-draft-test
            status: draft
            ---

            ## Sub-plan registry

            | # | Slug | Status |
            |---|---|---|
            | 1 | 2026-06-08-held.md | pending |
        """)
        sub = textwrap.dedent("""\
            ---
            plan: held
            status: pending
            current_step: 0
            estimated_steps: 3
            ---

            # Held
        """)
        (d / "MASTER-2026-06-08-draft-test.md").write_text(master, encoding="utf-8")
        (d / "2026-06-08-held.md").write_text(sub, encoding="utf-8")
        return tmp_path

    def test_resolve_status_reports_draft_held(self, tmp_path: Path) -> None:
        data = resolve_status(self._draft_plans(tmp_path))
        assert data["master_status"] == "draft"
        assert data["next"] is None          # non-runnable: runner won't execute it
        assert data["queue_exit"] == 0
        assert data["subplans"][0]["status"] == "pending"  # NOT shipped

    def test_text_does_not_claim_all_shipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        root = self._draft_plans(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        out = capsys.readouterr().out
        assert "held" in out.lower()
        assert "shipped -- nothing to do" not in out  # the false message
        out.encode("gbk"); out.encode("ascii")  # stays console-safe
