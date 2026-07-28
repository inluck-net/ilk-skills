"""Canonical plan-slug and sub-plan-filename patterns.

Single source of truth for the shape of a sub-plan filename:

    YYYY-MM-DD[<letter>]-<slug>[.md]

The optional single lowercase letter after the date disambiguates **more
than one batch planned on the same day** — e.g. ``2026-07-28-wire-push-and-pr``
and ``2026-07-28b-doctor-is-a-gate`` are two batches from 2026-07-28.

Why this module exists
----------------------
This pattern was duplicated as an inline regex literal across eight call
sites in four skills (``loop_status``, ``plan_status``,
``promote_next_master``, ``gen_mock_masters``, ``collect``,
``status_progress``).  When the letter suffix was introduced, only one of
those literals was updated, so the loop could *discover* a
``2026-07-28b-*`` sub-plan while five other parsers silently failed to
parse the same filename — a partial fix that turns a clean
"file not found" into a harder-to-diagnose half-parsed state.  Any change
to the filename shape must happen HERE, once.

Consumers in ``ilk-loop/scripts`` import this directly (same directory).
Consumers in sibling skills resolve this directory relative to their own
``__file__`` — see ``scheduler_scan.py`` for the established pattern.
"""

from __future__ import annotations

import re

#: The date-prefix fragment, with the optional same-day disambiguator.
#: Embed this in a larger pattern rather than re-typing the date shape.
DATE_PREFIX = r"\d{4}-\d{2}-\d{2}[a-z]?"

#: The slug body following the date prefix (lowercase, digits, hyphens).
SLUG_BODY = r"[a-z0-9][a-z0-9-]*"

# The lookbehind requires the preceding character to be a non-path
# character (start-of-line, whitespace, bracket, paren, or ``./``) — but
# specifically NOT ``/``, which would mean the filename lives in a
# subdirectory and is therefore not a top-level sub-plan.
_REF_LEAD = r"(?:^|(?<=[\s(\[|]))(?:\./)?"

#: Sub-plan references in a master body, ``.md`` REQUIRED.
SUBPLAN_REF_RE = re.compile(
    rf"{_REF_LEAD}({DATE_PREFIX}-{SLUG_BODY}\.md)",
    re.MULTILINE,
)

#: Sub-plan references in a master body, ``.md`` OPTIONAL (registry tables
#: sometimes list the bare slug).  Callers normalise the suffix themselves.
SUBPLAN_REF_OPTIONAL_MD_RE = re.compile(
    rf"{_REF_LEAD}({DATE_PREFIX}-{SLUG_BODY}(?:\.md)?)",
    re.MULTILINE,
)

_DATE_SPLIT_RE = re.compile(rf"^({DATE_PREFIX})-(.+)$")
_DATE_LEAD_RE = re.compile(rf"^{DATE_PREFIX}-")


def split_date_prefix(slug: str) -> tuple[str, str] | None:
    """Split *slug* into ``(date_prefix, remainder)``.

    Returns ``None`` when *slug* carries no date prefix.  The date prefix
    includes the same-day letter when present:

    >>> split_date_prefix("2026-07-28b-doctor-is-a-gate")
    ('2026-07-28b', 'doctor-is-a-gate')
    >>> split_date_prefix("doctor-is-a-gate") is None
    True
    """
    m = _DATE_SPLIT_RE.match(slug)
    return (m.group(1), m.group(2)) if m else None


def strip_date_prefix(slug: str) -> str:
    """Strip a leading date prefix from *slug*.

    Idempotent: a slug with no date prefix is returned unchanged.

    >>> strip_date_prefix("2026-07-28b-doctor-is-a-gate")
    'doctor-is-a-gate'
    >>> strip_date_prefix("doctor-is-a-gate")
    'doctor-is-a-gate'
    """
    parts = split_date_prefix(slug)
    return parts[1] if parts else slug


def has_date_prefix(slug: str) -> bool:
    """Return True when *slug* starts with a ``YYYY-MM-DD[<letter>]-`` prefix.

    >>> has_date_prefix("2026-07-28b-doctor-is-a-gate")
    True
    >>> has_date_prefix("combat-vfx")
    False
    """
    return bool(_DATE_LEAD_RE.match(slug))
