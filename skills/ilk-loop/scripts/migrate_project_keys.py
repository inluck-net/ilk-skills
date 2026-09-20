#!/usr/bin/env python3
"""Re-key on-disk project state after the C1 ``project_key`` fix.

Why this script exists
----------------------
``ilk_paths.project_key`` used to append a 7-char sha1 **only** when the
lowercased path slug exceeded 80 characters.  Below that cap the lossy slug
*was* the key, so distinct paths collided (C1 of MASTER-2026-09-08b).  The fix
makes the hash unconditional and takes it over the exact, case-preserving
absolute path.

That changes the key of a registered project whenever the slug was under the
cap (it gains a suffix it never had), and of an over-cap one whenever its path
holds an uppercase character (the hash input stopped being lowercased).  On
this box that is all 11 projects with state on disk, since every real path
begins ``/Users/...``; an all-lowercase over-cap path keeps its key and is
reported ``unchanged-key``.  Every project's state lives in one directory,
``ilk_data_root()/projects/<key>/`` (``plans/``, ``runtime/``, ``logs/``), so
the migration is one directory rename per project.  Until it runs, every tool
resolves to a new, empty state directory.

Operator contract
-----------------
* **Dry-run by default.**  ``--apply`` is opt-in, mirroring ``install.sh``.
* **Refuses to run while any loop is live.**  A loop resolves its paths
  through ``project_key`` mid-run; renaming under it strands the run.
  Liveness comes from ``selfmod_worktree._find_live_ilk_pids``: ``pgrep``
  exit 1 means "none", exit >1 raises, and a raised probe is treated as
  *unknown* and refuses — a broken probe and a true zero must never be
  byte-identical.
* **Idempotent.**  A second run after a successful one plans zero moves and
  exits 0.
* **Never merges.**  If two registered projects shared one legacy directory
  (the collision this batch fixes), that directory cannot be split
  automatically and the migration refuses it by name.  Likewise a destination
  that already exists alongside a live source is a refusal, not an overwrite.
* **Registry-driven, never pattern-driven.**  Only paths listed in the
  launcher's ``projects.json`` are touched.  ``~/.ilk-data/projects/`` also
  holds hundreds of pytest ``tmp_path`` debris directories; sweeping by
  pattern would move them.  Unrecognised directories are counted and left
  alone.

Usage::

    python3 migrate_project_keys.py                 # dry run, human output
    python3 migrate_project_keys.py --json          # dry run, machine output
    python3 migrate_project_keys.py --apply         # perform the renames

Exit codes::

    0  clean — plan is safe (dry run), or every move applied
    1  refused — at least one project cannot be migrated safely
    2  unsafe — a loop is live, or liveness could not be determined

Sub-plan: a-project-key-that-cannot-collide, step 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ilk_paths import ilk_data_root, project_key  # noqa: E402
from register_project import _default_registry_path  # noqa: E402

# ── the legacy transform ─────────────────────────────────────────────────────
#
# Reproduced verbatim from ``ilk_paths.project_key`` at 5b3df35 (the commit
# before the C1 fix).  It lives HERE, not in ilk_paths, because it is a dead
# concept everywhere except this migration: once the rename has run, nothing
# should be able to compute an old key by accident.

_LEGACY_PUNCT = re.compile(r"[^a-z0-9]+")
_LEGACY_CAP = 80
_LEGACY_HASH_LEN = 7


def legacy_project_key(root: str | Path) -> str:
    """The pre-C1 key for *root* — the directory name to migrate FROM.

    Note the lowercasing of ``abs_str`` before hashing: that is what made the
    over-cap suffix change (``-3bd7ec7`` -> ``-bc07129`` for this repo's own
    path) even for projects that were already on the hash path.
    """
    abs_str = str(Path(root).resolve()).lower()
    slug = _LEGACY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= _LEGACY_CAP:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:_LEGACY_HASH_LEN]
    return slug[: _LEGACY_CAP - _LEGACY_HASH_LEN - 1].rstrip("-") + "-" + h


# ── plan model ───────────────────────────────────────────────────────────────

#: An entry needs no action and nothing is wrong with it.
ACTION_MOVE = "move"
ACTION_ALREADY_MIGRATED = "already-migrated"
ACTION_NO_STATE = "no-state"
ACTION_UNCHANGED_KEY = "unchanged-key"
ACTION_REFUSE = "refuse"

#: Why an entry is refused.  Each is a case the operator must resolve by hand.
REFUSE_SHARED_LEGACY_DIR = "shared-legacy-dir"
REFUSE_DESTINATION_EXISTS = "destination-exists"
REFUSE_DUPLICATE_DESTINATION = "duplicate-destination"


@dataclass
class Entry:
    """One registered project's migration verdict."""

    name: str
    path: str
    old_key: str
    new_key: str
    action: str
    reason: str = ""
    detail: str = ""
    #: Set once the move has actually been performed.
    applied: bool = False
    #: True when the source directory holds git worktrees whose registration
    #: the rename invalidates (see ``WORKTREE_REPAIR_HINT``).
    has_worktrees: bool = False

    @property
    def refused(self) -> bool:
        return self.action == ACTION_REFUSE

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "old_key": self.old_key,
            "new_key": self.new_key,
            "action": self.action,
            "reason": self.reason,
            "detail": self.detail,
            "applied": self.applied,
            "has_worktrees": self.has_worktrees,
        }


