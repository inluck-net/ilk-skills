"""Red test: promote_next_master.py should treat `pending` as a
promotable alias of `queued` so unattended advancement picks up
masters whose status drifted from the canonical vocabulary.

AC-1: pending master is promotable (dry-run shows promoted != null).
AC-2: priority/tie-break ordering unchanged (queued > pending when
      priority is higher).
AC-3: active demoted even when no promotable exists; dir with no
      promotable and no active returns both null.
AC-4: non-dry-run on a pending master writes status: active.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ → ilk-loop/ → skills/ → root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "promote_next_master.py"
PLANS_DIR = REPO_ROOT / "scratch" / "promote-pending" / "plans"


# ── helpers ────────────────────────────────────────────────────────

def _write_master(name: str, *, title: str = "Test", status: str = "queued",
                  priority: int = 0, created: str = "2026-06-07T00:00:00+08:00") -> Path:
    """Write a minimal MASTER-*.md into PLANS_DIR."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    p = PLANS_DIR / name
    body = (
        "---\n"
        f"title: {title}\n"
        f"slug: {name.replace('MASTER-', '').replace('.md', '')}\n"
        f"created: {created}\n"
        f"status: {status}\n"
        f"priority: {priority}\n"
        "pause_after_ship: false\n"
        "branch: null\n"
        "goal: test fixture\n"
        "out_of_scope: []\n"
        "cross_cutting_invariants: []\n"
        "---\n"
        f"\n# {title}\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


def _run(*extra_args: str) -> dict:
    """Run promote_next_master.py with --plans-dir pointing at PLANS_DIR."""
    cmd = [sys.executable, str(SCRIPT), "--plans-dir", str(PLANS_DIR), *extra_args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


def _cleanup():
    """Remove all files in the scratch plans dir."""
    if PLANS_DIR.exists():
        for f in PLANS_DIR.iterdir():
            f.unlink()


# ── fixtures ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


# ── tests ──────────────────────────────────────────────────────────

class TestAC1_PendingIsPromotable:
    """A single master with status: pending should be promoted."""

    def test_dry_run_promotes_pending(self):
        _write_master("MASTER-01.md", status="pending", priority=5)
        result = _run("--dry-run")
        assert result["promoted"] == "MASTER-01.md"
        assert result["queued_count_before"] >= 1
        assert result["dry_run"] is True


class TestAC2_PriorityOrdering:
    """Queued with higher priority wins over pending with lower."""

    def test_queued_higher_priority_wins(self):
        _write_master("MASTER-low.md", status="pending", priority=1,
                      created="2026-06-07T01:00:00+08:00")
        _write_master("MASTER-high.md", status="queued", priority=10,
                      created="2026-06-07T02:00:00+08:00")
        result = _run("--dry-run")
        assert result["promoted"] == "MASTER-high.md"

    def test_pending_higher_priority_wins_over_queued_lower(self):
        _write_master("MASTER-queued.md", status="queued", priority=1,
                      created="2026-06-07T01:00:00+08:00")
        _write_master("MASTER-pending.md", status="pending", priority=10,
                      created="2026-06-07T02:00:00+08:00")
        result = _run("--dry-run")
        assert result["promoted"] == "MASTER-pending.md"

    def test_same_priority_created_earlier_wins(self):
        _write_master("MASTER-later.md", status="pending", priority=5,
                      created="2026-06-07T03:00:00+08:00")
        _write_master("MASTER-earlier.md", status="pending", priority=5,
                      created="2026-06-07T01:00:00+08:00")
        result = _run("--dry-run")
        assert result["promoted"] == "MASTER-earlier.md"


class TestAC3_DemoteAndNoop:
    """Active demoted even without promotable; both null when nothing to do."""

    def test_active_demoted_no_promotable(self):
        _write_master("MASTER-act.md", status="active", priority=5)
        result = _run("--dry-run")
        assert result["demoted"] == "MASTER-act.md"
        assert result["promoted"] is None

    def test_no_active_no_promotable_returns_nulls(self):
        _write_master("MASTER-shipped.md", status="shipped", priority=5)
        result = _run("--dry-run")
        assert result["demoted"] is None
        assert result["promoted"] is None


class TestAC4_NonDryRunWritesActive:
    """Non-dry-run on a pending master writes status: active."""

    def test_pending_becomes_active(self):
        p = _write_master("MASTER-target.md", status="pending", priority=5)
        result = _run()  # no --dry-run
        assert result["promoted"] == "MASTER-target.md"
        assert result["dry_run"] is False
        # Re-read the file and verify frontmatter was mutated.
        text = p.read_text(encoding="utf-8")
        lines = [l.strip() for l in text.splitlines() if l.strip().startswith("status:")]
        assert len(lines) == 1
        assert lines[0] == "status: active"
