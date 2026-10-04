"""RED pins — batches-per-project and judgment-call sections on the digest page.

Builds a synthetic ``ILK_DATA_HOME`` with fake projects, masters and
sub-plans.  All imports happen inside test bodies so collection works
even when the module path differs.

AC-1..AC-5 are ``xfail(strict=True)`` because the two new sections do
not yet exist in ``ilk_digest.render``.  AC-6 (control) is unmarked —
sub-plan 0 already satisfies it.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── fixture builders ──────────────────────────────────────────────────────────


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _master_md(
    title: str = "Test batch",
    slug: str = "test-batch",
    status: str = "active",
    created: str = "2026-10-03T10:00:00+08:00",
    rationale: str | None = None,
    registry_rows: list[str] | None = None,
) -> str:
    """Build a MASTER .md with optional Execution rationale.

    *registry_rows*: raw table rows for the Sub-plan registry, e.g.
    ``["| 1 | [2026-10-03-sp-a.md](./2026-10-03-sp-a.md) | 2 | pending |"]``.
    """
    rows = ""
    if registry_rows:
        rows = "\n".join(registry_rows) + "\n"
    body = (
        f"---\n"
        f"master_plan: {slug}\n"
        f"batch_date: 2026-10-03\n"
        f"status: {status}\n"
        f"created: {created}\n"
        f"title: {title}\n"
        f"---\n\n"
        f"# MASTER plan\n\n"
        f"## Sub-plan registry\n\n"
        f"| # | Slug | Steps | Status |\n"
        f"|---|---|---|---|\n"
        f"{rows}"
    )
    if rationale is not None:
        body += f"\n## Execution rationale\n\n{rationale}\n"
    return body


def _subplan_md(
    slug: str = "sp-a",
    status: str = "pending",
    last_updated: str = "2026-10-03",
    current_step: int = 0,
    estimated_steps: int = 2,
) -> str:
    return (
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        f"last_updated: {last_updated}\n"
        f"depends_on: []\n"
        f"---\n\n"
        f"# Sub-plan: {slug}\n"
    )


def _sentinel_json(pid: int, state: str = "running") -> str:
    return json.dumps({"pid": pid, "state": state})


def _dead_pid() -> int:
    """Return a PID that is almost certainly not alive."""
    return 99999


def _build_project(
    base: Path,
    key: str,
    *,
    masters: list[tuple[str, str]] | None = None,
    subplans: list[tuple[str, str, str]] | None = None,
    sentinel_pid: int | None = None,
    sentinel_state: str = "running",
    postmortem_class: str | None = None,
    postmortem_time: str | None = None,
) -> None:
    """Create a synthetic project under ``base/projects/<key>/``.

    *masters*: list of ``(filename, content)`` pairs.
    *subplans*: list of ``(filename, slug, status)`` triples.
    """
    proj = base / "projects" / key
    plans = proj / "plans"
    runtime = proj / "runtime"
    launcher = runtime / "launcher"

    if subplans:
        for fname, slug, status in subplans:
            _write_file(plans / fname, _subplan_md(slug, status))

    # Patch master content to include registry rows referencing the sub-plans.
    if masters and subplans:
        registry_rows = []
        for idx, (fname, _slug, _status) in enumerate(subplans, 1):
            registry_rows.append(
                f"| {idx} | [{fname}](./{fname}) | 2 | pending |"
            )
        patched_masters = []
        for fname, content in masters:
            # Insert registry rows after the table header
            header = "| # | Slug | Steps | Status |\n|---|---|---|---|\n"
            if header in content:
                rows_text = "\n".join(registry_rows) + "\n"
                content = content.replace(header, header + rows_text)
            patched_masters.append((fname, content))
        masters = patched_masters

    if masters:
        for fname, content in masters:
            _write_file(plans / fname, content)

    # Sentinel (optional)
    if sentinel_pid is not None:
        launcher.mkdir(parents=True, exist_ok=True)
        _write_file(launcher / "last-exit.json", _sentinel_json(sentinel_pid, sentinel_state))

    # Postmortem (optional)
    if postmortem_class and postmortem_time:
        _write_file(
            launcher / "postmortems" / "run-001.md",
            (
                f"---\n"
                f"classification: {postmortem_class}\n"
                f"generated_at: {postmortem_time}\n"
                f"---\n\n"
                f"# Postmortem\n\n"
                f"Classification: {postmortem_class}\n"
            ),
        )


# ── AC-1: shipped and idle batches ───────────────────────────────────────────


def test_ac1_batches_shipped_and_idle(tmp_path: Path):
    """AC-1: p1 has 2 sub-plans shipped on D, 1 pending on D; p2 is idle.

    Batches says ``2 shipped of 3 sub-plans last updated on D`` for p1
    and ``1 of 2 projects idle: p2``.
    """
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # p1: 2 shipped + 1 pending, all last_updated on D
    _build_project(
        tmp_path,
        "p1",
        masters=[
            ("MASTER-2026-10-03-batch.md", _master_md()),
        ],
        subplans=[
            ("2026-10-03-sp-a.md", "sp-a", "shipped"),
            ("2026-10-03-sp-b.md", "sp-b", "shipped"),
            ("2026-10-03-sp-c.md", "sp-c", "pending"),
        ],
    )

    # p2: no sentinel, no postmortem, active master with pending sub-plans → idle
    _build_project(
        tmp_path,
        "p2",
        masters=[
            ("MASTER-2026-10-02-batch.md", _master_md(
                created="2026-10-02T10:00:00+08:00",
                status="active",
            )),
        ],
        subplans=[
            ("2026-10-02-sp-x.md", "sp-x", "pending"),
        ],
    )

    page = render(day, root=tmp_path, now=now)

    assert "## Batches" in page
    assert "2 shipped of 3 sub-plans last updated on D" in page
    assert "1 of 2 projects idle: p2" in page


# ── AC-2: blocked project ────────────────────────────────────────────────────


def test_ac2_blocked_project(tmp_path: Path):
    """AC-2: a project blacklisted within backoff shows ``blocked:`` with reason."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)
    recent = datetime.now().isoformat(timespec="seconds")

    _build_project(
        tmp_path,
        "p-blocked",
        masters=[
            ("MASTER-2026-10-03-batch.md", _master_md()),
        ],
        subplans=[
            ("2026-10-03-sp-a.md", "sp-a", "pending"),
        ],
        postmortem_class="local-checks-stuck",
        postmortem_time=recent,
    )

    page = render(day, root=tmp_path, now=now)

    assert "## Batches" in page
    assert "blocked:" in page


