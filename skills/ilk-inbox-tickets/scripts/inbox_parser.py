"""Parse the cross-project handoffs inbox (~/Documents/handoffs/_inbox.md)
into structured entry objects.

Stdlib only — mirrors ilk-lark-tickets/lark_client.py conventions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_INBOX = Path.home() / "Documents" / "handoffs" / "_inbox.md"

# Split on H2 date headings: ## YYYY-MM-DD — <slug>
_HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (\S+)", re.MULTILINE)

# Match **Key**: value lines
_FIELD_RE = re.compile(r"^\*\*([^*]+)\*\*:\s*(.*)", re.MULTILINE)


@dataclass
class Entry:
    """A single inbox entry."""

    slug: str
    date: str
    fields: dict[str, str]
    body: str
    related_handoff: Path | None = None
    status: dict[str, Any] = field(default_factory=dict)


def parse_status(text: str) -> dict[str, Any]:
    """Parse a prose **Status** value into {state, remaining}.

    Detects lifecycle state from the leading token and captures
    remaining-scope prose when a REMAINING marker is present.
    """
    stripped = text.strip()
    lower = stripped.lower()

    state = "pending"
    for candidate in ("shipped", "in-progress", "blocked", "pending"):
        if lower.startswith(candidate):
            state = candidate
            break

    remaining = ""
    rem_match = re.search(r"REMAINING[:\s]+(.*)", stripped, re.IGNORECASE)
    if rem_match:
        remaining = rem_match.group(1).strip()

    return {"state": state, "remaining": remaining}


def _parse_entry_block(
    date: str, slug: str, block: str, inbox_dir: Path
) -> Entry:
    """Parse one entry block (text between two H2 headings) into an Entry."""
    fields: dict[str, str] = {}
    body = block.strip()

    for m in _FIELD_RE.finditer(block):
        key = m.group(1).strip()
        value = m.group(2).strip()
        fields[key] = value

    # Resolve **Related** to a handoff file if it references *-handoff.md
    related_handoff: Path | None = None
    related_text = fields.get("Related", "")
    if related_text and related_text.endswith("-handoff.md"):
        candidate = inbox_dir / related_text
        related_handoff = candidate.resolve()

    status = parse_status(fields.get("Status", ""))

    return Entry(
        slug=slug,
        date=date,
        fields=fields,
        body=body,
        related_handoff=related_handoff,
        status=status,
    )


def parse_inbox(path: str | Path | None = None) -> list[Entry]:
    """Parse an inbox markdown file into a list of Entry objects.

    Args:
        path: Path to the inbox file. Defaults to
              ``~/Documents/handoffs/_inbox.md``.

    Returns:
        List of Entry objects in document order.
    """
    inbox_path = Path(path) if path else _DEFAULT_INBOX
    text = inbox_path.read_text(encoding="utf-8")
    inbox_dir = inbox_path.parent.resolve()

    # Find all H2 heading positions
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []

    entries: list[Entry] = []
    for i, m in enumerate(matches):
        date = m.group(1)
        slug = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        entries.append(_parse_entry_block(date, slug, block, inbox_dir))

    return entries
