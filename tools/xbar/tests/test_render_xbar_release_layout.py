"""The xbar "Start now" row resolves through ~/.ilk/current on a release host.

Part of sub-plan a-release-host-never-runs-from-the-clone.

  AC-9  With a fake HOME on the release layout (current -> releases/v9
        holding skills/ilk-runner/scripts/ilk-run.sh), a manually_runnable
        entry's "Start now" row has param1 under <home>/.ilk/current/.
        With no layout file it is the repo path (control).

All tests use tmp dirs; none touch the real ~/.ilk or the real xbar plugin.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RENDER_PY = _REPO_ROOT / "tools" / "xbar" / "render_xbar.py"


@pytest.mark.xfail(
    strict=True,
    reason="render_xbar release-layout resolution not yet implemented",
    raises=Exception,
)
def test_ac9_start_now_row_resolves_through_current(tmp_path: Path) -> None:
    """AC-9: on release layout, 'Start now' param1 is under ~/.ilk/current/."""
    # Build a fake release layout
    home = tmp_path / "home"
    home.mkdir()
    ilk = home / ".ilk"
    ilk.mkdir()
    releases = ilk / "releases"
    releases.mkdir()
    v9 = releases / "v9"
    v9.mkdir()
    # Create the run script in the release
    script_dir = v9 / "skills" / "ilk-runner" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "ilk-run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # current -> v9
    current = ilk / "current"
    current.symlink_to(v9)
    # Layout file
    (ilk / "layout").write_text("release\n", encoding="utf-8")

    # Import the renderer and check the "Start now" row
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_xbar", str(_RENDER_PY))
    mod = importlib.util.module_from_spec(spec)
    # Set env before import so module-level defaults resolve
    old_home = os.environ.get("HOME")
    old_releases = os.environ.get("ILK_RELEASES_ROOT")
    try:
        os.environ["HOME"] = str(home)
        os.environ.pop("ILK_RELEASES_ROOT", None)
        spec.loader.exec_module(mod)

        # Build a minimal manually_runnable entry
        entry = {
            "name": "test-project",
            "manually_runnable": True,
            "run_script": mod._DEFAULT_RUN_SCRIPT,
            "resume_script": mod._DEFAULT_RESUME_SCRIPT,
            "copy_script": mod._DEFAULT_COPY_SCRIPT,
        }
        # Check that the run script path goes through current
        assert str(current) in mod._DEFAULT_RUN_SCRIPT, (
            f"run script {mod._DEFAULT_RUN_SCRIPT!r} does not resolve through "
            f"~/.ilk/current (expected prefix {str(current)!r})"
        )
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_releases is not None:
            os.environ["ILK_RELEASES_ROOT"] = old_releases
        else:
            os.environ.pop("ILK_RELEASES_ROOT", None)


def test_ac9_control_no_layout_uses_repo_path(tmp_path: Path) -> None:
    """AC-9 (control): without a layout file, run script is repo-relative."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_xbar", str(_RENDER_PY))
    mod = importlib.util.module_from_spec(spec)
    old_home = os.environ.get("HOME")
    old_releases = os.environ.get("ILK_RELEASES_ROOT")
    try:
        # Point HOME at a dir with no .ilk/layout
        home = tmp_path / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ.pop("ILK_RELEASES_ROOT", None)
        spec.loader.exec_module(mod)
        # The default should be repo-relative
        assert "ilk-runner" in mod._DEFAULT_RUN_SCRIPT
        assert str(home) not in mod._DEFAULT_RUN_SCRIPT, (
            "run script should not resolve through HOME when no layout exists"
        )
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_releases is not None:
            os.environ["ILK_RELEASES_ROOT"] = old_releases
        else:
            os.environ.pop("ILK_RELEASES_ROOT", None)