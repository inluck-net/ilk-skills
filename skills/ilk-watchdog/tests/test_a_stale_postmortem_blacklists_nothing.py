"""Tests for stale-postmortem blacklist decision.

A postmortem older than the last run's sentinel should not blacklist
the next run.  AC-1 is the new behaviour (built in step 1);
AC-2..AC-5 are controls that pass at base.

Hermetic: ``tmp_path`` project data dirs; injected fixed ``now``; never
touches the real ~/.ilk-data.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import blacklist_status as bl  # noqa: E402

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

# The postmortem is from run 20261009-133401 with local-checks-stuck.
PM_RUN_ID = "20261009-133401"
PM_CLASSIFICATION = "local-checks-stuck"
PM_GENERATED_AT = "2026-10-09T13:40:00"
NOW = dt.datetime(2026, 10, 9, 13, 50, 0)  # 10 min after generated_at


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_pm(
    data_dir: Path,
    run_id: str,
    classification: str,
    generated_at: str,
    mtime: float,
    *,
    master: str | None = None,
) -> None:
    """Write a postmortem markdown file with frontmatter."""
    pm_dir = data_dir / "runtime" / "launcher" / "postmortems"
    pm_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "project: x",
        f'classification: "{classification}"',
        f'generated_at: "{generated_at}"',
        f'run_id: "{run_id}"',
    ]
    if master is not None:
        lines.append(f'master: "{master}"')
    lines.append("---")
    pm_path = pm_dir / f"{run_id}.md"
    pm_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(pm_path, (mtime, mtime))


def _write_sentinel(data_dir: Path, run_id: str, state: str = "local_checks_failed") -> None:
    """Write a last-exit.json sentinel."""
    launcher_dir = data_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "run_id": run_id,
        "ended_at": "2026-10-09T14:00:00+0800",
    }
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_ac1_stale_postmortem_not_blacklisted(tmp_path: Path) -> None:
    """AC-1: postmortem 20261009-133401 with local-checks-stuck, and
    last-exit run_id 20261009-143942 (newer).  Not blacklisted, with
    reason == 'stale-postmortem'.
    """
    data_dir = tmp_path / "data"
    _write_pm(data_dir, PM_RUN_ID, PM_CLASSIFICATION, PM_GENERATED_AT, mtime=1.0)
    _write_sentinel(data_dir, "20261009-143942")
    result = bl.is_blacklisted(data_dir, now=NOW)
    assert result["blacklisted"] is False
    assert result["reason"] == "stale-postmortem"
    assert result["stale_postmortem_run_id"] == PM_RUN_ID
    assert result["last_run_id"] == "20261009-143942"


def test_ac2_own_run_blacklisted(tmp_path: Path) -> None:
    """AC-2 (control): sentinel run_id matches the postmortem run_id.
    Blacklisted as today (within backoff).
    """
    data_dir = tmp_path / "data"
    _write_pm(data_dir, PM_RUN_ID, PM_CLASSIFICATION, PM_GENERATED_AT, mtime=1.0)
    _write_sentinel(data_dir, PM_RUN_ID)
    result = bl.is_blacklisted(data_dir, now=NOW)
    assert result["blacklisted"] is True


def test_ac3_no_sentinel_blacklisted(tmp_path: Path) -> None:
    """AC-3 (control): no last-exit.json.  Blacklisted as today.
    """
    data_dir = tmp_path / "data"
    _write_pm(data_dir, PM_RUN_ID, PM_CLASSIFICATION, PM_GENERATED_AT, mtime=1.0)
    result = bl.is_blacklisted(data_dir, now=NOW)
    assert result["blacklisted"] is True


def test_ac4_older_sentinel_blacklisted(tmp_path: Path) -> None:
    """AC-4 (control): sentinel run_id 20261009-120000 is older than the
    postmortem's 20261009-133401.  Blacklisted as today.
    """
    data_dir = tmp_path / "data"
    _write_pm(data_dir, PM_RUN_ID, PM_CLASSIFICATION, PM_GENERATED_AT, mtime=1.0)
    _write_sentinel(data_dir, "20261009-120000")
    result = bl.is_blacklisted(data_dir, now=NOW)
    assert result["blacklisted"] is True


def test_ac5_clean_newest_not_blacklisted(tmp_path: Path) -> None:
    """AC-5 (control): a newest postmortem with clean-success is not
    blacklisted, with the same reason value as today.
    """
    data_dir = tmp_path / "data"
    _write_pm(data_dir, "20261009-150000", "clean-success", "2026-10-09T15:10:00", mtime=2.0)
    result = bl.is_blacklisted(data_dir, now=NOW)
    assert result["blacklisted"] is False
    assert result["reason"] == "latest-not-blacklist-class"