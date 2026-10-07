"""Red-first pins: a plain owned red is relaunched by rule, not escalated.

Sub-plan: a-plain-red-is-relaunched-not-escalated (step 0).
Drives run_triage / apply with a hermetic data dir.

AC-1 (contract bullet 1): rule-first ack-and-relaunch — when stop reason is
    local_checks_failed and gate-history names (slug, step) with failing node
    ids, run_triage applies ack-and-relaunch WITHOUT calling decide() and
    appends a ``#### Triage <ts> (rule)`` block to Findings with the failing
    ids, the red-owner verdict line, and ``Fix the code, never the assertion.``

AC-2 (contract bullet 2): bound — the rule fires at most twice for the same
    (slug, step). A third consecutive red goes to decide().

AC-3 (contract bullet 3): park-and-escalate notifies with event
    ``triage-escalated`` and detail = project + slug + reason, writes
    reason/finding into ``runtime/triage/<run_id>.log``, and writes an
    audit row.

AC-4 (control): the two-strikes mechanism still escalates after matching
    signatures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _write_plan(
    plans_dir: Path,
    filename: str,
    slug: str,
    status: str = "in-progress",
    current_step: int = 1,
) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / filename
    plan_path.write_text(
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: 3\n"
        f"last_updated: 2026-10-07\n"
        f"---\n\n"
        f"# {slug}\n\n"
        f"## Steps\n\n"
        f"### Step 0\n\n"
        f"Done.\n\n"
        f"## Findings\n\n"
        f"Nothing yet.\n",
        encoding="utf-8",
    )
    return plan_path


def _build_data_dir(
    tmp_path: Path,
    *,
    run_id: str = "20261007-192941",
    slug: str = "a-plain-red-is-relaunched-not-escalated",
    current_step: int = 1,
    state: str = "local_checks_failed",
    red_node_ids: list[str] | None = None,
    include_red_owner: bool = True,
) -> Path:
    """Build a hermetic data dir with sentinel, gate-history, and a sub-plan.

    *tmp_path* IS the data dir — no extra ``project/`` subdirectory is created,
    so ``plans/`` and ``runtime/`` sit directly under it (matching
    ``run_triage``'s ``data_root / "projects" / project_key`` resolution).
    """
    if red_node_ids is None:
        red_node_ids = ["tests/test_something.py::test_case"]

    data_dir = tmp_path

    # Sentinel: stopped with local_checks_failed
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": state,
        "run_id": run_id,
        "pid": 12345,
        "started_at": "2026-10-07T19:29:41+0800",
        "ended_at": "2026-10-07T19:41:10+0800",
        "iterations": 2,
        "project_path": str(data_dir),
        "failed_check": {
            "slug": slug,
            "step": current_step,
            "command": "python3 -m pytest ...",
        },
    })

    # Gate-history: a red row for (slug, step) with node_ids
    red_row = {
        "run_id": run_id,
        "slug": slug,
        "step": current_step,
        "outcome": "fail",
        "attribution": {"node_ids": red_node_ids},
    }
    if include_red_owner:
        red_row["red_owner"] = (
            f"owned (base e7f0b51) — green at iteration base — "
            f"this iteration broke it"
        )

    _write_json(data_dir / "runtime" / "launcher" / "gate-history.jsonl", red_row)
    # Write as JSONL (one JSON object per line)
    gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    gate_path.write_text(json.dumps(red_row) + "\n", encoding="utf-8")

    # Sub-plan
    _write_plan(
        data_dir / "plans",
        f"2026-10-07l-{slug}.md",
        slug,
        status="in-progress",
        current_step=current_step,
    )

    return data_dir


# ── AC-1: rule-first ack-and-relaunch ────────────────────────────────────────


class TestAC1_RuleFirstAckAndRelaunch:
    """When stop reason is local_checks_failed and gate-history has red rows
    with (slug, step) and failing node ids, triage applies ack-and-relaunch
    WITHOUT calling decide()."""

    def test_rule_first_skips_decide_and_relapses(self, tmp_path, monkeypatch):
        """run_triage applies ack-and-relaunch by rule when the sentinel is
        local_checks_failed and gate-history names the failing ids.
        """
        from ilk_triage import run_triage
        import ilk_triage
        import triage_apply

        # run_triage resolves: data_root / "projects" / project_key
        data_dir = _build_data_dir(tmp_path / "projects" / "project")

        # Patch decide to fail if called — the rule should bypass it.
        monkeypatch.setattr(
            ilk_triage, "decide",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("decide() must not be called for rule-first ack-and-relaunch")
            ),
        )

        # Patch ilk_data_root to return our tmp_path.
        monkeypatch.setattr("ilk_triage.ilk_data_root", lambda: tmp_path)
        monkeypatch.setattr("triage_apply.write_resume_ack", lambda dd: None)
        monkeypatch.setattr(triage_apply, "ilk_notify", lambda **kw: None)

        result = run_triage(
            project_key="project",
            run_id="20261007-192941",
            home=None,
            timeout_s=5,
        )

        assert result["action"] == "ack-and-relaunch", (
            f"expected ack-and-relaunch by rule, got {result.get('action')}"
        )

    def test_rule_first_appends_finding_with_ids_and_verdict(self, tmp_path, monkeypatch):
        """The rule-first ack-and-relaunch appends a ``#### Triage <ts> (rule)``
        block to the sub-plan's Findings, containing the failing node ids,
        the red-owner verdict line, and ``Fix the code, never the assertion.``
        """
        from ilk_triage import run_triage
        import ilk_triage
        import triage_apply

        run_id = "20261007-192941"
        slug = "a-plain-red-is-relaunched-not-escalated"
        node_ids = ["tests/test_something.py::test_case", "tests/test_other.py::test_foo"]

        # run_triage resolves: data_root / "projects" / project_key
        data_dir = _build_data_dir(
            tmp_path / "projects" / "project", run_id=run_id, slug=slug, red_node_ids=node_ids,
        )

        monkeypatch.setattr(
            ilk_triage, "decide",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("decide() must not be called")
            ),
        )
        monkeypatch.setattr("ilk_triage.ilk_data_root", lambda: tmp_path)
        monkeypatch.setattr("triage_apply.write_resume_ack", lambda dd: None)
        monkeypatch.setattr(triage_apply, "ilk_notify", lambda **kw: None)

        run_triage(project_key="project", run_id=run_id, home=None, timeout_s=5)

        # Check the sub-plan's Findings section.
        plan_path = data_dir / "plans" / f"2026-10-07l-{slug}.md"
        text = plan_path.read_text(encoding="utf-8")

        assert "#### Triage" in text, "Findings must contain a Triage heading"
        assert "(rule)" in text, "Triage heading must indicate rule-based decision"
        for nid in node_ids:
            assert nid in text, f"failing node id {nid!r} missing from Findings"
        assert "Fix the code, never the assertion." in text

    def test_rule_first_includes_red_owner_verdict(self, tmp_path, monkeypatch):
        """When gate-history includes a red-owner verdict line, the rule-format
        Triage block includes it.
        """
        from ilk_triage import run_triage
        import ilk_triage
        import triage_apply

        run_id = "20261007-192941"
        slug = "a-plain-red-is-relaunched-not-escalated"

        # run_triage resolves: data_root / "projects" / project_key
        data_dir = _build_data_dir(
            tmp_path / "projects" / "project", run_id=run_id, slug=slug, include_red_owner=True,
        )

        monkeypatch.setattr(
            ilk_triage, "decide",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("decide() must not be called")
            ),
        )
        monkeypatch.setattr("ilk_triage.ilk_data_root", lambda: tmp_path)
        monkeypatch.setattr("triage_apply.write_resume_ack", lambda dd: None)
        monkeypatch.setattr(triage_apply, "ilk_notify", lambda **kw: None)

        run_triage(project_key="project", run_id=run_id, home=None, timeout_s=5)

        plan_path = data_dir / "plans" / f"2026-10-07l-{slug}.md"
        text = plan_path.read_text(encoding="utf-8")

        assert "owned (base e7f0b51)" in text, (
            "red-owner verdict line must be in Triage block"
        )


# ── AC-2: bound — at most twice ──────────────────────────────────────────────


class TestAC2_BoundAtMostTwice:
    """The rule-first ack-and-relaunch fires at most twice in a row for the
    same (slug, step). A third consecutive red goes to decide().
    """

    def test_third_consecutive_red_goes_to_decide(self, tmp_path, monkeypatch):
        """After two rule-first ack-and-relaunches for the same (slug, step),
        a third local_checks_failed goes to decide() instead of the rule.
        """
        from ilk_triage import run_triage
        import ilk_triage
        import triage_apply

        run_id_1 = "20261007-192941"
        run_id_2 = "20261007-202950"
        run_id_3 = "20261007-212959"
        slug = "a-plain-red-is-relaunched-not-escalated"

        decide_called = {"count": 0}

        def mock_decide(evidence, *, home, timeout_s=600, skip_audit=False):
            decide_called["count"] += 1
            # At base, decide() always returns ack-and-relaunch for a plain red.
            return {
                "action": "ack-and-relaunch",
                "slug": slug,
                "step": 1,
                "finding": "gate red",
                "basis": "exit code 1",
                "falsifier": "gate green",
                "model": "claude-opus-test",
            }

        # run_triage resolves: data_root / "projects" / project_key
        monkeypatch.setattr("ilk_triage.ilk_data_root", lambda: tmp_path)
        monkeypatch.setattr(ilk_triage, "decide", mock_decide)
        monkeypatch.setattr("triage_apply.write_resume_ack", lambda dd: None)
        monkeypatch.setattr(triage_apply, "ilk_notify", lambda **kw: None)

        # Run 1
        _build_data_dir(tmp_path / "projects" / "run1", run_id=run_id_1, slug=slug)
        run_triage(project_key="run1", run_id=run_id_1, home=None, timeout_s=5)

        # Run 2
        _build_data_dir(tmp_path / "projects" / "run2", run_id=run_id_2, slug=slug)
        run_triage(project_key="run2", run_id=run_id_2, home=None, timeout_s=5)

        # Run 3: at base, decide() is called for ALL three runs (count == 3).
        # After the bound is implemented, count should be 1 (only the third).
        _build_data_dir(tmp_path / "projects" / "run3", run_id=run_id_3, slug=slug)
        run_triage(project_key="run3", run_id=run_id_3, home=None, timeout_s=5)

        assert decide_called["count"] == 1, (
            f"decide() should be called once (third red only), "
            f"was called {decide_called['count']} times — "
            f"the bound/rule-first path is not implemented"
        )


# ── AC-3: park-and-escalate notification ─────────────────────────────────────


class TestAC3_ParkAndEscalateNotification:
    """Every park-and-escalate (rule or model or fallback) calls ilk_notify
    with event triage-escalated and detail = project + slug + reason, writes
    reason/finding into runtime/triage/<run_id>.log, and writes an audit row."""

    def test_escalate_notifies_with_triage_escalated_event(self, tmp_path, monkeypatch):
        """park-and-escalate must call ilk_notify with event=triage-escalated
        (not event=blocked).
        """
        import triage_apply

        notify_calls = []
        monkeypatch.setattr(
            triage_apply, "ilk_notify",
            lambda *, event, project, detail=None: notify_calls.append(
                {"event": event, "project": project, "detail": detail}
            ),
        )

        data_dir = _build_data_dir(tmp_path)
        plans_dir = data_dir / "plans"

        decision = {
            "action": "park-and-escalate",
            "slug": "a-plain-red-is-relaunched-not-escalated",
            "step": 1,
            "finding": "test finding",
            "basis": "test basis",
            "falsifier": "test falsifier",
            "reason": "test_reason",
        }

        triage_apply._do_escalate(
            decision, data_dir, plans_dir,
            project="project", run_id="20261007-192941",
            reason="test_reason",
        )

        assert len(notify_calls) == 1, f"expected 1 notify call, got {len(notify_calls)}"
        call = notify_calls[0]
        assert call["event"] == "triage-escalated", (
            f"expected event=triage-escalated, got event={call['event']!r}"
        )
        assert "project" in call["detail"], "detail must contain project name"
        assert "a-plain-red-is-relaunched-not-escalated" in call["detail"], (
            "detail must contain the slug"
        )
        assert "test_reason" in call["detail"], "detail must contain the reason"

    def test_escalate_writes_triage_log_with_reason_and_finding(self, tmp_path, monkeypatch):
        """park-and-escalate writes reason/finding into
        runtime/triage/<run_id>.log.
        """
        import triage_apply
        monkeypatch.setattr(
            triage_apply, "ilk_notify",
            lambda **kw: None,
        )

        data_dir = _build_data_dir(tmp_path)
        plans_dir = data_dir / "plans"

        decision = {
            "action": "park-and-escalate",
            "slug": "a-plain-red-is-relaunched-not-escalated",
            "step": 1,
            "finding": "the gate failed on test_case",
            "basis": "exit code 1 from pytest",
            "falsifier": "gate passes",
            "reason": "local_checks_stuck",
        }

        triage_apply._do_escalate(
            decision, data_dir, plans_dir,
            project="project", run_id="20261007-192941",
            reason="local_checks_stuck",
        )

        log_path = data_dir / "runtime" / "triage" / "20261007-192941.log"
        assert log_path.exists(), f"triage log must be written at {log_path}"
        log_text = log_path.read_text(encoding="utf-8")
        assert "local_checks_stuck" in log_text, "log must contain the reason"
        assert "the gate failed on test_case" in log_text, "log must contain the finding"


# ── AC-4 (control): two-strikes still works ──────────────────────────────────


class TestAC4_Control_TwoStrikes:
    """Control: the two-strikes mechanism still escalates after matching
    signatures. This must not regress."""

    def test_two_strikes_escalates(self, tmp_path, monkeypatch):
        """Same slug + signature triaged twice → second is escalated with
        reason 'two strikes'. This is existing behaviour that must not regress.

        Gate-history must have entries for both run_ids so the two-strikes
        signature (which reads node_ids from gate-history for the current
        run_id) matches across runs.
        """
        import triage_apply

        monkeypatch.setattr("triage_apply.write_resume_ack", lambda dd: None)
        monkeypatch.setattr(
            triage_apply, "ilk_notify",
            lambda **kw: None,
        )

        slug = "a-plain-red-is-relaunched-not-escalated"
        node_ids = ["tests/test_something.py::test_case"]

        # Build data dir with gate-history entries for BOTH run_ids.
        data_dir = tmp_path / "project"
        _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
            "state": "local_checks_failed",
            "run_id": "20261007-192941",
            "pid": 12345,
            "started_at": "2026-10-07T19:29:41+0800",
            "ended_at": "2026-10-07T19:41:10+0800",
            "iterations": 2,
            "project_path": str(data_dir),
            "failed_check": {"slug": slug, "step": 1, "command": "pytest"},
        })

        # Gate-history: red rows for both run_ids with same (slug, step, node_ids).
        gate_rows = [
            {"run_id": "20261007-192941", "slug": slug, "step": 1,
             "outcome": "fail", "attribution": {"node_ids": node_ids}},
            {"run_id": "20261007-202950", "slug": slug, "step": 1,
             "outcome": "fail", "attribution": {"node_ids": node_ids}},
        ]
        gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
        gate_path.write_text(
            "\n".join(json.dumps(r) for r in gate_rows) + "\n",
            encoding="utf-8",
        )

        _write_plan(
            data_dir / "plans",
            f"2026-10-07l-{slug}.md",
            slug,
            status="in-progress",
            current_step=1,
        )

        plans_dir = data_dir / "plans"
        decision = {
            "action": "ack-and-relaunch",
            "slug": slug,
            "step": 1,
            "finding": "first triage",
            "basis": "gate red",
            "falsifier": "gate green",
        }

        # First triage: applies.
        result_1 = triage_apply.apply(decision, data_dir, run_id="20261007-192941")
        assert result_1.get("audit_kind") == "triage-applied"

        # Second triage with same signature: escalates.
        result_2 = triage_apply.apply(decision, data_dir, run_id="20261007-202950")
        assert result_2.get("action") == "park-and-escalate", (
            f"expected escalated on second strike, got {result_2.get('action')}"
        )
        assert result_2.get("reason") == "two strikes", (
            f"expected reason 'two strikes', got {result_2.get('reason')}"
        )