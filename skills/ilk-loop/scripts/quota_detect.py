"""Quota-exhaustion classifier — a pure function over parsed JSON.

Design: docs/architecture/provider-switching-and-quota-fallback.md §8.1.

The discriminator is the reset timestamp, not the status code.  A bare 429
is a rate limit; a 429 with a future reset timestamp is a quota cap.

This module is a pure classifier — no file reads, no subprocesses, no clock
other than the injected ``now``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

# N consecutive iterations ending api_error with 0 tokens → exhausted.
# Named, not buried: tuning this value is a deliberate edit to the golden
# fixture, never a side effect.
CONSECUTIVE_ERROR_THRESHOLD = 3

# Pattern for extracting a reset timestamp from a 429 error message.
# Matches the Zhipu GLM format: "…将在 2026-09-22 15:16:35 重置"
_RESET_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_reset_at(raw: Any) -> Optional[datetime]:
    """Parse a reset_at value into a timezone-aware datetime.

    Accepts ISO-8601 strings (with or without offset) and the bare
    ``YYYY-MM-DD HH:MM:SS`` format from Zhipu's error messages.  Returns
    ``None`` on any parse failure — degrade-safe.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None

    # Try ISO-8601 first (most common in structured payloads).
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            # Assume UTC if no offset — conservative.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    # Try bare timestamp from error message text.
    m = _RESET_PATTERN.search(str(raw))
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            # Bare timestamps from Zhipu are in the local timezone (+08:00).
            dt = dt.replace(tzinfo=timezone(offset=__import__("datetime").timedelta(hours=8)))
            return dt
        except (ValueError, TypeError):
            pass

    return None


def _extract_reset_from_error(error: Any) -> Optional[str]:
    """Extract a reset timestamp string from a terminal error message.

    Returns the raw timestamp string (not parsed) so the caller can store
    it in the output.
    """
    if not isinstance(error, str):
        return None
    m = _RESET_PATTERN.search(error)
    return m.group(1) if m else None


def _get_terminal_result(payload: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the last entry with type 'result', or None."""
    for entry in reversed(payload):
        if isinstance(entry, dict) and entry.get("type") == "result":
            return entry
    return None


# ── Classifier ────────────────────────────────────────────────────────────────

def _ensure_datetime(value: Any) -> datetime:
    """Coerce a value to a timezone-aware datetime.

    Accepts ``datetime`` objects and ISO-8601 strings.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    raise TypeError(f"cannot coerce {type(value).__name__!r} to datetime")


def classify(
    payload: List[Dict[str, Any]],
    *,
    now: Any,
    consecutive_errors: int = 0,
) -> Dict[str, Any]:
    """Classify an iteration's terminal payload as exhausted or not.

    Parameters
    ----------
    payload : list[dict]
        Parsed JSON lines from one iteration.
    now : datetime
        The current time (timezone-aware).  Used to decide whether a reset
        timestamp is in the future or past.
    consecutive_errors : int
        How many consecutive iterations have ended ``api_error`` with 0
        tokens *before* this one.  When this reaches
        :data:`CONSECUTIVE_ERROR_THRESHOLD`, exhaustion is declared even
        without a reset timestamp.

    Returns
    -------
    dict
        ``{"exhausted": bool, "reset_at": str | None}``
    """
    now = _ensure_datetime(now)

    terminal = _get_terminal_result(payload)

    # ── Signal 1: reset timestamp in the terminal payload ─────────────────
    if terminal is not None:
        # Check the explicit reset_at field first, then the error message.
        reset_raw = terminal.get("reset_at")
        if reset_raw is None and terminal.get("is_error"):
            reset_raw = _extract_reset_from_error(terminal.get("error"))

        if reset_raw is not None:
            reset_dt = _parse_reset_at(reset_raw)
            if reset_dt is not None:
                if reset_dt > now:
                    # Future reset → quota cap active.
                    return {
                        "exhausted": True,
                        "reset_at": reset_dt.isoformat(),
                    }
                else:
                    # Past reset → window already reopened.
                    return {"exhausted": False, "reset_at": None}

    # ── Signal 2: consecutive error threshold ─────────────────────────────
    if consecutive_errors >= CONSECUTIVE_ERROR_THRESHOLD:
        return {"exhausted": True, "reset_at": None}

    # ── Default: not exhausted ────────────────────────────────────────────
    return {"exhausted": False, "reset_at": None}