@dataclass
class Plan:
    """The whole migration: what would move, what is refused, what is ignored."""

    entries: list[Entry] = field(default_factory=list)
    #: Directories under ``projects/`` that no registered project claims.
    #: Counted, never touched — most of them are pytest ``tmp_path`` debris.
    unclaimed: list[str] = field(default_factory=list)
    projects_root: Path = Path()

    @property
    def moves(self) -> list[Entry]:
        return [e for e in self.entries if e.action == ACTION_MOVE]

    @property
    def refusals(self) -> list[Entry]:
        return [e for e in self.entries if e.refused]

    def to_json(self) -> dict:
        return {
            "projects_root": str(self.projects_root),
            "entries": [e.to_json() for e in self.entries],
            "unclaimed_count": len(self.unclaimed),
            "unclaimed": self.unclaimed,
            "move_count": len(self.moves),
            "refusal_count": len(self.refusals),
        }


WORKTREE_REPAIR_HINT = (
    "git worktree registrations store absolute paths on both sides; after the "
    "rename run `git -C <repo> worktree repair` (or prune) for each affected repo"
)


# ── registry ─────────────────────────────────────────────────────────────────


def read_registry(registry: Path) -> list[dict]:
    """Return the ``projects`` list from the launcher registry.

    An unreadable or unparseable registry raises rather than yielding an empty
    list: "zero projects to migrate" and "I could not read the registry" must
    not look the same.
    """
    raw = registry.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "projects" not in data:
        raise ValueError(
            f"{registry}: expected an object with a 'projects' key, "
            f"got {type(data).__name__} with keys {sorted(data)[:5] if isinstance(data, dict) else '-'}"
        )
    projects = data["projects"]
    if not isinstance(projects, list):
        raise ValueError(f"{registry}: 'projects' is {type(projects).__name__}, expected a list")
    return projects


# ── planning ─────────────────────────────────────────────────────────────────


def _has_worktrees(state_dir: Path) -> bool:
    wt = state_dir / "runtime" / "launcher" / "worktrees"
    return wt.is_dir() and any(wt.iterdir())


