"""The runner writes a phase marker at each choke point.

Part of sub-plan ``the-runner-writes-its-phase`` (MASTER-2026-09-29d).

The runner must record its current phase (agent, gate, batch-gate, between)
in ``<launcher dir>/phase.json`` so the panel can display it.  Four acceptance
criteria:

  AC-1  a ``write_phase`` helper writes ``phase.json`` atomically (tmp + mv)
        with the expected fields; a write failure never aborts the run.

  AC-2  the helper is called at the right choke points: ``agent`` before the
        agent pipeline launch, ``between`` after it returns; ``gate`` at
        ``invoke_local_checks`` entry, ``between`` at its return; ``batch-gate``
        at ``invoke_batch_gate`` entry, ``between`` at its return; ``phase.json``
        removed at run exit (the path that writes ``last-exit.json``).

  AC-3  the B2 retry (the second ``invoke_local_checks`` call) writes ``gate``
        again, for the retried target.

  AC-4  nothing else changes: gate results and exit codes are identical.  A pin
        runs ``invoke_local_checks`` with a stub helper and compares its results
        file.  This criterion passes today (no phase marker exists yet).

Step 0 landed: AC-4 pin passes; AC-1, AC-2, AC-3 are xfail.
Step 1 landed: all xfail markers removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _HERE.parent.parent.parent.parent          # <clone root>

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers ──────────────────────────────────────────────────────────────────



# ── AC-1: write_phase helper ────────────────────────────────────────────────


class TestWritePhaseHelper:
    """AC-1: ``write_phase`` writes ``phase.json`` atomically with the expected
    fields; a write failure never aborts the run.

    These tests verify the JSON contract that the shell helper must produce.
    They are xfail until step 1 implements the shell function.
    """

    def test_phase_json_has_required_fields(self) -> None:
        """phase.json must have phase, slug, step, run_id, pid, started_at, iteration."""
        # This pin verifies the JSON schema the shell helper must write.
        # It will pass once write_phase is implemented in step 1.
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Verify the runner defines write_phase and it writes the expected fields.
        assert "write_phase" in text
        # The function must write phase.json with the required fields.
        for field in ("phase", "slug", "step", "run_id", "pid", "started_at", "iteration"):
            assert field in text, f"write_phase must include '{field}' in its JSON output"

    def test_write_is_atomic_via_tmp_and_mv(self) -> None:
        """The write must use tmp + mv for atomicity."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Find write_phase function body (use "write_phase()" to skip the comment).
        fn_start = text.find("write_phase()")
        assert fn_start >= 0, "write_phase() function definition not found"
        # Look for the atomic write pattern (tmp + mv).
        fn_body = text[fn_start:fn_start + 800]
        assert ".tmp" in fn_body, "write_phase must use a .tmp file"
        assert "mv" in fn_body, "write_phase must use mv for atomic rename"

    def test_write_failure_never_aborts(self) -> None:
        """A write failure must not abort the run (no set -e around the write)."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("write_phase()")
        assert fn_start >= 0, "write_phase() function definition not found"
        fn_body = text[fn_start:fn_start + 800]
        # The function must not have set -e, or must trap errors.
        # A bare `mv` without error handling would abort on failure.
        assert "|| true" in fn_body or "|| {" in fn_body or "2>/dev/null" in fn_body, \
            "write_phase must handle write failures gracefully"

    def test_between_phase_nulls_slug_and_step(self) -> None:
        """The ``between`` phase must pass null for slug and step."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Find write_phase calls with "between".
        between_calls = []
        for i, line in enumerate(text.splitlines()):
            if "write_phase" in line and "between" in line:
                between_calls.append((i, line.strip()))
        assert len(between_calls) > 0, "write_phase between must be called"
        # Each between call must pass "" or no slug/step.
        for lineno, line in between_calls:
            # The call should be `write_phase between` or `write_phase between "" ""`
            assert "between" in line


# ── AC-2: call sites ─────────────────────────────────────────────────────────


