"""Enumerate projects with runnable masters, FIFO-annotated.

Resolves the projects root via ``ilk_paths.ilk_data_root() / "projects"``
(honors ``$ILK_DATA_HOME``). For each project directory, parses every
MASTER-*.md to find sub-plan references, reads their front-matter, and
emits a JSON array of projects that have a **runnable master** — an
``active`` master with ≥1 non-shipped sub-plan, or a ``queued`` master
(with ≥1 non-shipped sub-plan) that promotion can activate.

Each entry::

    {
      "key": "<project-key>",
      "path": "<absolute project data dir under ~/.ilk-data>",
      "repo_path": "<absolute SOURCE repo path, or null if unresolved>",
      "oldest_queued_ts": "<ISO 8601 timestamp>"
    }

``path`` is the data dir (used for the per-project sentinel + postmortems).
``repo_path`` is the real source repo the loop must ``cd`` into — this is
what the scheduler passes to ``launch.* -ProjectPath``. It is resolved from
the project's ``runtime/launcher/last-launch.json`` (``project_path``, written
by every launch), falling back to the ``ilk-launcher/projects.json`` registry.
``repo_path`` is ``null`` when the project has neither — the scheduler then
skips it (``skip-unresolved``) rather than dispatching a wrong path.

Sorted by ``oldest_queued_ts`` ascending (oldest first = FIFO).

A project is excluded only when EVERY master is ``shipped`` (no runnable
master). A project whose only remaining master is ``queued`` is included
(promotion can activate it).

Exit code 0 always (empty list is valid — means "nothing to do").
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# --- import ilk_paths from sibling ilk-loop/scripts/ ---
_SKILL_ROOT_CANDIDATES = [
    Path.home() / ".codex" / "skills",
    Path.home() / ".cursor" / "skills",
    Path.home() / ".claude" / "skills",
]


def _resolve_skill_root() -> Path:
    env = os.environ.get("ILK_SKILL_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
    for candidate in _SKILL_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Cannot resolve ilk skill root.")


_SKILL_ROOT = _resolve_skill_root()
_ILK_LOOP_SCRIPTS = _SKILL_ROOT / "ilk-loop" / "scripts"
if _ILK_LOOP_SCRIPTS.is_dir():
    sys.path.insert(0, str(_ILK_LOOP_SCRIPTS))

from ilk_paths import ilk_data_root, project_key  # noqa: E402
from plan_status import (  # noqa: E402
    extract_subplan_files,
    master_has_nonshipped,
    normalize_master_status,
    parse_frontmatter,
    reconcile_master_status,
)


def resolve_repo_path(project_dir: Path, key: str) -> str | None:
    """Resolve the real SOURCE repo path for a project data dir.

    1. ``<data>/runtime/launcher/last-launch.json`` → ``project_path``
       (written by every launch — the reliable primary source).
    2. ``<skill-root>/ilk-launcher/projects.json`` registry — match an
       entry whose path hashes to the same key (covers registered but
       never-launched projects).
    3. ``None`` if neither resolves.
    """
    last_launch = project_dir / "runtime" / "launcher" / "last-launch.json"
    if last_launch.is_file():
        try:
            data = json.loads(last_launch.read_text(encoding="utf-8-sig"))
            p = data.get("project_path")
            if p:
                return str(p)
        except (OSError, ValueError):
            pass

    registry = _SKILL_ROOT / "ilk-launcher" / "projects.json"
    if registry.is_file():
        try:
            data = json.loads(registry.read_text(encoding="utf-8-sig"))
            for entry in data.get("projects", []):
                ep = entry.get("path")
                if not ep:
                    continue
                try:
                    if project_key(Path(ep)) == key:
                        return str(ep)
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError):
            pass

    return None


def _parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-ish timestamp string. Returns None on failure."""
    if not raw:
        return None
    try:
        # Handle common formats: 2026-06-06, 2026-06-06T13:40:00+08:00
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None




def scan_projects() -> list[dict]:
    """Scan all projects and return those with a runnable master, FIFO-sorted.

    A project has a **runnable master** if:
    - An ``active`` master has ≥1 non-shipped sub-plan, OR
    - A ``queued`` master (with ≥1 non-shipped sub-plan) exists that
      promotion can activate.

    Projects where every master is ``shipped`` are excluded.

    ``oldest_queued_ts`` comes from the runnable master: active first
    (oldest non-shipped sub-plan timestamp), else the next-to-promote
    queued master.
    """
    root = ilk_data_root() / "projects"
    if not root.is_dir():
        return []

    results: list[dict] = []

    for project_dir in sorted(root.iterdir()):
        try:
            entry = _scan_one_project(project_dir)
        except Exception:
            # Resilience: this scanner runs unattended while other ilk loops
            # concurrently WRITE plan files in these same dirs. A read that
            # races a half-written master/sub-plan (or any other per-project
            # surprise) must skip just that project, never abort the whole
            # scan — an aborted scan emits a traceback that, under the
            # scheduler's `$ErrorActionPreference='Stop'` + `2>&1` merge,
            # killed the daemon outright (the 2026-06-30 three-project crash).
            continue
        if entry is not None:
            results.append(entry)

    # FIFO: oldest first; ties broken by project key for determinism
    results.sort(key=lambda r: (r["oldest_queued_ts"], r["key"]))
    return results


