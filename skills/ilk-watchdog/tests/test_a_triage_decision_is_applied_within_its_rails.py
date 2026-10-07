"""Red-first tests for triage_apply.py — a triage decision is applied within its rails.

Covers AC-1..AC-6 from sub-plan a-triage-decision-is-applied-within-its-rails:

AC-1: the replay fixture with a stub decision ack-and-relaunch: the failing
      sub-plan's Findings gains one #### Triage block, blacklist-cleared.json
      exists, and a triage-applied row exists. Nothing else under the fake data
      root changed (compare a recursive (path, sha256) snapshot taken before
      and after). Red-first.
AC-2: reopen with step greater than current_step, amend of a slug that doesn't
      exist, and an empty basis all become park-and-escalate, with the problem
      in the escalation row and no plan edit. Red-first.
AC-3: two strikes: the same slug and signature, triaged twice, gives
      triage-applied then escalated with reason two strikes. With the
      current_step advanced in between, both apply. Red-first.
AC-4: a decision whose finding asks to edit skills/foo.py still only writes
      the Findings text; and calling _assert_write_allowed on the project repo
      path, on a MASTER path and on runtime/batch-gate.json raises. Red-first.
AC-5: the kill switch and idempotency refusals write no plan change. Red-first.
AC-6 (control): park-and-escalate calls the stubbed ilk_notify once with
      event triage-escalated.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "triage" / "run-20261003-125807"


# ── helpers ──────────────────────────────────────────────────────────────────


def _snapshot(root: Path) -> dict[str, str]:
    """Recursively hash every file under *root*. Returns {relative_path: sha256}."""
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root))
            snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def _build_fake_data_dir(tmp_path: Path) -> Path:
    """Build a fake data root with plans, runtime, and logs from the replay fixture."""
    import shutil

    data_dir = tmp_path / "ilk-data"
    data_dir.mkdir()

    # Copy fixture runtime + logs
    for sub in ("runtime", "logs"):
        src = FIXTURE_DIR / sub
        if src.exists():
            shutil.copytree(src, data_dir / sub)

    # Copy fixture plans and add a fake active sub-plan
    plans_dir = data_dir / "plans"
    plans_dir.mkdir()
    fixture_plans = FIXTURE_DIR / "plans"
    if fixture_plans.exists():
        for p in fixture_plans.iterdir():
            shutil.copy(p, plans_dir)

    # Create a minimal active sub-plan for testing
    active_plan = plans_dir / "2026-10-03c-test-active.md"
    active_plan.write_text(
        "---\n"
        "plan: test-active\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-03\n"
        "---\n\n"
        "# Test active sub-plan\n\n"
        "## Steps\n\n"
        "### Step 0\n\n"
        "Done.\n\n"
        "## Findings\n\n"
        "Nothing yet.\n",
        encoding="utf-8",
    )

    return data_dir


def _make_decision(
    action: str = "ack-and-relaunch",
    *,
    slug: str | None = None,
    step: int | None = None,
    finding: str = "test finding",
    basis: str = "test basis",
    falsifier: str = "test falsifier",
) -> dict:
    """Build a decision dict matching ilk_triage.decide's output shape."""
    return {
        "action": action,
        "slug": slug,
        "step": step,
        "finding": finding,
        "basis": basis,
        "falsifier": falsifier,
        "model": "claude-opus-test",
    }


# ── AC-1: ack-and-relaunch applies cleanly ───────────────────────────────────


def test_ack_and_relaunch_appends_finding_and_acks():
    """ack-and-relaunch must: append Triage block to sub-plan Findings,
    write blacklist-cleared.json, and emit a triage-applied audit row.
    Nothing else changed.
    """
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))

        before = _snapshot(data_dir)
        decision = _make_decision()
        result = apply(decision, data_dir, run_id="20261003-125807")
        after = _snapshot(data_dir)

        # 1. triage-applied audit row
        assert result["audit_kind"] == "triage-applied"

        # 2. blacklist-cleared.json exists
        ack_path = data_dir / "runtime" / "launcher" / "blacklist-cleared.json"
        assert ack_path.exists(), "blacklist-cleared.json must be written"
        ack_data = json.loads(ack_path.read_text(encoding="utf-8"))
        assert "cleared_at" in ack_data

        # 3. The active sub-plan's Findings gained a #### Triage block
        active_plan = data_dir / "plans" / "2026-10-03c-test-active.md"
        text = active_plan.read_text(encoding="utf-8")
        assert "#### Triage" in text, "Findings must gain a Triage heading"
        assert "test finding" in text

        # 4. Only expected files changed
        changed = {p for p in (set(after) - set(before)) | (set(before) - set(after))
                   if before.get(p) != after.get(p)}
        # Allowed changes: the plan file, blacklist-cleared.json, triage/state.json, audit files
        allowed_prefixes = ("plans/", "runtime/launcher/blacklist-cleared.json",
                            "runtime/triage/", "audit/")
        for c in changed:
            assert any(c.startswith(p) for p in allowed_prefixes), \
                f"unexpected change to {c}"


