"""A master whose sub-plans mix naive and aware ``last_updated`` must scan.

Regression guard for the 2026-08-20 silent-retirement incident. Two independent
defects compounded:

1. ``_parse_ts`` returned whatever ``datetime.fromisoformat`` gave it, so
   ``last_updated: 2026-08-03`` came back **naive** while
   ``last_updated: 2026-08-03T17:30:00+08:00`` came back **aware**. One master
   carrying both formats reached ``min(master_sub_ts)`` with a mixed list and
   raised ``TypeError: can't compare offset-naive and offset-aware datetimes``.
2. ``scan_projects`` swallows every per-project exception by design (the
   2026-06-30 daemon-crash guard) but did so **silently**, so the project
   vanished from the scan while the scheduler logged the generic
   ``all-queues-empty`` and ilk-doctor reported ``pass: all gates clear``.

Measured before the fix: 2 of 9 real projects raised at ``scheduler_scan.py:394``
and the scheduler logged ``all-queues-empty`` on every 5-minute poll for over
three hours. Hermetic — no live scheduler, no network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
SCAN_SCRIPT = SCRIPTS_ILK_WATCHDOG / "scheduler_scan.py"


def _fresh_scheduler_scan():
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod in ("scheduler_scan", "ilk_paths", "plan_status"):
        sys.modules.pop(mod, None)
    import scheduler_scan
    return scheduler_scan


def _write_mixed_ts_project(tmp_path: Path, key: str = "mixed-ts-proj") -> Path:
    """One active master, two runnable sub-plans, mixed ``last_updated`` shapes.

    This is the exact shape observed on the two live projects: a date-only
    value alongside a full offset-bearing timestamp, under one master.
    """
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-mixed.md").write_text(
        "---\n"
        "title: MASTER-mixed\n"
        "created: 2026-08-03T00:00:00+08:00\n"
        "status: active\n"
        "---\n\n"
        "# MASTER-mixed\n\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-08-03-naive.md](./2026-08-03-naive.md) | in-progress |\n"
        "| 2 | [2026-08-03-aware.md](./2026-08-03-aware.md) | in-progress |\n",
        encoding="utf-8",
    )
    # naive: date only
    (plans_dir / "2026-08-03-naive.md").write_text(
        "---\n"
        "plan: 2026-08-03-naive\n"
        "status: in-progress\n"
        "last_updated: 2026-08-03\n"
        "---\n\n# naive\n",
        encoding="utf-8",
    )
    # aware: full timestamp with an offset
    (plans_dir / "2026-08-03-aware.md").write_text(
        "---\n"
        "plan: 2026-08-03-aware\n"
        "status: in-progress\n"
        "last_updated: 2026-08-03T17:30:00+08:00\n"
        "---\n\n# aware\n",
        encoding="utf-8",
    )
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": "/repo/mixed"}), encoding="utf-8"
    )
    return project_dir


# --------------------------------------------------------------------------
# Defect B1 — the comparison itself
# --------------------------------------------------------------------------

def test_parse_ts_always_returns_naive():
    """Both input shapes come back naive, hence mutually comparable."""
    scan = _fresh_scheduler_scan()
    naive = scan._parse_ts("2026-08-03")
    aware = scan._parse_ts("2026-08-03T17:30:00+08:00")

    assert naive is not None and aware is not None
    assert naive.tzinfo is None, f"date-only should be naive, got {naive.tzinfo}"
    assert aware.tzinfo is None, (
        f"offset-bearing value must be normalised to naive, got {aware.tzinfo}"
    )
    # The operation that raised: min() over a mixed list.
    assert min([naive, aware]) == naive
    # And comparable with both fallbacks used by _scan_one_project.
    assert min([naive, aware, datetime.now(), datetime.min]) == datetime.min


def test_scan_one_project_survives_mixed_last_updated(tmp_path):
    """The reported repro: mixed formats under one master must not raise."""
    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path
    project_dir = _write_mixed_ts_project(tmp_path)

    # Before the fix this raised TypeError at the min() over master_sub_ts.
    entry = scan._scan_one_project(project_dir)

    assert entry is not None, "a runnable master must yield an entry"
    assert entry["key"] == "mixed-ts-proj"
    assert entry["has_active_master"] is True
    # FIFO timestamp is the oldest of the two, emitted without an offset.
    assert entry["oldest_queued_ts"].startswith("2026-08-03T00:00:00"), (
        f"oldest should be the date-only sub-plan, got {entry['oldest_queued_ts']}"
    )


def test_scan_projects_surfaces_mixed_ts_project(tmp_path):
    """End to end: the project appears in the scan instead of disappearing."""
    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path
    _write_mixed_ts_project(tmp_path)

    results = scan.scan_projects()

    assert [r["key"] for r in results] == ["mixed-ts-proj"]
    assert scan.SCAN_ERRORS == [], f"no project should have failed: {scan.SCAN_ERRORS}"


# --------------------------------------------------------------------------
# Defect B2 — the swallow is observable
# --------------------------------------------------------------------------

def test_swallowed_exception_is_recorded_not_silent(tmp_path, monkeypatch, capsys):
    """The `continue` stays, but the skip is now attributable."""
    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path
    _write_mixed_ts_project(tmp_path, key="good-proj")
    _write_mixed_ts_project(tmp_path, key="bad-proj")

    orig = scan._scan_one_project

    def explode(project_dir):
        if project_dir.name == "bad-proj":
            raise TypeError("can't compare offset-naive and offset-aware datetimes")
        return orig(project_dir)

    monkeypatch.setattr(scan, "_scan_one_project", explode)

    # Resilience preserved: does not raise, healthy project still returned.
    results = scan.scan_projects()
    assert [r["key"] for r in results] == ["good-proj"]

    # Observability added: the failure is attributable.
    assert len(scan.SCAN_ERRORS) == 1, scan.SCAN_ERRORS
    err = scan.SCAN_ERRORS[0]
    assert err["key"] == "bad-proj"
    assert err["error_type"] == "TypeError"
    assert "offset-naive" in err["error"]
    assert err["where"].startswith("test_scheduler_scan_ts_awareness.py:"), err["where"]

    # ...and named on stderr, so an unattended scheduler run leaves a trace.
    captured = capsys.readouterr()
    assert "[scan-error]" in captured.err
    assert "bad-proj" in captured.err


def test_scan_errors_reset_between_scans(tmp_path, monkeypatch):
    """A later clean scan must not report the previous scan's failure."""
    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path
    _write_mixed_ts_project(tmp_path, key="bad-proj")

    monkeypatch.setattr(
        scan, "_scan_one_project",
        lambda p: (_ for _ in ()).throw(ValueError("boom")),
    )
    scan.scan_projects()
    assert len(scan.SCAN_ERRORS) == 1

    monkeypatch.undo()
    scan.scan_projects()
    assert scan.SCAN_ERRORS == [], "accumulator must be cleared per scan"


