#!/usr/bin/env python3
"""Per-project unified tracker store for ilk-feedback.

Thin layer over ``improvement_backlog`` that routes reads/writes to a
per-project tracker directory at ``<ilk_data_root>/projects/<key>/``,
resolved via ``ilk_paths``.

Design decision (batch 2 convergence): per-project tracker reuses the
``improvement_backlog`` IO layer (``backlog_dir`` parameter) and the
``source_id`` upsert path from batch 1. The global toolkit backlog at
``~/.ilk-data/ilk-skills-improvements/`` stays as-is.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Import ilk_paths from sibling skill ──────────────────────────────────────

_HERE = Path(__file__).resolve()
_LOOP_SCRIPTS = _HERE.parent.parent.parent / "ilk-loop" / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

import ilk_paths  # noqa: E402


# ── Public API ───────────────────────────────────────────────────────────────


def tracker_dir(
    project: str | Path | None = None,
    key: str | None = None,
) -> Path:
    """Return the per-project tracker directory path.

    Resolves the project key from *project* (an absolute path) via
    ``ilk_paths.resolve_project_key``.  Alternatively, pass *key* directly
    (bypasses resolution).

    Returns ``<ilk_data_root>/projects/<key>/`` — callers can use this
    as the ``backlog_dir`` parameter for ``improvement_backlog`` functions.

    Raises ``ValueError`` when neither *project* nor *key* is provided,
    or when *project* doesn't resolve to a valid project key.
    """
    if project is None and key is None:
        raise ValueError("provide either project (path) or key (str)")

    if key is None:
        resolved = ilk_paths.resolve_project_key(Path(project))
        if resolved is None:
            raise ValueError(
                f"cannot resolve project key from path: {project}"
            )
        key = resolved

    return ilk_paths.ilk_data_root() / "projects" / key