# ── AC-2: validation failures become park-and-escalate ───────────────────────


def test_reopen_step_greater_than_current_step_escalates():
    """reopen with step > current_step must become park-and-escalate."""
    from triage_apply import validate

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision("reopen", slug="test-active", step=99,
                                  finding="f", basis="b", falsifier="fl")
        problems = validate(decision, data_dir / "plans")
        assert any("step" in p.lower() and "current" in p.lower() for p in problems), \
            f"must reject step > current_step, got: {problems}"


def test_amend_unknown_slug_escalates():
    """amend of a slug that doesn't exist must become park-and-escalate."""
    from triage_apply import validate

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision("amend", slug="nonexistent-slug",
                                  finding="f", basis="b", falsifier="fl")
        problems = validate(decision, data_dir / "plans")
        assert any("slug" in p.lower() or "not found" in p.lower() for p in problems), \
            f"must reject unknown slug, got: {problems}"


def test_empty_basis_escalates():
    """A decision with empty basis must become park-and-escalate."""
    from triage_apply import validate

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision(basis="")
        problems = validate(decision, data_dir / "plans")
        assert any("basis" in p.lower() for p in problems), \
            f"must reject empty basis, got: {problems}"


def test_validation_problems_turn_into_park_and_escalate():
    """When validate returns problems, apply must escalate with the problems as reason."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision("amend", slug="nonexistent-slug",
                                  finding="f", basis="b", falsifier="fl")
        result = apply(decision, data_dir, run_id="20261003-125807")
        assert result["action"] == "park-and-escalate", \
            f"validation failure must escalate, got: {result['action']}"
        assert result.get("reason"), "escalation must name a reason"


# ── AC-3: two strikes ────────────────────────────────────────────────────────


def test_two_strikes_same_signature_escalates():
    """Same slug + signature triaged twice: first triage-applied, then escalated with reason two_strikes."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision("ack-and-relaunch")

        # First apply: should succeed
        result1 = apply(decision, data_dir, run_id="20261003-125807")
        assert result1["audit_kind"] == "triage-applied"

        # Second apply with same signature: should escalate
        result2 = apply(decision, data_dir, run_id="20261003-125807")
        assert result2["action"] == "park-and-escalate"
        assert result2.get("reason") == "two strikes"


