#!/usr/bin/env python3
"""
preserve_active_run — copy active-run evidence into an archive directory
before log cleanup or migration removes legacy skill-root logs.

Preserves:
  - Per-iteration logs  (iter-NN.log, iter-NN.log.jsonl, heads-*.tmp)
  - JSONL summary entries for this run_id
  - last-exit.json sentinel
  - last-launch.json launcher metadata

Archive layout:
  ~/.ilk-data/projects/<key>/logs/archive/<run-id>/
      iter-NN.log
      iter-NN.log.jsonl
      .ilk-loop.log          (filtered: only this run_id)
      last-exit.json
      last-launch.json

Idempotent: running twice does not duplicate or corrupt files.

Usage:
  python3 preserve_active_run.py                     # cwd walk-up
  python3 preserve_active_run.py --project-path /path
  python3 preserve_active_run.py --run-id 20260528-103919
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Resolve sibling ilk_paths module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import (
    archive_run_dir,
    external_launcher_dir,
    external_logs_dir,
    external_runtime_dir,
    find_project_root,
    project_key,
)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _copy_file(src: Path, dst: Path) -> bool:
    """Copy a single file. Returns True if copied."""
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_run_iter_logs(run_log_dir: Path, archive: Path) -> int:
    """Copy all iter-NN.log* and heads-*.tmp from run_log_dir to archive.

    Returns number of files copied.
    """
    if not run_log_dir.is_dir():
        return 0
    count = 0
    for src in sorted(run_log_dir.iterdir()):
        if src.is_file():
            dst = archive / src.name
            shutil.copy2(src, dst)
            count += 1
    return count


def _filter_jsonl_for_run(jsonl_path: Path, run_id: str, dst: Path) -> int:
    """Copy JSONL entries matching run_id from jsonl_path to dst.

    Returns number of records written.
    """
    if not jsonl_path.exists():
        return 0
    count = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with (
        jsonl_path.open("r", encoding="utf-8", errors="replace") as src_fh,
        dst.open("w", encoding="utf-8") as dst_fh,
    ):
        for line in src_fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if rec.get("run_id") == run_id:
                dst_fh.write(stripped + "\n")
                count += 1
    return count


def preserve(project_path: Path, run_id: str | None = None) -> Path:
    """Preserve active-run evidence. Returns the archive directory path.

    Raises FileNotFoundError if project cannot be resolved or no launch
    metadata exists.
    """
    root, _kind = find_project_root(project_path)
    if root is None:
        raise FileNotFoundError(
            f"No project root (.git or .ilk-meta.json) found from {project_path}"
        )
    key = project_key(root)

    # Resolve run_id from last-launch.json if not provided
    launcher_dir = external_launcher_dir(key)
    last_launch = _read_json(launcher_dir / "last-launch.json")

    if run_id is None:
        if last_launch is None:
            raise FileNotFoundError(
                "No --run-id and no last-launch.json found. "
                "Cannot determine which run to preserve."
            )
        run_id = last_launch.get("run_id")
        if not run_id:
            # Fallback: extract from log_file path
            # e.g. .../launcher/ilk-skills-20260528-103919.log → 20260528-103919
            import re
            log_file = last_launch.get("log_file", "")
            m = re.search(r"(\d{8}-\d{6})(?:\.log)?$", log_file)
            if m:
                run_id = m.group(1)
        if not run_id:
            raise FileNotFoundError(
                "last-launch.json exists but has no run_id field "
                "and could not extract from log_file path."
            )

    archive = archive_run_dir(key, run_id)
    archive.mkdir(parents=True, exist_ok=True)

    # 1. Per-iteration logs
    logs_dir = external_logs_dir(key)
    run_log_dir = logs_dir / "runs" / run_id
    iter_count = _copy_run_iter_logs(run_log_dir, archive)

    # 2. JSONL summary (filtered for this run_id)
    jsonl_src = logs_dir / ".ilk-loop.log"
    jsonl_dst = archive / ".ilk-loop.log"
    jsonl_count = _filter_jsonl_for_run(jsonl_src, run_id, jsonl_dst)

    # 3. Sentinel (last-exit.json)
    runtime_dir = external_runtime_dir(key)
    sentinel_src = runtime_dir / "last-exit.json"
    sentinel_copied = _copy_file(sentinel_src, archive / "last-exit.json")

    # 4. Launcher metadata (last-launch.json)
    launch_src = launcher_dir / "last-launch.json"
    launch_copied = _copy_file(launch_src, archive / "last-launch.json")

    print(f"[preserve] archive: {archive}")
    print(f"[preserve] iter files: {iter_count}")
    print(f"[preserve] JSONL records: {jsonl_count}")
    print(f"[preserve] sentinel: {'yes' if sentinel_copied else 'no'}")
    print(f"[preserve] launch metadata: {'yes' if launch_copied else 'no'}")

    return archive


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve active-run evidence before log cleanup."
    )
    parser.add_argument(
        "--project-path", default=None,
        help="Project root. Default: cwd walk-up."
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Run ID (YYYYMMDD-HHMMSS). Default: read from last-launch.json."
    )
    args = parser.parse_args()

    project_path = Path(args.project_path).resolve() if args.project_path else Path.cwd()

    try:
        preserve(project_path, args.run_id)
    except FileNotFoundError as e:
        print(f"[preserve] error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
