"""Red-first tests: a run started through ``current`` keeps its release after a flip.

Each AC sources one script via ``<tmp>/current/skills/…``, flips the symlink
from v1 to v2, and checks that the resolved paths still point at v1.

AC-1..AC-5 fail today (logical ``pwd``, empty ``ILK_SKILL_HOME``, no scheduler
guard).  AC-6 and AC-7 are controls that pass today and after.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _HERE.parent.parent.parent.parent  # worktree root

# ── helpers ──────────────────────────────────────────────────────────────────

_PATH = os.environ.get("PATH", "")


def _copy_skills(src: Path, dst: Path) -> None:
    """Copy the skills tree, ignoring __pycache__."""
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__"),
    )


@pytest.fixture(scope="module")
def _release_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build two releases and a ``current`` pointer.

    Returns dict with keys: releases, current, v1, v2, skills_src.
    """
    root = tmp_path_factory.mktemp("release-fixture")
    releases = root / "releases"
    releases.mkdir()

    # v1 and v2 are independent copies of the skills tree.
    skills_src = _REPO / "skills"
    v1 = releases / "v1"
    v2 = releases / "v2"
    _copy_skills(skills_src, v1)
    _copy_skills(skills_src, v2)

    # current -> v1
    current = root / "current"
    current.symlink_to(v1)

    return {
        "releases": releases,
        "current": current,
        "v1": v1,
        "v2": v2,
        "skills_src": skills_src,
    }


def _env_no_skill_home(home: Path) -> dict[str, str]:
    """Env with HOME, ILK_DATA_HOME pinned, no ILK_SKILL_HOME."""
    return {
        "PATH": _PATH,
        "HOME": str(home),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
        # Explicitly unset so the script doesn't inherit from the test runner.
        "ILK_SKILL_HOME": "",
    }


def _flip_current(current: Path, releases: Path, target: str) -> None:
    """Atomic flip: current -> releases/<target>."""
    new_link = current.parent / "current.new"
    new_link.symlink_to(releases / target)
    # mv -f is atomic on the same filesystem.
    subprocess.run(
        ["mv", "-f", str(new_link), str(current)],
        check=True, timeout=10,
    )


