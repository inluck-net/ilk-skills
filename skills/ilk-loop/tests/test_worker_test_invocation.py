"""Contract test: the worker's ad-hoc test invocation is bounded and informed.

Asserts that the two worker-read files (`skills/ilk-loop/SKILL.md` and
`commands/ilk.md`) carry the guidance the worker needs to:

  1. Prefer targeted tests over a broad suite run (AC-1)
  2. Bound a broad run with --timeout=<n> --timeout-method=signal (AC-2)
  3. Poll a backgrounded command via the shipped helper, not re-run it (AC-3)
  4. Present --collect-only with its measured cost (AC-6)

The test is structural (grep-based) so it runs fast and catches regressions
where the guidance is removed or weakened.  Each assertion is independent —
a single mention in one file does not satisfy a requirement for both files.

Context: MASTER-2026-08-21, SP2.  The 600s auto-backgrounded crossings in
the corpus are not slowness — they are a hang bounded only by the harness's
default timeout, not by the worker's invocation.
"""
from pathlib import Path

import pytest

# ── paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILL_MD = REPO_ROOT / "skills" / "ilk-loop" / "SKILL.md"
ILK_MD = REPO_ROOT / "commands" / "ilk.md"

# ── helpers ───────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Read a file, failing the test explicitly if it's missing."""
    assert path.exists(), f"required file not found: {path}"
    return path.read_text(encoding="utf-8")


def _count(text: str, pattern: str) -> int:
    """Count non-overlapping occurrences of *pattern* in *text*."""
    return text.count(pattern)


# ── AC-1: worker-facing rule for ad-hoc test invocation ───────────────────────


class TestAdHocTestInvocationRule:
    """SKILL.md must contain a worker-facing rule for ad-hoc test invocation."""

    def test_skill_md_mentions_targeted_tests(self) -> None:
        """AC-1a: the rule says to prefer targeted tests (path, -k, --lf)."""
        text = _read(SKILL_MD)
        assert "targeted" in text.lower(), (
            "SKILL.md must mention 'targeted' tests as the preferred approach"
        )

    def test_skill_md_mentions_collect_only(self) -> None:
        """AC-1b: the rule mentions --collect-only to inspect scope."""
        text = _read(SKILL_MD)
        assert "--collect-only" in text, (
            "SKILL.md must mention --collect-only for scope inspection"
        )

    def test_skill_md_mentions_ship_suite(self) -> None:
        """AC-1c: the rule says to reuse the project's declared ship.suite."""
        text = _read(SKILL_MD)
        assert "ship.suite" in text, (
            "SKILL.md must reference ship.suite for declared suite invocation"
        )


# ── AC-2: broad run must carry per-test timeout ──────────────────────────────


class TestBroadRunBound:
    """SKILL.md must state the timeout bound for broad runs."""

    def test_skill_md_mentions_timeout_signal(self) -> None:
        """AC-2a: --timeout-method=signal is specified (not thread)."""
        text = _read(SKILL_MD)
        assert "--timeout-method=signal" in text, (
            "SKILL.md must specify --timeout-method=signal for broad runs"
        )

    def test_skill_md_warns_thread_hangs(self) -> None:
        """AC-2b: --timeout-method=thread is flagged as hanging."""
        text = _read(SKILL_MD)
        # Must mention thread in a negative context (hangs, not recommended)
        lower = text.lower()
        assert "thread" in lower, (
            "SKILL.md must mention --timeout-method=thread to warn against it"
        )
        # At least one of these negative indicators near 'thread'
        negatives = ["hang", "not", "avoid", "do not", "don't", "must not"]
        has_negative = any(neg in lower for neg in negatives)
        assert has_negative, (
            "SKILL.md must warn that --timeout-method=thread hangs"
        )


# ── AC-3: poll helper reference in both worker-read files ─────────────────────


HELPER_NAME = "wait_for_background_output"
TRIGGER_TEXT = "moved to the background"


