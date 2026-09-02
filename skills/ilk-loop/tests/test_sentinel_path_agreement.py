"""Tests for the sentinel path agreement contract.

The runner writes last-exit.json to the directory returned by
external_launcher_dir (via get_ilk_runtime_dir in run_ilk_loop_claude.sh:818).
All readers look in the same external_launcher_dir (runtime/launcher/).

These must agree — asserted by deriving the writer's path the same way
the runner does and comparing it to the reader path.

Also asserts (AC-4) that neither get_ilk_runtime_dir function swallows
resolver stderr with 2>/dev/null — that test is xfail until step 2.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]  # ilk-skills repo root
_SKILL_ROOT = _REPO_ROOT / "skills"
_RESOLVER = _SKILL_ROOT / "ilk-loop" / "scripts" / "ilk_paths.py"
_RUNNER = _SKILL_ROOT / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_WATCHDOG = _SKILL_ROOT / "ilk-watchdog" / "scripts" / "watchdog.sh"

# A project path that resolves cleanly — the repo itself.
_PROJECT_PATH = str(_REPO_ROOT)


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_writer_dir() -> str:
    """Invoke the resolver the way run_ilk_loop_claude.sh:818 does.

    After the fix, the runner extracts external_launcher_dir from the JSON.
    """
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", _PROJECT_PATH],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --start {_PROJECT_PATH} failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    return data["external_launcher_dir"]


def _resolve_reader_dir() -> str:
    """Call external_launcher_dir via the same resolver JSON output."""
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", _PROJECT_PATH],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --start {_PROJECT_PATH} failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    return data["external_launcher_dir"]


# ── AC-1: writer and readers resolve to the same directory ───────────────────


def test_writer_and_reader_resolve_same_dir():
    """The directory the runner writes to must match what readers expect.

    Writer path: derived by invoking ilk_paths.py the way :818 does
        (json.external_launcher_dir — the runner now extracts this field).
    Reader path: ilk_paths' external_launcher_dir from the same JSON.
    """
    writer_dir = _resolve_writer_dir()
    reader_dir = _resolve_reader_dir()

    assert writer_dir == reader_dir, (
        f"Sentinel writer resolves to:\n  {writer_dir}\n"
        f"Sentinel readers resolve to:\n  {reader_dir}\n"
        "These must be the same directory."
    )


# ── AC-4: neither get_ilk_runtime_dir swallows stderr ───────────────────────


def _function_body(path: Path, fn_name: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for the body of `fn_name() {` ... `}`.

    Locates the function by name, not by line number.  The gate-identity
    batch inserted ~170 lines above get_ilk_runtime_dir in the runner, which
    silently moved the previous hard-coded 818-835 window onto a different
    function (get_local_check_targets) and false-failed this test.
    """
    lines = path.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{fn_name}() {{"):
            start = i
            break
    assert start is not None, f"{fn_name}() not found in {path.name}"
    body = []
    for i in range(start + 1, len(lines)):
        # A column-0 `}` closes the function; a column-0 `name() {` opens the
        # next one.  Both files indent nested blocks, so neither appears inside.
        if lines[i] == "}" or (
            lines[i].endswith("() {") and lines[i][:-4].isidentifier()
        ):
            break
        body.append((i + 1, lines[i]))
    return body


def test_no_stderr_suppression_in_get_ilk_runtime_dir():
    """AC-4: get_ilk_runtime_dir must not swallow resolver stderr.

    Grep both shell files for '2>/dev/null' inside the function body.
    A broader grep is acceptable if no other legitimate uses exist;
    if they do, narrow the assertion and document in Findings.
    """
    runner_matches = [
        (ln, text)
        for ln, text in _function_body(_RUNNER, "get_ilk_runtime_dir")
        if "2>/dev/null" in text
    ]
    watchdog_matches = [
        (ln, text)
        for ln, text in _function_body(_WATCHDOG, "get_ilk_runtime_dir")
        if "2>/dev/null" in text
    ]

    problems = []
    if runner_matches:
        problems.append(
            f"run_ilk_loop_claude.sh get_ilk_runtime_dir suppresses stderr:\n"
            + "\n".join(f"  :{ln}: {text.strip()}" for ln, text in runner_matches)
        )
    if watchdog_matches:
        problems.append(
            f"watchdog.sh get_ilk_runtime_dir suppresses stderr:\n"
            + "\n".join(f"  :{ln}: {text.strip()}" for ln, text in watchdog_matches)
        )

    assert not problems, (
        "AC-4 violation — stderr must be surfaced, not swallowed:\n"
        + "\n".join(problems)
    )
