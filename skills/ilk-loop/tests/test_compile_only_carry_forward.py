"""Tests for compile-only carry-forward enforcement in loop_status.py.

Builds temp plans dirs with mixed verification_tier values and asserts:
  - AC-1: a shipped compile-only sub-plan triggers a HUMAN VERIFY REQUIRED
          banner with correct count and slugs.
  - AC-2: all-loop-verified batches produce NO banner (no false alarm).
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from loop_status import _compile_only_summary, resolve_status, main as loop_status_main  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MASTER_MIXED = textwrap.dedent("""\
    ---
    master_plan: 2026-06-28-test
    batch_date: 2026-06-28
    status: active
    total_tickets: 4
    ---

    ## Sub-plan registry

    | # | Slug | Status |
    |---|---|---|
    | 1 | 2026-06-28-alpha.md | shipped |
    | 2 | 2026-06-28-beta.md | shipped |
    | 3 | 2026-06-28-gamma.md | shipped |
    | 4 | 2026-06-28-delta.md | pending |
""")

SUB_ALPHA_SHIPPED_COMPILE = textwrap.dedent("""\
    ---
    plan: alpha
    status: shipped
    current_step: 3
    estimated_steps: 3
    verification_tier: compile-only
    ---

    # Alpha (compile-only)
""")

SUB_BETA_SHIPPED_DEVICE = textwrap.dedent("""\
    ---
    plan: beta
    status: shipped
    current_step: 3
    estimated_steps: 3
    verification_tier: device-manual
    ---

    # Beta (device-manual)
""")

SUB_GAMMA_SHIPPED_LOOP = textwrap.dedent("""\
    ---
    plan: gamma
    status: shipped
    current_step: 2
    estimated_steps: 2
    ---

    # Gamma (loop-verified by default)
""")

SUB_DELTA_PENDING = textwrap.dedent("""\
    ---
    plan: delta
    status: pending
    current_step: 0
    estimated_steps: 2
    ---

    # Delta (pending)
""")


@pytest.fixture()
def plans_dir_mixed(tmp_path: Path) -> Path:
    """Plans dir with one compile-only, one device-manual, one loop-verified shipped, one pending."""
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-06-28-test.md").write_text(MASTER_MIXED, encoding="utf-8")
    (d / "2026-06-28-alpha.md").write_text(SUB_ALPHA_SHIPPED_COMPILE, encoding="utf-8")
    (d / "2026-06-28-beta.md").write_text(SUB_BETA_SHIPPED_DEVICE, encoding="utf-8")
    (d / "2026-06-28-gamma.md").write_text(SUB_GAMMA_SHIPPED_LOOP, encoding="utf-8")
    (d / "2026-06-28-delta.md").write_text(SUB_DELTA_PENDING, encoding="utf-8")
    return tmp_path


MASTER_ALL_LOOP = textwrap.dedent("""\
    ---
    master_plan: 2026-06-28-clean
    batch_date: 2026-06-28
    status: active
    total_tickets: 2
    ---

    ## Sub-plan registry

    | # | Slug | Status |
    |---|---|---|
    | 1 | 2026-06-28-x.md | shipped |
    | 2 | 2026-06-28-y.md | shipped |
""")

SUB_X_SHIPPED_LOOP = textwrap.dedent("""\
    ---
    plan: x
    status: shipped
    current_step: 2
    estimated_steps: 2
    ---

    # X (loop-verified)
""")

SUB_Y_SHIPPED_LOOP = textwrap.dedent("""\
    ---
    plan: y
    status: shipped
    current_step: 3
    estimated_steps: 3
    ---

    # Y (loop-verified)
