"""Pin that a ship-integrity check that cannot RUN reverts nothing.

gh-resolve run 20261010-003018: a release prune deleted the run's pinned
release dir mid-iteration.  test_ship_integrity checks the script once at entry,
then calls it per sub-plan after minutes of gates; python's "can't open file"
(exit 2) was read as a violation and reverted a legitimately shipped sub-plan
(launcher log :4291-4292).

AC-1: the script vanishes between the entry check and the call -> the shipped
      sub-plan stays shipped, and stderr says the check is UNAVAILABLE.
AC-2 (control): the same red gate with the script present -> reverted to
      in-progress, as before.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_ilk_loop_claude.sh"
SKILLS = RUNNER.parents[2]
_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _shipped_subplan(plans: Path, slug: str) -> Path:
    sp = plans / f"{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        "local_checks:\n"
        "  - command: python3 -c 'raise SystemExit(1)'\n"
        "    timeout: 60\n"
        "---\n\n"
        f"# {slug}\n\n### Step 0 — do the thing\n\nBody.\n",
        encoding="utf-8",
    )
    return sp


def _status_of(sp: Path) -> str:
    m = re.search(r"^status:\s*(\S+)", sp.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else "<none>"


def _skill_farm(tmp_path: Path, *, vanish: bool) -> Path:
    """A skills root of symlinks to the real one; ship_integrity.py optionally
    deletes itself and fails the way python does on a missing file."""
    farm = tmp_path / "skills"
    farm.mkdir()
    for entry in SKILLS.iterdir():
        if entry.name != "ilk-loop":
            os.symlink(entry, farm / entry.name)
    loop = farm / "ilk-loop"
    loop.mkdir()
    for entry in (SKILLS / "ilk-loop").iterdir():
        if entry.name != "scripts":
            os.symlink(entry, loop / entry.name)
    scripts = loop / "scripts"
    scripts.mkdir()
    for entry in (SKILLS / "ilk-loop" / "scripts").iterdir():
        if entry.name != "ship_integrity.py":
            os.symlink(entry, scripts / entry.name)
    si = scripts / "ship_integrity.py"
    if vanish:
        si.write_text(
            "import os, sys\n"
            "os.unlink(__file__)\n"
            "sys.stderr.write(\"python3: can't open file '%s': [Errno 2] No such file or directory\\n\" % __file__)\n"
            "sys.exit(2)\n")
    else:
        os.symlink(SKILLS / "ilk-loop" / "scripts" / "ship_integrity.py", si)
    return farm


def _run(tmp_path: Path, *, vanish: bool) -> tuple[subprocess.CompletedProcess, Path]:
    plans = tmp_path / "plans"
    plans.mkdir()
    sp = _shipped_subplan(plans, "red-this-iteration")
    lc = tmp_path / "local_checks_results.jsonl"
    lc.write_text(json.dumps({"slug": "red-this-iteration", "step": 0,
                              "outcome": "fail", "exit_code": 1}) + "\n")
    farm = _skill_farm(tmp_path, vanish=vanish)
    script = (f"export ILK_DOTSOURCE_ONLY=1; source '{RUNNER}'; _SKILL_ROOT='{farm}'; "
              f"set +e; test_ship_integrity '{plans}' '{lc}'; echo \"RC=$?\"")
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, cwd=str(tmp_path),
                          env={"ILK_DOTSOURCE_ONLY": "1", "PATH": _PATH, "HOME": str(tmp_path)})
    return proc, sp


def test_a_vanished_check_reverts_nothing(tmp_path: Path) -> None:
    proc, sp = _run(tmp_path, vanish=True)
    assert _status_of(sp) == "shipped", proc.stderr[-2000:]
    assert "check UNAVAILABLE" in proc.stderr, proc.stderr[-2000:]
    assert "ship-integrity VIOLATION" not in proc.stderr, proc.stderr[-2000:]


def test_control_a_present_check_still_reverts_a_red_gate(tmp_path: Path) -> None:
    proc, sp = _run(tmp_path, vanish=False)
    assert _status_of(sp) == "in-progress", proc.stderr[-2000:]
    assert "check UNAVAILABLE" not in proc.stderr
