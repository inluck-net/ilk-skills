"""Tests: a release-launched run pins to its own release dir.

Builds a fake release tree in ``tmp_path`` (``rel/.ilk/releases/v9.9.9/…``)
with a COPY of the runner and its sourced helpers, sets
``ILK_SKILL_HOME=<farm>/skills``, and checks the resolved env vars.

AC-1: with marker → ILK_SKILL_HOME == physical release skills dir.
AC-2: without marker → ILK_SKILL_HOME == farm path (today's behaviour).
AC-3: runner NOT under .ilk/releases/ → keeps today's behaviour even with marker.
AC-4: RUN_PINS_RELEASE exists in the repo.
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


def _copy_scripts(src: Path, dst: Path) -> None:
    """Copy the runner and its sourced helpers into *dst*."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in (
        "run_ilk_loop_claude.sh",
        "_ilk_skill_root.sh",
        "steer_hook.sh",
        "_ilk_marker.sh",
        "_ilk_data_dir.sh",
    ):
        shutil.copy2(src / name, dst / name)


def _build_release_tree(
    tmp_path: Path, *, marker: bool = True
) -> dict[str, Path]:
    """Build ``rel/.ilk/releases/v9.9.9/skills/ilk-loop/scripts/`` and a farm.

    Returns dict with keys: release_scripts, farm, release_dir.
    """
    rel = tmp_path / "rel"
    scripts_dst = rel / ".ilk" / "releases" / "v9.9.9" / "skills" / "ilk-loop" / "scripts"
    _copy_scripts(_SCRIPTS, scripts_dst)
    if marker:
        (scripts_dst / "RUN_PINS_RELEASE").write_text(
            "Marker: this runner pins to its own release.\n"
            "Read by bounce_daemons.sh and release_train.py.\n"
        )

    # Farm: a different directory the symlink would resolve to.
    farm = tmp_path / "farm" / "skills" / "ilk-loop"
    farm.mkdir(parents=True)
    # Symlink the farm to the real skills dir so _ilk_skill_root has a valid target.
    (farm / "scripts").symlink_to(_SCRIPTS)

    release_dir = rel / ".ilk" / "releases" / "v9.9.9"
    return {"release_scripts": scripts_dst, "farm": farm, "release_dir": release_dir}


def _env(home: Path, farm: Path) -> dict[str, str]:
    """Env with HOME, ILK_DATA_HOME pinned, ILK_SKILL_HOME = farm/skills."""
    return {
        "PATH": _PATH,
        "HOME": str(home),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
        "ILK_SKILL_HOME": str(farm.parent.parent),  # farm/skills
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _parse_kv(output: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from stdout."""
    result: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


# ── AC-1: with marker → release pins ─────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="a run follows the current symlink",
)
def test_release_run_pins_with_marker(tmp_path: Path) -> None:
    """AC-1: with marker, ILK_SKILL_HOME == physical release skills dir."""
    tree = _build_release_tree(tmp_path, marker=True)
    scripts = tree["release_scripts"]
    farm = tree["farm"]
    release_dir = tree["release_dir"]

    script = f"""
source '{scripts}/run_ilk_loop_claude.sh'
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
echo "ILK_RUN_RELEASE_DIR=$ILK_RUN_RELEASE_DIR"
"""
    env = _env(tmp_path, farm)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    expected_skills = str(release_dir / "skills")
    expected_release = str(release_dir)

    assert kv["ILK_SKILL_HOME"] == expected_skills, (
        f"ILK_SKILL_HOME={kv['ILK_SKILL_HOME']!r} != {expected_skills!r}"
    )
    assert kv["ILK_RUN_RELEASE_DIR"] == expected_release, (
        f"ILK_RUN_RELEASE_DIR={kv['ILK_RUN_RELEASE_DIR']!r} != {expected_release!r}"
    )


# ── AC-2: without marker → farm behaviour ────────────────────────────────────

def test_release_run_without_marker_keeps_farm(tmp_path: Path) -> None:
    """AC-2: without marker, ILK_SKILL_HOME == farm path (today's behaviour)."""
    tree = _build_release_tree(tmp_path, marker=False)
    scripts = tree["release_scripts"]
    farm = tree["farm"]

    script = f"""
source '{scripts}/run_ilk_loop_claude.sh'
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
echo "ILK_RUN_RELEASE_DIR=${{ILK_RUN_RELEASE_DIR:-<empty>}}"
"""
    env = _env(tmp_path, farm)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    expected_farm = str(farm.parent.parent)  # farm/skills
    assert kv["ILK_SKILL_HOME"] == expected_farm, (
        f"ILK_SKILL_HOME={kv['ILK_SKILL_HOME']!r} != {expected_farm!r}"
    )
    assert kv["ILK_RUN_RELEASE_DIR"] == "<empty>", (
        f"ILK_RUN_RELEASE_DIR should be empty, got {kv['ILK_RUN_RELEASE_DIR']!r}"
    )


# ── AC-3: non-release runner keeps farm even with marker ─────────────────────

def test_non_release_runner_ignores_marker(tmp_path: Path) -> None:
    """AC-3: runner NOT under .ilk/releases/ keeps farm behaviour with marker."""
    # Build a fake "clone" dir — not under .ilk/releases/.
    clone_scripts = tmp_path / "clone" / "skills" / "ilk-loop" / "scripts"
    _copy_scripts(_SCRIPTS, clone_scripts)
    # Put the marker there anyway — it should be ignored.
    (clone_scripts / "RUN_PINS_RELEASE").write_text("marker\n")

    farm = tmp_path / "farm" / "skills" / "ilk-loop"
    farm.mkdir(parents=True)
    (farm / "scripts").symlink_to(_SCRIPTS)

    script = f"""
source '{clone_scripts}/run_ilk_loop_claude.sh'
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
echo "ILK_RUN_RELEASE_DIR=${{ILK_RUN_RELEASE_DIR:-<empty>}}"
"""
    env = _env(tmp_path, farm)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, f"source failed: {result.stderr}"
    kv = _parse_kv(result.stdout)

    expected_farm = str(farm.parent.parent)
    assert kv["ILK_SKILL_HOME"] == expected_farm, (
        f"ILK_SKILL_HOME={kv['ILK_SKILL_HOME']!r} != {expected_farm!r}"
    )
    assert kv["ILK_RUN_RELEASE_DIR"] == "<empty>", (
        f"ILK_RUN_RELEASE_DIR should be empty, got {kv['ILK_RUN_RELEASE_DIR']!r}"
    )


# ── AC-4: RUN_PINS_RELEASE exists in repo ────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="marker file does not exist at base",
)
def test_run_pins_release_marker_exists() -> None:
    """AC-4: RUN_PINS_RELEASE exists in the repo."""
    marker = _SCRIPTS / "RUN_PINS_RELEASE"
    assert marker.is_file(), f"Missing marker: {marker}"