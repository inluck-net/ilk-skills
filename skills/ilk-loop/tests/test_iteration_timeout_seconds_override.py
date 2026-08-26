"""A seconds-level iteration-timeout override, for tests that wait one out.

Measured 2026-08-26 from the batch gate's ``--durations=25``: the ilk-skills
suite is 299.55s wall-clock, and **three tests are 181.25s of it (61%)**:

    60.53s  test_runner_timeout_dirty_tree.py::test_timeout_preserves_dirty_tree_via_real_cli
    60.51s  test_runner_timeout_dirty_tree.py::test_timeout_clean_tree_no_wip
    60.21s  test_bare_pytest_bounded.py::test_hanging_fixture_killed_by_config_timeout

Everything else — roughly 2130 tests — totals about 61s.  The suite is not
broadly slow; three tests each wait out a real 60-second bound to prove the
bound fires.

The first two are pinned at 60s by the runner's own arithmetic:
``run_ilk_loop_claude.sh`` computes ``timeout_sec=$((ITERATION_TIMEOUT_MIN *
60))``, and ``--iteration-timeout-min`` takes whole minutes, so 1 minute is
the floor.  They are honest integration tests of a real mechanism; they are
expensive only because the number they wait on cannot be made smaller.

``ILK_ITERATION_TIMEOUT_SEC`` gives them a smaller number without weakening
what they assert: the runner still spawns a real iteration, still bounds it
with the same ``gtimeout`` path, and still takes the same timeout branch.
Only the wall-clock changes.

**This is a test affordance, not a new operator control.**  It is
deliberately env-only rather than a CLI flag: a sub-minute production
iteration timeout is never wanted, and a documented flag invites one.

``run_ilk_loop_claude.*`` is on the §7h contract-governed list, so the
change cites the contracts: ``references/detached-component-contracts.md``
(this is a new *reader* of runtime configuration, not a new writer of
runtime state — no sentinel, PID file, or record schema is touched) and
``references/orchestration-collaboration.md`` (the L1-L4 invariants are
unaffected: the timeout branch, its exit code, and the WIP-preservation
path all behave identically at any bound).
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
RUNNER = SCRIPTS / "run_ilk_loop_claude.sh"


# ── the override exists and takes precedence ────────────────────────────────

class TestOverrideResolution:
    """Drive the runner's arithmetic directly by dot-sourcing it."""

    def _resolve(self, env_extra: dict[str, str], minutes: str = "30") -> str:
        env = {"ILK_DOTSOURCE_ONLY": "1", "PATH": os.environ.get("PATH", "")}
        env.update(env_extra)
        script = (
            f"export ILK_DOTSOURCE_ONLY=1; source '{RUNNER}' 2>/dev/null; "
            f"ITERATION_TIMEOUT_MIN={minutes}; "
            "echo \"RESOLVED=$(resolve_iteration_timeout_sec)\""
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True, timeout=30, env=env, encoding="utf-8")
        m = re.search(r"RESOLVED=(\d+)", out.stdout)
        assert m, f"resolver produced nothing: {out.stdout!r} {out.stderr!r}"
        return m.group(1)

    def test_without_override_minutes_times_sixty(self) -> None:
        assert self._resolve({}, minutes="30") == "1800"

    def test_override_wins(self) -> None:
        assert self._resolve({"ILK_ITERATION_TIMEOUT_SEC": "5"}, minutes="30") == "5"

    def test_override_allows_sub_minute(self) -> None:
        """The whole point: 1 minute is the floor via the CLI flag."""
        assert int(self._resolve({"ILK_ITERATION_TIMEOUT_SEC": "3"})) < 60

    @pytest.mark.parametrize("bad", ["", "0", "-5", "abc", "3.5", " "])
    def test_a_bad_override_falls_back_to_minutes(self, bad: str) -> None:
        """Never let a typo produce a zero or negative bound.

        A 0 would make every iteration time out instantly; a non-numeric
        value would make the arithmetic error out mid-run.  Both are worse
        than ignoring the override.
        """
        assert self._resolve({"ILK_ITERATION_TIMEOUT_SEC": bad}, minutes="2") == "120"


# ── the override actually bounds a real iteration ───────────────────────────

class TestOverrideBoundsARealRun:
    """Behaviour, not just arithmetic — the number must reach gtimeout."""

    @pytest.mark.timeout(120)
    def test_a_hanging_iteration_is_cut_off_in_seconds(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        (proj / "docs" / "plans").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@e.com", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=proj, check=True, capture_output=True)

        mock_bin = tmp_path / "bin"
        mock_bin.mkdir()
        mock_claude = mock_bin / "claude"
        mock_claude.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            sleep 300
        """), encoding="utf-8")
        mock_claude.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{mock_bin}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DOTSOURCE_ONLY": "",
            "ILK_ITERATION_TIMEOUT_SEC": "5",
        }

        import time
        started = time.monotonic()
        subprocess.run(
            ["bash", str(RUNNER),
             "--project-path", str(proj),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            capture_output=True, text=True, timeout=110, env=env,
            cwd=str(proj), encoding="utf-8",
        )
        elapsed = time.monotonic() - started

        assert elapsed < 45, (
            f"iteration ran {elapsed:.1f}s with ILK_ITERATION_TIMEOUT_SEC=5; "
            "the override did not reach the gtimeout bound (60s floor still "
            "in force)"
        )


# ── the production default is untouched ─────────────────────────────────────

def test_cli_flag_is_still_minutes_and_undocumented_env() -> None:
    """The override must not become an operator-facing knob.

    A sub-minute production iteration timeout is never wanted; documenting
    a flag for it invites one.  --help must keep advertising minutes only.
    """
    out = subprocess.run(["bash", str(RUNNER), "--help"],
                         capture_output=True, text=True, timeout=30,
                         encoding="utf-8")
    combined = out.stdout + out.stderr
    assert "--iteration-timeout-min" in combined
    assert "ILK_ITERATION_TIMEOUT_SEC" not in combined, (
        "the seconds override is a test affordance; keep it out of --help"
    )
