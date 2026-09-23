#!/usr/bin/env python3
"""Resolve a master plan's declared ``work_tree:`` frontmatter field.

Usage:
  work_tree.py --plans-dir DIR --project-path DIR [--json]

Reads the active master's ``work_tree:`` field and validates it:

- Must be an absolute path.
- Must be an existing directory.
- Must be a git work tree (``git rev-parse --is-inside-work-tree``).
- Its ``git rev-parse --git-common-dir`` must equal the project-path's.

Returns JSON: ``{"path": str|None, "error": str|None}``.

Exit codes:
  0 — valid work tree (or absent — path is None, error is None)
  1 — invalid (error describes why)
  2 — infrastructure error (plans dir not found, etc.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import find_plans_dir  # noqa: E402
from loop_status import pick_active_master  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402


def _git_common_dir(path: Path) -> str | None:
    """Return ``git rev-parse --git-common-dir`` for *path*, or None.

    The result is resolved to an absolute path, because ``git-common-dir``
    returns ``.git`` (relative) from the clone but an absolute path from a
    sibling worktree.
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        if r.returncode == 0:
            raw = r.stdout.strip()
            # Resolve relative to the repo we ran in, not the process cwd.
            return str((path / raw).resolve())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _is_work_tree(path: Path) -> bool:
    """Return True if *path* is inside a git work tree."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_work_tree(
    plans_dir: Path,
    project_path: Path,
) -> dict:
    """Resolve and validate the active master's ``work_tree:`` field.

    Returns ``{"path": str|None, "error": str|None}``.
    """
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        return {"path": None, "error": "no master plans found"}

    try:
        chosen, _ = pick_active_master(masters, json_mode=True)
    except Exception as e:  # noqa: BLE001
        return {"path": None, "error": f"pick_active_master failed: {e}"}

    text = chosen.read_text(encoding="utf-8-sig")
    fm = parse_frontmatter(text)
    wt = fm.get("work_tree", "").strip()

    if not wt:
        return {"path": None, "error": None}

    # Absolute path check.
    p = Path(wt)
    if not p.is_absolute():
        return {
            "path": None,
            "error": f"work_tree is not absolute: {wt!r}",
        }

    # Existing directory check.
    if not p.is_dir():
        return {
            "path": None,
            "error": f"work_tree does not exist: {wt}",
        }

    # Must be inside a git work tree.
    if not _is_work_tree(p):
        return {
            "path": None,
            "error": f"work_tree is not a git work tree: {wt}",
        }

    # git-common-dir must match the project-path's.
    wt_gcd = _git_common_dir(p)
    proj_gcd = _git_common_dir(project_path)
    if wt_gcd is None or proj_gcd is None:
        return {
            "path": None,
            "error": "could not resolve git-common-dir for work_tree or project-path",
        }
    if wt_gcd != proj_gcd:
        return {
            "path": None,
            "error": (
                f"work_tree git-common-dir ({wt_gcd}) does not match "
                f"project-path git-common-dir ({proj_gcd})"
            ),
        }

    return {"path": str(p), "error": None}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--plans-dir", type=Path, required=True)
    ap.add_argument("--project-path", type=Path, required=True)
    ap.add_argument("--json", action="store_true", dest="json_mode",
                    help="print JSON even on success")
    a = ap.parse_args(argv)

    result = resolve_work_tree(a.plans_dir, a.project_path)

    if result["error"]:
        print(json.dumps(result, indent=2))
        return 1

    if a.json_mode or not sys.stdout.isatty():
        print(json.dumps(result, indent=2))
    else:
        if result["path"]:
            print(result["path"])
        else:
            print("(no work_tree declared)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
