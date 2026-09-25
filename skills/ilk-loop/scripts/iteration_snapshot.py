#!/usr/bin/env python3
"""Snapshot dirty/untracked state before an iteration; diff against it after.

Two commands:

  take <repo> --out <file>
    Writes JSON: {"head": <sha>, "dirty": {<path>: <hash or "deleted">},
                  "untracked": [<paths>]}.
    Uses git status --porcelain=v1 -z --untracked-files=all.
    --exclude-standard semantics come from git itself.

  changed-since <repo> --snapshot <file>
    Prints, NUL-separated, every path that is dirty or untracked NOW and
    either was not in the snapshot or now has a different content hash.
    With a missing or unreadable snapshot: exits 2 and prints nothing on
    stdout.  Never prints an empty list for input it couldn't read.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _git_hash_object(repo: Path, path: str) -> str | None:
    """Return the blob hash of a working-tree file, or None if missing."""
    r = subprocess.run(
        ["git", "hash-object", path],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if r.returncode == 0:
        return r.stdout.strip()
    return None


def _git_status(repo: Path) -> list[tuple[str, str]]:
    """Parse git status --porcelain=v1 -z --untracked-files=all.

    Returns list of (status_code, path) tuples.
    The -z flag uses NUL as delimiter; XY status is the first two chars.
    """
    r = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    raw = r.stdout
    # -z format: entries separated by NUL.  Each entry is "XY<space>path".
    # The space between XY and path is part of the format, NOT a separator.
    # Do NOT strip — leading space is the first char of the XY status
    # (e.g. " M" means modified in working tree, not staged).
    entries: list[tuple[str, str]] = []
    parts = raw.split("\0")
    for part in parts:
        if not part or len(part) < 4:  # minimum: "XY p"
            continue
        xy = part[:2]
        path = part[3:]  # skip "XY " (status + space)
        entries.append((xy, path))
    return entries


def take(repo: Path, out: Path) -> None:
    """Snapshot dirty and untracked files in *repo* to *out* as JSON."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    head_sha = head.stdout.strip() if head.returncode == 0 else "(unknown)"

    entries = _git_status(repo)
    dirty: dict[str, str] = {}
    untracked: list[str] = []

    for xy, path in entries:
        if xy == "??":
            untracked.append(path)
        else:
            # Tracked file with working-tree change.
            h = _git_hash_object(repo, path)
            dirty[path] = h if h is not None else "deleted"

    snapshot = {
        "head": head_sha,
        "dirty": dirty,
        "untracked": untracked,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot), encoding="utf-8")


def _load_snapshot(path: Path) -> dict:
    """Load a snapshot JSON file.  Raises on bad content."""
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def changed_since(repo: Path, snapshot_path: Path) -> None:
    """Print NUL-separated paths changed since the snapshot.

    A path qualifies if it is dirty/untracked NOW and either was not in
    the snapshot or has a different content hash.

    Exit 0: printed something (or nothing changed — empty list is valid).
    Exit 2: snapshot missing or unreadable; nothing on stdout.
    """
    if not snapshot_path.is_file():
        sys.exit(2)

    try:
        snap = _load_snapshot(snapshot_path)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        sys.exit(2)

    snap_dirty: dict[str, str] = snap.get("dirty", {})
    snap_untracked: set[str] = set(snap.get("untracked", []))

    entries = _git_status(repo)
    result: list[str] = []

    for xy, path in entries:
        if xy == "??":
            # Untracked now.  Qualifies if it was NOT in the snapshot.
            if path not in snap_untracked:
                result.append(path)
        else:
            # Tracked dirty now.
            h = _git_hash_object(repo, path)
            current_hash = h if h is not None else "deleted"
            snap_hash = snap_dirty.get(path)
            if snap_hash is None:
                # Not in snapshot at all → new dirty file.
                result.append(path)
            elif snap_hash != current_hash:
                # Was dirty but content changed.
                result.append(path)
            # else: same hash → not changed since snapshot.

    sys.stdout.write("\0".join(result))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: iteration_snapshot.py {take|changed-since} ...",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "take":
        if len(sys.argv) != 5 or sys.argv[3] != "--out":
            print("usage: iteration_snapshot.py take <repo> --out <file>",
                  file=sys.stderr)
            sys.exit(1)
        repo = Path(sys.argv[2]).resolve()
        out = Path(sys.argv[4]).resolve()
        take(repo, out)

    elif cmd == "changed-since":
        if len(sys.argv) != 5 or sys.argv[3] != "--snapshot":
            print("usage: iteration_snapshot.py changed-since <repo> "
                  "--snapshot <file>", file=sys.stderr)
            sys.exit(1)
        repo = Path(sys.argv[2]).resolve()
        snapshot_path = Path(sys.argv[4]).resolve()
        changed_since(repo, snapshot_path)

    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()