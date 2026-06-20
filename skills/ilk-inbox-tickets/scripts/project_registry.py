"""Resolve inbox entries' ``**Project**:`` strings to on-disk repo roots.

Reads the registry from ``$ILK_DATA_HOME/inbox-projects.json`` (default
``~/.ilk-data/inbox-projects.json``).  The JSON shape::

    {
      "projects": {
        "<Project string>": {"path": "/abs/repo"},
        "<Project string>": {"not_plannable": true}
      }
    }

``<Project string>`` is the exact ``**Project**:`` value from the inbox
entry (case-sensitive).  When a registry value has ``"not_plannable": true``
the entry is intentionally excluded from ilk planning (e.g. template dirs,
design docs repos).

Stdlib only — mirrors ilk-lark-tickets/lark_client.py conventions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------

_NOT_PLANNABLE = type("NOT_PLANNABLE", (), {"__repr__": lambda s: "NOT_PLANNABLE"})()
_UNRESOLVED = type("UNRESOLVED", (), {"__repr__": lambda s: "UNRESOLVED"})()

NOT_PLANNABLE: Any = _NOT_PLANNABLE
UNRESOLVED: Any = _UNRESOLVED

# ---------------------------------------------------------------------------
# Data-home resolution (mirrors lark_client._resolve_data_root)
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY_FILE = "inbox-projects.json"


def _resolve_data_root() -> Path:
    """Resolve the canonical data root via ``ilk_paths.ilk_data_root()``.

    Tries a relative ``sys.path`` insert to import ``ilk_paths`` from the
    sibling ``ilk-loop/scripts`` directory.  Falls back to ``~/.ilk-data``.
    """
    try:
        here = Path(__file__).resolve()
        loop_scripts = here.parent.parent.parent / "ilk-loop" / "scripts"
        if loop_scripts.is_dir():
            if str(loop_scripts) not in sys.path:
                sys.path.insert(0, str(loop_scripts))
            import importlib
            import ilk_paths
            importlib.reload(ilk_paths)
            return ilk_paths.ilk_data_root()
    except Exception:
        pass
    return Path.home() / ".ilk-data"


def _registry_path() -> Path:
    """Return the canonical path to ``inbox-projects.json``."""
    return _resolve_data_root() / _DEFAULT_REGISTRY_FILE


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the inbox-projects registry from *path* (or the canonical location).

    Returns an empty ``{"projects": {}}`` dict when the file does not exist
    or is invalid JSON — never raises.
    """
    p = Path(path) if path else _registry_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"projects": {}}
        data.setdefault("projects", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"projects": {}}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(project_str: str, registry: dict[str, Any] | None = None) -> Any:
    """Resolve *project_str* to a repo-root path, ``NOT_PLANNABLE``, or ``UNRESOLVED``.

    Args:
        project_str: The raw ``**Project**:`` value from an inbox entry.
        registry: Pre-loaded registry dict (as returned by :func:`load_registry`).
            When ``None`` the canonical file is loaded.

    Returns:
        An absolute path string for a resolved project,
        :data:`NOT_PLANNABLE` for intentionally-excluded entries,
        or :data:`UNRESOLVED` for unmapped strings.

    Resolution order:
      1. Exact match on ``project_str``.
      2. If the string contains a space (e.g. ``"slug (path)"``), try the
         leading slug token (everything before the first space).
    """
    if registry is None:
        registry = load_registry()

    projects = registry.get("projects", {})

    # 1. Exact match
    if project_str in projects:
        return _resolve_entry(projects[project_str])

    # 2. Leading-slug fallback for "slug (path)" strings
    if " " in project_str:
        slug = project_str.split()[0]
        if slug in projects:
            return _resolve_entry(projects[slug])

    return UNRESOLVED


def _resolve_entry(entry_value: Any) -> Any:
    """Interpret a single registry project value."""
    if not isinstance(entry_value, dict):
        return UNRESOLVED
    if entry_value.get("not_plannable"):
        return NOT_PLANNABLE
    path = entry_value.get("path")
    if path:
        return path
    return UNRESOLVED


# ---------------------------------------------------------------------------
# Needs-mapping report
# ---------------------------------------------------------------------------


def needs_mapping(
    entries: list[Any], registry: dict[str, Any] | None = None
) -> list[Any]:
    """Return the subset of *entries* whose ``**Project**:`` is :data:`UNRESOLVED`.

    Each entry object must expose a ``fields`` dict with a ``"Project"`` key
    (the ``Entry`` dataclass from ``inbox_parser`` satisfies this).

    The returned list preserves the original order.
    """
    if registry is None:
        registry = load_registry()

    unresolved: list[Any] = []
    for entry in entries:
        project_str = entry.fields.get("Project", "")
        if not project_str:
            unresolved.append(entry)
        elif resolve(project_str, registry) is UNRESOLVED:
            unresolved.append(entry)
    return unresolved
