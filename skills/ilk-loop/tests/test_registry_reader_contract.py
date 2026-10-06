"""Contract test: every registry reader uses the canonical data-home path.

No Python reader may hardcode a ``skill-root/ilk-launcher/projects.json``
literal.  Each must resolve through ``ilk_data_root()`` (or the shell
equivalent) so that ``$ILK_DATA_HOME`` / ``$ILK_DATA_DIR`` move every
reader together.

The test greps the source files listed in ``_READER_SOURCES`` for the
forbidden pattern ``ilk-launcher/projects.json`` outside of acceptable
legacy-fallback contexts (comments, docstrings, and the canonical
``register_project.py`` which owns the migration logic).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Files that MUST route through the canonical data-home resolver.
_READER_SOURCES = [
    "skills/ilk-feedback/scripts/collect.py",
    "skills/ilk-watchdog/scripts/scheduler_scan.py",
    "skills/ilk-loop/scripts/status_all.py",
]

# Lines matching this pattern are acceptable (legacy fallback, comments,
# docstrings, or the canonical migration code in register_project.py).
_LEGACY_OK = re.compile(
    r"(?:^\s*#)"           # comment
    r"|(?:^\s*\"\"\")"     # docstring start
    r"|(?:``.*ilk-launcher)"  # docstring RST/markdown reference
    r"|(?:legacy)"         # legacy mention in prose
    r"|(?:_skill_root\(\))" # explicit legacy fallback call
    r"|(?:_SKILL_ROOT)"    # explicit legacy fallback variable
)


def _find_hardcoded_registry_lines(rel: str) -> list[tuple[int, str]]:
    """Return (line_number, text) for forbidden hardcoded registry refs."""
    path = REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(f"source not found: {rel}")
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if "ilk-launcher/projects.json" not in line:
            continue
        if _LEGACY_OK.search(line):
            continue
        hits.append((i, line.strip()))
    return hits


@pytest.mark.parametrize("rel", _READER_SOURCES)
def test_no_hardcoded_skill_root_registry(rel: str):
    """Reader must not hardcode ``ilk-launcher/projects.json``."""
    hits = _find_hardcoded_registry_lines(rel)
    assert not hits, (
        f"{rel} hardcodes the skill-root registry path on line(s) "
        f"{[h[0] for h in hits]}.  Route through ilk_data_root() instead."
    )