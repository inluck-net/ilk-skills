"""Find projects whose sentinel is a fresh success and whose queue is empty.

Called by ``scheduler.sh offer_release_trains`` on every pass — including
passes where the dispatch scan returned 0 projects — so a scheduler-started
release train no longer depends on permits landing in the 38 s window before
the project drops out of the scan.

Sub-plan: an-empty-queue-still-starts-the-train.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Same sys.path setup as scheduler_scan.py and release_train_dispatch.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_train_dispatch import sentinel_all_shipped  # noqa: E402
from scheduler_scan import resolve_repo_path  # noqa: E402


def train_candidates(data_root: Path, exclude_keys: set[str]) -> list[dict]:
    """Return projects whose sentinel is a fresh success with no started marker.

    Each entry: ``{"key", "path", "repo", "run_id"}``.
    Skips unreadable entries (never raises).
    """
    projects_dir = data_root / "projects"
    if not projects_dir.is_dir():
        return []

    results: list[dict] = []
    try:
        entries = sorted(projects_dir.iterdir())
    except OSError:
        return []

    for d in entries:
        if not d.is_dir():
            continue
        key = d.name
        if key in exclude_keys:
            continue

        sentinel = d / "runtime" / "launcher" / "last-exit.json"
        if not sentinel.is_file():
            continue

        try:
            data = json.loads(sentinel.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue

        run_id = data.get("run_id", "")
        if not run_id:
            continue

        if not sentinel_all_shipped(sentinel):
            continue

        marker = d / "runtime" / "release" / f"{run_id}.started"
        if marker.exists():
            continue

        repo = resolve_repo_path(d, key)
        if not repo:
            continue

        results.append({"key": key, "path": str(d), "repo": repo, "run_id": run_id})

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="List train candidates")
    parser.add_argument("--exclude-keys", default="",
                        help="Comma-separated keys to exclude (from the scan)")
    args = parser.parse_args()

    exclude = set()
    if args.exclude_keys:
        exclude = {k.strip() for k in args.exclude_keys.split(",") if k.strip()}

    try:
        from ilk_paths import ilk_data_root
        data_root = ilk_data_root()
    except Exception:
        data_root = Path.home() / ".ilk-data"

    for candidate in train_candidates(data_root, exclude):
        print(json.dumps(candidate))


if __name__ == "__main__":
    main()