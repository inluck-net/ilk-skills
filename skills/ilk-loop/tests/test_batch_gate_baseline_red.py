"""The batch gate must distinguish declared known-red from a real regression.

/ilk-ship Phase 0, 2026-08-26.  `batch_gate.py` had **0** references to
`baseline_red`; the verdict was a raw ``exit_code == 0``.  `baseline_red` was
read only by ship_config.py and baseline_diff.py — the Phase 1 side.  So:

    inherited failures -> gate verdict `fail`
                       -> ship_audit final_gate `fail`
                       -> Phase 0 unproven
                       -> no release, ever

ilk-skills declares 3 `baseline_red` entries covering 28 of its 39 failing
node ids, and they changed nothing, because the gate never looked.  The
repo could not release itself no matter what a batch did — not a judgement
call, a structural gap.

What this must NOT become is a rubber stamp.  The whole point of the
distinction is that an **undeclared** failure still fails the gate.  A
declaration is a claim someone wrote down with a reason and a date; a
regression is not excused by being adjacent to one.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _project(tmp: Path, baseline_red: list[dict], suite_cmd: str) -> Path:
    proj = tmp / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True)
    (proj / ".ilk-launch.json").write_text(json.dumps({
        "ship": {"suite": {"command": suite_cmd, "timeout": 60},
                 "baseline_red": baseline_red}
    }), encoding="utf-8")
    return proj


def _fake_suite(tmp: Path, failures: list[str], exit_code: int = 1) -> str:
    """A command that prints pytest-shaped FAILED lines and exits non-zero."""
    s = tmp / "fake_suite.sh"
    lines = "\n".join(f'echo "FAILED {f} - AssertionError"' for f in failures)
    s.write_text(f"#!/bin/bash\n{lines}\nexit {exit_code}\n", encoding="utf-8")
    s.chmod(0o755)
    return str(s)


def _run(proj: Path, runtime: Path):
    from batch_gate import run_batch_gate
    helper = SCRIPTS / "wait_for_background_output.sh"
    return run_batch_gate(proj, runtime, _wait_helper=helper, _poll_timeout=30)


# ── every failure declared → pass ───────────────────────────────────────────

class TestAllFailuresDeclared:

    def test_fully_declared_failures_are_a_pass(self, tmp_path: Path) -> None:
        cmd = _fake_suite(tmp_path, ["tests/test_known.py::test_a",
                                     "tests/test_known.py::test_b"])
        proj = _project(tmp_path, [{"node_id": "tests/test_known.py",
                                    "reason": "inherited", "as_of": "2026-08-26"}], cmd)
        rec = _run(proj, tmp_path / "rt")
        assert rec is not None
        assert rec.verdict == "pass", (
            "every failing node id is declared baseline_red, so the gate must "
            f"not report fail.  Got {rec.verdict!r}"
        )

    def test_exact_node_id_declaration_matches(self, tmp_path: Path) -> None:
        node = "tests/test_x.py::TestC::test_one[param]"
        cmd = _fake_suite(tmp_path, [node])
        proj = _project(tmp_path, [{"node_id": node, "reason": "r",
                                    "as_of": "2026-08-26"}], cmd)
        rec = _run(proj, tmp_path / "rt")
        assert rec is not None and rec.verdict == "pass"


# ── the part that must not become a rubber stamp ────────────────────────────

class TestUndeclaredFailureStillFails:

    def test_one_undeclared_failure_fails_the_gate(self, tmp_path: Path) -> None:
        cmd = _fake_suite(tmp_path, ["tests/test_known.py::test_a",
                                     "tests/test_new.py::test_regression"])
        proj = _project(tmp_path, [{"node_id": "tests/test_known.py",
                                    "reason": "inherited", "as_of": "2026-08-26"}], cmd)
        rec = _run(proj, tmp_path / "rt")
        assert rec is not None
        assert rec.verdict == "fail", (
            "an undeclared failure must fail the gate even when it sits "
            "beside declared ones — otherwise the declaration excuses "
            "regressions by adjacency"
        )

    def test_no_declarations_at_all_still_fails(self, tmp_path: Path) -> None:
        cmd = _fake_suite(tmp_path, ["tests/test_a.py::test_x"])
        proj = _project(tmp_path, [], cmd)
        rec = _run(proj, tmp_path / "rt")
        assert rec is not None and rec.verdict == "fail"

    def test_a_declaration_does_not_match_a_different_file(self, tmp_path: Path) -> None:
        """Prefix matching must not be so loose it swallows a sibling."""
        cmd = _fake_suite(tmp_path, ["tests/test_known_other.py::test_a"])
        proj = _project(tmp_path, [{"node_id": "tests/test_known.py",
                                    "reason": "r", "as_of": "2026-08-26"}], cmd)
        rec = _run(proj, tmp_path / "rt")
        assert rec is not None
        assert rec.verdict == "fail", (
            "'tests/test_known.py' must not excuse 'tests/test_known_other.py'"
        )


# ── a genuinely green suite is unaffected ───────────────────────────────────

def test_green_suite_is_still_a_pass(tmp_path: Path) -> None:
    proj = _project(tmp_path, [], "true")
    rec = _run(proj, tmp_path / "rt")
    assert rec is not None and rec.verdict == "pass"


# ── the excusing must be visible, never silent ──────────────────────────────

class TestExcusingIsAnnounced:

    def test_cli_reports_how_many_were_excused(self, tmp_path: Path) -> None:
        """A pass that hides 28 failures must say so, or it reads as green."""
        import sys
        cmd = _fake_suite(tmp_path, ["tests/test_known.py::test_a",
                                     "tests/test_known.py::test_b"])
        proj = _project(tmp_path, [{"node_id": "tests/test_known.py",
                                    "reason": "inherited", "as_of": "2026-08-26"}], cmd)
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "batch_gate.py"),
             "--project", str(proj), "--runtime-dir", str(tmp_path / "rt2"),
             "--run", "--poll-timeout", "30"],
            capture_output=True, text=True, timeout=120, encoding="utf-8",
        )
        assert "verdict=pass" in out.stdout
        assert "excused" in out.stdout.lower(), (
            f"the pass must name how many failures it excused.  Got: {out.stdout!r}"
        )

    def test_cli_names_the_undeclared_ones_on_fail(self, tmp_path: Path) -> None:
        import sys
        cmd = _fake_suite(tmp_path, ["tests/test_new.py::test_regression"])
        proj = _project(tmp_path, [{"node_id": "tests/test_known.py",
                                    "reason": "r", "as_of": "2026-08-26"}], cmd)
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "batch_gate.py"),
             "--project", str(proj), "--runtime-dir", str(tmp_path / "rt3"),
             "--run", "--poll-timeout", "30"],
            capture_output=True, text=True, timeout=120, encoding="utf-8",
        )
        combined = out.stdout + out.stderr
        assert "test_new.py::test_regression" in combined, (
            "a failing gate must name the undeclared failures so the operator "
            f"knows what to fix.  Got: {combined!r}"
        )
