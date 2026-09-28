"""collect.py imports loop_status from its own tree, not the installed one.

``collect.py`` put ``~/.cursor/skills/ilk-loop/scripts`` -- an install.sh
symlink into whichever checkout was installed last -- FIRST on sys.path and
imported ``loop_status``, and through it ``plan_status``.  Every module
imported afterwards got the live clone's copy: a suite run from a worktree
collected the worktree's tests against the clone's scripts (2026-09-29,
ImportError for a function only the worktree had).

The decoy install stands in for the clone.  With the defect, collect imports
the decoy.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_COLLECT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def test_collect_prefers_its_sibling_loop_status(tmp_path: Path) -> None:
    decoy = tmp_path / ".cursor" / "skills" / "ilk-loop" / "scripts"
    decoy.mkdir(parents=True)
    (decoy / "loop_status.py").write_text("DECOY = True\n")
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import collect; "
        "m = collect._loop_status; "
        "print(getattr(m, '__file__', None)); print(getattr(m, 'DECOY', False))"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe, str(_COLLECT_DIR)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    file_line, decoy_line = r.stdout.strip().splitlines()[-2:]
    assert decoy_line == "False", f"collect imported the installed decoy: {file_line}"
    assert Path(file_line).resolve().parent == (_COLLECT_DIR.parent.parent / "ilk-loop" / "scripts").resolve()
