"""Contract tests for the one-page daily digest renderer.

Each test imports ``ilk_digest`` inside its body so the file can collect
even when the module is on a different path.  All ACs are xfail(strict=True)
until ``ilk_digest.py`` exists.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_audit_row(
    tmp_path: Path,
    kind: str,
    project: str,
    *,
    ts: str | None = None,
    **fields: object,
) -> dict[str, object]:
    """Write a single audit row to ``tmp_path/audit/<today>.jsonl``."""
    from ilk_audit import write_audit

    row = write_audit(kind, project, root=tmp_path, **fields)
    return row


def _build_day(
    tmp_path: Path,
    day: str,
    rows: list[dict[str, object]],
) -> None:
    """Write *rows* as JSON lines to ``tmp_path/audit/<day>.jsonl``."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    day_file = audit_dir / f"{day}.jsonl"
    with open(day_file, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


# ── AC-1: fixture day with escalations, releases, triage actions ─────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac1_fixture_day_with_escalations_releases_triage(tmp_path: Path):
    """AC-1: escalations section first, names 'two strikes'; releases names
    v0.0.2 and says 1 nothing-to-release; triage says 3 applied, 0 refused,
    1 escalated, of 4 decisions."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # 4 triage-decided
    rows = [
        {"ts": "2026-10-03T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
        {"ts": "2026-10-03T10:01:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "ack-and-relaunch"},
        {"ts": "2026-10-03T10:02:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "reopen"},
        {"ts": "2026-10-03T10:03:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
        # 3 triage-applied
        {"ts": "2026-10-03T10:04:00+08:00", "host": "h", "kind": "triage-applied", "project": "p", "action": "amend", "slug": "s1"},
        {"ts": "2026-10-03T10:05:00+08:00", "host": "h", "kind": "triage-applied", "project": "p", "action": "ack-and-relaunch", "slug": "s2"},
        {"ts": "2026-10-03T10:06:00+08:00", "host": "h", "kind": "triage-applied", "project": "p", "action": "reopen", "slug": "s3"},
        # 1 escalated
        {"ts": "2026-10-03T10:07:00+08:00", "host": "h", "kind": "escalated", "project": "p", "reason": "two strikes", "run_id": "r1"},
        # 1 released (deployed)
        {"ts": "2026-10-03T10:08:00+08:00", "host": "h", "kind": "released", "project": "p", "tag": "v0.0.2", "outcome": "deployed"},
        # 1 released (skipped)
        {"ts": "2026-10-03T10:09:00+08:00", "host": "h", "kind": "released", "project": "p", "outcome": "skipped", "reason": "nothing to release"},
    ]
    _build_day(tmp_path, day, rows)

    page = render(day, root=tmp_path, now=now)

    # Escalations section is first
    esc_idx = page.index("## Escalations")
    rel_idx = page.index("## Releases")
    tri_idx = page.index("## Triage actions")
    assert esc_idx < rel_idx < tri_idx, "escalations must come first"

    # Escalations names "two strikes"
    assert "two strikes" in page, "escalation reason must appear"

    # Releases names v0.0.2
    assert "v0.0.2" in page, "release tag must appear"
    assert "nothing to release" in page.lower() or "1 release-train checks found nothing to release" in page

    # Triage actions: 3 applied, 0 refused, 1 escalated, of 4 decisions
    assert "3 applied" in page
    assert "0 refused" in page
    assert "1 escalated" in page
    assert "4 decisions" in page


# ── AC-2: no escalations, denominators present ───────────────────────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac2_no_escalations_shows_denominators(tmp_path: Path):
    """AC-2: 0 of 4 triage decisions escalated."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    rows = [
        {"ts": "2026-10-03T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
        {"ts": "2026-10-03T10:01:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
        {"ts": "2026-10-03T10:02:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
        {"ts": "2026-10-03T10:03:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
    ]
    _build_day(tmp_path, day, rows)

    page = render(day, root=tmp_path, now=now)

    assert "0 of 4 triage decisions escalated" in page, "denominator must appear"


# ── AC-3: unreadable audit file ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac3_unreadable_audit_file(tmp_path: Path):
    """AC-3: second line is 'not json'; page contains unreadable, path:2,
    no ' 0 of ' count anywhere."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # Build a day file with one good line and one bad line
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    day_file = audit_dir / f"{day}.jsonl"
    good = json.dumps({"ts": "2026-10-03T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"})
    day_file.write_text(good + "\nnot json\n", encoding="utf-8")

    page = render(day, root=tmp_path, now=now)

    assert "unreadable" in page, "page must flag unreadable"
    assert f"{day_file}:2" in page or str(day_file) in page, "must name file and line"
    # No " 0 of " anywhere — unreadable means unknown, not zero
    assert " 0 of " not in page, "unreadable must not produce zero counts"


# ── AC-4: render --day D as subprocess writes only digest/ ───────────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac4_subprocess_render_writes_only_digest(tmp_path: Path):
    """AC-4: render --day D writes <tmp>/digest/D.md and nothing else."""
    day = "2026-10-03"

    # Write a minimal audit file
    _build_day(tmp_path, day, [
        {"ts": "2026-10-03T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
    ])

    # Snapshot files before
    before = set()
    for p in tmp_path.rglob("*"):
        if p.is_file():
            before.add(p.relative_to(tmp_path))

    script = Path(__file__).resolve().parent.parent / "scripts" / "ilk_digest.py"
    result = subprocess.run(
        [sys.executable, str(script), "render", "--day", day],
        env={**os.environ, "ILK_DATA_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"exit 0 expected, got {result.returncode}\n{result.stderr}"

    # Snapshot files after
    after = set()
    for p in tmp_path.rglob("*"):
        if p.is_file():
            after.add(p.relative_to(tmp_path))

    new_files = after - before
    assert new_files == {Path("digest") / f"{day}.md"}, (
        f"only digest/{day}.md should be created, got {new_files}"
    )


# ── AC-5: today prints page, writes nothing ─────────────────────────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac5_today_prints_writes_nothing(tmp_path: Path):
    """AC-5: 'today' prints a page whose title holds today's date, writes no file."""
    from ilk_digest import render

    today = date.today().isoformat()

    # Write a minimal audit file for today
    _build_day(tmp_path, today, [
        {"ts": f"{today}T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"},
    ])

    # Snapshot files before
    before = set()
    for p in tmp_path.rglob("*"):
        if p.is_file():
            before.add(p.relative_to(tmp_path))

    script = Path(__file__).resolve().parent.parent / "scripts" / "ilk_digest.py"
    result = subprocess.run(
        [sys.executable, str(script), "today"],
        env={**os.environ, "ILK_DATA_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"exit 0 expected, got {result.returncode}\n{result.stderr}"
    assert today in result.stdout, "title must contain today's date"

    # No new files
    after = set()
    for p in tmp_path.rglob("*"):
        if p.is_file():
            after.add(p.relative_to(tmp_path))
    assert after == before, "today must not write any file"


# ── AC-6 (control): no day file ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="ilk_digest.py not yet written")
def test_ac6_no_day_file_missing_source(tmp_path: Path):
    """AC-6: no day file → source says 'missing — nothing was recorded',
    escalations empty state says '0 of 0'."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # No audit dir at all
    page = render(day, root=tmp_path, now=now)

    assert "missing" in page.lower(), "source line must say missing"
    assert "0 of 0" in page, "escalations empty state must say 0 of 0"