"""A ``selfmod_worktree.py merge`` shim that never touches the repo.

The deferred-merge tests need ``selfmod_worktree.py merge`` to return a chosen
exit code.  They used to get it by OVERWRITING the real script in the repo
under test with a shim, backing the original up to ``.bak`` and moving it back
in a ``finally``.  Two failures, both measured 2026-10-03:

* Under pytest-xdist every other worker that ran the real script while a shim
  was in place got the shim.  When the two shim-installing files overlapped,
  the second backup copied the FIRST shim over the real backup, and the real
  1216-line script was lost for good (a 9-line shim left in the tree; 60
  importer tests red).
* Serially, a test killed between install and restore (a pytest-timeout, a
  Ctrl-C) leaves the shim in the live clone, which IS the deploy on a clone
  host.

The runner finds the script at ``${_SKILL_ROOT}/ilk-loop/scripts/``, so the
shim lives in a throwaway mirror of the skill root instead: every entry is a
symlink to the real one except the shimmed script.  The test points
``_SKILL_ROOT`` at the mirror after sourcing the runner.
"""
from __future__ import annotations

import textwrap
from pathlib import Path


def make_shim_skill_root(base: Path, real_skill_root: Path, merge_rc: int) -> Path:
    """Build ``base/shim-skill-root`` whose selfmod_worktree.py exits *merge_rc*
    on ``merge`` and delegates every other subcommand to the real script."""
    root = base / "shim-skill-root"
    root.mkdir(parents=True)
    for entry in real_skill_root.iterdir():
        if entry.name != "ilk-loop":
            (root / entry.name).symlink_to(entry)
    loop = root / "ilk-loop"
    loop.mkdir()
    real_loop = real_skill_root / "ilk-loop"
    for entry in real_loop.iterdir():
        if entry.name != "scripts":
            (loop / entry.name).symlink_to(entry)
    scripts = loop / "scripts"
    scripts.mkdir()
    real_scripts = real_loop / "scripts"
    for entry in real_scripts.iterdir():
        if entry.name != "selfmod_worktree.py":
            (scripts / entry.name).symlink_to(entry)
    real = real_scripts / "selfmod_worktree.py"
    (scripts / "selfmod_worktree.py").write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Shim: override merge exit code, delegate create/remove.\"\"\"
            import subprocess, sys
            REAL = {str(real)!r}
            if len(sys.argv) > 1 and sys.argv[1] == "merge":
                print("[shim] merge returning exit {merge_rc}")
                sys.exit({merge_rc})
            else:
                sys.exit(subprocess.call([sys.executable, REAL] + sys.argv[1:]))
        """),
        encoding="utf-8",
    )
    return root
