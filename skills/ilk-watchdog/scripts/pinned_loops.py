#!/usr/bin/env python3
"""pinned_loops.py — distinguish pinned loops (run from a release) from unpinned ones.

A "pinned loop" is a ``run_ilk_loop_claude.(sh|ps1)`` process whose runner
lives under ``~/.ilk/releases/<tag>/`` and whose release dir contains
``skills/ilk-loop/scripts/RUN_PINS_RELEASE``.  Pinned loops run every helper
from that same release, so they are safe to ignore during a bounce or release
train — a mid-run release flip cannot corrupt them.

Stdlib only: no third-party imports.
"""
from __future__ import annotations

import os
import re
import sys

_RELEASE_DIR_RE = re.compile(
    r"(/\S*?/\.ilk/releases/[^/]+)/skills/ilk-loop/scripts/run_ilk_loop_claude\.(sh|ps1)"
)


def release_dir_of(command: str) -> str | None:
    """Extract the release dir from a runner command, or None if not a release runner."""
    m = _RELEASE_DIR_RE.search(command)
    return m.group(1) if m else None


def is_pinned(command: str, exists: callable = os.path.exists) -> bool:
    """Return True if the command is a release runner with the marker file."""
    d = release_dir_of(command)
    if d is None:
        return False
    return exists(os.path.join(d, "skills", "ilk-loop", "scripts", "RUN_PINS_RELEASE"))


def unpinned(
    entries: list[tuple[int, str]],
    exists: callable = os.path.exists,
) -> list[int]:
    """Return the pids whose command is NOT pinned.

    ``entries`` is a list of ``(pid, command)`` tuples.
    """
    return [pid for pid, cmd in entries if not is_pinned(cmd, exists)]


def _filter_stdin() -> None:
    """CLI: read ``pid<TAB>command`` lines on stdin, print unpinned pids."""
    entries: list[tuple[int, str]] = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        entries.append((pid, parts[1]))
    for pid in unpinned(entries):
        print(pid)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "filter":
        _filter_stdin()
    else:
        print("usage: pinned_loops.py filter", file=sys.stderr)
        sys.exit(2)
