"""Compute a stable fingerprint of a plan file's surface.

The surface is the part of the plan that a planner edits — everything
above ``## Findings``, with the frontmatter keys ``current_step``,
``status``, and ``last_updated`` excluded (the runner writes those).

For MASTER files, the surface also:
- blanks the Status cell of every row in the ``## Sub-plan registry``
  table (the reconciler writes those);
- drops the ``## Progress log`` section entirely (sessions append rows).

Two plan files that differ only below ``## Findings``, in the excluded
keys, in the registry Status cells, or in the Progress log produce the
same fingerprint.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

# Frontmatter keys the runner writes — excluded from the surface.
_EXCLUDED_FRONTMATTER_KEYS = ("current_step", "status", "last_updated")


def surface(text: str, *, master: bool = False) -> str:
    """Return the plan surface: the editable part above ``## Findings``.

    Parameters
    ----------
    text:
        The full plan file content.
    master:
        When true, also blank registry Status cells and drop the
        Progress log section.
    """
    lines = text.splitlines(keepends=True)

    # ── Step 1: strip excluded frontmatter keys (inside frontmatter only) ──
    in_frontmatter = False
    cleaned: list[str] = []
    for line in lines:
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                cleaned.append(line)
                continue
            else:
                in_frontmatter = False
                cleaned.append(line)
                continue
        if in_frontmatter:
            stripped = line.lstrip()
            if any(stripped.startswith(f"{k}:") for k in _EXCLUDED_FRONTMATTER_KEYS):
                continue
        cleaned.append(line)
    text = "".join(cleaned)

    # ── Step 2: cut at the first ``## Findings`` HEADING ──
    # Match ``## Findings`` as a heading: start of line, optional leading
    # whitespace, exactly ``## `` followed by ``Findings``, optional trailing
    # whitespace, then EOL.  This avoids matching inline mentions like
    # ``the `## Findings` section``.
    findings_re = re.compile(r"^\s*## Findings\s*$", re.MULTILINE)
    m = findings_re.search(text)
    if m:
        text = text[: m.start()]

    # ── Step 3: MASTER-specific exclusions ──
    if master:
        text = _blank_registry_status_cells(text)
        text = _drop_progress_log(text)

    return text


def _blank_registry_status_cells(text: str) -> str:
    """Blank the Status column of every row in the ``## Sub-plan registry`` table.

    Finds the column by its header name (case-insensitive), matching the
    logic in ``reconcile_master_registry``.
    """
    lines = text.splitlines(keepends=True)
    result: list[str] = []

    in_registry = False
    status_idx: int | None = None
    header_found = False

    for line in lines:
        stripped = line.strip()

        # Detect the registry section heading.
        if re.match(r"^## Sub-plan registry\s*$", stripped):
            in_registry = True
            status_idx = None
            header_found = False
            result.append(line)
            continue

        # End of registry section: next ``## `` heading.
        if in_registry and re.match(r"^## ", stripped):
            in_registry = False
            status_idx = None
            header_found = False
            result.append(line)
            continue

        if not in_registry:
            result.append(line)
            continue

        # We're inside the registry section.
        if not stripped.startswith("|"):
            # Not a table row.
            result.append(line)
            continue

        cells = line.rstrip("\n").rstrip("\r").split("|")

        # Header row: locate the Status column.
        if not header_found:
            for idx, cell in enumerate(cells):
                if cell.strip().lower() == "status":
                    status_idx = idx
                    break
            header_found = True
            result.append(line)
            continue

        # Separator row (|---|---|).
        if set(stripped.replace("|", "").replace(":", "").strip()) <= {"-"}:
            result.append(line)
            continue

        # Data row: blank the Status cell.
        if status_idx is not None and status_idx < len(cells):
            cells[status_idx] = " "
            result.append("|".join(cells) + "\n" if line.endswith("\n") else "|".join(cells))
        else:
            result.append(line)

    return "".join(result)


def _drop_progress_log(text: str) -> str:
    """Drop the ``## Progress log`` section, from its heading to the next ``## `` or EOF.

    Also strips any trailing blank lines left after removal.
    """
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    skipping = False

    for line in lines:
        stripped = line.strip()
        if re.match(r"^## Progress log\s*$", stripped):
            skipping = True
            continue
        if skipping and re.match(r"^## ", stripped):
            skipping = False
        if not skipping:
            result.append(line)

    # Strip trailing blank lines left after section removal.
    while result and result[-1].strip() == "":
        result.pop()

    return "".join(result)


def fingerprint(path: Path) -> str:
    """Return the sha256 hex digest of the plan surface.

    ``master`` is true when the file name starts with ``MASTER-``.
    Returns the hex digest; raises ``FileNotFoundError`` if the file is missing.
    """
    text = path.read_text(encoding="utf-8-sig")
    master = path.name.startswith("MASTER-")
    return hashlib.sha256(surface(text, master=master).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: plan_fingerprint.py <file>", file=sys.stderr)
        sys.exit(1)
    p = Path(sys.argv[1])
    if not p.exists():
        print(f"file not found: {p}", file=sys.stderr)
        sys.exit(1)
    print(fingerprint(p))