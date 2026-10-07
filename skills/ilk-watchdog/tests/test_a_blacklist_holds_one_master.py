"""Tests for master-scoped blacklist — a postmortem names its master; the
scheduler skips only that master and dispatches another runnable one.

Red-first: AC-1 asserts behaviour that does NOT hold at base
(``is_blacklisted`` has no ``master`` parameter).  Marked
``xfail(strict=True)`` so the suite is green at base and turns red when
the implementation lands.

AC-2 (back-compat: absent master keeps project-wide meaning) passes at base
and is left unmarked.

Hermetic: ``tmp_path`` project data dirs; injected fixed ``now``; never
touches the real ~/.ilk-data.
"""
from __future__ import annotations

import datetime as dt
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
GEN = "2026-10-07T19:00:00"
NOW_WITHIN = dt.datetime(2026, 10, 7, 19, 30, 0)   # < expiry (20:00)


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
    ]
    if master is not None:
        lines.append(f'master: "{master}"')
    lines += ["---", "", f"# Postmortem {run_id}", ""]
    p = pm_dir / f"{run_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    os.utime(p, (mtime, mtime))


def _make_project(tmp_path: Path) -> Path:
    """Scaffold a minimal project data directory."""
    d = tmp_path / "projects" / "test-proj"
    (d / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# AC-1: with master given, a postmortem whose master differs does NOT
#        blacklist it; a matching master DOES blacklist
# ---------------------------------------------------------------------------

class TestMasterScopedBlacklist:
    """is_blacklisted respects the master parameter."""

    @pytest.mark.xfail(
        strict=True,
        reason="is_blacklisted has no master parameter at base; "
               "a postmortem for master A blacklists master B",
    )
    def test_different_master_does_not_blacklist(self, tmp_path: Path) -> None:
        """Postmortem names master-A; querying for master-B is not blacklisted."""
        d = _make_project(tmp_path)
        _write_pm(d, "r01", "local-checks-stuck", GEN, mtime=NOW_WITHIN.timestamp(),
                  master="MASTER-2026-10-07d.md")
        result = bl.is_blacklisted(d, now=NOW_WITHIN, master="MASTER-2026-10-07l.md")
        assert result["blacklisted"] is False

    @pytest.mark.xfail(
        strict=True,
        reason="is_blacklisted has no master parameter at base; "
               "cannot match on master",
    )
    def test_matching_master_blacklists(self, tmp_path: Path) -> None:
        """Postmortem names master-A; querying for master-A is blacklisted."""
        d = _make_project(tmp_path)
        _write_pm(d, "r01", "local-checks-stuck", GEN, mtime=NOW_WITHIN.timestamp(),
                  master="MASTER-2026-10-07l.md")
        result = bl.is_blacklisted(d, now=NOW_WITHIN, master="MASTER-2026-10-07l.md")
        assert result["blacklisted"] is True
        assert result["classification"] == "local-checks-stuck"


# ---------------------------------------------------------------------------
# AC-2: absent master field keeps project-wide meaning (back-compat)
# ---------------------------------------------------------------------------

class TestBackCompatAbsentMaster:
    """A postmortem with no master field blacklists the whole project."""

    def test_absent_master_blacklists_without_master_kwarg(self, tmp_path: Path) -> None:
        """No master field, no master kwarg → project-wide blacklist (base)."""
        d = _make_project(tmp_path)
        _write_pm(d, "r01", "local-checks-stuck", GEN, mtime=NOW_WITHIN.timestamp())
        result = bl.is_blacklisted(d, now=NOW_WITHIN)
        assert result["blacklisted"] is True

    @pytest.mark.xfail(
        strict=True,
        reason="is_blacklisted has no master parameter at base; "
               "absent-master back-compat path does not exist",
    )
    def test_absent_master_blacklists_even_with_master_kwarg(self, tmp_path: Path) -> None:
        """No master field in postmortem → still blacklists even when master given."""
        d = _make_project(tmp_path)
        _write_pm(d, "r01", "local-checks-stuck", GEN, mtime=NOW_WITHIN.timestamp())
        result = bl.is_blacklisted(d, now=NOW_WITHIN, master="MASTER-2026-10-07l.md")
        assert result["blacklisted"] is True