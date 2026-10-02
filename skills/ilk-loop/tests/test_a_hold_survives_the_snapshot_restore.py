"""Pins for the master_snapshot restore accepting park/hold changes.

A park or hold written mid-iteration (by ``park_master.py`` or an operator)
must survive the post-iteration restore.  The restore's job is to undo a
worker's unauthorised edits to runner-owned fields — but stopping is always
fail-safe, and a park/hold is a move toward stopping.

AC-1: snapshot taken while the master is active with no hold.  Then
      ``park_master.py`` adds a park and ``hold: human``.  ``restore`` leaves
      ``hold: human`` and ``parked_at`` in place and exits 0.  Red at HEAD
      (an AssertionError on the hold).
AC-2 (control): snapshot taken while the master is blocked with ``hold: human``.
      The master is then edited to active with no hold.  ``restore`` puts the
      park and hold back and exits 3.
AC-3 (control): status active → shipped is still accepted when every sub-plan
      is shipped (the existing behaviour).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from master_snapshot import OWNED_FIELDS, restore, take  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

def _master(plans: Path, name: str, status: str, subplan: str,
            extra: str = "") -> Path:
    p = plans / name
    p.write_text(
        "---\n"
        f"master_plan: {name[:-3]}\n"
        f"status: {status}\n"
        f"{extra}"
        "---\n\n# batch\n\n| # | Slug | Status |\n|---|---|---|\n"
        f"| 1 | {subplan} | pending |\n",
        encoding="utf-8",
    )
    return p


def _subplan(plans: Path, fname: str, status: str = "pending") -> None:
    slug = fname[len("2026-10-02d-"):-3]
    (plans / fname).write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n### Step 0 — do it\n",
        encoding="utf-8",
    )


def _fm(p: Path) -> dict:
    return parse_frontmatter(p.read_text(encoding="utf-8"))


def _take_and_restore(plans: Path, master_name: str) -> dict:
    """Take a snapshot, then restore — returns the restore result dict."""
    snap_file = plans / "_snapshot.json"
    snap = take(plans, [master_name])
    snap_file.write_text(json.dumps(snap), encoding="utf-8")
    # Re-read so we get the dict back.
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    return restore(plans, snap_data)


# ── AC-1 ────────────────────────────────────────────────────────────────────

def test_a_hold_made_mid_iteration_survives_the_restore(tmp_path: Path) -> None:
    """A park + hold:human added after the snapshot must not be undone."""
    plans = tmp_path / "plans"
    plans.mkdir()
    _subplan(plans, "2026-10-02d-aaa.md")
    master = _master(plans, "MASTER-2026-10-02d.md", "active", "2026-10-02d-aaa.md")

    # Snapshot the active master with no hold.
    snap_file = plans / "_snapshot.json"
    snap = take(plans, [master.name])
    snap_file.write_text(json.dumps(snap), encoding="utf-8")

    # Operator parks mid-iteration: status → blocked, hold: human, parked_at.
    park_script = _SCRIPTS / "park_master.py"
    import subprocess
    r = subprocess.run(
        [sys.executable, str(park_script),
         "--plans-dir", str(plans),
         "--master", master.name,
         "--reason", "operator redesign #56"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    fm_after_park = _fm(master)
    assert fm_after_park["status"] == "blocked"
    assert fm_after_park.get("hold") == "human"
    assert fm_after_park.get("parked_at") is not None

    # Restore from the pre-park snapshot.
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    result = restore(plans, snap_data)

    # The hold and park must survive — exit 0 means nothing was restored.
    assert result["restored"] == [], f"hold was undone: {result}"
    fm_after = _fm(master)
    assert fm_after.get("hold") == "human", f"hold lost: {fm_after}"
    assert fm_after.get("parked_at") is not None, f"parked_at lost: {fm_after}"
    assert fm_after["status"] == "blocked", f"status changed: {fm_after}"


# ── AC-2 (control) ──────────────────────────────────────────────────────────

def test_a_hold_removed_mid_iteration_is_restored(tmp_path: Path) -> None:
    """Removing a hold is NOT a move toward stopping — restore puts it back."""
    plans = tmp_path / "plans"
    plans.mkdir()
    _subplan(plans, "2026-10-02d-aaa.md")
    master = _master(plans, "MASTER-2026-10-02d.md", "active", "2026-10-02d-aaa.md")

    # Park the master first so the snapshot has a hold.
    park_script = _SCRIPTS / "park_master.py"
    import subprocess
    r = subprocess.run(
        [sys.executable, str(park_script),
         "--plans-dir", str(plans),
         "--master", master.name,
         "--reason", "operator hold"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert _fm(master).get("hold") == "human"

    # Snapshot the held master.
    snap_file = plans / "_snapshot.json"
    snap = take(plans, [master.name])
    snap_file.write_text(json.dumps(snap), encoding="utf-8")

    # Simulate a worker removing the hold (edit to active, drop park fields).
    master.write_text(
        "---\n"
        "master_plan: MASTER-2026-10-02d\n"
        "status: active\n"
        "---\n\n# batch\n\n| # | Slug | Status |\n|---|---|---|\n"
        "| 1 | 2026-10-02d-aaa.md | pending |\n",
        encoding="utf-8",
    )

    # Restore from the held snapshot.
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    result = restore(plans, snap_data)

    # The hold should be restored — exit 3 means at least one master restored.
    assert result["restored"], f"nothing restored: {result}"
    fm_after = _fm(master)
    assert fm_after.get("hold") == "human", f"hold not restored: {fm_after}"
    assert fm_after["status"] == "blocked", f"status not restored: {fm_after}"
    assert fm_after.get("parked_at") is not None, f"parked_at not restored: {fm_after}"


# ── AC-3 (control) ──────────────────────────────────────────────────────────

def test_status_active_to_shipped_is_still_accepted(tmp_path: Path) -> None:
    """The existing shipped-acceptance logic still works."""
    plans = tmp_path / "plans"
    plans.mkdir()
    _subplan(plans, "2026-10-02d-aaa.md", status="shipped")
    master = _master(plans, "MASTER-2026-10-02d.md", "active", "2026-10-02d-aaa.md")

    # Snapshot while active.
    snap_file = plans / "_snapshot.json"
    snap = take(plans, [master.name])
    snap_file.write_text(json.dumps(snap), encoding="utf-8")

    # Simulate the scheduler reconciling status to shipped.
    master.write_text(
        master.read_text().replace("status: active", "status: shipped"),
        encoding="utf-8",
    )

    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    result = restore(plans, snap_data)

    # Should be accepted, not restored.
    assert result["restored"] == [], f"shipped was restored: {result}"
    assert len(result["accepted"]) == 1
    assert result["accepted"][0]["to"] == "shipped"
    assert _fm(master)["status"] == "shipped"