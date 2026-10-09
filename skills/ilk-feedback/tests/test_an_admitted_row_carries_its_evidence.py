"""Pins: a hand-filed backlog row carries its evidence.

Sub-plan: an-admitted-row-carries-its-evidence (step 0 pins; step 1 built the
admission gate and stepped AC-1..AC-5, AC-8 out of their xfail markers;
step 2 added the overlap listing and stepped AC-6 out).
Drives the ``improvement_backlog`` CLI in a subprocess (``sys.executable``,
script path derived from ``__file__``) with a hermetic data root: ``HOME``,
``ILK_DATA_HOME`` and ``ILK_DATA_DIR`` are all ``tmp_path``-derived, so
nothing reads or writes ``~/.ilk-data/ilk-skills-improvements/``.

``<B>`` below is ``<ILK_DATA_HOME>/ilk-skills-improvements/candidates.json``.

AC-1: a placeholder title is refused with ``title-placeholder``
    and nothing is written.
AC-2: a title under four words is refused with ``title-too-short``.
AC-3: a gap with no anchor is refused with ``no-evidence-anchor``;
    the same call plus ``--file``/``--line`` is admitted and ``<B>`` holds 1 row.
AC-4: a bug with no fix is refused with ``no-proposed-fix``; the
    same call plus ``--no-fix-yet`` is admitted and the row records the reason.
AC-5: a real row (d61afe74f26dd73d's own strings) passes the
    admission gate and is filed. The pin asserts both that
    ``validate_candidate`` admits the row and that the CLI files it.
AC-6: filing lists the open rows that name the same file,
    skips wontfix rows, and prints the ``1 row(s)`` footer.
AC-7 (control, green at base): automated writers are unchanged — ``add_candidate``
    still files a row with no refusal.
AC-8: ``validate_candidate`` is pure and returns reason codes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SCRIPT = _SCRIPTS / "improvement_backlog.py"

# Row d61afe74f26dd73d's title, the first sentence of its gap, and its first
# fix sentence — embedded literally.  Never read the real backlog.
REAL_TITLE = (
    "a worker ignores the ILK_MASTER pin, opens another master via the wrong "
    "skill, and ships its quarantined sub-plan by editing plan files"
)
REAL_GAP = (
    "gh-resolve run 20261009-212756 is pinned to ILK_MASTER=MASTER-2026-10-09b."
)
REAL_FIX = (
    "Refuse worker Edit/Write on any plan file outside the pinned master's registry."
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _hermetic_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["ILK_DATA_HOME"] = str(tmp_path / "ilk-data")
    env["ILK_DATA_DIR"] = str(tmp_path / "ilk-data-dir")
    return env


def _backlog_path(tmp_path: Path) -> Path:
    """<B>: the hermetic candidates.json the CLI must write to."""
    return tmp_path / "ilk-data" / "ilk-skills-improvements" / "candidates.json"


def _backlog_dir(tmp_path: Path) -> Path:
    return _backlog_path(tmp_path).parent


def _run_cli(tmp_path: Path, *argv: str):
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        env=_hermetic_env(tmp_path),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _refusal_reasons(stderr: str) -> list:
    """Reasons from the one JSON refusal line on stderr (none -> empty)."""
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("status") == "refused":
            return obj.get("reasons", [])
    return []


def _rows(tmp_path: Path) -> list:
    return json.loads(_backlog_path(tmp_path).read_text(encoding="utf-8"))


def _set_status(backlog_dir: Path, entry_id: str, status: str) -> None:
    p = backlog_dir / "candidates.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    for row in rows:
        if row.get("id") == entry_id:
            row["status"] = status
    p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── AC-1 ─────────────────────────────────────────────────────────────────────


def test_ac1_placeholder_title_is_refused(tmp_path):
    rc, out, err = _run_cli(
        tmp_path,
        "--title", "triage amend: no-progress at -",
        "--gap", "run 20261009-143942 x",
    )
    assert rc == 2
    assert "title-placeholder" in _refusal_reasons(err)
    assert not _backlog_path(tmp_path).exists()


# ── AC-2 ─────────────────────────────────────────────────────────────────────


def test_ac2_short_title_is_refused(tmp_path):
    rc, out, err = _run_cli(
        tmp_path,
        "--title", "fix the gate",
        "--gap", "see scheduler.sh:615",
    )
    assert rc == 2
    assert "title-too-short" in _refusal_reasons(err)
    assert not _backlog_path(tmp_path).exists()


# ── AC-3 ─────────────────────────────────────────────────────────────────────


def test_ac3_row_without_an_anchor_is_refused(tmp_path):
    title = "the train is never offered after a draft"
    rc, out, err = _run_cli(tmp_path, "--title", title, "--gap", "no train starts")
    assert rc == 2
    assert "no-evidence-anchor" in _refusal_reasons(err)
    assert not _backlog_path(tmp_path).exists()

    rc, out, err = _run_cli(
        tmp_path,
        "--title", title,
        "--gap", "no train starts",
        "--file", "skills/ilk-watchdog/scripts/scheduler.sh",
        "--line", "615",
    )
    assert rc == 0
    assert len(_rows(tmp_path)) == 1


# ── AC-4 ─────────────────────────────────────────────────────────────────────


def test_ac4_bug_without_a_fix_is_refused_unless_no_fix_yet(tmp_path):
    argv = [
        "--kind", "bug",
        "--title", "the gate never records why it refused the row",
        "--gap", "see scheduler.sh:615",
    ]
    rc, out, err = _run_cli(tmp_path, *argv)
    assert rc == 2
    assert "no-proposed-fix" in _refusal_reasons(err)
    assert not _backlog_path(tmp_path).exists()

    rc, out, err = _run_cli(
        tmp_path, *argv, "--no-fix-yet", "cause not measured yet"
    )
    assert rc == 0
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["relations"]["no_fix_yet"] == "cause not measured yet"


# ── AC-5 ─────────────────────────────────────────────────────────────────────


def test_ac5_a_real_row_passes(tmp_path):
    from improvement_backlog import validate_candidate

    assert (
        validate_candidate(
            title=REAL_TITLE,
            kind="bug",
            gap=REAL_GAP,
            evidence={},
            proposed_fix=REAL_FIX,
        )
        == []
    )

    rc, out, err = _run_cli(
        tmp_path,
        "--kind", "bug",
        "--title", REAL_TITLE,
        "--gap", REAL_GAP,
        "--proposed-fix", REAL_FIX,
    )
    assert rc == 0
    assert json.loads(out)["title"] == REAL_TITLE
    assert len(_rows(tmp_path)) == 1


# ── AC-6 ─────────────────────────────────────────────────────────────────────


def test_ac6_filing_lists_the_open_rows_naming_the_same_file(tmp_path):
    import improvement_backlog

    backlog_dir = _backlog_dir(tmp_path)
    runner = "skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

    open_row = improvement_backlog.add_candidate(
        title="the runner drops its child on a second launch",
        kind="gap",
        gap="the child is dropped when the runner relaunches",
        evidence={"file": runner},
        backlog_dir=backlog_dir,
    )
    wontfix_row = improvement_backlog.add_candidate(
        title="the runner log line is truncated at four k",
        kind="gap",
        gap="the log line is cut short",
        evidence={"file": runner},
        backlog_dir=backlog_dir,
    )
    scheduler_row = improvement_backlog.add_candidate(
        title="the scheduler never records its permit refusal",
        kind="gap",
        gap="the permit refusal is invisible at scheduler.sh:1500",
        backlog_dir=backlog_dir,
    )
    _set_status(backlog_dir, wontfix_row.id, "wontfix")

    rc, out, err = _run_cli(
        tmp_path,
        "--title", "the runner drops its child on relaunch",
        "--gap", "the child is dropped again",
        "--file", runner,
        "--line", "3288",
    )
    assert rc == 0
    assert open_row.id in err
    assert wontfix_row.id not in err
    assert scheduler_row.id not in err
    assert "1 row(s) name the same file" in err


# ── AC-7 (control) ───────────────────────────────────────────────────────────


def test_ac7_automated_writers_are_unchanged(tmp_path):
    import improvement_backlog

    backlog_dir = tmp_path / "backlog"
    entry = improvement_backlog.add_candidate(
        title="local-checks-stuck: gh-resolve",
        kind="toolkit",
        gap="Loop classified",
        backlog_dir=backlog_dir,
    )
    assert entry is not None
    rows = json.loads((backlog_dir / "candidates.json").read_text(encoding="utf-8"))
    assert len(rows) == 1


# ── AC-8 ─────────────────────────────────────────────────────────────────────


def test_ac8_validate_candidate_is_pure():
    from improvement_backlog import validate_candidate

    assert (
        validate_candidate(
            title="triage amend: no-progress at -",
            kind="toolkit",
            gap="run 20261009-143942 x",
            evidence={},
            proposed_fix="",
        )
        == ["title-placeholder"]
    )
    assert (
        validate_candidate(
            title=REAL_TITLE,
            kind="bug",
            gap=REAL_GAP,
            evidence={},
            proposed_fix=REAL_FIX,
        )
        == []
    )