def _scan_one_project(project_dir: Path) -> dict | None:
    """Classify a single project; return its scan entry or None if not runnable.

    Split out of ``scan_projects`` so its caller can isolate per-project
    failures: one locked / partially-written plan file skips that project,
    it does not abort (and crash) the whole unattended scan.
    """
    if not project_dir.is_dir():
        return None
    plans_dir = project_dir / "plans"
    if not plans_dir.is_dir():
        return None

    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        return None

    # Reconcile pass: auto-flip any all-shipped master to status: shipped.
    # Idempotent + best-effort (tolerates concurrent writes).
    for m in masters:
        try:
            reconcile_master_status(m, plans_dir)
        except OSError:
            pass

    # --- pass 1: classify masters by status + runnable check ---
    active_ts: list[datetime] = []
    queued_ts: list[datetime] = []

    for master_path in masters:
        try:
            master_text = master_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue

        fm = parse_frontmatter(master_text)
        master_status = normalize_master_status(fm.get("status") or "")

        # `supervised_only` masters are never autonomously dispatched.
        # They edit the loop's own infrastructure (or are otherwise
        # sensitive) and must be run by a human via manual `/ilk`. The
        # manual path (loop_status) deliberately still selects them.
        if (fm.get("supervised_only") or "").strip().lower() in ("true", "yes", "1"):
            continue

        # Only masters with non-shipped sub-plans are runnable.
        if not master_has_nonshipped(master_path, plans_dir):
            continue

        # Collect per-sub-plan timestamps for FIFO ordering.
        master_sub_ts: list[datetime] = []
        for fname in extract_subplan_files(master_text):
            sub_path = plans_dir / fname
            if not sub_path.exists():
                continue
            try:
                sub_text = sub_path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            sub_fm = parse_frontmatter(sub_text)
            if sub_fm.get("status", "pending") == "shipped":
                continue
            ts = _parse_ts(sub_fm.get("last_updated", ""))
            if ts is None:
                try:
                    ts = datetime.fromtimestamp(sub_path.stat().st_mtime)
                except OSError:
                    ts = datetime.min
            master_sub_ts.append(ts)

        if not master_sub_ts:
            continue

        oldest_sub = min(master_sub_ts)

        if master_status == "active":
            active_ts.append(oldest_sub)
        elif master_status == "queued":
            queued_ts.append(oldest_sub)
        # Masters with other statuses (e.g. draft, paused) are
        # not runnable by the queue model — skip them. `draft` =
        # authored-but-not-yet-released (the /ilk-plan authoring gate); it
        # is intentionally invisible to the autonomous scheduler.
        # Note: legacy `pending` is normalized to `queued` above.

    # --- pass 2: decide inclusion + FIFO timestamp ---
    # A project is included iff it has a runnable master.
    if not active_ts and not queued_ts:
        return None

    # Active master wins for FIFO timestamp; else next-to-promote queued.
    has_active = bool(active_ts)
    if has_active:
        oldest = min(active_ts)
    else:
        oldest = min(queued_ts)

    return {
        "key": project_dir.name,
        "path": str(project_dir),
        "repo_path": resolve_repo_path(project_dir, project_dir.name),
        "oldest_queued_ts": oldest.isoformat(),
        "has_active_master": has_active,
    }


def main() -> int:
    # Force UTF-8 on stdout/stderr. The scheduler captures our stdout (the JSON
    # project list) and reads it back as UTF-8. On a zh-CN console Python
    # defaults stdout to GBK (cp936); a non-ASCII byte in any field then makes
    # `print(json.dumps(..., ensure_ascii=False))` die with UnicodeEncodeError,
    # whose traceback — under the scheduler's `$ErrorActionPreference='Stop'`
    # — killed the daemon. Reconfiguring to UTF-8 makes the JSON survive any
    # non-ASCII path/title. Mirrors run_local_checks.main().
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    projects = scan_projects()
    print(json.dumps(projects, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