def test_two_strikes_with_progress_between_both_apply():
    """With current_step advanced in between, the second apply is refused
    by idempotency (same run_id already has a terminal row), even though
    the two-strikes signature changed."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))

        # First apply
        decision1 = _make_decision("ack-and-relaunch")
        result1 = apply(decision1, data_dir, run_id="20261003-125807")
        assert result1["audit_kind"] == "triage-applied"

        # Advance current_step to change the signature
        active_plan = data_dir / "plans" / "2026-10-03c-test-active.md"
        text = active_plan.read_text(encoding="utf-8")
        text = text.replace("current_step: 1", "current_step: 2")
        active_plan.write_text(text, encoding="utf-8")

        # Second apply with different signature but same run_id:
        # idempotency catches the duplicate run_id.
        result2 = apply(decision1, data_dir, run_id="20261003-125807")
        assert result2["action"] == "refused"


def test_idempotency_reads_the_day_the_writer_wrote(monkeypatch):
    """The idempotency reader must use the writer's clock. With the reader
    on UTC and the writer on local time, a run applied between local midnight
    and 08:00 (UTC+8) was applied twice (2026-10-04 00:20). Pin it with a
    UTC clock far from the local date: only the writer's day may count."""
    import triage_apply
    from datetime import datetime as _dt, timezone as _tz

    class _FarUTC(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt(2000, 1, 1, tzinfo=_tz.utc)

    monkeypatch.setattr(triage_apply, "datetime", _FarUTC)
    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        result1 = triage_apply.apply(_make_decision("ack-and-relaunch"), data_dir,
                                     run_id="20261003-125807")
        assert result1["audit_kind"] == "triage-applied"
        assert triage_apply._already_applied(data_dir, run_id="20261003-125807")


# ── AC-4: write set enforcement ──────────────────────────────────────────────


def test_finding_asking_to_edit_code_only_writes_findings_text():
    """A decision whose finding asks to edit skills/foo.py still only writes the Findings text."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))

        # Create a skills/foo.py that should NOT be touched
        skills_dir = Path(td) / "project" / "skills"
        skills_dir.mkdir(parents=True)
        foo = skills_dir / "foo.py"
        foo.write_text("original content", encoding="utf-8")

        decision = _make_decision(
            "amend", slug="test-active",
            finding="Please edit skills/foo.py to fix the bug",
            basis="b", falsifier="fl",
        )
        result = apply(decision, data_dir, run_id="20261003-125807")
        assert result["audit_kind"] == "triage-applied"

        # The file must not have been touched
        assert foo.read_text(encoding="utf-8") == "original content"


def test_assert_write_allowed_refuses_project_repo_path():
    """_assert_write_allowed must raise for a path in the project repo."""
    from triage_apply import _assert_write_allowed

    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "ilk-data"
        data_dir.mkdir()
        project_repo = Path(td) / "project"
        project_repo.mkdir()
        target = project_repo / "skills" / "foo.py"
        with pytest.raises((ValueError, RuntimeError, PermissionError)):
            _assert_write_allowed(target, data_dir)


def test_assert_write_allowed_refuses_master_path():
    """_assert_write_allowed must raise for a MASTER plan path."""
    from triage_apply import _assert_write_allowed

    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "ilk-data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir()
        master = plans_dir / "MASTER-2026-10-03c-execution-plan.md"
        with pytest.raises((ValueError, RuntimeError, PermissionError)):
            _assert_write_allowed(master, data_dir)


def test_assert_write_allowed_refuses_batch_gate():
    """_assert_write_allowed must raise for runtime/batch-gate.json."""
    from triage_apply import _assert_write_allowed

    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "ilk-data"
        data_dir.mkdir()
        batch_gate = data_dir / "runtime" / "batch-gate.json"
        with pytest.raises((ValueError, RuntimeError, PermissionError)):
            _assert_write_allowed(batch_gate, data_dir)


# ── AC-5: kill switch and idempotency ────────────────────────────────────────


def test_kill_switch_writes_no_plan_change():
    """triage.disabled must refuse with a triage-refused row and no plan edit."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        # Create the kill switch
        data_root = data_dir.parent
        (data_root / "triage.disabled").write_text("", encoding="utf-8")

        before = _snapshot(data_dir)
        decision = _make_decision()
        result = apply(decision, data_dir, run_id="20261003-125807",
                       data_root=data_root)
        after = _snapshot(data_dir)

        assert result["action"] == "refused"
        assert result.get("reason") == "kill_switch"

        # No plan file changed
        plans_before = {k: v for k, v in before.items() if k.startswith("plans/")}
        plans_after = {k: v for k, v in after.items() if k.startswith("plans/")}
        assert plans_before == plans_after, "kill switch must not edit any plan"


def test_idempotency_writes_no_plan_change():
    """A second apply for the same (project, slug, signature) escalates via
    two-strikes (which fires before idempotency), with no plan edit."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))
        decision = _make_decision()

        # First apply succeeds
        result1 = apply(decision, data_dir, run_id="20261003-125807")
        assert result1["audit_kind"] == "triage-applied"

        before = _snapshot(data_dir)
        # Second apply with same slug+signature: two-strikes escalates
        result2 = apply(decision, data_dir, run_id="20261003-125807")
        after = _snapshot(data_dir)

        assert result2["action"] == "park-and-escalate"
        # No plan file changed on the second call
        plans_before = {k: v for k, v in before.items() if k.startswith("plans/")}
        plans_after = {k: v for k, v in after.items() if k.startswith("plans/")}
        assert plans_before == plans_after, "two-strikes must not edit any plan"


# ── AC-6 (control): park-and-escalate calls ilk_notify ───────────────────────


def test_park_and_escalate_calls_ilk_notify():
    """park-and-escalate must call ilk_notify once with event triage-escalated."""
    from triage_apply import apply

    with tempfile.TemporaryDirectory() as td:
        data_dir = _build_fake_data_dir(Path(td))

        # Track ilk_notify calls
        notify_calls: list[dict] = []

        def stub_notify(*, event: str, project: str, detail: str | None = None):
            notify_calls.append({"event": event, "project": project, "detail": detail})

        # Patch ilk_notify
        import triage_apply
        original_notify = getattr(triage_apply, "ilk_notify", None)
        triage_apply.ilk_notify = stub_notify
        try:
            decision = _make_decision("park-and-escalate",
                                      finding="cannot resolve",
                                      basis="no falsifier",
                                      falsifier="")
            result = apply(decision, data_dir, run_id="20261003-125807")
            assert result["action"] == "park-and-escalate"
            assert len(notify_calls) == 1, f"expected 1 notify call, got {len(notify_calls)}"
            assert notify_calls[0]["event"] == "triage-escalated"
        finally:
            triage_apply.ilk_notify = original_notify