# ── AC-3: unreadable project ─────────────────────────────────────────────────


def test_ac3_unreadable_project(tmp_path: Path, monkeypatch):
    """AC-3: resolve_project_status raises for a project →
    ``status unreadable`` and header counts 1."""
    from ilk_digest import render
    import ilk_digest

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # p-good: normal project
    _build_project(
        tmp_path,
        "p-good",
        masters=[
            ("MASTER-2026-10-03-batch.md", _master_md()),
        ],
        subplans=[
            ("2026-10-03-sp-a.md", "sp-a", "shipped"),
        ],
    )

    # p-bad: has a plans dir so it gets iterated
    _build_project(
        tmp_path,
        "p-bad",
        masters=[
            ("MASTER-2026-10-03-broken.md", "not a valid master file at all"),
        ],
    )

    # Monkeypatch resolve_project_status to raise for p-bad
    _real_resolve = ilk_digest.resolve_project_status

    def _patched_resolve(project_dir, **kwargs):
        if project_dir.name == "p-bad":
            raise ValueError("unparseable frontmatter")
        return _real_resolve(project_dir, **kwargs)

    monkeypatch.setattr(ilk_digest, "resolve_project_status", _patched_resolve)

    page = render(day, root=tmp_path, now=now)

    assert "## Batches" in page
    assert "status unreadable" in page
    assert "1 unreadable" in page


# ── AC-4: judgment calls from masters created on D ───────────────────────────


def test_ac4_judgment_calls_from_masters_on_day(tmp_path: Path):
    """AC-4: master on D with 2 judgment calls (one multi-line) + master on D-1 with 1.

    ``2 judgment calls in 1 of 1 masters created on D`` and the multi-line
    call's ``Wrong if`` text appears whole.
    """
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    rationale_d = (
        "- Judgment call: the renderer reads fields tolerantly. Basis: the memory\n"
        "  rule. Wrong if the shipped writers use other names.\n"
        "- Judgment call: an unreadable audit day file makes every section say\n"
        "  unknown. Basis: the memory rule that an empty answer must be\n"
        "  unconstructible without looking. Wrong if a single torn line ever\n"
        "  hides a whole day that mattered.\n"
    )

    rationale_d1 = (
        "- Judgment call: something old. Basis: old basis. Wrong if old falsifier.\n"
    )

    _build_project(
        tmp_path,
        "p1",
        masters=[
            ("MASTER-2026-10-03-batch.md", _master_md(rationale=rationale_d)),
            ("MASTER-2026-10-02-old.md", _master_md(
                created="2026-10-02T10:00:00+08:00",
                status="shipped",
                rationale=rationale_d1,
            )),
        ],
        subplans=[
            ("2026-10-03-sp-a.md", "sp-a", "shipped"),
        ],
    )

    page = render(day, root=tmp_path, now=now)

    assert "## Judgment calls made" in page
    assert "2 judgment calls" in page
    assert "1 of 1 masters created on D" in page
    # Multi-line Wrong if appears whole
    assert "hides a whole day that mattered" in page


# ── AC-5: master on D with no Execution rationale ────────────────────────────


def test_ac5_master_on_day_no_rationale(tmp_path: Path):
    """AC-5: master created on D with no Execution rationale →
    ``0 judgment calls in 1 masters created on D``."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    _build_project(
        tmp_path,
        "p1",
        masters=[
            # No rationale argument → no Execution rationale section
            ("MASTER-2026-10-03-batch.md", _master_md()),
        ],
        subplans=[
            ("2026-10-03-sp-a.md", "sp-a", "shipped"),
        ],
    )

    page = render(day, root=tmp_path, now=now)

    assert "## Judgment calls made" in page
    assert "0 judgment calls" in page
    assert "1 masters created on D" in page


# ── AC-6 (control): sub-plan 0 ACs still hold ───────────────────────────────


def test_ac6_control_escalations_section_first(tmp_path: Path):
    """AC-6 (control): the first ``## `` section is still ``Escalations needing Chad``."""
    from ilk_digest import render

    day = "2026-10-03"
    now = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)

    # Minimal audit row so the page is non-empty
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"{day}.jsonl").write_text(
        json.dumps({"ts": "2026-10-03T10:00:00+08:00", "host": "h", "kind": "triage-decided", "project": "p", "action": "amend"}) + "\n",
        encoding="utf-8",
    )

    page = render(day, root=tmp_path, now=now)

    # First ## section must be Escalations
    sections = [line for line in page.splitlines() if line.startswith("## ")]
    assert sections[0] == "## Escalations needing Chad"