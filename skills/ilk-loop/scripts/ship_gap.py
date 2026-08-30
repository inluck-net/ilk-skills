#!/usr/bin/env python3
"""
ship_gap.py — committed-vs-changed path accounting for an iteration.

Pure, importable, no side effects.  Computes the gap between paths an
iteration committed and paths still dirty in the tree at iteration end.

Usage:
  ship_gap.py --repo <path> --head-before <sha> --head-after <sha> [--json]

Output (default): one line per repo with the three numbers.
Output (--json): a JSON object with committed_paths, tree_paths, gap, unexplained.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def compute_gap(repo: Path, head_before: str, head_after: str) -> dict:
    """Compute the committed-vs-changed gap for an iteration.

    Parameters
    ----------
    repo : Path
        The git repository root.
    head_before : str
        HEAD at iteration start.
    head_after : str
        HEAD at iteration end.

    Returns
    -------
    dict
        ``committed_paths`` — paths in the iteration's commits.
        ``tree_paths`` — uncommitted + untracked paths at iteration end.
        ``gap`` — tree_paths - committed_paths (when unexplained).
        ``unexplained`` — True iff committed_paths > 0 and tree_paths > 0.
    """
    committed = _committed_paths(repo, head_before, head_after)
    tree = _tree_paths(repo)
    unexplained = len(committed) > 0 and len(tree) > 0
    return {
        "committed_paths": len(committed),
        "tree_paths": len(tree),
        "gap": len(tree) - len(committed) if unexplained else 0,
        "unexplained": unexplained,
    }


def _committed_paths(repo: Path, head_before: str, head_after: str) -> set[str]:
    """Paths changed in commits between head_before and head_after."""
    try:
        cp = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r",
             f"{head_before}..{head_after}"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
        )
        if cp.returncode != 0:
            return set()
        return {line.strip() for line in cp.stdout.splitlines() if line.strip()}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()


def _tree_paths(repo: Path) -> set[str]:
    """Uncommitted tracked modifications + untracked files."""
    try:
        # Tracked modifications (staged + unstaged)
        cp_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
        )
        cp_cached = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
        )
        # Untracked files
        cp_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
        )
        paths = set()
        for out in (cp_diff.stdout, cp_cached.stdout, cp_untracked.stdout):
            for line in out.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
        return paths
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--head-before", required=True)
    ap.add_argument("--head-after", required=True)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    result = compute_gap(args.repo, args.head_before, args.head_after)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["unexplained"]:
            print(
                f"verified {result['tree_paths']} changed paths, "
                f"committed {result['committed_paths']} — "
                f"{result['gap']} uncommitted at iteration end"
            )
        else:
            print("no ship gap")

    return 0


if __name__ == "__main__":
    sys.exit(main())