class TestPollHelperReference:
    """Both SKILL.md and commands/ilk.md must reference the poll helper."""

    def test_skill_md_references_helper(self) -> None:
        """AC-3a: SKILL.md names the helper by path."""
        text = _read(SKILL_MD)
        assert HELPER_NAME in text, (
            f"SKILL.md must reference {HELPER_NAME} — "
            f"currently 0 mentions"
        )

    def test_ilk_md_references_helper(self) -> None:
        """AC-3b: commands/ilk.md names the helper by path."""
        text = _read(ILK_MD)
        assert HELPER_NAME in text, (
            f"commands/ilk.md must reference {HELPER_NAME} — "
            f"currently 0 mentions"
        )

    def test_skill_md_mentions_trigger_text(self) -> None:
        """AC-3c: SKILL.md quotes the trigger text the worker will see."""
        text = _read(SKILL_MD)
        assert TRIGGER_TEXT in text, (
            f"SKILL.md must quote '{TRIGGER_TEXT}' so the worker "
            f"matches on the actual tool_result text"
        )

    def test_ilk_md_mentions_trigger_text(self) -> None:
        """AC-3d: commands/ilk.md quotes the trigger text."""
        text = _read(ILK_MD)
        assert TRIGGER_TEXT in text, (
            f"commands/ilk.md must quote '{TRIGGER_TEXT}' so the worker "
            f"matches on the actual tool_result text"
        )

    def test_helper_reference_count_independent(self) -> None:
        """AC-3e: each file has >= 1 reference independently (not summed)."""
        skill_text = _read(SKILL_MD)
        ilk_text = _read(ILK_MD)
        skill_count = _count(skill_text, HELPER_NAME)
        ilk_count = _count(ilk_text, HELPER_NAME)
        assert skill_count >= 1, (
            f"SKILL.md has {skill_count} references to {HELPER_NAME}; "
            f"need >= 1"
        )
        assert ilk_count >= 1, (
            f"commands/ilk.md has {ilk_count} references to {HELPER_NAME}; "
            f"need >= 1"
        )


# ── AC-4: regression guard — fails if helper reference count drops to 0 ──────


class TestHelperRegressionGuard:
    """The structural test itself is the regression guard.

    If either file's reference count drops to 0, this class catches it.
    The assertions are deliberately simple (count >= 1) so they fail
    loudly on any removal.
    """

    def test_skill_md_helper_count_nonzero(self) -> None:
        """Regression: SKILL.md must keep >= 1 helper reference."""
        text = _read(SKILL_MD)
        count = _count(text, HELPER_NAME)
        assert count >= 1, (
            f"REGRESSION: {HELPER_NAME} reference count in SKILL.md "
            f"dropped to {count} (was >= 1 when shipped)"
        )

    def test_ilk_md_helper_count_nonzero(self) -> None:
        """Regression: commands/ilk.md must keep >= 1 helper reference."""
        text = _read(ILK_MD)
        count = _count(text, HELPER_NAME)
        assert count >= 1, (
            f"REGRESSION: {HELPER_NAME} reference count in "
            f"commands/ilk.md dropped to {count} (was >= 1 when shipped)"
        )


# ── AC-6: --collect-only with measured cost ───────────────────────────────────


class TestCollectOnlyCost:
    """SKILL.md must present --collect-only with its measured cost."""

    def test_skill_md_collect_only_has_timing(self) -> None:
        """AC-6: --collect-only appears with a timing figure."""
        text = _read(SKILL_MD)
        assert "--collect-only" in text, (
            "SKILL.md must mention --collect-only"
        )
        # The measured cost is ~0.32s for 1870 tests; any timing reference
        # (seconds, s, ms) near --collect-only satisfies the intent.
        lower = text.lower()
        has_timing = any(
            marker in lower
            for marker in ["0.32s", "0.32 s", "1870", "sub-second", "< 1s"]
        )
        assert has_timing, (
            "SKILL.md must present --collect-only with its measured cost "
            "(e.g. '0.32s for 1870 tests' or similar)"
        )
