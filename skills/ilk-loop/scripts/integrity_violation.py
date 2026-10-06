"""Parser for runner ship_integrity_violation lines.

Extracts structured violation data from the three runner VIOLATION line
formats and the [gates] marker line written by gate_snapshot.py.

Used by ilk_triage (build_evidence) and collect (_integrity_violations)
so that both readers share one parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

_VIOLATION_RE = re.compile(r"\[ship-integrity VIOLATION\]\s*(.+)")
_GATES_RE = re.compile(r"\[gates\]\s*([^:]+):")


def parse(lines: Iterable[str]) -> list[dict]:
    """Return one dict per runner VIOLATION line, in log order, de-duplicated
    on (kind, slug), keeping the first."""
    seen: set[tuple[str, str | None]] = []
    results: list[dict] = []

    for line in lines:
        # Check for [gates] marker first (not a VIOLATION line)
        gates_m = _GATES_RE.search(line)
        if gates_m:
            slug = gates_m.group(1).strip()
            key = ("gates-edited", slug)
            if key not in seen:
                seen.append(key)
                results.append({"kind": "gates-edited", "slug": slug})
            continue

        # Check for [ship-integrity VIOLATION]
        viol_m = _VIOLATION_RE.search(line)
        if not viol_m:
            continue

        rest = viol_m.group(1).strip()

        # Worker-edit forms: check first, their text begins with "the worker"
        if rest.startswith("the worker changed gates for "):
            # "the worker changed gates for <slug>; restored to ..."
            slug = rest[len("the worker changed gates for "):].split(";")[0].strip()
            key = ("gates-edited", slug)
            if key not in seen:
                seen.append(key)
                results.append({"kind": "gates-edited", "slug": slug})
        elif rest.startswith("the worker changed master state"):
            key = ("master-edited", None)
            if key not in seen:
                seen.append(key)
                results.append({"kind": "master-edited", "slug": None})
        else:
            # Red-ship: "<slug>: <reason>"
            colon_idx = rest.find(":")
            if colon_idx > 0:
                slug = rest[:colon_idx].strip()
                reason = rest[colon_idx + 1:].strip()
                key = ("red-ship", slug)
                if key not in seen:
                    seen.append(key)
                    results.append({"kind": "red-ship", "slug": slug, "reason": reason})

    return results


def parse_file(path: Path | str | None) -> list[dict]:
    """Parse a launcher log file. Returns [] when the file is missing or
    unreadable, or when path is None."""
    if path is None:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return parse(text.splitlines())