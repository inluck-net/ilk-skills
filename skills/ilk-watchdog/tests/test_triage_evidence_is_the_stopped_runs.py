"""Red-first tests for build_evidence run-scoped slug and sentinel matching.

Covers AC-1..AC-4 from sub-plan triage-evidence-is-the-stopped-runs:

AC-1: slug from the sentinel's failed_check when run_id matches.
AC-2: slug from gate-history when sentinel is superseded (different run_id).
AC-3: no slug when sentinel is superseded and no gate-history rows for this run.
AC-4: exact slug match (foo does not select foo-verify).

Each test builds a hermetic data dir under tmp_path with:
  - runtime/launcher/last-exit.json
  - runtime/launcher/gate-history.jsonl
  - plans/*.md with --- frontmatter

No project_path is set, so git probes are skipped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable (same idiom as test_a_stopped_run_gets_a_diagnosis.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# -- helpers ------------------------------------------------------------------

def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _write_gate_history(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, separators=(",", ":")) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_plan(plans_dir: Path, filename: str, slug: str, status: str,
                findings_body: str = "") -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = f"---\nplan: {slug}\nstatus: {status}\n---\n"
    if findings_body:
        body += f"\n## Findings\n\n{findings_body}\n"
    (plans_dir / filename).write_text(body, encoding="utf-8")


# -- AC-1: slug from sentinel's failed_check when run_id matches -------------

@pytest.mark.xfail(strict=True, reason="evidence is not yet run-scoped")
def test_ac1_slug_from_sentinel_when_run_id_matches(tmp_path: Path):
    """When sentinel run_id == triaged run_id, slug comes from failed_check."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    # Sentinel: run_id R, failed_check.slug = zzz-target
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "local_checks_failed",
        "run_id": "R",
        "failed_check": {"slug": "zzz-target", "step": 1, "command": "pytest"},
    })

    # Two plans: aaa-other (pending) and zzz-target (in-progress, with findings)
    _write_plan(plans_dir, "2026-01-01-aaa-other.md", "aaa-other", "pending")
    _write_plan(plans_dir, "2026-01-02-zzz-target.md", "zzz-target", "in-progress",
                findings_body="TARGET-FINDINGS")

    evidence = build_evidence(data_dir, "R")

    assert "plan: zzz-target" in evidence["subplan_frontmatter"]
    assert evidence["subplan_findings"] == "TARGET-FINDINGS"
    assert evidence.get("subplan_slug_source") == "last_exit"
    assert evidence.get("subplan_slug") == "zzz-target"


# -- AC-2: slug from gate-history when sentinel is superseded ----------------

@pytest.mark.xfail(strict=True, reason="evidence is not yet run-scoped")
def test_ac2_slug_from_gate_history_when_sentinel_superseded(tmp_path: Path):
    """When sentinel run_id != triaged run_id, slug comes from gate-history."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    # Sentinel belongs to R2 (superseded), not R
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "blocked-no-runnable",
        "run_id": "R2",
        "failed_check": {"slug": "aaa-other", "step": 0, "command": "pytest"},
    })

    # Gate-history: R has zzz-target pass then fail; R2 has aaa-other fail
    _write_gate_history(data_dir / "runtime" / "launcher" / "gate-history.jsonl", [
        {"slug": "zzz-target", "step": 0, "outcome": "pass", "run_id": "R", "iteration": 1},
        {"slug": "zzz-target", "step": 1, "outcome": "fail", "run_id": "R", "iteration": 2},
        {"slug": "aaa-other", "step": 0, "outcome": "fail", "run_id": "R2", "iteration": 1},
    ])

    _write_plan(plans_dir, "2026-01-01-aaa-other.md", "aaa-other", "pending")
    _write_plan(plans_dir, "2026-01-02-zzz-target.md", "zzz-target", "in-progress")

    evidence = build_evidence(data_dir, "R")

    assert "plan: zzz-target" in evidence["subplan_frontmatter"]
    assert evidence.get("subplan_slug_source") == "gate_history_fail"
    assert evidence.get("subplan_slug") == "zzz-target"
    # Sentinel is superseded: last_exit should not carry state
    assert "state" not in evidence["last_exit"]
    assert evidence["last_exit"]["superseded"] is True
    assert any("superseded" in m for m in evidence["missing_sources"])


# -- AC-3: no slug when sentinel superseded and no gate-history for this run --

@pytest.mark.xfail(strict=True, reason="evidence is not yet run-scoped")
def test_ac3_no_slug_when_no_gate_history_for_run(tmp_path: Path):
    """When sentinel is superseded and no gate-history rows exist for this run,
    subplan_frontmatter is empty and missing_sources names the sub-plan gap."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "blocked-no-runnable",
        "run_id": "R2",
    })

    # Gate-history only has rows for R2, not R
    _write_gate_history(data_dir / "runtime" / "launcher" / "gate-history.jsonl", [
        {"slug": "aaa-other", "step": 0, "outcome": "fail", "run_id": "R2", "iteration": 1},
    ])

    _write_plan(plans_dir, "2026-01-01-aaa-other.md", "aaa-other", "pending")
    _write_plan(plans_dir, "2026-01-02-zzz-target.md", "zzz-target", "in-progress")

    evidence = build_evidence(data_dir, "R")

    assert evidence["subplan_frontmatter"] == ""
    assert evidence.get("subplan_slug") is None
    assert any("sub-plan" in m for m in evidence["missing_sources"])


# -- AC-4: exact slug match (foo does not select foo-verify) ------------------

@pytest.mark.xfail(strict=True, reason="evidence is not yet run-scoped")
def test_ac4_exact_slug_match_not_substring(tmp_path: Path):
    """Slug 'foo' must match plan: foo, not plan: foo-verify."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    # Sentinel run_id matches, failed_check.slug = foo
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "local_checks_failed",
        "run_id": "R",
        "failed_check": {"slug": "foo", "step": 0, "command": "pytest"},
    })

    _write_plan(plans_dir, "2026-01-01-foo.md", "foo", "in-progress")
    _write_plan(plans_dir, "2026-01-01-foo-verify.md", "foo-verify", "pending")

    evidence = build_evidence(data_dir, "R")

    assert "plan: foo" in evidence["subplan_frontmatter"]
    assert "plan: foo-verify" not in evidence["subplan_frontmatter"]
    assert evidence.get("subplan_slug") == "foo"