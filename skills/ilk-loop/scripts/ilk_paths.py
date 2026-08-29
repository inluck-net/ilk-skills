"""
ilk_paths — central project-key + plans-dir resolution for ilk-* tools.

Convention
==========

Every ilk-* project is identified by a `project_key`, derived from the
absolute path of its root. Plans, runtime state, and per-project logs
live OUTSIDE the project tree, under:

    ~/.ilk-data/projects/<project-key>/
        plans/         # MASTER-*.md and sub-plans
        runtime/       # last-exit.json, queue cursors, etc.
        logs/          # per-project loop output
        logs/archive/  # preserved active-run evidence before cleanup

This keeps the project repo clean (no skill artifacts polluting SCM) and
makes the same skill suite usable across personal and employer projects
without leaking artifacts into each other's git history.

Project kinds
=============

  * single: classic case. Project root is the nearest ancestor with a
    `.git` (dir or file). All commits, local_checks, push happen there.

  * meta:   "polyrepo" case. Project root is the nearest ancestor that
    contains a `.ilk-meta.json` marker listing sibling git repos
    (e.g. a parent directory with api/, portal/, ops/, ... each its
    own repo). A single MASTER plan drives sub-plans whose `repo:`
    frontmatter names which member repo each one targets. The loop
    driver cd's into the named member repo for that sub-plan's git
    operations.

A meta marker found anywhere above `start` wins over `.git` — that is
how `<meta>/api/src/foo.py` resolves to the meta root `<meta>/` rather
than to the api sub-repo. Validation rejects markers whose listed
repos don't exist on disk, so a stray `~/.ilk-meta.json` doesn't
accidentally swallow every project.

Resolution policy
=================

`find_plans_dir(start)` returns a (path, source) tuple. Order:

  1. External:  ~/.ilk-data/projects/<key>/plans/   (preferred)
  2. In-tree:   <git_root>/docs/plans/              (legacy / migration; single only)
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
import json
import os
import re
from pathlib import Path
from typing import Literal


# ── skill-root resolution ───────────────────────────────────────────────────

_SKILL_ROOT_CANDIDATES = [
    Path.home() / ".codex" / "skills",
    Path.home() / ".cursor" / "skills",
    Path.home() / ".claude" / "skills",
]


def skill_root(*, from_file: str | os.PathLike | None = None) -> Path:
    """Resolve the installed ``ilk-*`` skills directory.

    Resolution order:

    1. ``ILK_SKILL_HOME`` environment variable (absolute path to the
       skills directory, e.g. ``~/.codex/skills``).
    2. Auto-detect from *from_file*: walk up from that path looking for
       a ``skills/<name>/scripts/`` ancestor.  Works for any host
       (Cursor, Claude Code, Codex) since every host installs the same
       directory structure.
    3. First existing candidate from ``~/.codex/skills``,
       ``~/.cursor/skills``, ``~/.claude/skills``.

    Stdlib only.  Raises ``FileNotFoundError`` when nothing matches.
    """
    env = os.environ.get("ILK_SKILL_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p

    if from_file is not None:
        cur = Path(from_file).resolve()
        # scripts/<name>/scripts/<this_file> → scripts/<name> → skills dir
        # The path is: <skills_dir>/<skill_name>/scripts/<script>
        # So walk up until we find a parent whose parent has ilk-* siblings.
        for _ in range(6):  # bounded walk
            if cur.name == "scripts" and cur.parent.name.startswith("ilk-"):
                candidate = cur.parent.parent  # the skills/ directory
                if candidate.is_dir():
                    return candidate
            cur = cur.parent
            if cur == cur.parent:
                break

    for candidate in _SKILL_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "Cannot resolve ilk skill root. Set ILK_SKILL_HOME or install "
        "skills to ~/.codex/skills, ~/.cursor/skills, or ~/.claude/skills."
    )


# ── project-root derivation ──────────────────────────────────────────────────

META_MARKER = ".ilk-meta.json"

ProjectKind = Literal["single", "meta"]


def git_root(start: Path) -> Path | None:
    """First ancestor of `start` that contains a `.git` (dir or file)."""
    cur = Path(start).resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def meta_root(start: Path) -> Path | None:
    """First ancestor of `start` that contains a valid `.ilk-meta.json`.

    Walks all the way to the filesystem root. Returns None if no marker
    exists or every candidate fails validation (see `read_meta_manifest`).
    """
    cur = Path(start).resolve()
    while True:
        candidate = cur / META_MARKER
        if candidate.is_file():
            try:
                read_meta_manifest(cur)
                return cur
            except MetaManifestError:
                # Invalid marker — keep walking; an outer valid marker
                # might still claim this tree, otherwise fall through.
                pass
        if cur.parent == cur:
            return None
        cur = cur.parent


class MetaManifestError(ValueError):
    """Raised when `.ilk-meta.json` is missing, malformed, or its
    declared member repos don't exist on disk as git repos."""


