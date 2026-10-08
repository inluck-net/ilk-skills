"""Tests that a pinned run's gates rewrite ``~/.ilk/current/skills`` paths
to the run's own release directory (``ILK_SKILL_HOME``).

AC-1 through AC-4 from sub-plan gates-never-pin-current step 0.
All marked xfail until the rewrite is implemented in step 1.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_local_checks as rlc  # noqa: E402


# ---------------------------------------------------------------------------
# AC-1: absolute HOME-prefixed and tilde paths are rewritten when pinned
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="gates follow the current symlink")
def test_pin_rewrites_home_prefix() -> None:
    """``$HOME/.ilk/current/skills/…`` → ``$ILK_SKILL_HOME/…``."""
    env = {
        "HOME": "/h",
        "ILK_RUN_RELEASE_DIR": "/r/v1",
        "ILK_SKILL_HOME": "/r/v1/skills",
    }
    cmd = "python3 /h/.ilk/current/skills/ilk-loop/scripts/x.py --a"
    result = rlc.pin_gate_command(cmd, env)
    assert result == "python3 /r/v1/skills/ilk-loop/scripts/x.py --a"


@pytest.mark.xfail(strict=True, reason="gates follow the current symlink")
def test_pin_rewrites_tilde_prefix() -> None:
    """``~/.ilk/current/skills/…`` → ``$ILK_SKILL_HOME/…``."""
    env = {
        "HOME": "/h",
        "ILK_RUN_RELEASE_DIR": "/r/v1",
        "ILK_SKILL_HOME": "/r/v1/skills",
    }
    cmd = "~/.ilk/current/skills/y"
    result = rlc.pin_gate_command(cmd, env)
    assert result == "/r/v1/skills/y"


# ---------------------------------------------------------------------------
# AC-2: without ILK_RUN_RELEASE_DIR the command is unchanged
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="gates follow the current symlink")
def test_no_pin_when_release_dir_unset() -> None:
    """Without ``ILK_RUN_RELEASE_DIR`` the command passes through."""
    env = {"HOME": "/h"}
    cmd_home = "python3 /h/.ilk/current/skills/ilk-loop/scripts/x.py --a"
    cmd_tilde = "~/.ilk/current/skills/y"
    assert rlc.pin_gate_command(cmd_home, env) == cmd_home
    assert rlc.pin_gate_command(cmd_tilde, env) == cmd_tilde


# ---------------------------------------------------------------------------
# AC-3: relative paths are never rewritten (pinned or not)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="gates follow the current symlink")
def test_relative_path_unchanged_when_pinned() -> None:
    """A relative ``skills/…`` path stays as-is."""
    env = {
        "HOME": "/h",
        "ILK_RUN_RELEASE_DIR": "/r/v1",
        "ILK_SKILL_HOME": "/r/v1/skills",
    }
    cmd = "python3 skills/ilk-loop/scripts/x.py"
    assert rlc.pin_gate_command(cmd, env) == cmd


# ---------------------------------------------------------------------------
# AC-4: wiring — run_one rewrites the command before execution
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="gates follow the current symlink")
def test_run_one_pins_gate_command(tmp_path: Path) -> None:
    """A gate through ``run_one`` prints the rewritten path, not ``current``."""
    release = tmp_path / "release"
    skills = release / "skills"
    skills.mkdir(parents=True)
    env_patch = {
        "HOME": str(tmp_path / "home"),
        "ILK_RUN_RELEASE_DIR": str(release),
        "ILK_SKILL_HOME": str(skills),
    }
    target = str(tmp_path / "home" / ".ilk" / "current" / "skills" / "z")
    gate_cmd = f"echo {target}"
    with patch.dict("os.environ", env_patch, clear=False):
        r = rlc.run_one({"command": gate_cmd}, "step", tmp_path)
    assert r.passed, f"gate failed: {r.error}"
    expected = str(skills / "z")
    assert expected in r.stdout_tail, (
        f"expected {expected!r} in stdout, got {r.stdout_tail!r}"
    )