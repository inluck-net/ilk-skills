"""GitHub state enrichment for inbox entries.

Extracts ``#NNN`` issue/PR references from an entry's ``**Related**`` and
``**Status**`` fields, then optionally looks up each reference's live GitHub
state via an injectable runner (default: shells out to ``gh``).

Stdlib only — mirrors the conventions of ``inbox_parser.py``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"#(\d+)")


@dataclass
class GhRef:
    """A GitHub issue/PR reference with live state."""

    number: int
    state: str = ""  # "OPEN" | "CLOSED"
    is_closed: bool = False


@dataclass
class EnrichedEntry:
    """An inbox entry augmented with GitHub reference state."""

    slug: str
    refs: list[GhRef] = field(default_factory=list)
    has_closed_ref: bool = False


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------

# Type alias for the injectable runner: ``runner(issue_number) -> dict``
# where the dict has at least ``{"state": "OPEN"|"CLOSED"}``.
Runner = Callable[[int], dict[str, Any]]


def _default_runner(number: int) -> dict[str, Any]:
    """Default runner: shells out to ``gh issue view <n> --json state``."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(number), "--json", "state"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"state": "UNKNOWN", "error": result.stderr.strip()}
        data = json.loads(result.stdout)
        return {"state": data.get("state", "UNKNOWN")}
    except FileNotFoundError:
        return {"state": "UNKNOWN", "error": "gh not found"}
    except Exception as e:
        return {"state": "UNKNOWN", "error": str(e)}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def extract_refs(entry) -> set[int]:
    """Extract the set of ``#NNN`` numbers from an entry's Related/Status fields.

    Args:
        entry: An :class:`inbox_parser.Entry` (or any object with a ``.fields``
            dict containing ``Related`` and/or ``Status`` keys).

    Returns:
        Set of integer issue/PR numbers found. Empty set if none.
    """
    refs: set[int] = set()
    for key in ("Related", "Status"):
        text = entry.fields.get(key, "")
        for m in _REF_RE.finditer(text):
            refs.add(int(m.group(1)))
    return refs


def annotate(entry, runner: Runner | None = None) -> EnrichedEntry:
    """Look up each ``#NNN`` ref's live GitHub state and build an enriched entry.

    Args:
        entry: An :class:`inbox_parser.Entry`.
        runner: Callable ``(number) -> {"state": "OPEN"|"CLOSED", ...}``.
            Defaults to :func:`_default_runner` (shells ``gh``).

    Returns:
        :class:`EnrichedEntry` with refs populated and ``has_closed_ref`` set.
    """
    if runner is None:
        runner = _default_runner

    numbers = extract_refs(entry)
    refs: list[GhRef] = []
    has_closed = False

    for num in sorted(numbers):
        info = runner(num)
        state = info.get("state", "UNKNOWN")
        is_closed = state.upper() == "CLOSED"
        if is_closed:
            has_closed = True
        refs.append(GhRef(number=num, state=state, is_closed=is_closed))

    return EnrichedEntry(
        slug=entry.slug,
        refs=refs,
        has_closed_ref=has_closed,
    )