def read_meta_manifest(meta_dir: Path) -> dict:
    """Load + validate `<meta_dir>/.ilk-meta.json`.

    Returns a normalized dict:

        {
          "name":  "<project name>",
          "repos": [{"name": "<short>", "path": Path(absolute)}, ...]
        }

    Raises `MetaManifestError` if the file is missing, JSON-malformed,
    schema-invalid, or any declared repo's path doesn't resolve to a
    directory containing `.git`. The repo-on-disk check is what
    prevents a stray marker (e.g. accidentally at `~/`) from claiming
    every project below it.
    """
    path = Path(meta_dir) / META_MARKER
    if not path.is_file():
        raise MetaManifestError(f"{path} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise MetaManifestError(f"{path}: invalid JSON: {e}") from e

    if not isinstance(raw, dict):
        raise MetaManifestError(f"{path}: top-level must be an object")
    repos_raw = raw.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise MetaManifestError(f"{path}: 'repos' must be a non-empty list")

    name = str(raw.get("name") or meta_dir.name)
    repos: list[dict] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(repos_raw):
        if not isinstance(entry, dict):
            raise MetaManifestError(f"{path}: repos[{i}] must be an object")
        r_name = entry.get("name")
        r_path = entry.get("path")
        if not isinstance(r_name, str) or not r_name:
            raise MetaManifestError(f"{path}: repos[{i}].name missing")
        if not isinstance(r_path, str) or not r_path:
            raise MetaManifestError(f"{path}: repos[{i}].path missing")
        if r_name in seen_names:
            raise MetaManifestError(f"{path}: duplicate repo name '{r_name}'")
        seen_names.add(r_name)
        abs_path = (Path(meta_dir) / r_path).resolve()
        if not abs_path.is_dir():
            raise MetaManifestError(
                f"{path}: repos[{i}] path '{r_path}' is not a directory"
            )
        if not (abs_path / ".git").exists():
            raise MetaManifestError(
                f"{path}: repos[{i}] path '{r_path}' has no .git — "
                "is it really a git repo?"
            )
        repos.append({"name": r_name, "path": abs_path})

    return {"name": name, "repos": repos}


def find_project_root(start: Path) -> tuple[Path | None, ProjectKind]:
    """Locate the project root for `start`, preferring meta over single.

    Returns `(root, kind)`. `kind` is "meta" when a valid `.ilk-meta.json`
    marker covers `start`, else "single" when a `.git` ancestor exists,
    else `(None, "single")`.
    """
    m = meta_root(start)
    if m is not None:
        return m, "meta"
    g = git_root(start)
    return g, "single"


def meta_member_for(meta_dir: Path, start: Path) -> dict | None:
    """Return the meta-member dict whose path contains `start`, or None.

    Useful for "which sub-repo am I sitting in right now" when the
    caller already knows it's a meta project.
    """
    start = Path(start).resolve()
    try:
        manifest = read_meta_manifest(meta_dir)
    except MetaManifestError:
        return None
    for repo in manifest["repos"]:
        try:
            start.relative_to(repo["path"])
            return repo
        except ValueError:
            continue
    return None


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
    """Resolve the canonical ilk data root directory.

    Precedence (identical in all languages):
      1. ``$ILK_DATA_HOME`` — primary env var.
      2. ``$ILK_DATA_DIR`` — back-compat alias (currently honored by
         the PowerShell/bash side; this makes Python agree).
      3. ``~/.ilk-data`` — default.
    """
    env = os.environ.get("ILK_DATA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    env = os.environ.get("ILK_DATA_DIR")
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


def logs_launcher_dir(key: str) -> Path:
    """Launcher stdout/stderr logs: ~/.ilk-data/projects/<key>/logs/launcher/"""
    return external_logs_dir(key) / "launcher"


def run_log_dir(key: str, run_id: str) -> Path:
    """Per-run iteration artifacts: ~/.ilk-data/projects/<key>/logs/runs/<run_id>/"""
    return external_logs_dir(key) / "runs" / run_id


def archive_run_dir(key: str, run_id: str) -> Path:
    """Preserved active-run evidence: ~/.ilk-data/projects/<key>/logs/archive/<run_id>/"""
    return external_logs_dir(key) / "archive" / run_id


def jsonl_summary_path(key: str) -> Path:
    """Stable JSONL summary for all runs: ~/.ilk-data/projects/<key>/logs/.ilk-loop.log"""
    return external_logs_dir(key) / ".ilk-loop.log"


def external_launcher_dir(key: str) -> Path:
    return external_runtime_dir(key) / "launcher"


def sentinel_path(key: str) -> Path:
    """Canonical path to the exit sentinel: <external_launcher_dir>/last-exit.json.

    Every reader of last-exit.json MUST resolve it through this accessor.
    A bare ``runtime / "last-exit.json"`` join is a bug — the sentinel
    lives under ``runtime/launcher/`` since commit 736d6d5.
    """
    return external_launcher_dir(key) / "last-exit.json"


def external_watchdog_dir(key: str) -> Path:
    return external_runtime_dir(key) / "watchdog"


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
    Resolution order: external → in-tree (under git root, single mode
    only) → walk-up legacy fallback. The walk-up branch matches the
    pre-externalisation behaviour so legacy projects continue to work
    without migration.

    In meta mode the project key derives from the meta root, not from
    any member repo, so all members share one plans directory.
    """
    start = Path(start).resolve()

    root, kind = find_project_root(start)
    if root is not None:
        ext = external_plans_dir(project_key(root))
        if _has_master(ext):
            return ext, "external"
        # In-tree fallback only makes sense for single-repo projects.
        # Meta roots don't have a `.git` at all and shouldn't grow one.
        if kind == "single":
            in_tree = root / "docs" / "plans"
            if _has_master(in_tree):
                return in_tree, "in-tree"
        # Meta mode is deliberately strict: if there are no external
        # plans yet, return "" rather than walking up into a sibling
        # member's legacy in-tree plans. The migration path is to move
        # those plans into the meta-level external dir on purpose.
        if kind == "meta":
            return None, ""

    # Walk-up legacy fallback: useful when invoked from a sub-directory
    # of a non-git project that opts out of the meta convention (rare,
    # but supported by the original loop_status.py and we don't want
    # to regress).
    cur = start
    while True:
        candidate = cur / "docs" / "plans"
        if _has_master(candidate):
            return candidate, "walk-up"
        if cur.parent == cur:
            return None, ""
        cur = cur.parent


def resolve_project_key(start: Path) -> str | None:
    """`project_key` for the project root containing `start`, or None.

    Prefers a meta root over a `.git` root — see `find_project_root`.
    """
    root, _kind = find_project_root(start)
    if root is None:
        return None
    return project_key(root)


# ── self-test entrypoint (python -m ilk_paths or direct exec) ────────────────

def _selftest() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ilk_paths quick lookup")
    ap.add_argument("--start", type=Path, default=Path.cwd())
    ap.add_argument("--where", action="store_true",
                    help="Print human-readable state paths (one per line) instead of JSON.")
    ap.add_argument("--sentinel-path", action="store_true",
                    help="Print the canonical sentinel path and exit.")
    args = ap.parse_args()
    g_root = git_root(args.start)
    m_root = meta_root(args.start)
    root, kind = find_project_root(args.start)
    key = project_key(root) if root else None
    plans, src = find_plans_dir(args.start)

    if args.sentinel_path:
        if key is None:
            print(f"error: no project root for {args.start.resolve()}", file=__import__("sys").stderr)
            return 1
        print(sentinel_path(key))
        return 0

    member: dict | None = None
    members: list[dict] = []
    if kind == "meta" and root is not None:
        try:
            manifest = read_meta_manifest(root)
            members = [
                {"name": r["name"], "path": str(r["path"])} for r in manifest["repos"]
            ]
            m = meta_member_for(root, args.start)
            if m is not None:
                member = {"name": m["name"], "path": str(m["path"])}
        except MetaManifestError as e:
            print(f"[ilk] meta manifest invalid: {e}", file=__import__("sys").stderr)

    if args.where:
        if key is None:
            print(f"error: no project root for {args.start.resolve()}", file=__import__("sys").stderr)
            return 1
        print(f"plans: {external_plans_dir(key)}")
        print(f"runtime: {external_runtime_dir(key)}")
        print(f"launcher: {external_launcher_dir(key)}")
        print(f"sentinel: {sentinel_path(key)}")
        print(f"watchdog: {external_watchdog_dir(key)}")
        print(f"logs: {external_logs_dir(key)}")
        print(f"logs-launcher: {logs_launcher_dir(key)}")
        print(f"jsonl-summary: {jsonl_summary_path(key)}")
        print(f"archive: {external_logs_dir(key) / 'archive'}")
        return 0

    print(json.dumps({
        "start": str(args.start.resolve()),
        "git_root": str(g_root) if g_root else None,
        "meta_root": str(m_root) if m_root else None,
        "project_root": str(root) if root else None,
        "project_kind": kind,
        "project_key": key,
        "ilk_data_root": str(ilk_data_root()),
        "external_plans_dir": str(external_plans_dir(key)) if key else None,
        "external_runtime_dir": str(external_runtime_dir(key)) if key else None,
        "external_logs_dir": str(external_logs_dir(key)) if key else None,
        "logs_launcher_dir": str(logs_launcher_dir(key)) if key else None,
        "jsonl_summary_path": str(jsonl_summary_path(key)) if key else None,
        "external_launcher_dir": str(external_launcher_dir(key)) if key else None,
        "sentinel_path": str(sentinel_path(key)) if key else None,
        "external_watchdog_dir": str(external_watchdog_dir(key)) if key else None,
        "archive_base": str(external_logs_dir(key) / "archive") if key else None,
        "resolved_plans_dir": str(plans) if plans else None,
        "resolved_source": src,
        "meta_members": members,
        "current_member": member,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
