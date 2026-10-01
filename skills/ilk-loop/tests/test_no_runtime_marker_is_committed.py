"""Pin that no runtime marker written by the runner is tracked in git.

Regression for 6f165de (2026-10-01): `.ilk-merge-deferred` was committed
together with code files, so every fresh worktree checks out a stale
marker.

The runner writes `.ilk-merge-deferred` and `.ilk-remote-type` into the
project root.  Both must be gitignored and untracked.  The config files
`.ilk-launch.json` and `.ilk-meta.json` are intentionally tracked and
serve as a control.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]  # worktree root
_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"

# Files the runner writes into the project root (not ~/.ilk-data).
# Derived by scanning run_ilk_loop_claude.sh for write operations on
# ${PROJECT_PATH}/.ilk-* or ${SELFMOD_WORKTREE_PATH}/.ilk-*.
_RUNNER_WRITES: list[str] = [
    ".ilk-merge-deferred",
    ".ilk-remote-type",
]

# Config files that are intentionally tracked.
_ALLOWLIST: set[str] = {
    ".ilk-launch.json",
    ".ilk-meta.json",
}


def _scan_runner_for_ilk_writes() -> list[str]:
    """Derive root-level .ilk-* filenames the runner writes.

    Scans run_ilk_loop_claude.sh for paths formed as
    ``${PROJECT_PATH}/.ilk-<name>`` or
    ``${SELFMOD_WORKTREE_PATH}/.ilk-<name>`` used with ``>``, ``>>``,
    ``tee``, ``mv``, or ``printf … >``.  Returns sorted, deduped basenames.
    """
    text = _RUNNER.read_text(encoding="utf-8")
    # Only match writes to the project root or selfmod worktree root,
    # not to ~/.ilk-data or other internal directories.
    pattern = re.compile(
        r"\$\{(PROJECT_PATH|SELFMOD_WORKTREE_PATH)(:-?)?\}/\.ilk-([a-z][a-z0-9-]*)"
    )
    found: set[str] = set()
    for m in pattern.finditer(text):
        name = f".ilk-{m.group(3)}"
        found.add(name)
    return sorted(found)


# ── AC-1: .ilk-merge-deferred is untracked and ignored ──────────────────────


def test_merge_deferred_not_tracked() -> None:
    """AC-1: git ls-files .ilk-merge-deferred is empty."""
    result = subprocess.run(
        ["git", "ls-files", ".ilk-merge-deferred"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "", (
        f".ilk-merge-deferred is tracked: {result.stdout.strip()!r}"
    )


def test_merge_deferred_ignored() -> None:
    """AC-1: git check-ignore matches .ilk-merge-deferred."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".ilk-merge-deferred"],
        cwd=_REPO_ROOT,
        timeout=10,
    )
    assert result.returncode == 0, (
        ".ilk-merge-deferred is not ignored by .gitignore"
    )


# ── AC-2: meta-test — every runner-written .ilk-* is ignored and untracked ──


def test_runner_writes_are_not_tracked() -> None:
    """AC-2: every .ilk-* file the runner writes is untracked."""
    scanned = _scan_runner_for_ilk_writes()
    # Must find at least the two known markers.
    assert len(scanned) >= 2, (
        f"Runner scan found only {len(scanned)} .ilk-* writes; "
        f"expected >= 2. Found: {scanned}"
    )
    offenders: list[str] = []
    for name in scanned:
        if name in _ALLOWLIST:
            continue
        result = subprocess.run(
            ["git", "ls-files", name],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            offenders.append(name)
    assert not offenders, (
        f"Runner-written .ilk-* files are tracked: {offenders}"
    )


def test_runner_writes_are_ignored() -> None:
    """AC-2: every .ilk-* file the runner writes is gitignored."""
    scanned = _scan_runner_for_ilk_writes()
    assert len(scanned) >= 2, (
        f"Runner scan found only {len(scanned)} .ilk-* writes; "
        f"expected >= 2. Found: {scanned}"
    )
    offenders: list[str] = []
    for name in scanned:
        if name in _ALLOWLIST:
            continue
        result = subprocess.run(
            ["git", "check-ignore", "-q", name],
            cwd=_REPO_ROOT,
            timeout=10,
        )
        if result.returncode != 0:
            offenders.append(name)
    assert not offenders, (
        f"Runner-written .ilk-* files are not ignored: {offenders}"
    )


def test_runner_scan_finds_known_markers() -> None:
    """AC-2: the runner scan must find .ilk-merge-deferred and .ilk-remote-type."""
    scanned = _scan_runner_for_ilk_writes()
    for expected in _RUNNER_WRITES:
        assert expected in scanned, (
            f"Runner scan did not find {expected!r} in {scanned}"
        )


# ── AC-3 (control): .ilk-launch.json is still tracked ───────────────────────


def test_launch_json_still_tracked() -> None:
    """AC-3: .ilk-launch.json is tracked (control)."""
    result = subprocess.run(
        ["git", "ls-files", ".ilk-launch.json"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == ".ilk-launch.json", (
        f".ilk-launch.json is not tracked: {result.stdout.strip()!r}"
    )