def plan_migration(projects: list[dict], data_root: Path) -> Plan:
    """Compute the per-project verdict without touching the filesystem.

    *projects* is the registry's ``projects`` list (``{name, path}`` dicts).
    *data_root* is an ``ilk_data_root()``-shaped directory; state lives under
    ``<data_root>/projects/<key>``.
    """
    projects_root = Path(data_root) / "projects"
    plan = Plan(projects_root=projects_root)

    # Pass 1 — compute both keys for every registered project.
    computed: list[tuple[dict, str, str]] = []
    for proj in projects:
        path = str(proj.get("path", "")).strip()
        if not path:
            continue
        computed.append((proj, legacy_project_key(path), project_key(Path(path))))

    # Two projects sharing one OLD key is the C1 collision itself: a single
    # directory holding two projects' state.  It cannot be split by any rule
    # this script knows, so both sides are refused by name.
    old_key_owners: dict[str, list[str]] = {}
    new_key_owners: dict[str, list[str]] = {}
    for proj, old_key, new_key in computed:
        old_key_owners.setdefault(old_key, []).append(str(proj.get("path", "")))
        new_key_owners.setdefault(new_key, []).append(str(proj.get("path", "")))

    for proj, old_key, new_key in computed:
        name = str(proj.get("name") or Path(str(proj.get("path", ""))).name)
        path = str(proj.get("path", ""))
        src = projects_root / old_key
        dst = projects_root / new_key
        entry = Entry(name=name, path=path, old_key=old_key, new_key=new_key,
                      action=ACTION_MOVE)
        entry.has_worktrees = _has_worktrees(src)

        sharers = [p for p in old_key_owners[old_key] if p != path]
        dup_dests = [p for p in new_key_owners[new_key] if p != path]

        if old_key == new_key:
            # Defensive: the new construction always differs from the old one,
            # but a future re-run of this script after a partial migration must
            # not rename a directory onto itself.
            entry.action = ACTION_UNCHANGED_KEY
        elif dup_dests:
            entry.action = ACTION_REFUSE
            entry.reason = REFUSE_DUPLICATE_DESTINATION
            entry.detail = (
                f"new key {new_key!r} is also claimed by: {', '.join(sorted(dup_dests))}"
            )
        elif sharers and src.exists():
            entry.action = ACTION_REFUSE
            entry.reason = REFUSE_SHARED_LEGACY_DIR
            entry.detail = (
                f"{src} holds the state of {len(sharers) + 1} projects that collided "
                f"under the old key (also: {', '.join(sorted(sharers))}); split it by "
                f"hand before migrating"
            )
        elif not src.exists():
            entry.action = ACTION_ALREADY_MIGRATED if dst.exists() else ACTION_NO_STATE
        elif dst.exists():
            entry.action = ACTION_REFUSE
            entry.reason = REFUSE_DESTINATION_EXISTS
            entry.detail = (
                f"both {src} and {dst} exist; moving would merge two state trees"
            )

        plan.entries.append(entry)

    # Pass 2 — everything on disk that no registered project accounts for.
    claimed = {e.old_key for e in plan.entries} | {e.new_key for e in plan.entries}
    if projects_root.is_dir():
        plan.unclaimed = sorted(
            d.name for d in projects_root.iterdir()
            if d.is_dir() and d.name not in claimed
        )

    return plan


# ── liveness ─────────────────────────────────────────────────────────────────


def live_loop_pids() -> list[int]:
    """PIDs of live ilk runners.  Raises ``RuntimeError`` if the probe fails."""
    from selfmod_worktree import _find_live_ilk_pids  # noqa: PLC0415

    return _find_live_ilk_pids()


# ── applying ─────────────────────────────────────────────────────────────────


