"""
ilk_paths — central project-key + plans-dir resolution for ilk-* tools.

Convention
==========

Every ilk-* project is identified by a `project_key`, derived from the
absolute path of its `.git` root. Plans, runtime state, and per-project
logs live OUTSIDE the project tree, under:

    ~/.ilk-data/projects/<project-key>/
        plans/         # MASTER-*.md and sub-plans
        runtime/       # last-exit.json, queue cursors, etc.
        logs/          # per-project loop output

This keeps the project repo clean (no skill artifacts polluting SCM) and
makes the same skill suite usable across personal and employer projects
without leaking artifacts into each other's git history.

Resolution policy
=================

`find_plans_dir(start)` returns a (path, source) tuple. Order:

  1. External:  ~/.ilk-data/projects/<key>/plans/   (preferred)
  2. In-tree:   <git_root>/docs/plans/              (legacy / migration)
  3. Walk-up:   first ancestor docs/plans/MASTER-*.md (legacy fallback)

`source` is one of "external", "in-tree", "walk-up", or "" when the
result is None. Callers can warn when source != "external" to nudge
users to migrate.

The lookup is read-only and pure: it never creates directories. The
`external_plans_dir(...)` accessor is provided for writers (plan
generators, migration tools) that need to create the path.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal


# ── project-key derivation ───────────────────────────────────────────────────

def git_root(start: Path) -> Path | None:
    """First ancestor of `start` that contains a `.git` (dir or file)."""
    cur = Path(start).resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def project_key(root: Path) -> str:
    """
    Stable, readable key from an absolute project root path.

    Lowercase, ASCII, hyphenated path. Capped at 80 chars; if the raw
    slug would exceed the cap, the tail is replaced with a 7-char sha1
    suffix to keep the key unique while still searchable by humans.
    """
    abs_str = str(Path(root).resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


def ilk_data_root() -> Path:
    """`$ILK_DATA_HOME` if set, else `~/.ilk-data`."""
    env = os.environ.get("ILK_DATA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".ilk-data"


def project_data_dir(key: str) -> Path:
    return ilk_data_root() / "projects" / key


def external_plans_dir(key: str) -> Path:
    return project_data_dir(key) / "plans"


def external_runtime_dir(key: str) -> Path:
    return project_data_dir(key) / "runtime"


def external_logs_dir(key: str) -> Path:
    return project_data_dir(key) / "logs"


# ── plans-dir resolution ─────────────────────────────────────────────────────

PlansSource = Literal["external", "in-tree", "walk-up", ""]


def _has_master(d: Path) -> bool:
    try:
        return d.is_dir() and any(d.glob("MASTER-*.md"))
    except OSError:
        return False


def find_plans_dir(start: Path) -> tuple[Path | None, PlansSource]:
    """
    Locate the active plans directory for the project containing `start`.

    Returns (path, source). When source == "" the path is None.
    Resolution order: external → in-tree (under .git root) → walk-up
    legacy fallback. The walk-up branch matches the pre-externalisation
    behaviour so legacy projects continue to work without migration.
    """
    start = Path(start).resolve()

    root = git_root(start)
    if root is not None:
        ext = external_plans_dir(project_key(root))
        if _has_master(ext):
            return ext, "external"
        in_tree = root / "docs" / "plans"
        if _has_master(in_tree):
            return in_tree, "in-tree"

    # Walk-up legacy fallback: useful when invoked from a sub-directory
    # of a non-git project (rare, but supported by the original
    # loop_status.py and we don't want to regress).
    cur = start
    while True:
        candidate = cur / "docs" / "plans"
        if _has_master(candidate):
            return candidate, "walk-up"
        if cur.parent == cur:
            return None, ""
        cur = cur.parent


def resolve_project_key(start: Path) -> str | None:
    """`project_key` for the .git root containing `start`, or None."""
    root = git_root(start)
    if root is None:
        return None
    return project_key(root)


# ── self-test entrypoint (python -m ilk_paths or direct exec) ────────────────

def _selftest() -> int:
    import argparse, json
    ap = argparse.ArgumentParser(description="ilk_paths quick lookup")
    ap.add_argument("--start", type=Path, default=Path.cwd())
    args = ap.parse_args()
    root = git_root(args.start)
    key = project_key(root) if root else None
    plans, src = find_plans_dir(args.start)
    print(json.dumps({
        "start": str(args.start.resolve()),
        "git_root": str(root) if root else None,
        "project_key": key,
        "ilk_data_root": str(ilk_data_root()),
        "external_plans_dir": str(external_plans_dir(key)) if key else None,
        "external_runtime_dir": str(external_runtime_dir(key)) if key else None,
        "external_logs_dir": str(external_logs_dir(key)) if key else None,
        "resolved_plans_dir": str(plans) if plans else None,
        "resolved_source": src,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
