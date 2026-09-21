"""Tests for the quota-exhaustion classifier.

Design: docs/architecture/provider-switching-and-quota-fallback.md §8.1.

The discriminator is the reset timestamp, not the status code.  A bare 429
is a rate limit; a 429 with a future reset timestamp is a quota cap.

Red-first: ``skills/ilk-loop/scripts/quota_detect.py`` does not exist yet;
every test here fails until step 1 implements it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure the module under test is importable.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from quota_detect import classify, CONSECUTIVE_ERROR_THRESHOLD

# ── Timestamps ────────────────────────────────────────────────────────────────

# "now" for tests — a fixed point so assertions are deterministic.
NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
NOW_ISO = NOW.isoformat()

# A reset time in the future (tomorrow).
FUTURE_RESET = (NOW + timedelta(days=1)).isoformat()

# A reset time in the past (yesterday).
PAST_RESET = (NOW - timedelta(days=1)).isoformat()


# ── Payloads ──────────────────────────────────────────────────────────────────

def _transient_429s_then_success() -> list[dict]:
    """Ten api_retry 429 lines followed by a successful result.

    Mirrors the 2026-09-21 iteration 02 shape: the provider rate-limited
    transiently but the iteration eventually succeeded.
    """
    lines = []
    for i in range(10):
        lines.append({
            "type": "api_retry",
            "status": 429,
            "attempt": i + 1,
            "timestamp": (NOW + timedelta(seconds=i)).isoformat(),
        })
    # Terminal: success.
    lines.append({
        "type": "result",
        "is_error": False,
        "result": "ok",
        "total_tokens": 42,
        "timestamp": (NOW + timedelta(seconds=20)).isoformat(),
    })
    return lines


def _cap_with_future_reset() -> list[dict]:
    """Terminal payload carrying a future reset timestamp.

    This is the real 2026-09-21 cap: the provider returned a quota-exceeded
    error with a reset time in the future.
    """
    return [
        {
            "type": "api_retry",
            "status": 429,
            "attempt": 1,
            "timestamp": NOW.isoformat(),
        },
        {
            "type": "result",
            "is_error": True,
            "error": f"429 · [1310] 您已达到每周/每月使用上限，您的限额将在 {FUTURE_RESET} 重置",
            "reset_at": FUTURE_RESET,
            "total_tokens": 0,
            "timestamp": (NOW + timedelta(seconds=5)).isoformat(),
        },
    ]


def _cap_with_past_reset() -> list[dict]:
    """Terminal payload whose reset timestamp is in the past.

    The window already reopened — this is not exhaustion.
    """
    return [
        {
            "type": "result",
            "is_error": True,
            "error": f"429 · [1310] 您已达到每周/每月使用上限，您的限额将在 {PAST_RESET} 重置",
            "reset_at": PAST_RESET,
            "total_tokens": 0,
            "timestamp": NOW.isoformat(),
        },
    ]


def _consecutive_api_errors(threshold: int) -> list[list[dict]]:
    """N consecutive iterations ending api_error with 0 tokens.

    Returns a list of iteration payloads (each is a list of dicts).
    """
    iterations = []
    for i in range(threshold):
        iterations.append([
            {
                "type": "result",
                "is_error": True,
                "error": "api_error",
                "total_tokens": 0,
                "timestamp": (NOW + timedelta(minutes=i)).isoformat(),
            },
        ])
    return iterations


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTransient429:
    """AC1: transient 429s + successful result → not exhausted."""

    def test_not_exhausted(self):
        payload = _transient_429s_then_success()
        result = classify(payload, now=NOW)
        assert result["exhausted"] is False
        assert result.get("reset_at") is None


class TestCapWithFutureReset:
    """AC2: terminal payload with future reset → exhausted."""

    def test_exhausted(self):
        payload = _cap_with_future_reset()
        result = classify(payload, now=NOW)
        assert result["exhausted"] is True

    def test_reset_at_parsed_iso8601(self):
        """reset_at must be ISO-8601 with offset."""
        payload = _cap_with_future_reset()
        result = classify(payload, now=NOW)
        assert result["reset_at"] is not None
        # Must parse as a valid datetime with timezone offset.
        parsed = datetime.fromisoformat(result["reset_at"])
        assert parsed.tzinfo is not None


class TestCapWithPastReset:
    """AC3: terminal payload whose reset timestamp is in the past → not exhausted."""

    def test_not_exhausted(self):
        payload = _cap_with_past_reset()
        result = classify(payload, now=NOW)
        assert result["exhausted"] is False


class TestConsecutiveErrors:
    """AC4: N consecutive api_error with 0 tokens → exhausted."""

    def test_threshold_triggers_exhaustion(self):
        iterations = _consecutive_api_errors(CONSECUTIVE_ERROR_THRESHOLD)
        # Classify each iteration; the Nth should trigger.
        for i, payload in enumerate(iterations[:-1]):
            result = classify(payload, now=NOW, consecutive_errors=i + 1)
            # Before threshold, not exhausted (unless other signal).
        result = classify(iterations[-1], now=NOW,
                          consecutive_errors=CONSECUTIVE_ERROR_THRESHOLD)
        assert result["exhausted"] is True

    def test_below_threshold_not_exhausted(self):
        iterations = _consecutive_api_errors(CONSECUTIVE_ERROR_THRESHOLD - 1)
        payload = iterations[-1]
        result = classify(payload, now=NOW,
                          consecutive_errors=CONSECUTIVE_ERROR_THRESHOLD - 1)
        assert result["exhausted"] is False

    def test_threshold_is_named_constant(self):
        """The threshold must be importable, not a buried literal."""
        assert isinstance(CONSECUTIVE_ERROR_THRESHOLD, int)
        assert CONSECUTIVE_ERROR_THRESHOLD >= 2


class TestPureFunction:
    """AC5: classify is a pure function — no file reads, no subprocesses,
    no clock other than the injected now."""

    def test_deterministic(self):
        payload = _cap_with_future_reset()
        r1 = classify(payload, now=NOW)
        r2 = classify(payload, now=NOW)
        assert r1 == r2

    def test_now_injection_controls_reset_comparison(self):
        """The same payload should be exhausted in one 'now' and not in another."""
        payload = _cap_with_future_reset()
        # When now is before the reset, it's exhausted.
        early = datetime(2026, 9, 21, 0, 0, 0,
                         tzinfo=timezone(timedelta(hours=8)))
        assert classify(payload, now=early)["exhausted"] is True
        # When now is after the reset, it's not.
        late = datetime(2026, 9, 23, 0, 0, 0,
                        tzinfo=timezone(timedelta(hours=8)))
        assert classify(payload, now=late)["exhausted"] is False


class TestGoldenBaseline:
    """AC7: golden fixture pins the classifier's verdicts.

    Changing N or any parsing rule must not silently reclassify a recorded
    payload.
    """

    GOLDEN_PATH = (
        Path(__file__).resolve().parent / "fixtures" / "quota-payloads" / "golden.json"
    )

    def test_golden_file_exists(self):
        assert self.GOLDEN_PATH.is_file(), (
            f"golden baseline not found: {self.GOLDEN_PATH}"
        )

    def test_golden_baseline_matches(self):
        """Every golden entry's expected classification must match."""
        baseline = json.loads(self.GOLDEN_PATH.read_text(encoding="utf-8"))
        bad = []
        for entry in baseline:
            result = classify(entry["payload"], now=entry["now"])
            if result["exhausted"] != entry["expected"]:
                bad.append(entry["name"])
        assert not bad, f"golden classification drifted: {bad}"