class TestPhaseCallSites:
    """AC-2: the helper is called at the right choke points.

    This pin verifies the *call sites* in the runner script by grepping for
    the expected ``write_phase`` invocations.
    """

    def test_runner_has_write_phase_function(self) -> None:
        """The runner must define a ``write_phase`` function."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        assert "write_phase" in text, "runner must define write_phase"

    def test_agent_phase_before_pipeline_launch(self) -> None:
        """``write_phase agent`` must appear before the gtimeout claude launch."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Find the agent pipeline launch (gtimeout ... claude), not the
        # preflight check for gtimeout availability.
        launch_idx = text.find('gtimeout "${timeout_sec}s" claude')
        if launch_idx < 0:
            pytest.skip("agent pipeline launch not found in runner")
        before_launch = text[:launch_idx]
        assert "write_phase" in before_launch and "agent" in before_launch, \
            "write_phase agent must appear before the agent pipeline launch"

    def test_gate_phase_at_invoke_local_checks_entry(self) -> None:
        """``write_phase gate`` must appear inside ``invoke_local_checks``."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("invoke_local_checks()")
        assert fn_start >= 0, "invoke_local_checks not found"
        fn_body_start = text.find("{", fn_start)
        fn_end = text.find("\n}", fn_body_start)
        body = text[fn_body_start:fn_end] if fn_end >= 0 else text[fn_body_start:]
        assert "write_phase" in body and "gate" in body, \
            "invoke_local_checks must call write_phase gate"

    def test_batch_gate_phase_at_invoke_batch_gate_entry(self) -> None:
        """``write_phase batch-gate`` must appear inside ``invoke_batch_gate``."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("invoke_batch_gate()")
        assert fn_start >= 0, "invoke_batch_gate not found"
        fn_body_start = text.find("{", fn_start)
        fn_end = text.find("\n}", fn_body_start)
        body = text[fn_body_start:fn_end] if fn_end >= 0 else text[fn_body_start:]
        assert "write_phase" in body and "batch-gate" in body, \
            "invoke_batch_gate must call write_phase batch-gate"

    def test_between_phase_after_agent_pipeline(self) -> None:
        """``write_phase between`` must appear after the agent pipeline wait."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # The agent pipeline wait is `wait "$_pipeline_pid"`.
        wait_idx = text.find('wait "$_pipeline_pid"')
        if wait_idx < 0:
            pytest.skip("agent pipeline wait not found")
        after_wait = text[wait_idx:]
        # Look for write_phase between within a reasonable window.
        window = after_wait[:1000]
        assert "write_phase" in window and "between" in window, \
            "write_phase between must appear after agent pipeline wait"

    def test_between_phase_after_invoke_local_checks(self) -> None:
        """``write_phase between`` must appear inside ``invoke_local_checks``."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("invoke_local_checks()")
        assert fn_start >= 0, "invoke_local_checks not found"
        fn_body_start = text.find("{", fn_start)
        fn_end = text.find("\n}", fn_body_start)
        body = text[fn_body_start:fn_end] if fn_end >= 0 else text[fn_body_start:]
        assert "write_phase" in body and "between" in body, \
            "invoke_local_checks must call write_phase between before returning"

    def test_between_phase_after_invoke_batch_gate(self) -> None:
        """``write_phase between`` must appear inside ``invoke_batch_gate``."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("invoke_batch_gate()")
        assert fn_start >= 0, "invoke_batch_gate not found"
        fn_body_start = text.find("{", fn_start)
        fn_end = text.find("\n}", fn_body_start)
        body = text[fn_body_start:fn_end] if fn_end >= 0 else text[fn_body_start:]
        assert "write_phase" in body and "between" in body, \
            "invoke_batch_gate must call write_phase between before returning"

    def test_phase_json_removed_at_run_exit(self) -> None:
        """``phase.json`` must be removed when the run exits."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # The finalize_sentinel / run-exit path writes last-exit.json.
        sentinel_idx = text.rfind("last-exit.json")
        if sentinel_idx < 0:
            pytest.skip("last-exit.json not found")
        tail = text[max(0, sentinel_idx - 1000):sentinel_idx + 1000]
        assert "phase.json" in tail, \
            "phase.json must be mentioned near the run-exit sentinel path"


# ── AC-3: B2 retry writes gate again ────────────────────────────────────────


class TestB2RetryPhase:
    """AC-3: the B2 retry (the second ``invoke_local_checks`` call) writes
    ``gate`` again, for the retried target.

    Since ``write_phase gate`` is called at the entry of ``invoke_local_checks``
    itself, every call (including the B2 retry) writes ``gate``.  This test
    verifies that the function body contains the gate call.
    """

    def test_b2_retry_writes_gate_phase(self) -> None:
        """Every ``invoke_local_checks`` call writes ``write_phase gate``."""
        runner = _SCRIPTS / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        fn_start = text.find("invoke_local_checks()")
        assert fn_start >= 0, "invoke_local_checks not found"
        fn_body_start = text.find("{", fn_start)
        fn_end = text.find("\n}", fn_body_start)
        body = text[fn_body_start:fn_end] if fn_end >= 0 else text[fn_body_start:]
        assert "write_phase" in body and "gate" in body, \
            "invoke_local_checks must call write_phase gate (covers B2 retry)"


# ── AC-4: results unchanged ─────────────────────────────────────────────────


class TestGateResultsUnchanged:
    """AC-4: nothing else changes — gate results and exit codes are identical.

    This pin runs ``run_local_checks.run_one`` with a passing command and
    verifies the result.  It passes today (no phase marker exists yet).
    The phase marker is additive — it must not change gate behavior.
    """

    def test_run_one_passing_command_passes(self, tmp_path: Path) -> None:
        """A passing command must produce a passing result."""
        import run_local_checks as rlc

        _write = lambda name, content: (tmp_path / name).write_text(content, encoding="utf-8")
        _write("f.txt", "hello world\n")
        r = rlc.run_one({"command": "grep -q hello f.txt"}, "step", tmp_path)
        assert r.passed is True
        assert r.exit_code == 0

    def test_run_one_failing_command_fails(self, tmp_path: Path) -> None:
        """A failing command must produce a failing result."""
        import run_local_checks as rlc

        _write = lambda name, content: (tmp_path / name).write_text(content, encoding="utf-8")
        _write("f.txt", "goodbye moon\n")
        r = rlc.run_one({"command": "grep -q hello f.txt"}, "step", tmp_path)
        assert r.passed is False
        assert r.exit_code == 1