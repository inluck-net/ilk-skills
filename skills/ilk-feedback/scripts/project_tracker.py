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
import improvement_backlog  # noqa: E402


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


def add(
    *,
    title: str,
    source: str = "",
    source_id: str = "",
    kind: str = "toolkit",
    gap: str = "",
    project: str | Path | None = None,
    key: str | None = None,
    **kwargs,
) -> improvement_backlog.Entry:
    """Add or update an entry in the per-project tracker.

    Delegates to ``improvement_backlog.add_candidate`` with ``backlog_dir``
    resolved via :func:`tracker_dir`.  The ``(source, source_id)`` upsert
    path from batch 1 is inherited automatically.

    Returns the (possibly updated) ``Entry``.
    """
    td = tracker_dir(project=project, key=key)
    return improvement_backlog.add_candidate(
        title=title,
        kind=kind,
        gap=gap or title,
        source=source,
        source_id=source_id,
        backlog_dir=td,
        **kwargs,
    )


def load(
    *,
    project: str | Path | None = None,
    key: str | None = None,
) -> list[improvement_backlog.Entry]:
    """Load all entries from the per-project tracker."""
    td = tracker_dir(project=project, key=key)
    return improvement_backlog.load(backlog_dir=td)


def list_open(
    *,
    project: str | Path | None = None,
    key: str | None = None,
) -> list[improvement_backlog.Entry]:
    """List open entries from the per-project tracker."""
    td = tracker_dir(project=project, key=key)
    return improvement_backlog.list_entries(status="open", backlog_dir=td)


def set_status(
    entry_id: str,
    status: str,
    *,
    project: str | Path | None = None,
    key: str | None = None,
) -> None:
    """Flip an entry's status in the per-project tracker.

    Loads all entries, finds the one matching *entry_id*, updates its
    ``status`` field, and atomically saves back.  Raises ``KeyError``
    if no entry with that id exists.
    """
    td = tracker_dir(project=project, key=key)
    entries = improvement_backlog.load(backlog_dir=td)
    for entry in entries:
        if entry.id == entry_id:
            entry.status = status
            improvement_backlog._save_raw(
                td, [e.to_dict() for e in entries]
            )
            return
    raise KeyError(f"no entry with id {entry_id!r} in tracker")
