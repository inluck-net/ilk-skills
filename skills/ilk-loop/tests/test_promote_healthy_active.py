"""promote_next_master must not park a HEALTHY active master as `blocked`.

The demotion filter keyed on `master_has_nonshipped` alone, while the
comment directly above it — and the one above `demote_status` — both define
a stalled master as non-shipped AND non-drainable.  So any active master
with outstanding work qualified for demotion, including one with a loop
running against it.

Observed on gh-resolve 2026-09-05 by --dry-run against a live batch:
    {"demoted": "MASTER-2026-09-05b-...", "demote_status": "blocked"}

It stayed latent in production only because scheduler.sh gates the call on
the project having no active master (`if [[ "$dactive" == "false" ]]`).  A
direct invocation — which the docs present as an operator action — hits it.

AC-1  A drainable active is not demoted.
AC-2  A drainable active also blocks promotion: promoting underneath it
      would leave TWO actives, which is worse than the original bug.
AC-3  A stalled active (non-shipped, non-drainable) is still demoted
      to `blocked` — the intended behaviour must survive.
AC-4  With a stalled active out of the way, the queued master promotes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "promote_next_master.py"


def _master(plans: Path, name: str, status: str, subplans: list[str] = (),
            priority: int = 0, created: str = "2026-09-01T00:00:00+08:00") -> Path:
    rows = "\n".join(f"| {i} | [{f}]({f}) |" for i, f in enumerate(subplans))
    p = plans / name
    p.write_text(
        "---\n"
        f"title: {name}\n"
        f"created: {created}\n"
        f"status: {status}\n"
        f"priority: {priority}\n"
        "---\n\n"
        f"# {name}\n\n## Sub-plans\n\n| # | file |\n|---|---|\n{rows}\n",
        encoding="utf-8",
    )
    return p


def _subplan(plans: Path, name: str, status: str) -> Path:
    p = plans / name
    p.write_text(
        f"---\nplan: {name[:-3]}\nstatus: {status}\ncurrent_step: 0\n---\n\n"
        f"# Sub-plan\n\n### Step 0\n- work\n",
        encoding="utf-8",
    )
    return p


def _run(plans: Path, *extra: str) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--plans-dir", str(plans), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


@pytest.fixture
def plans(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


class TestHealthyActiveIsNotParked:
    """AC-1 / AC-2 — the defect."""

    @pytest.fixture
    def healthy(self, plans: Path) -> Path:
        _subplan(plans, "2026-09-01-work.md", "pending")   # runnable => drainable
        _master(plans, "MASTER-2026-09-01-live.md", "active", ["2026-09-01-work.md"])
        return plans

    def test_healthy_active_is_not_demoted(self, healthy: Path) -> None:
        out = _run(healthy, "--dry-run")
        assert out["demoted"] is None, (
            "a drainable active with runnable work was selected for demotion"
        )

    def test_healthy_active_is_not_marked_blocked(self, healthy: Path) -> None:
        out = _run(healthy, "--dry-run")
        assert out["demote_status"] != "blocked"

    def test_reason_names_the_master(self, healthy: Path) -> None:
        out = _run(healthy, "--dry-run")
        assert out.get("skipped_healthy_active") == ["MASTER-2026-09-01-live.md"]
        assert "still drainable" in out.get("reason", "")

    def test_healthy_active_blocks_promotion(self, healthy: Path) -> None:
        """AC-2: promoting underneath a healthy active leaves two actives."""
        _subplan(healthy, "2026-09-02-next.md", "pending")
        _master(healthy, "MASTER-2026-09-02-next.md", "queued", ["2026-09-02-next.md"])
        out = _run(healthy, "--dry-run")
        assert out["promoted"] is None, (
            "promoted a second master while a healthy one was still active"
        )

    def test_non_dry_run_leaves_the_file_active(self, healthy: Path) -> None:
        """The write path, not just the plan: status must be untouched."""
        m = healthy / "MASTER-2026-09-01-live.md"
        _run(healthy)
        assert "status: active" in m.read_text(encoding="utf-8")
        assert "status: blocked" not in m.read_text(encoding="utf-8")


class TestStalledActiveStillParks:
    """AC-3 / AC-4 — the intended behaviour must survive the fix."""

    @pytest.fixture
    def stalled(self, plans: Path) -> Path:
        # Non-shipped but NOT runnable => stalled.
        _subplan(plans, "2026-09-01-stuck.md", "blocked")
        _master(plans, "MASTER-2026-09-01-stalled.md", "active", ["2026-09-01-stuck.md"])
        _subplan(plans, "2026-09-02-next.md", "pending")
        _master(plans, "MASTER-2026-09-02-next.md", "queued", ["2026-09-02-next.md"])
        return plans

    def test_stalled_active_is_demoted_blocked(self, stalled: Path) -> None:
        out = _run(stalled, "--dry-run")
        assert out["demoted"] == "MASTER-2026-09-01-stalled.md"
        assert out["demote_status"] == "blocked"

    def test_queued_master_promotes_past_it(self, stalled: Path) -> None:
        out = _run(stalled, "--dry-run")
        assert out["promoted"] == "MASTER-2026-09-02-next.md"

    def test_write_path_parks_and_promotes(self, stalled: Path) -> None:
        _run(stalled)
        assert "status: blocked" in (stalled / "MASTER-2026-09-01-stalled.md").read_text()
        assert "status: active" in (stalled / "MASTER-2026-09-02-next.md").read_text()