def test_default_stdout_stays_a_bare_project_array(tmp_path):
    """The stdout contract is unchanged — scheduler.sh/.ps1 parse a bare list."""
    _write_mixed_ts_project(tmp_path)
    env = dict(os.environ)
    env["ILK_DATA_HOME"] = str(tmp_path)

    proc = subprocess.run(
        # encoding= is explicit, not implied by text=True: without it the child's
        # output decodes via the locale codec (FM-0003), which is what the
        # repo's subprocess-encoding lint gates on.
        [sys.executable, str(SCAN_SCRIPT)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, list), f"stdout must stay a bare array, got {type(parsed)}"
    assert [p["key"] for p in parsed] == ["mixed-ts-proj"]


def test_scan_errors_flag_reports_failures(tmp_path, monkeypatch, capsys):
    """`--scan-errors` gives the scheduler a distinguishable reason to log.

    In-process rather than via subprocess because the failure has to be
    INJECTED: ``_scan_one_project`` already guards its own file reads with
    ``except OSError`` (line 393), so a corrupt-on-disk fixture is swallowed
    below the level this test is about. The condition under test is the
    unguarded class — the TypeError that actually occurred.
    """
    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path
    _write_mixed_ts_project(tmp_path, key="good-proj")
    _write_mixed_ts_project(tmp_path, key="bad-proj")

    orig = scan._scan_one_project

    def explode(project_dir):
        if project_dir.name == "bad-proj":
            raise TypeError("can't compare offset-naive and offset-aware datetimes")
        return orig(project_dir)

    monkeypatch.setattr(scan, "_scan_one_project", explode)
    monkeypatch.setattr(sys, "argv", [str(SCAN_SCRIPT), "--scan-errors"])

    rc = scan.main()
    assert rc == 0
    out = capsys.readouterr()
    errors = json.loads(out.out)
    assert [e["key"] for e in errors] == ["bad-proj"], (
        f"the unscannable project must be named, got {errors}"
    )
    assert errors[0]["error_type"] == "TypeError"
    assert {"key", "path", "error_type", "error", "where"} <= set(errors[0])

    # The DEFAULT invocation keeps the bare-array stdout contract AND names the
    # skip on stderr, so the same condition is visible to an unattended
    # scheduler that never passes a flag.
    monkeypatch.setattr(sys, "argv", [str(SCAN_SCRIPT)])
    rc = scan.main()
    assert rc == 0
    out = capsys.readouterr()
    parsed = json.loads(out.out)
    assert isinstance(parsed, list), "stdout must stay a bare array"
    assert [pr["key"] for pr in parsed] == ["good-proj"], (
        "the healthy project must still dispatch"
    )
    assert "[scan-error]" in out.err and "bad-proj" in out.err, (
        f"a swallowed per-project failure must reach stderr, got {out.err!r}"
    )
