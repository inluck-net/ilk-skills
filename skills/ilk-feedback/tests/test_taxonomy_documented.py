"""Verify every classification label appears in the SKILL.md taxonomy table.

AC-1: Every label in CLASSIFICATION_LABELS has a row in the taxonomy table.
AC-2: Every Label-column entry in that table is a member of CLASSIFICATION_LABELS.
AC-3: Failure message names the offending labels.
AC-4: Both sets are derived at runtime — no hardcoded expected list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_SKILL_MD = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "SKILL.md"
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


def _table_labels() -> set[str]:
    """Parse the taxonomy table's Label column from SKILL.md."""
    doc = _SKILL_MD.read_text()
    return set(
        re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|", doc, re.MULTILINE)
    )


def _code_labels() -> set[str]:
    """Return the set of labels from the module constant."""
    return set(collect.CLASSIFICATION_LABELS)


class TestTaxonomyTableDrift:
    """The taxonomy table in SKILL.md and CLASSIFICATION_LABELS must agree."""

    def test_all_code_labels_documented(self):
        """AC-1: every label the code can emit has a row in the table."""
        code = _code_labels()
        documented = _table_labels()
        missing = code - documented
        assert not missing, (
            f"Labels in CLASSIFICATION_LABELS but missing from taxonomy table: "
            f"{sorted(missing)}"
        )

    def test_all_table_labels_are_real(self):
        """AC-2: every row in the table is a real label the code emits."""
        code = _code_labels()
        documented = _table_labels()
        phantom = documented - code
        assert not phantom, (
            f"Labels in taxonomy table but not in CLASSIFICATION_LABELS: "
            f"{sorted(phantom)}"
        )