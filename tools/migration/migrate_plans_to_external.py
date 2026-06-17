#!/usr/bin/env python3
"""
migrate_plans_to_external — move <git_root>/docs/plans/ to
~/.ilk-data/projects/<key>/plans/.

Default mode is dry-run: prints what would happen but touches nothing.
Pass --apply to perform the migration.

Behaviour
---------
1. Resolve the .git root that contains --project (default: cwd).
2. Compute project_key, derive destination = external_plans_dir(key).
3. Refuse to run if:
   - a running ilk-loop for this project is detected (PID file alive);
   - destination already exists and contains files (use --force-overwrite
     to merge into it; the migration will not overwrite individual files
     that already exist on the destination — those are reported and
     skipped).
4. Copy every file under <git_root>/docs/plans/ into the destination,
   preserving sub-directory structure (e.g. ship-reports/, findings/,
   archive/). Tracked files are recorded so they can be `git rm`ed in
   step 5; untracked files are simply copied.
5. With --apply, run `git rm` on tracked source files (you commit the
   deletion yourself afterwards). Untracked source files are left in
   place — re-run with --delete-source-untracked to also remove them
   (irreversible, so guarded behind its own flag).

The script never deletes anything from the destination. Worst case is
a partial copy — fix the cause, re-run with --apply (the per-file
already-exists guard makes that safe).

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Reach into skills/ilk-loop/scripts/ for ilk_paths.py — this tool
# lives outside the skill tree on purpose (see tools/README.md).
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
from ilk_paths import (  # noqa: E402
    git_root, project_key, external_plans_dir,
    project_data_dir,
)


def find_running_pid_file(project_path: Path) -> Path | None:
    """
    Detect a live ilk-loop for the project. We look in the launcher's
    runtime dir for any pid file matching this project name. The file
    layout is dictated by ilk-launcher (one PID file per running loop).
    """
    candidates = [
        Path.home() / ".cursor" / "skills" / "ilk-launcher" / "runtime",
        Path.home() / ".ilk-data" / "runtime",  # future home, harmless if absent
    ]
    name_hint = project_path.name.lower()
    for d in candidates:
        if not d.is_dir():
            continue
        for pid_file in d.glob("*.pid"):
            try:
                pid = int(pid_file.read_text().strip())
            except (OSError, ValueError):
                continue
            if not _pid_alive(pid):
                continue
            # Crude name match: pid file stem contains the project name.
            if name_hint in pid_file.stem.lower():
                return pid_file
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: tasklist filter by PID
            cp = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            return f'"{pid}"' in cp.stdout
        # POSIX
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def list_tracked(repo: Path, paths: list[Path]) -> set[Path]:
    """Return the subset of `paths` that git considers tracked, as
    absolute paths."""
    if not paths:
        return set()
    rels = [str(p.relative_to(repo)) for p in paths]
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", *rels],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return set()
    if cp.returncode != 0:
        # fall back to a non-erroring ls-files
        cp = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--", *rels],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    tracked = set()
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        tracked.add((repo / line).resolve())
    return tracked


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="path inside the project (default: cwd)")
    ap.add_argument("--apply", action="store_true",
                    help="perform the migration (default is dry-run)")
    ap.add_argument("--force-overwrite", action="store_true",
                    help="proceed even when the destination already has files; "
                         "files that exist on both sides are skipped, never overwritten")
    ap.add_argument("--delete-source-untracked", action="store_true",
                    help="after a successful --apply, delete untracked source "
                         "files too (irreversible)")
    ap.add_argument("--ignore-running", action="store_true",
                    help="skip the pid-file check (use only if you're sure no "
                         "loop is active)")
    args = ap.parse_args(argv)

    root = git_root(args.project)
    if root is None:
        print(f"error: no .git directory found above {args.project}", file=sys.stderr)
        return 2

    src = root / "docs" / "plans"
    if not src.is_dir():
        print(f"error: source dir does not exist: {src}", file=sys.stderr)
        print("nothing to migrate.", file=sys.stderr)
        return 2

    key = project_key(root)
    dst = external_plans_dir(key)
    proj_dir = project_data_dir(key)

    # Safety: refuse when a loop is alive for this project.
    if not args.ignore_running:
        pid_file = find_running_pid_file(root)
        if pid_file is not None:
            print(f"error: live ilk-loop detected (pid file: {pid_file})", file=sys.stderr)
            print("stop the loop first, or pass --ignore-running if you are sure.", file=sys.stderr)
            return 3

    # Collect work units.
    files = sorted([p for p in src.rglob("*") if p.is_file()])
    if not files:
        print(f"source has no files: {src}")
        return 0

    tracked = list_tracked(root, files)

    # Plan: decide per-file outcome.
    plan: list[dict] = []
    for f in files:
        rel = f.relative_to(src)
        target = dst / rel
        if target.exists():
            outcome = "skip-exists"
        else:
            outcome = "copy"
        plan.append({
            "src": str(f),
            "dst": str(target),
            "rel": str(rel),
            "tracked": f.resolve() in tracked,
            "outcome": outcome,
        })

    # Destination guard.
    if dst.exists() and any(dst.iterdir()) and not args.force_overwrite:
        non_empty = sum(1 for _ in dst.rglob("*") if _.is_file())
        print(f"error: destination is not empty ({dst} has {non_empty} files)", file=sys.stderr)
        print("re-run with --force-overwrite to merge (existing files are never overwritten).", file=sys.stderr)
        # still print the plan so the operator can sanity-check
        _print_plan(plan, src, dst, key, applied=False)
        return 4

    _print_plan(plan, src, dst, key, applied=args.apply)

    if not args.apply:
        print("\ndry-run complete. Re-run with --apply to migrate.")
        return 0

    # Apply: create destination, copy, then git rm tracked sources.
    proj_dir.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)

    copied = skipped = 0
    for entry in plan:
        s = Path(entry["src"]); t = Path(entry["dst"])
        if entry["outcome"] == "copy":
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t)
            copied += 1
        else:
            skipped += 1

    tracked_rels = [
        str(Path(e["src"]).relative_to(root))
        for e in plan if e["tracked"]
    ]
    if tracked_rels:
        cmd = ["git", "-C", str(root), "rm", "--", *tracked_rels]
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if cp.returncode != 0:
            print(f"error: git rm failed:\n{cp.stderr}", file=sys.stderr)
            print("destination is populated but tracked sources were NOT removed; "
                  "fix and re-run, or remove them manually.", file=sys.stderr)
            return 5

    untracked_files = [Path(e["src"]) for e in plan if not e["tracked"]]
    deleted_untracked = 0
    if args.delete_source_untracked:
        for f in untracked_files:
            try:
                f.unlink()
                deleted_untracked += 1
            except OSError as ex:
                print(f"warn: could not delete untracked {f}: {ex}", file=sys.stderr)

    print()
    print(f"copied:      {copied} file(s)")
    print(f"skipped:     {skipped} file(s) (already at destination)")
    print(f"git-rm'd:    {len(tracked_rels)} tracked source(s)")
    print(f"untracked:   {len(untracked_files)} (deleted: {deleted_untracked})")
    print()
    print(f"destination: {dst}")
    print()
    if tracked_rels:
        print("Next: review and commit the deletions:")
        print(f"  git -C {root} status")
        print(f"  git -C {root} commit -m \"chore(plans): migrate to ~/.ilk-data\"")
    if untracked_files and not args.delete_source_untracked:
        print("Untracked source files were NOT deleted. Re-run with "
              "--delete-source-untracked to remove them, or do it manually.")
    return 0


def _print_plan(plan: list[dict], src: Path, dst: Path, key: str, applied: bool) -> None:
    label = "APPLY" if applied else "DRY-RUN"
    print(f"=== ilk plans migration ({label}) ===")
    print(f"project key:   {key}")
    print(f"source:        {src}")
    print(f"destination:   {dst}")
    print(f"file count:    {len(plan)}")
    by_outcome: dict[str, int] = {}
    by_tracked = {True: 0, False: 0}
    for e in plan:
        by_outcome[e["outcome"]] = by_outcome.get(e["outcome"], 0) + 1
        by_tracked[e["tracked"]] += 1
    print(f"  by outcome:  {by_outcome}")
    print(f"  by tracked:  tracked={by_tracked[True]}, untracked={by_tracked[False]}")
    print()
    for e in plan[:50]:
        flag = "T" if e["tracked"] else "u"
        print(f"  [{flag}] {e['outcome']:11s} {e['rel']}")
    if len(plan) > 50:
        print(f"  ... and {len(plan) - 50} more")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
