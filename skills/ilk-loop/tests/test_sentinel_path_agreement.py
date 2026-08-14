"""Tests for the sentinel path agreement contract.

The runner writes last-exit.json to the directory returned by
external_runtime_dir (via get_ilk_runtime_dir in run_ilk_loop_claude.sh:818).
Eight readers look in external_launcher_dir (runtime/launcher/).

These two must agree.  Until they do, the path-agreement test is xfail(strict).

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


def _resolve_external_runtime_dir() -> str:
    """Invoke the resolver the way run_ilk_loop_claude.sh:818 does."""
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", _PROJECT_PATH],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --start {_PROJECT_PATH} failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    return data["external_runtime_dir"]


def _resolve_external_launcher_dir() -> str:
    """Call external_launcher_dir via the same resolver JSON output."""
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", _PROJECT_PATH],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --start {_PROJECT_PATH} failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    return data["external_launcher_dir"]


# ── AC-1: writer and readers resolve to the same directory ───────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known disagreement: writer uses external_runtime_dir, "
        "readers use external_launcher_dir.  Flipped in step 1."
    ),
)
def test_writer_and_reader_resolve_same_dir():
    """The directory the runner writes to must match what readers expect.

    Writer path: derived by invoking ilk_paths.py the way :818 does
        (json.external_runtime_dir).
    Reader path: ilk_paths' external_launcher_dir from the same JSON.
    """
    writer_dir = _resolve_external_runtime_dir()
    reader_dir = _resolve_external_launcher_dir()

    assert writer_dir == reader_dir, (
        f"Sentinel writer resolves to:\n  {writer_dir}\n"
        f"Sentinel readers resolve to:\n  {reader_dir}\n"
        "These must be the same directory."
    )


# ── AC-4: neither get_ilk_runtime_dir swallows stderr ───────────────────────


def _grep_file_for_pattern(path: Path, pattern: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every match of *pattern* in *path*."""
    matches = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if pattern in line:
            matches.append((i, line))
    return matches


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Both get_ilk_runtime_dir functions suppress resolver stderr with "
        "2>/dev/null.  Fixed in step 2."
    ),
)
def test_no_stderr_suppression_in_get_ilk_runtime_dir():
    """AC-4: get_ilk_runtime_dir must not swallow resolver stderr.

    Grep both shell files for '2>/dev/null' inside the function body.
    A broader grep is acceptable if no other legitimate uses exist;
    if they do, narrow the assertion and document in Findings.
    """
    runner_matches = _grep_file_for_pattern(_RUNNER, "2>/dev/null")
    watchdog_matches = _grep_file_for_pattern(_WATCHDOG, "2>/dev/null")

    # Filter to lines inside get_ilk_runtime_dir (approximate: lines 818-826
    # for runner, 169-183 for watchdog).  If the grep is clean, this is empty.
    runner_in_fn = [
        (ln, text) for ln, text in runner_matches if 818 <= ln <= 830
    ]
    watchdog_in_fn = [
        (ln, text) for ln, text in watchdog_matches if 169 <= ln <= 185
    ]

    problems = []
    if runner_in_fn:
        problems.append(
            f"run_ilk_loop_claude.sh get_ilk_runtime_dir suppresses stderr:\n"
            + "\n".join(f"  :{ln}: {text.strip()}" for ln, text in runner_in_fn)
        )
    if watchdog_in_fn:
        problems.append(
            f"watchdog.sh get_ilk_runtime_dir suppresses stderr:\n"
            + "\n".join(f"  :{ln}: {text.strip()}" for ln, text in watchdog_in_fn)
        )

    assert not problems, (
        "AC-4 violation — stderr must be surfaced, not swallowed:\n"
        + "\n".join(problems)
    )
