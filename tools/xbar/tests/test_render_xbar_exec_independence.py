"""The panel's actions must not depend on a target's execute bit.

Field report, 2026-08-26: clicking "Start now" in the SwiftBar panel did
nothing at all.  Root cause — the row rendered as

    --Start now | bash='<repo>/skills/ilk-runner/scripts/ilk-run.sh' ...

and SwiftBar's ``bash=<path>`` **execs the target directly**.  The script was
committed 100644, so the exec failed with 126 ("found but not executable").
Because the row also carries ``terminal=false``, that failure had nowhere to
surface: no window, no error, the menu just refreshed.  A click that silently
does nothing is indistinguishable from one that worked.

The same script invoked as ``bash <script>`` works fine — which is why
``/ilk-run`` succeeded from the agent while the panel did not, and why the
discrepancy went unexplained.

Two properties are pinned here, and the second matters more than the first:

1. The action invokes an interpreter with the script as an argument, so the
   target's mode cannot break it.  This is the pattern the launchd plist
   already uses for ``scheduler.sh`` (``/bin/bash <script> --poll-min ...``).
2. Every script a rendered action points at is **reachable** — the row is not
   emitted for a target that does not exist.  A menu item that cannot work
   should not look identical to one that can.

`chmod +x` alone would have fixed the symptom and left the class intact: the
next contributor to add an action row, or the next `git checkout` on a
filesystem that drops modes, reintroduces it silently.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_xbar import (  # noqa: E402
    _DEFAULT_RESUME_SCRIPT,
    _DEFAULT_RUN_SCRIPT,
    render_xbar,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _entry(**over) -> dict:
    e = {
        "project_key": "proj",
        "alive": False,
        "state": "none",
        "step": "",
        "next_subplan": "",
        "runnable": False,
        "manually_runnable": False,
        "parked": False,
        "path": "/data/proj",
        "repo_path": "/src/proj",
    }
    e.update(over)
    return e


def _action_lines(text: str, label: str) -> list[str]:
    """Rows that are actually *clickable* — i.e. carry a bash= directive.

    The notice row ("--Start now unavailable …") also begins with the label,
    so a startswith-only match would count it as an action and make the
    suppression test vacuous.
    """
    return [ln for ln in text.splitlines()
            if ln.startswith(f"--{label}") and "bash=" in ln]


def _bash_target(line: str) -> str:
    """The path SwiftBar would actually exec for this row."""
    m = re.search(r"bash=(\S+)", line)
    assert m, f"row has no bash= directive: {line}"
    return shlex.split(m.group(1))[0]


# ── 1. the action must not exec the script directly ─────────────────────────

class TestExecBitIndependence:

    def test_start_now_invokes_an_interpreter_not_the_script(self) -> None:
        text = render_xbar([_entry(manually_runnable=True)])
        line = _action_lines(text, "Start now")[0]
        target = _bash_target(line)

        assert not target.endswith(".sh"), (
            "bash= points straight at the .sh, so SwiftBar execs it and a "
            f"non-executable target fails with 126, silently.  Got: {target}"
        )
        assert os.path.basename(target) in ("bash", "sh"), (
            f"expected an interpreter as the bash= target, got {target}"
        )

    def test_the_script_is_passed_as_an_argument(self) -> None:
        text = render_xbar([_entry(manually_runnable=True)])
        line = _action_lines(text, "Start now")[0]
        assert "ilk-run.sh" in line, "the run script must still be invoked"

    def test_project_path_still_reaches_the_script(self) -> None:
        """Regression guard: the repo path must survive the reshuffle."""
        text = render_xbar([_entry(manually_runnable=True, repo_path="/src/mine")])
        line = _action_lines(text, "Start now")[0]
        assert "/src/mine" in line

    def test_a_non_executable_target_still_runs(self, tmp_path: Path) -> None:
        """End-to-end: build the row for a 0644 script and run what it says.

        This is the positive experiment the original bug needed — it fails
        against the old rendering and passes against the new one.
        """
        script = tmp_path / "fake-run.sh"
        script.write_text("#!/usr/bin/env bash\necho RAN \"$1\"\n", encoding="utf-8")
        script.chmod(0o644)          # exactly the committed mode that broke it
        assert not os.access(script, os.X_OK)

        text = render_xbar([_entry(manually_runnable=True, repo_path="/src/mine")],
                           run_script=str(script))
        line = _action_lines(text, "Start now")[0]

        # Reconstruct SwiftBar's invocation: bash= plus param1..paramN.
        target = _bash_target(line)
        params = [shlex.split(m)[0] for m in
                  re.findall(r"param\d+=(\S+)", line)]
        proc = subprocess.run([target, *params], capture_output=True,
                              text=True, timeout=30, encoding="utf-8")

        assert proc.returncode == 0, (
            f"the rendered action failed: rc={proc.returncode} "
            f"stderr={proc.stderr!r}"
        )
        assert "RAN /src/mine" in proc.stdout


# ── 2. an unreachable target must not render as a working row ───────────────

class TestUnreachableTargetIsNotSilent:

    def test_missing_run_script_suppresses_the_row(self, tmp_path: Path) -> None:
        text = render_xbar([_entry(manually_runnable=True)],
                           run_script=str(tmp_path / "does-not-exist.sh"))
        assert _action_lines(text, "Start now") == [], (
            "a row was rendered for a script that does not exist — clicking "
            "it can only fail silently"
        )

    def test_missing_run_script_says_so(self, tmp_path: Path) -> None:
        text = render_xbar([_entry(manually_runnable=True)],
                           run_script=str(tmp_path / "does-not-exist.sh"))
        assert "unavailable" in text.lower(), (
            "suppressing the row without saying why makes a broken install "
            "look like a project with no work to do"
        )

    def test_present_script_still_renders(self) -> None:
        text = render_xbar([_entry(manually_runnable=True)])
        assert len(_action_lines(text, "Start now")) == 1


# ── 3. the shipped defaults are reachable ───────────────────────────────────

class TestShippedDefaultsExist:
    """Catches the packaging half: a default pointing at a moved file."""

    @pytest.mark.parametrize("script", [_DEFAULT_RUN_SCRIPT, _DEFAULT_RESUME_SCRIPT])
    def test_default_script_exists(self, script: str) -> None:
        assert Path(script).is_file(), f"default action target missing: {script}"


# ── 4. entry-point scripts keep their exec bit ──────────────────────────────

class TestEntryPointsExecutable:
    """Belt to the interpreter's braces — and a tripwire if a mode regresses.

    Sourced helpers (``_``-prefixed) are deliberately excluded: they are read
    by ``source``, never exec'd, and 0644 is correct for them.
    """

    ENTRY_POINTS = [
        "skills/ilk-runner/scripts/ilk-run.sh",
        "skills/ilk-runner/scripts/ilk-schedule.sh",
        "skills/ilk-runner/scripts/ilk-status.sh",
        "skills/ilk-upgrade/scripts/upgrade.sh",
        "skills/ilk-watchdog/scripts/scheduler.sh",
    ]

    @pytest.mark.parametrize("rel", ENTRY_POINTS)
    def test_entry_point_is_executable(self, rel: str) -> None:
        p = REPO_ROOT / rel
        assert p.is_file(), f"missing: {p}"
        mode = p.stat().st_mode
        assert mode & stat.S_IXUSR, (
            f"{rel} is not executable; a bash=<path> caller gets exit 126"
        )
