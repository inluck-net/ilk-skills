#!/usr/bin/env python3
"""Measure a tag's test baseline in a throwaway worktree.

Sub-plan: a-tags-baseline-can-be-measured, step 1.

This module runs the test suite at a specific git tag to capture which tests
fail there (the "baseline").  The baseline is stored for later comparison by
baseline_diff.py.

AC-1: check is missing before; measure stores; load_baseline returns ids
AC-2: measurement runs at the tag (not HEAD)
AC-3: no-summary run stores nothing; attempts tracked; unmeasurable after 2
AC-4: worktree is gone after measure (both success and failure)
AC-5: check with live pid marker is measuring
AC-6: CLI: check prints state word; measure prints result JSON
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


# ── Constants ─────────────────────────────────────────────────────────────────

MARKER_SUBDIR = "runtime/release"


def _marker_path(data_dir: Path, tag: str) -> Path:
    """Path to the marker file for a given tag."""
    return data_dir / MARKER_SUBDIR / f"baseline-measure-{tag}.json"


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _count_worktrees(project: Path) -> int:
    """Count git worktrees (excluding the main one)."""
    r = subprocess.run(
        ["git", "worktree", "list"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    return len(lines)


def _parse_test_output(output: str) -> set[str]:
    """Parse pytest output to extract failing test node ids.

    Looks for lines like:
        FAILED test_bad.py::test_bad
        FAILED test_bad.py::test_bad - assert False
    """
    node_ids = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            # Extract just the node id (before any " - " explanation)
            node_id = line[len("FAILED "):].strip()
            if " - " in node_id:
                node_id = node_id[:node_id.index(" - ")].strip()
            if node_id:
                node_ids.add(node_id)
    return node_ids


def _count_collected(output: str) -> int:
    """Parse pytest output to extract the number of collected tests.

    Handles both verbose and quiet output formats:
        collected 3 items
        1 failed, 2 passed in 0.01s
        3 passed in 0.01s
    """
    import re

    # Try verbose format first: "collected N items"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("collected ") and " item" in line:
            try:
                return int(line.split()[1])
            except (ValueError, IndexError):
                pass

    # Try quiet format: "N passed in Xs" or "N failed, M passed in Xs"
    for line in output.splitlines():
        line = line.strip()
        # Match patterns like "1 failed, 2 passed in 0.01s" or "3 passed in 0.01s"
        match = re.search(r'(\d+)\s+passed\s+in\s+', line)
        if match:
            passed = int(match.group(1))
            # Also count failed if present
            failed_match = re.search(r'(\d+)\s+failed', line)
            failed = int(failed_match.group(1)) if failed_match else 0
            return passed + failed

    return 0


# ── Core API ──────────────────────────────────────────────────────────────────

def check(project: Path, data_dir: Path, tag: Optional[str] = None) -> Dict[str, Any]:
    """Check the baseline measurement state for a tag.

    Returns a dict with "state" key:
      - "missing": no baseline stored for the tag
      - "present": baseline is stored and available
      - "no-tag": the project has no release tag (nothing to compare against)
      - "measuring": a measurement is currently in progress (live pid marker)
      - "unmeasurable": measurement failed after max attempts
    """
    # If no tag specified, try to resolve from latest tag
    if tag is None:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(project),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # No tag exists: not "missing" — there is no release to measure.
            return {"state": "no-tag"}
        tag = r.stdout.strip()

    # Check if baseline is already stored
    from baseline_diff import load_baseline, baseline_key, baseline_dir

    # We need to check with a default invocation first
    # But the tests pass data_dir directly, not project_root
    # Let's check if there's any baseline file for this tag
    baseline_d = data_dir / ".ilk-baselines"
    if baseline_d.exists():
        for f in baseline_d.glob(f"{tag}__*.json"):
            return {"state": "present"}

    # Check for marker file
    marker = _marker_path(data_dir, tag)
    if marker.exists():
        try:
            marker_data = json.loads(marker.read_text())
        except (json.JSONDecodeError, OSError):
            return {"state": "missing"}

        # Check if measurement is in progress
        pid = marker_data.get("pid")
        if pid and _is_pid_alive(pid):
            return {"state": "measuring"}

        # Check attempts
        attempts = marker_data.get("attempts", 0)
        if attempts >= 2:
            return {"state": "unmeasurable"}

    return {"state": "missing"}


def measure(
    project: Path,
    data_dir: Path,
    tag: str,
    invocation: str,
) -> Dict[str, Any]:
    """Measure the baseline at a specific tag.

    Creates a worktree at the tag, runs the test suite, captures failures,
    stores the baseline, and cleans up the worktree.

    Returns a dict with "stored" key (True if baseline was stored).
    """
    from baseline_diff import store_baseline

    # Ensure data dir exists
    data_dir.mkdir(parents=True, exist_ok=True)

    # Create marker directory
    marker_dir = data_dir / MARKER_SUBDIR
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = _marker_path(data_dir, tag)

    # Read existing marker or create new one
    marker_data = {"pid": os.getpid(), "tag": tag, "attempts": 0, "started_at": None}
    if marker.exists():
        try:
            marker_data = json.loads(marker.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Update marker with current pid and increment attempts
    marker_data["pid"] = os.getpid()
    marker_data["attempts"] = marker_data.get("attempts", 0) + 1
    marker_data["started_at"] = subprocess.run(
        ["date", "+%Y-%m-%dT%H:%M:%S"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    marker.write_text(json.dumps(marker_data, indent=2) + "\n")

    # Create worktree at tag (absolute path required for git worktree)
    worktree_path = (data_dir / f".worktree-{tag}").resolve()
    try:
        # Remove existing worktree if any
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(project),
            capture_output=True,
        )

        # Create new worktree at tag
        r = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), tag],
            cwd=str(project),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return {"stored": False}

        # Run the test suite in the worktree
        r = subprocess.run(
            invocation,
            shell=True,
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
        )

        # Parse output to get failing tests and total collected
        node_ids = _parse_test_output(r.stdout + r.stderr)
        search_space = _count_collected(r.stdout + r.stderr)

        # If no summary (no collected tests), don't store
        if search_space == 0:
            # Clear PID from marker (measurement complete, not in progress)
            marker_data.pop("pid", None)
            marker.write_text(json.dumps(marker_data, indent=2) + "\n")
            return {"stored": False}

        # Store the baseline
        store_baseline(
            project_root=data_dir,
            tag=tag,
            suite_invocation=invocation,
            node_ids=frozenset(node_ids),
            search_space=search_space,
        )

        # Remove marker on success
        if marker.exists():
            marker.unlink()

        return {"stored": True}

    finally:
        # Always clean up worktree
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(project),
            capture_output=True,
        )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure a tag's test baseline in a throwaway worktree.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check subcommand
    check_parser = sub.add_parser("check", help="Check baseline state")
    check_parser.add_argument("--project", required=True, help="Project root")
    check_parser.add_argument("--data-dir", required=True, help="Data directory")
    check_parser.add_argument("--tag", help="Tag to check (default: latest)")

    # measure subcommand
    measure_parser = sub.add_parser("measure", help="Measure baseline at tag")
    measure_parser.add_argument("--project", required=True, help="Project root")
    measure_parser.add_argument("--data-dir", required=True, help="Data directory")
    measure_parser.add_argument(
        "--tag", help="Tag to measure (default: the latest release tag)"
    )
    measure_parser.add_argument(
        "--invocation",
        default=None,
        help="Test invocation command (default: the train's resolved invocation)",
    )

    args = parser.parse_args()

    if args.command == "check":
        result = check(
            project=Path(args.project),
            data_dir=Path(args.data_dir),
            tag=args.tag,
        )
        print(result["state"])
    elif args.command == "measure":
        project = Path(args.project)
        tag = args.tag
        if tag is None:
            from release_train import _get_latest_tag

            tag = _get_latest_tag(project)
        invocation = args.invocation
        if invocation is None:
            from release_train import _resolve_invocation

            invocation = _resolve_invocation(project)
        result = measure(
            project=project,
            data_dir=Path(args.data_dir),
            tag=tag,
            invocation=invocation,
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()