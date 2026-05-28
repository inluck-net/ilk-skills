#!/usr/bin/env python3
"""
migrate_project_runtime_dirs — move <project>/.ilk-launcher/ and
<project>/.ilk-watchdog/ into ~/.ilk-data/projects/<key>/runtime/.

Default mode is dry-run: prints what would happen but touches nothing.
Pass --apply to perform the migration.

NOTE: If you also plan to remove legacy log directories under
<skill-root>/ilk-loop/logs/, run preserve_active_run.py FIRST to
archive active-run evidence:

    python3 <skill-root>/ilk-loop/scripts/preserve_active_run.py --project-path .
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Reach into skills/ilk-loop/scripts/ for ilk_paths.py — this tool
# lives outside the skill tree on purpose (see tools/README.md).
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
from ilk_paths import (  # noqa: E402
    git_root,
    project_key,
    external_launcher_dir,
    external_watchdog_dir,
)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _find_live_pid(src_dir: Path) -> int | None:
    """Return the live PID from src_dir/running.pid or src_dir/watchdog.pid, or None."""
    for name in ("running.pid", "watchdog.pid"):
        pid_file = src_dir / name
        if not pid_file.is_file():
            continue
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            continue
        if _pid_alive(pid):
            return pid
    return None


def _migrate_one_project(project_path: Path, apply: bool) -> int:
    root = git_root(project_path)
    if root is None:
        print(f"error: no .git directory found above {project_path}", file=sys.stderr)
        return 2

    key = project_key(root)
    pairs = [
        (root / ".ilk-launcher", external_launcher_dir(key), "launcher"),
        (root / ".ilk-watchdog", external_watchdog_dir(key), "watchdog"),
    ]

    plan: list[dict] = []
    for src, dst, label in pairs:
        if not src.is_dir():
            continue
        live_pid = _find_live_pid(src)
        if live_pid is not None:
            print(
                f"error: {root} has a live {label} PID {live_pid}; stop it before migrating",
                file=sys.stderr,
            )
            return 2
        files = sorted([p for p in src.rglob("*") if p.is_file()])
        plan.append({
            "src": src,
            "dst": dst,
            "label": label,
            "files": files,
        })

    if not plan:
        print(f"nothing to migrate for {root}")
        return 0

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== ilk runtime migration ({mode}) ===")
    print(f"project key:   {key}")
    print(f"project root:  {root}")
    for entry in plan:
        print(f"  [{entry['label']}] {len(entry['files'])} file(s) -> {entry['dst']}")
        for f in entry["files"][:20]:
            rel = f.relative_to(entry["src"])
            print(f"      move {rel}")
        if len(entry["files"]) > 20:
            print(f"      ... and {len(entry['files']) - 20} more")

    if not apply:
        print("\ndry-run complete. Re-run with --apply to migrate.")
        return 0

    for entry in plan:
        dst = entry["dst"]
        dst.mkdir(parents=True, exist_ok=True)
        for f in entry["files"]:
            rel = f.relative_to(entry["src"])
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target))
        # Try to remove the source directory if now empty
        try:
            entry["src"].rmdir()
            print(f"  removed empty {entry['src']}")
        except OSError:
            # Directory not empty — surface leftover paths
            leftovers = sorted([p for p in entry["src"].rglob("*")])
            if leftovers:
                print(f"  warn: {entry['src']} not empty after move; leftovers:")
                for p in leftovers:
                    print(f"      {p.relative_to(entry['src'])}")
            else:
                print(f"  warn: could not remove {entry['src']} (permissions?)")

    print("\nmigration complete.")
    return 0


def _load_all_projects() -> list[Path]:
    projects_json = Path.home() / ".cursor" / "skills" / "ilk-launcher" / "projects.json"
    if not projects_json.is_file():
        print(f"error: {projects_json} not found", file=sys.stderr)
        return []
    try:
        data = json.loads(projects_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON in {projects_json}: {e}", file=sys.stderr)
        return []
    projects = data.get("projects", [])
    paths: list[Path] = []
    for p in projects:
        path = p.get("path")
        if path:
            paths.append(Path(path))
    return paths


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="path inside the project (default: cwd)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="iterate all projects in ~/.cursor/skills/ilk-launcher/projects.json",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="print what would happen without changing anything (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration",
    )
    args = ap.parse_args(argv)

    # argparse default for --dry-run is True, but if --apply is passed we want apply.
    apply = args.apply

    if args.all:
        projects = _load_all_projects()
        if not projects:
            return 2
        rc = 0
        for p in projects:
            proj_rc = _migrate_one_project(p, apply)
            if proj_rc != 0:
                rc = proj_rc
        return rc

    return _migrate_one_project(args.project, apply)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
