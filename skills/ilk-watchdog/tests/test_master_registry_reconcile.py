"""Reconcile the MASTER sub-plan registry table against the sub-plan files.

The registry row duplicates a fact the sub-plan front-matter already owns.
``reconcile_master_status`` deliberately rewrites only the front-matter
``status:`` line and leaves the table byte-for-byte alone, and nothing else kept
the copy honest, so a completed master could carry ``status: shipped`` while its
only registry row still read ``pending`` — observed 2026-08-03 on
``MASTER-issue-2340-2026-08-03.md``, whose sub-plan file said ``shipped``. Two of
three sources agreed and the table dissented; an external consumer that reads the
registry to decide whether work is finished concludes it is not.

AC-1: a shipped sub-plan's registry row is rewritten to ``shipped``
AC-2: idempotent — a table already in agreement is not rewritten
AC-3: the Status column is found by name, not position
AC-4: a registered sub-plan whose file is missing is left alone, not guessed
AC-5: rows for unregistered files, and every non-Status cell, are untouched
AC-6: front-matter and body outside the table are preserved byte-for-byte
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"

if str(SCRIPTS_ILK_LOOP) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))

import plan_status as ps  # noqa: E402


def _write_subplan(plans: Path, fname: str, status: str) -> None:
    plans.mkdir(parents=True, exist_ok=True)
    (plans / fname).write_text(
        "---\n"
        f"plan: {fname[:-3]}\n"
        f"status: {status}\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "---\n"
        "\n"
        "# Sub-plan\n",
        encoding="utf-8",
    )


def _master_text(rows: str, *, header: str = "| # | Sub-plan | Status |",
                 sep: str = "|---|---|---|") -> str:
    return (
        "---\n"
        "master_plan: issue-2340-2026-08-03\n"
        "status: shipped\n"
        "current_subplan: issue-2340-work\n"
        "---\n"
        "\n"
        "# MASTER — issue #2340\n"
        "\n"
        "## Provenance\n"
        "\n"
        "- **required_sections**: ['Input', 'Output', 'Done']\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        f"{header}\n"
        f"{sep}\n"
        f"{rows}"
    )


def test_shipped_subplan_row_is_reconciled(tmp_path: Path) -> None:
    """AC-1: the drift observed on the real master is corrected."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "2026-08-03-issue-2340-work.md", "shipped")
    master = plans / "MASTER-issue-2340-2026-08-03.md"
    master.write_text(
        _master_text(
            "| 1 | [2026-08-03-issue-2340-work.md](./2026-08-03-issue-2340-work.md) | pending |\n"
        ),
        encoding="utf-8",
    )

    assert ps.reconcile_master_registry(master, plans) is True
    text = master.read_text(encoding="utf-8")
    assert "| shipped |" in text
    assert "| pending |" not in text


def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    """AC-2: a table already in agreement is a no-op with no rewrite churn."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "2026-08-03-work.md", "shipped")
    master = plans / "MASTER-2026-08-03-x.md"
    master.write_text(
        _master_text("| 1 | [2026-08-03-work.md](./2026-08-03-work.md) | shipped |\n"),
        encoding="utf-8",
    )
    before = master.read_text(encoding="utf-8")

    assert ps.reconcile_master_registry(master, plans) is False
    assert master.read_text(encoding="utf-8") == before


def test_status_column_located_by_name(tmp_path: Path) -> None:
    """AC-3: a 4-column table with Status last is handled, not position-guessed."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "2026-08-03-work.md", "shipped")
    master = plans / "MASTER-2026-08-03-x.md"
    master.write_text(
        _master_text(
            "| 1 | [2026-08-03-work.md](./2026-08-03-work.md) | 3 | pending |\n",
            header="| # | Slug | Steps | Status |",
            sep="|---|---|---|---|",
        ),
        encoding="utf-8",
    )

    assert ps.reconcile_master_registry(master, plans) is True
    text = master.read_text(encoding="utf-8")
    assert "| shipped |" in text
    # The Steps cell must survive untouched.
    assert "| 3 |" in text


def test_missing_subplan_file_is_left_alone(tmp_path: Path) -> None:
    """AC-4: an unauthored sub-plan's row is not given an invented status."""
    plans = tmp_path / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    master = plans / "MASTER-2026-08-03-x.md"
    master.write_text(
        _master_text("| 1 | [2026-08-03-absent.md](./2026-08-03-absent.md) | pending |\n"),
        encoding="utf-8",
    )
    before = master.read_text(encoding="utf-8")

    assert ps.reconcile_master_registry(master, plans) is False
    assert master.read_text(encoding="utf-8") == before


def test_only_registered_rows_and_status_cells_change(tmp_path: Path) -> None:
    """AC-5 + AC-6: everything except the registered row's Status cell survives."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "2026-08-03-work.md", "shipped")
    master = plans / "MASTER-2026-08-03-x.md"
    original = _master_text(
        "| 1 | [2026-08-03-work.md](./2026-08-03-work.md) | pending |\n"
        "| 2 | [2026-08-03-not-registered.md](./2026-08-03-not-registered.md) | pending |\n"
        "\n"
        "Trailing prose with a | pipe | that is not a table.\n"
    )
    master.write_text(original, encoding="utf-8")

    assert ps.reconcile_master_registry(master, plans) is True
    text = master.read_text(encoding="utf-8")

    # Front-matter and body preserved.
    assert "master_plan: issue-2340-2026-08-03" in text
    assert "- **required_sections**: ['Input', 'Output', 'Done']" in text
    assert "Trailing prose with a | pipe | that is not a table." in text

    # The registered row flipped; the row whose file is absent did not.
    assert "[2026-08-03-work.md](./2026-08-03-work.md) | shipped |" in text
    assert "[2026-08-03-not-registered.md](./2026-08-03-not-registered.md) | pending |" in text

    # Exactly one line differs from the original.
    diff = [
        (a, b)
        for a, b in zip(original.splitlines(), text.splitlines())
        if a != b
    ]
    assert len(diff) == 1, diff


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
