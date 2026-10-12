"""Pin — DEFAULT_ENGINE lands on a worker home, never the primary.

``launch.sh`` sets ``DEFAULT_ENGINE="claude-worker"``.  The engine-precedence
comment (launch.sh:33-37) records why: the engine system exists to keep loops
OFF the primary account, so a forgot-the-flag spawn must not land there.

AC-2: with NO last-launch record and NO config, ``resolve_engine``'s floor
is ``claude-worker`` — asserted functionally (source the function the way
``test_launch_config_resolution.sh`` does), not by grepping the constant.

Written RED-first and green once the default moved.  It stays green as long
as the floor holds; a revert to the planner default turns it red again.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_LAUNCH_SH = _SKILLS / "ilk-launcher" / "scripts" / "launch.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_function(script: Path, fn_name: str) -> str:
    """Extract a bash function definition from a script via sed."""
    result = subprocess.run(
        ["bash", "-c", f"sed -n '/^{fn_name}()/,/^}}/p' \"{script}\""],
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _resolve_engine(project_path: str, cli_engine: str = "") -> str:
    """Call the real ``resolve_engine`` from ``launch.sh``.

    Sources the function and its dependencies in a subshell with all
    config / env sources removed so the test exercises the DEFAULT_ENGINE
    floor.
    """
    # Extract the functions resolve_engine depends on.  runner_script_for_engine
    # is the availability check: resolve_engine now refuses any engine whose
    # runner is not packaged, so the extraction has to carry it (and _SKILL_ROOT)
    # or the function cannot resolve a runner path at all.
    resolve_fn = _extract_function(_LAUNCH_SH, "resolve_engine")
    read_cfg_fn = _extract_function(_LAUNCH_SH, "read_project_config")
    get_ext_fn = _extract_function(_LAUNCH_SH, "get_external_plans_dir")
    runner_fn = _extract_function(_LAUNCH_SH, "runner_script_for_engine")

    # Read the actual DEFAULT_ENGINE from launch.sh so the test tracks
    # the source of truth rather than hardcoding a stale value.
    result_grep = subprocess.run(
        ["bash", "-c", f'grep "^DEFAULT_ENGINE=" "{_LAUNCH_SH}" | head -1'],
        capture_output=True, text=True, timeout=5,
        encoding="utf-8", errors="replace",
    )
    default_engine_line = result_grep.stdout.strip() or 'DEFAULT_ENGINE="claude"'

    script = textwrap.dedent(f"""\
        set -euo pipefail
        # Provide the globals resolve_engine reads.
        VALID_ENGINES="claude claude-worker claude-manager"
        {default_engine_line}
        _SKILL_ROOT="{_SKILLS}"
        ILK_DEFAULT_ENGINE=""
        # Stub read_project_config to return {{}} (no config).
        {get_ext_fn}
        {read_cfg_fn}
        {runner_fn}
        {resolve_fn}
        resolve_engine "{project_path}" "{cli_engine}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"resolve_engine exited {result.returncode}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
    return result.stdout.strip()


# ── AC-2: the floor is claude-worker ────────────────────────────────────────

class TestDefaultEngineFloor:
    """AC-2: with no config, no CLI flag, no env var, resolve_engine's floor
    is ``claude-worker`` — a forgot-the-flag spawn lands on a worker home."""

    def test_resolve_engine_floor_is_claude_worker(self, tmp_path: Path) -> None:
        """No CLI, no config, no ILK_DEFAULT_ENGINE → ``claude-worker``."""
        project = str(tmp_path / "empty-project")
        os.makedirs(project, exist_ok=True)
        engine = _resolve_engine(project, cli_engine="")
        assert engine == "claude-worker", (
            f"resolve_engine floor is {engine!r}, expected 'claude-worker'. "
            f"A forgot-the-flag spawn currently lands on the primary account."
        )

    def test_cli_engine_still_overrides(self, tmp_path: Path) -> None:
        """CLI --engine takes precedence over the floor (no regression).

        Uses a packaged engine as the override token: ``codex`` is now
        refused by resolve_engine's availability check (it has no runner),
        which is the behaviour ``test_a_launcher_advertises_only_available_
        engines.py`` pins.  The property under test here is still only that
        the CLI flag beats the DEFAULT_ENGINE floor.
        """
        project = str(tmp_path / "empty-project")
        os.makedirs(project, exist_ok=True)
        engine = _resolve_engine(project, cli_engine="claude-manager")
        assert engine == "claude-manager", (
            f"CLI override broken: resolve_engine returned {engine!r}, "
            f"expected 'claude-manager'."
        )

    def test_config_still_overrides_floor(self, tmp_path: Path) -> None:
        """A project config worker_engine overrides the floor."""
        project = tmp_path / "configured-project"
        project.mkdir(parents=True, exist_ok=True)
        # Write a config with worker_engine.
        import json
        (project / ".ilk-launch.json").write_text(
            json.dumps({"worker_engine": "claude-manager"}),
            encoding="utf-8",
        )
        # We need a modified resolver that reads the real config.
        resolve_fn = _extract_function(_LAUNCH_SH, "resolve_engine")
        read_cfg_fn = _extract_function(_LAUNCH_SH, "read_project_config")
        get_ext_fn = _extract_function(_LAUNCH_SH, "get_external_plans_dir")
        runner_fn = _extract_function(_LAUNCH_SH, "runner_script_for_engine")

        script = textwrap.dedent(f"""\
            set -euo pipefail
            VALID_ENGINES="claude claude-worker claude-manager"
            DEFAULT_ENGINE="claude"
            _SKILL_ROOT="{_SKILLS}"
            ILK_DEFAULT_ENGINE=""
            {get_ext_fn}
            {read_cfg_fn}
            {runner_fn}
            {resolve_fn}
            resolve_engine "{project}" ""
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode == 0, (
            f"resolve_engine with config exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        engine = result.stdout.strip()
        assert engine == "claude-manager", (
            f"Config override broken: resolve_engine returned {engine!r}, "
            f"expected 'claude-manager'."
        )