def apply_migration(plan: Plan) -> list[str]:
    """Perform every ``move`` in *plan*.  Returns a list of error strings.

    The destination-absent check is re-done immediately before each rename, so
    a plan computed against a tree that changed underneath refuses rather than
    merges.
    """
    errors: list[str] = []
    for entry in plan.moves:
        src = plan.projects_root / entry.old_key
        dst = plan.projects_root / entry.new_key
        if not src.exists():
            errors.append(f"{entry.name}: source vanished before the move: {src}")
            continue
        if dst.exists():
            errors.append(f"{entry.name}: destination appeared before the move: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            errors.append(f"{entry.name}: {src} -> {dst} failed: {exc}")
            continue
        entry.applied = True
    return errors


# ── reporting ────────────────────────────────────────────────────────────────


def render(plan: Plan, *, applied: bool, errors: list[str]) -> str:
    out: list[str] = []
    verb = "migrated" if applied else "would migrate"
    out.append(f"Projects root: {plan.projects_root}")
    out.append(
        f"{len(plan.entries)} registered projects — {len(plan.moves)} {verb}, "
        f"{len(plan.refusals)} refused, "
        f"{sum(1 for e in plan.entries if e.action == ACTION_ALREADY_MIGRATED)} already migrated, "
        f"{sum(1 for e in plan.entries if e.action == ACTION_NO_STATE)} with no state on disk"
    )
    out.append(
        f"{len(plan.unclaimed)} directories under projects/ belong to no registered "
        f"project — left untouched (mostly pytest tmp_path debris)"
    )

    if plan.moves:
        out.append("")
        out.append("MOVES" if applied else "PLANNED MOVES (dry run)")
        for e in plan.moves:
            mark = "[done]" if e.applied else ("[fail]" if applied else "     -")
            out.append(f"  {mark} {e.name}")
            out.append(f"          {e.old_key}")
            out.append(f"       -> {e.new_key}")
            if e.has_worktrees:
                out.append(f"          ! holds git worktrees — {WORKTREE_REPAIR_HINT}")

    if plan.refusals:
        out.append("")
        out.append("REFUSED")
        for e in plan.refusals:
            out.append(f"  {e.name} [{e.reason}]")
            out.append(f"      {e.detail}")

    if errors:
        out.append("")
        out.append("ERRORS")
        out.extend(f"  {msg}" for msg in errors)

    if not applied and plan.moves and not plan.refusals:
        out.append("")
        out.append("Dry run — nothing was moved.  Re-run with --apply on a quiet box.")

    return "\n".join(out)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-key on-disk ilk project state after the C1 project_key fix.")
    ap.add_argument("--apply", action="store_true",
                    help="perform the renames (default: dry run)")
    ap.add_argument("--data-root", default=None,
                    help="override ilk_data_root() (testing)")
    ap.add_argument("--registry", default=None,
                    help="override the launcher projects.json path (testing)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the plan as JSON")
    args = ap.parse_args(argv)

    registry = Path(args.registry) if args.registry else _default_registry_path()
    if not registry.exists():
        print(f"registry not found: {registry}", file=sys.stderr)
        return 2
    try:
        projects = read_registry(registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"registry unreadable: {exc}", file=sys.stderr)
        return 2

    data_root = Path(args.data_root).expanduser() if args.data_root else ilk_data_root()
    plan = plan_migration(projects, data_root)

    errors: list[str] = []
    applied = False

    if args.apply:
        # Liveness is checked ONLY on the apply path: a dry run reads nothing a
        # live loop can corrupt, and refusing to even *plan* would make the
        # script useless on the machine that needs it.
        try:
            pids = live_loop_pids()
        except RuntimeError as exc:
            print(f"refusing to apply — liveness unknown: {exc}", file=sys.stderr)
            return 2
        if pids:
            print(
                "refusing to apply — "
                f"{len(pids)} ilk loop process(es) live: {', '.join(map(str, pids))}. "
                "Re-keying under a running loop strands it mid-run; stop the loop "
                "and the scheduler first.",
                file=sys.stderr,
            )
            return 2
        if plan.refusals:
            print(
                f"refusing to apply — {len(plan.refusals)} project(s) cannot be "
                "migrated safely; resolve them by hand first.",
                file=sys.stderr,
            )
        else:
            errors = apply_migration(plan)
            applied = True

    if args.as_json:
        payload = plan.to_json()
        payload["applied"] = applied
        payload["errors"] = errors
        print(json.dumps(payload, indent=2))
    else:
        print(render(plan, applied=applied, errors=errors))

    if errors:
        return 1
    if plan.refusals:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
