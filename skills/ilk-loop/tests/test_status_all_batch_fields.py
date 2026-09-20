"""The status payload names its batch: M/N sub-plan position + pending batches.

Operator request 2026-09-20: the panel reads "<batch> M/N" ahead of the
sub-plan name, plus a pending-batch count.  Covers the status_all half —
the resolver's 4-tuple, the batch display name, and the pending counter
shaping of the entry dict is covered end-to-end by the live panel.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from status_all import _batch_display_name, _resolve_next_subplan  # noqa: E402

import pytest


MASTER_TMPL = """---
master_plan: 2026-09-19-{name}-rereview
status: active
---

# MASTER plan

| # | Sub-plan |
|---|---|
| 1 | [2026-09-19-one.md](./2026-09-19-one.md) |
| 2 | [2026-09-19-two.md](./2026-09-19-two.md) |
| 3 | [2026-09-19-three.md](./2026-09-19-three.md) |
"""

SUBPLAN_TMPL = """---
plan: {slug}
status: {status}
current_step: 0
estimated_steps: 4
---

# Sub-plan: {slug}
"""


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    def sub(fname: str, slug: str, status: str) -> None:
        (tmp_path / fname).write_text(
            SUBPLAN_TMPL.format(slug=slug, status=status), encoding="utf-8"
        )

    sub("2026-09-19-one.md", "one", "shipped")
    sub("2026-09-19-two.md", "two", "in-progress")
    sub("2026-09-19-three.md", "three", "pending")
    (tmp_path / "MASTER-x.md").write_text(
        MASTER_TMPL.format(name="pv9"), encoding="utf-8"
    )
    return tmp_path


def test_resolver_returns_position_counting_shipped(plans_dir: Path) -> None:
    master = (plans_dir / "MASTER-x.md").read_text(encoding="utf-8")
    slug, step, idx, cnt = _resolve_next_subplan(plans_dir, master)
    # "two" is the first RUNNABLE; it sits at registry position 2 of 3 —
    # "one" shipped but still counts toward N.
    assert (slug, step, idx, cnt) == ("two", "0/4", 2, 3)


def test_resolver_empty_when_nothing_runnable(plans_dir: Path) -> None:
    (plans_dir / "2026-09-19-two.md").write_text(
        SUBPLAN_TMPL.format(slug="two", status="blocked"), encoding="utf-8"
    )
    (plans_dir / "2026-09-19-three.md").write_text(
        SUBPLAN_TMPL.format(slug="three", status="shipped"), encoding="utf-8"
    )
    master = (plans_dir / "MASTER-x.md").read_text(encoding="utf-8")
    assert _resolve_next_subplan(plans_dir, master) == ("", "", 0, 0)


def test_batch_display_name_strips_date_prefix() -> None:
    assert (
        _batch_display_name(MASTER_TMPL.format(name="pv5")) == "pv5-rereview"
    )


def test_batch_display_name_handles_letter_suffix() -> None:
    text = MASTER_TMPL.format(name="x").replace(
        "2026-09-19-x-rereview", "2026-09-08b-state-ownership"
    )
    assert _batch_display_name(text) == "state-ownership"


def test_batch_display_name_empty_when_absent() -> None:
    assert _batch_display_name("---\nstatus: active\n---\nbody") == ""