def _parse_kv(output: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from stdout."""
    result = {}
    for line in output.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


# ── AC-1 (runner) ────────────────────────────────────────────────────────────

def test_runner_pins_release_after_flip(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-1: sourcing the runner via ``current`` pins v1 after a flip."""
    fix = _release_fixture
    current = fix["current"]
    v1 = fix["v1"]

    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{current}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
echo "_SKILL_ROOT=$_SKILL_ROOT"
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    # Flip to v2.
    _flip_current(fix["current"], fix["releases"], "v2")

    skill_root = kv["_SKILL_ROOT"]
    skill_home = kv["ILK_SKILL_HOME"]

    # Must be the v1 realpath, not through current.
    v1_skills = str(v1)
    assert v1_skills in skill_root, (
        f"_SKILL_ROOT={skill_root!r} does not contain v1 path {v1_skills!r}"
    )
    assert v1_skills in skill_home, (
        f"ILK_SKILL_HOME={skill_home!r} does not contain v1 path {v1_skills!r}"
    )
    # Must not contain /current/ (the logical path).
    assert "/current/" not in skill_root, (
        f"_SKILL_ROOT still goes through current: {skill_root!r}"
    )
    assert "/current/" not in skill_home, (
        f"ILK_SKILL_HOME still goes through current: {skill_home!r}"
    )


# ── AC-2 (watchdog) ──────────────────────────────────────────────────────────

def test_watchdog_pins_release_after_flip(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-2: sourcing the watchdog via ``current`` pins v1 after a flip."""
    fix = _release_fixture
    current = fix["current"]
    v1 = fix["v1"]

    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{current}/skills/ilk-watchdog/scripts/watchdog.sh'
echo "_SKILL_ROOT=$_SKILL_ROOT"
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    _flip_current(fix["current"], fix["releases"], "v2")

    skill_root = kv["_SKILL_ROOT"]
    skill_home = kv["ILK_SKILL_HOME"]

    v1_skills = str(v1)
    assert v1_skills in skill_root, (
        f"_SKILL_ROOT={skill_root!r} does not contain v1 path {v1_skills!r}"
    )
    assert v1_skills in skill_home, (
        f"ILK_SKILL_HOME={skill_home!r} does not contain v1 path {v1_skills!r}"
    )
    assert "/current/" not in skill_root
    assert "/current/" not in skill_home


# ── AC-3 (scheduler) ─────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: scheduler does not pin by realpath, no dot-source guard")
def test_scheduler_pins_release_after_flip(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-3: sourcing the scheduler via ``current`` pins v1 after a flip.

    The scheduler today runs the daemon on source (no guard).  We set HOME
    to tmp, pass ``--dry-run --max-dispatches 0``, and wrap in ``gtimeout 20``
    so the red case fails fast instead of hanging.
    """
    fix = _release_fixture
    current = fix["current"]
    v1 = fix["v1"]

    # The scheduler acquires a pidfile and runs the daemon on source.
    # We use ILK_DOTSOURCE_ONLY=1 (after the guard is added) or
    # gtimeout + --dry-run --max-dispatches 0 to avoid hanging.
    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{current}/skills/ilk-watchdog/scripts/scheduler.sh'
echo "_SKILL_ROOT=$_SKILL_ROOT"
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
echo "SCAN_SCRIPT=$SCAN_SCRIPT"
echo "WATCHDOG_SCRIPT=$WATCHDOG_SCRIPT"
echo "LAUNCH_SCRIPT=$LAUNCH_SCRIPT"
echo "PROMOTE_SCRIPT=$PROMOTE_SCRIPT"
"""
    env = _env_no_skill_home(tmp_path)
    # Use gtimeout if available, else timeout, else just run.
    timeout_cmd = "gtimeout"
    if shutil.which("gtimeout") is None:
        timeout_cmd = "timeout" if shutil.which("timeout") else ""

    full_script = script
    if timeout_cmd:
        full_script = f"{timeout_cmd} 20 bash -c {repr(script)}"

    result = subprocess.run(
        ["bash", "-c", full_script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    # With the guard, exit 0 and we get the paths.
    # Without the guard, the scheduler runs and we get a timeout or hang.
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    _flip_current(fix["current"], fix["releases"], "v2")

    v1_skills = str(v1)
    for key in ("_SKILL_ROOT", "ILK_SKILL_HOME", "SCAN_SCRIPT",
                "WATCHDOG_SCRIPT", "LAUNCH_SCRIPT", "PROMOTE_SCRIPT"):
        val = kv.get(key, "")
        assert v1_skills in val, (
            f"{key}={val!r} does not contain v1 path {v1_skills!r}"
        )
        assert "/current/" not in val, (
            f"{key}={val!r} still goes through current"
        )


# ── AC-4 (children inherit) ──────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: ILK_SKILL_HOME not exported by runner")
def test_children_inherit_pinned_root(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-4: after the runner pins, a child process inherits the v1 realpath."""
    fix = _release_fixture
    current = fix["current"]
    v1 = fix["v1"]

    # Source the runner, then flip, then check a child process.
    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{current}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
# Flip after sourcing.
ln -sfn '{fix["releases"]}/v2' '{fix["current"]}.new' && mv -f '{fix["current"]}.new' '{fix["current"]}'
# Now check what a child sees.
child_out=$(bash -c "source '$ILK_SKILL_HOME/ilk-loop/scripts/_ilk_skill_root.sh'; ilk_skill_root")
echo "CHILD_ROOT=$child_out"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source or child failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    child_root = kv.get("CHILD_ROOT", "")
    v1_skills = str(v1)
    assert v1_skills in child_root, (
        f"CHILD_ROOT={child_root!r} does not contain v1 path {v1_skills!r}"
    )
    assert "/current/" not in child_root, (
        f"child still goes through current: {child_root!r}"
    )


# ── AC-5 (the next run takes the new release) ────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: fresh process resolves through current (logical)")
def test_fresh_process_gets_new_release(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-5: a fresh process after the flip gets v2."""
    fix = _release_fixture
    current = fix["current"]
    v2 = fix["v2"]

    # Flip to v2 first.
    _flip_current(fix["current"], fix["releases"], "v2")

    # Fresh process sources through current (now v2).
    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{current}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
echo "_SKILL_ROOT=$_SKILL_ROOT"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    skill_root = kv["_SKILL_ROOT"]
    v2_skills = str(v2)
    assert v2_skills in skill_root, (
        f"_SKILL_ROOT={skill_root!r} does not contain v2 path {v2_skills!r}"
    )
    assert "/current/" not in skill_root, (
        f"_SKILL_ROOT still goes through current: {skill_root!r}"
    )


# ── AC-6 (control, symlink farm) ─────────────────────────────────────────────

def test_symlink_farm_resolves_to_toolkit_clone(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-6: sourcing through a symlink farm resolves to the toolkit clone.

    This passes today and after — the farm is a symlink to this tree's
    skills, so _resolve_toolkit_clone finds the git toplevel.
    """
    fix = _release_fixture

    # Create a farm: <tmp>/farm/skills/ilk-loop -> this tree's skills/ilk-loop
    farm = tmp_path / "farm" / "skills"
    farm.mkdir(parents=True)
    (farm / "ilk-loop").symlink_to(fix["skills_src"] / "ilk-loop")

    script = f"""
ILK_DOTSOURCE_ONLY=1
source '{farm}/ilk-loop/scripts/run_ilk_loop_claude.sh'
echo "_SKILL_ROOT=$_SKILL_ROOT"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    # _resolve_toolkit_clone should print this tree's git toplevel.
    # On macOS, resolve the path to compare.
    expected = str(_REPO)
    skill_root = kv["_SKILL_ROOT"]
    # The skill root should resolve to the clone's skills dir.
    # We check that the parent of skills is the repo.
    assert "skills" in skill_root, (
        f"_SKILL_ROOT={skill_root!r} does not contain 'skills'"
    )


# ── AC-7 (control, selfmod) ──────────────────────────────────────────────────

def test_selfmod_override_wins(
    _release_fixture: dict[str, Path], tmp_path: Path,
) -> None:
    """AC-7: SELFMOD_WORKTREE_PATH sets ILK_SKILL_HOME to that dir's skills.

    This passes today and after — the selfmod override is unchanged.
    """
    fix = _release_fixture

    # Create a selfmod worktree dir with a skills subdirectory.
    selfmod = tmp_path / "selfmod-worktree" / "skills"
    selfmod.mkdir(parents=True)
    # Copy a minimal skill so the dir exists.
    _copy_skills(fix["skills_src"] / "ilk-loop", selfmod / "ilk-loop")

    script = f"""
ILK_DOTSOURCE_ONLY=1
SELFMOD_WORKTREE_PATH='{tmp_path / "selfmod-worktree"}'
source '{fix["current"]}/skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
"""
    env = _env_no_skill_home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    expected = str(selfmod)
    actual = kv["ILK_SKILL_HOME"]
    assert expected in actual, (
        f"ILK_SKILL_HOME={actual!r} does not contain selfmod path {expected!r}"
    )