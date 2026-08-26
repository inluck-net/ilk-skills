"""Red-first: the batch gate must poll for as long as the project declared.

Defect 7 from /ilk-ship Phase 0.  gh-resolve's ``.ilk-launch.json`` declares
``ship.suite.timeout: 1800``, but nothing read it: the runner passes no
``--poll-timeout`` and ``batch_gate``'s default is 600.  Its suite measured
**925.79s** on run 20260825-234253, so the poll bound expired 325s before the
suite could finish — the gate could never return a real verdict for that
project, whatever else was fixed.

Resolution order (explicit beats declared beats fallback):

  1. an explicit ``--poll-timeout`` / ``_poll_timeout`` from the caller
  2. the project's declared ``ship.suite.timeout``
  3. the existing 600s fallback

Judgment call, 2026-08-26: the fallback stays 600, not the 300 that
ilk-ship/SKILL.md documents for a *missing* ship block.  Lowering it would
newly truncate suites that pass today, which is a behaviour change nobody
asked for and the opposite of the defect being fixed.  Wrong if a project
with a ship block but no declared timeout should inherit the missing-block
default — that wants ship_config to fill the field in, not the gate to guess.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REAL_WAIT_HELPER = SCRIPTS / "wait_for_background_output.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_project(tmp: Path, command: str, timeout: int | None = None) -> Path:
    project = tmp / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project, check=True, capture_output=True,
    )
    suite: dict = {"command": command}
    if timeout is not None:
        suite["timeout"] = timeout
    (project / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": suite}}), encoding="utf-8")
    return project


def _argv_recording_helper(tmp: Path, dump: Path) -> Path:
    """A stub wait helper that records its argv and reports success."""
    h = tmp / "record_argv.sh"
    h.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        printf '%s\\n' "$@" > {str(dump)!r}
        echo 0
        exit 0
    """), encoding="utf-8")
    h.chmod(0o755)
    return h


def _bound_from(dump: Path) -> int:
    args = dump.read_text(encoding="utf-8").split()
    assert "--timeout" in args, f"helper got no --timeout: {args}"
    return int(args[args.index("--timeout") + 1])


# ── AC-1: the declared timeout reaches the poll ─────────────────────────────

class TestAC1DeclaredTimeoutIsUsed:

    def test_declared_timeout_becomes_the_poll_bound(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        dump = tmp_path / "argv.txt"
        project = _make_project(tmp_path, "true", timeout=1800)

        run_batch_gate(project, tmp_path / "runtime",
                       _wait_helper=_argv_recording_helper(tmp_path, dump))

        assert _bound_from(dump) == 1800, (
            "the project declared ship.suite.timeout: 1800 and the gate "
            f"polled for {_bound_from(dump)}s instead"
        )

    def test_the_gh_resolve_shape(self, tmp_path: Path) -> None:
        """The exact case that motivated this: 1800 declared, 925s suite."""
        from batch_gate import run_batch_gate

        dump = tmp_path / "argv.txt"
        project = _make_project(
            tmp_path, "python3 -m pytest --timeout=60", timeout=1800)

        run_batch_gate(project, tmp_path / "runtime",
                       _wait_helper=_argv_recording_helper(tmp_path, dump))

        assert _bound_from(dump) >= 926, (
            "the poll bound must cover the declared suite timeout; a 925.79s "
            "suite under a 600s bound is why gh-resolve never got a verdict"
        )


# ── AC-2: an explicit bound still wins ──────────────────────────────────────

class TestAC2ExplicitOverridesDeclared:

    def test_explicit_poll_timeout_beats_the_declared_value(
        self, tmp_path: Path,
    ) -> None:
        from batch_gate import run_batch_gate

        dump = tmp_path / "argv.txt"
        project = _make_project(tmp_path, "true", timeout=1800)

        run_batch_gate(project, tmp_path / "runtime",
                       _wait_helper=_argv_recording_helper(tmp_path, dump),
                       _poll_timeout=45)

        assert _bound_from(dump) == 45


# ── AC-3: fallback when nothing is declared ─────────────────────────────────

class TestAC3Fallback:

    def test_no_declared_timeout_falls_back_to_600(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        dump = tmp_path / "argv.txt"
        project = _make_project(tmp_path, "true")  # no timeout key

        run_batch_gate(project, tmp_path / "runtime",
                       _wait_helper=_argv_recording_helper(tmp_path, dump))

        assert _bound_from(dump) == 600


# ── AC-4: the bound is really in force, in both directions ──────────────────

class TestAC4BoundIsRealNotJustPassedAlong:
    """Prove the number is enforced, not merely forwarded — with the real helper."""

    def test_suite_within_the_declared_bound_passes(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "sleep 2", timeout=25)
        rec = run_batch_gate(project, tmp_path / "runtime",
                             _wait_helper=REAL_WAIT_HELPER)

        assert rec is not None and rec.verdict == "pass"

    def test_suite_exceeding_the_declared_bound_fails(self, tmp_path: Path) -> None:
        """A short declared bound must actually cut a long suite off."""
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "sleep 30", timeout=3)
        rec = run_batch_gate(project, tmp_path / "runtime",
                             _wait_helper=REAL_WAIT_HELPER)

        assert rec is not None and rec.verdict == "fail", (
            "a declared bound of 3s did not cut off a 30s suite — the value "
            "is being forwarded but not enforced"
        )


# ── AC-5: the CLI default no longer hard-codes 600 ──────────────────────────

class TestAC5CliDefault:

    def test_run_batch_gate_default_is_the_unset_sentinel(self) -> None:
        """``_poll_timeout`` must default to None, not 600.

        A default of 600 is indistinguishable from an explicit 600 and would
        silently outrank every project's declaration.  Asserted on the
        signature rather than on --help: argparse does not print defaults
        unless asked, so a help-text check passes whatever the default is.
        """
        import inspect

        import batch_gate

        for fn in (batch_gate.run_batch_gate, batch_gate._run_gate_inner):
            default = inspect.signature(fn).parameters["_poll_timeout"].default
            assert default is None, (
                f"{fn.__name__}._poll_timeout defaults to {default!r}; "
                "the declared ship.suite.timeout can never win"
            )

    def test_cli_honours_the_declared_timeout(self, tmp_path: Path) -> None:
        """End-to-end through the CLI, with no --poll-timeout passed."""
        project = _make_project(tmp_path, "sleep 30", timeout=3)
        runtime = tmp_path / "runtime"

        result = subprocess.run(
            ["python3", str(SCRIPTS / "batch_gate.py"),
             "--project", str(project), "--runtime-dir", str(runtime), "--run"],
            capture_output=True, text=True, timeout=120, encoding="utf-8",
        )

        assert "verdict=fail" in result.stdout, (
            "the CLI ignored ship.suite.timeout: 3 and waited on a 30s suite.  "
            f"stdout={result.stdout!r}"
        )
