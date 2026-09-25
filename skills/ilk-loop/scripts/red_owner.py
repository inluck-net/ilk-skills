"""Bisect helper to find which commit first broke a set of test nodes.

Called by the runner when the widest gate goes red and the failing node
ids passed at the master's ``base_sha``.  Binary-searches
``git rev-list --first-parent <base>..<head>`` to find the first commit
where the nodes fail.

Stdlib only.  Reads/writes with ``encoding='utf-8-sig'`` where needed.

Usage::

    python3 red_owner.py --repo <r> --base <sha> --head <sha> \\
        --cmd <gate cmd> --node <id> [--node <id> ...] [--budget-s 300]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_test(repo: Path, cmd: str, nodes: list[str]) -> bool:
    """Run the test command in ``repo``.  Returns True if green."""
    full_cmd = cmd
    if nodes:
        full_cmd = cmd + " " + " ".join(nodes)
    r = subprocess.run(
        ["bash", "-c", full_cmd],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    return r.returncode == 0


def _commit_subject(repo: Path, sha: str) -> str:
    r = subprocess.run(
        ["git", "log", "--format=%s", "-1", sha],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip()


def _rev_list_first_parent(repo: Path, base: str, head: str) -> list[str]:
    """Return commits in base..head (exclusive of base), first-parent."""
    r = subprocess.run(
        ["git", "rev-list", "--first-parent", f"{base}..{head}"],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"rev-list failed: {r.stderr}"
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def bisect_red_owner(
    repo: Path,
    base: str,
    head: str,
    cmd: str,
    nodes: list[str],
    budget_s: int = 300,
) -> dict:
    """Find the first commit in base..head that breaks the nodes.

    Returns a dict with:
      - ``first_red``: SHA or None
      - ``first_red_subject``: commit subject or None
      - ``base_green``: bool — whether the base is green
      - ``reason``: present only when ``first_red`` is None due to budget
    """
    import time
    start = time.monotonic()

    # Step 1: binary search.
    commits = list(reversed(_rev_list_first_parent(repo, base, head)))
    if not commits:
        return {"first_red": None, "first_red_subject": None, "base_green": True}

    # Create a detached worktree for testing (including the base check).
    workdir = Path(tempfile.mkdtemp(prefix="red-owner-"))
    wt_path = workdir / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_path), base],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    try:
        # Verify base is green (in the worktree, at the base commit).
        base_green = _run_test(wt_path, cmd, nodes)
        if not base_green:
            return {"first_red": None, "first_red_subject": None, "base_green": False}
        lo, hi = 0, len(commits) - 1
        first_red_idx = len(commits)  # sentinel: all green

        max_runs = math.ceil(math.log2(len(commits))) + 1
        runs = 0

        while lo <= hi and runs < max_runs:
            if time.monotonic() - start > budget_s:
                return {"first_red": None, "first_red_subject": None,
                        "base_green": True, "reason": "budget"}

            mid = (lo + hi) // 2
            sha = commits[mid]

            # Checkout the candidate in the worktree.
            subprocess.run(
                ["git", "checkout", "--detach", sha],
                cwd=wt_path, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )

            red_here = not _run_test(wt_path, cmd, nodes)
            runs += 1

            if red_here:
                first_red_idx = mid
                hi = mid - 1
            else:
                lo = mid + 1

        if first_red_idx < len(commits):
            sha = commits[first_red_idx]
            return {
                "first_red": sha,
                "first_red_subject": _commit_subject(repo, sha),
                "base_green": True,
            }
        return {"first_red": None, "first_red_subject": None, "base_green": True}

    finally:
        # Clean up worktree — never touch the live tree.
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        try:
            workdir.rmdir()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--base", required=True, help="base SHA (green)")
    ap.add_argument("--head", required=True, help="head SHA")
    ap.add_argument("--cmd", required=True, help="test command to run")
    ap.add_argument("--node", action="append", dest="nodes", default=[],
                    help="test node id to run (repeatable)")
    ap.add_argument("--budget-s", type=int, default=300,
                    help="wall-clock budget in seconds (default: 300)")
    args = ap.parse_args(argv)

    result = bisect_red_owner(
        args.repo, args.base, args.head, args.cmd, args.nodes, args.budget_s,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))