""")


@pytest.fixture()
def plans_dir_all_loop(tmp_path: Path) -> Path:
    """Plans dir with all shipped sub-plans loop-verified."""
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-06-28-clean.md").write_text(MASTER_ALL_LOOP, encoding="utf-8")
    (d / "2026-06-28-x.md").write_text(SUB_X_SHIPPED_LOOP, encoding="utf-8")
    (d / "2026-06-28-y.md").write_text(SUB_Y_SHIPPED_LOOP, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests for _compile_only_summary helper
# ---------------------------------------------------------------------------


class TestCompileOnlySummaryHelper:
    """Direct tests for the _compile_only_summary helper."""

    def test_returns_none_for_all_loop_verified(self) -> None:
        subplans = [
            {"status": "shipped", "verification_tier": "loop-verified"},
            {"status": "shipped", "verification_tier": "loop-verified"},
        ]
        assert _compile_only_summary(subplans) is None

    def test_returns_none_when_no_shipped(self) -> None:
        subplans = [
            {"status": "pending", "verification_tier": "compile-only"},
            {"status": "in-progress", "verification_tier": "device-manual"},
        ]
        assert _compile_only_summary(subplans) is None

    def test_detects_compile_only(self) -> None:
        subplans = [
            {"slug": "foo", "status": "shipped", "verification_tier": "compile-only"},
        ]
        result = _compile_only_summary(subplans)
        assert result is not None
        assert "HUMAN VERIFY REQUIRED: 1 compile-only/device-manual sub-plan" in result
        assert "foo" in result

    def test_detects_device_manual(self) -> None:
        subplans = [
            {"slug": "bar", "status": "shipped", "verification_tier": "device-manual"},
        ]
        result = _compile_only_summary(subplans)
        assert result is not None
        assert "bar" in result

    def test_correct_count_mixed(self) -> None:
        subplans = [
            {"slug": "a", "status": "shipped", "verification_tier": "compile-only"},
            {"slug": "b", "status": "shipped", "verification_tier": "device-manual"},
            {"slug": "c", "status": "shipped", "verification_tier": "loop-verified"},
        ]
        result = _compile_only_summary(subplans)
        assert result is not None
        assert "HUMAN VERIFY REQUIRED: 2 compile-only/device-manual sub-plans" in result
        # Check slugs line specifically to avoid false matches with "slugs" word
        slugs_line = [line for line in result.splitlines() if line.startswith("  slugs:")][0]
        assert "a" in slugs_line
        assert "b" in slugs_line
        # 'c' (loop-verified) must NOT appear in the slugs list
        slugs_value = slugs_line.split(":", 1)[1].strip()
        assert slugs_value == "a, b"

    def test_singular_form(self) -> None:
        subplans = [
            {"slug": "only-one", "status": "shipped", "verification_tier": "compile-only"},
        ]
        result = _compile_only_summary(subplans)
        assert "1 compile-only/device-manual sub-plan\n" in result  # singular, no trailing 's'

    def test_plural_form(self) -> None:
        subplans = [
            {"slug": "a", "status": "shipped", "verification_tier": "compile-only"},
            {"slug": "b", "status": "shipped", "verification_tier": "compile-only"},
        ]
        result = _compile_only_summary(subplans)
        assert "2 compile-only/device-manual sub-plans" in result

    def test_ascii_only_output(self) -> None:
        """Banner must round-trip through GBK without raising."""
        subplans = [
            {"slug": "x", "status": "shipped", "verification_tier": "compile-only"},
        ]
        result = _compile_only_summary(subplans)
        result.encode("gbk")
        result.encode("ascii")


# ---------------------------------------------------------------------------
# Integration tests: resolve_status / text mode
# ---------------------------------------------------------------------------


class TestCompileOnlySummaryInResolveStatus:
    """resolve_status carries compile_only_summary."""

    def test_summary_present_when_offenders_exist(self, plans_dir_mixed: Path) -> None:
        data = resolve_status(plans_dir_mixed)
        summary = data.get("compile_only_summary")
        assert summary is not None
        assert "HUMAN VERIFY REQUIRED" in summary
        assert "alpha" in summary
        assert "beta" in summary

    def test_summary_none_when_all_loop_verified(self, plans_dir_all_loop: Path) -> None:
        data = resolve_status(plans_dir_all_loop)
        assert data.get("compile_only_summary") is None

    def test_json_payload_includes_summary(self, plans_dir_mixed: Path) -> None:
        data = resolve_status(plans_dir_mixed)
        payload = json.loads(json.dumps(data, ensure_ascii=False))
        assert "compile_only_summary" in payload
        assert payload["compile_only_summary"] is not None
        assert "HUMAN VERIFY REQUIRED" in payload["compile_only_summary"]


class TestCompileOnlySummaryTextMode:
    """Text-mode output includes the summary banner."""

    def _run_text(self, plans_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
        monkeypatch.chdir(plans_dir)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        return capsys.readouterr().out

    def test_banner_present_with_offenders(self, plans_dir_mixed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        out = self._run_text(plans_dir_mixed, monkeypatch, capsys)
        assert "HUMAN VERIFY REQUIRED" in out
        assert "alpha" in out
        assert "beta" in out

    def test_banner_absent_when_all_loop_verified(self, plans_dir_all_loop: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        out = self._run_text(plans_dir_all_loop, monkeypatch, capsys)
        assert "HUMAN VERIFY REQUIRED" not in out

    def test_banner_ascii_safe(self, plans_dir_mixed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        out = self._run_text(plans_dir_mixed, monkeypatch, capsys)
        # Must round-trip through GBK (zh-CN console) without raising.
        out.encode("gbk")
        out.encode("ascii")
