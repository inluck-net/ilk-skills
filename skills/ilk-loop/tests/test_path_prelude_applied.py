"""Tests for path_prelude application in run_local_checks and ship_config.

AC-1: A gate whose executable lives only in a prelude dir runs.
AC-2: No prelude ⇒ command unchanged (regression guard).
AC-3: ship_config validates path_prelude as optional string.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SHIP_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-ship" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHIP_SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402
from ship_config import (  # noqa: E402
    MalformedConfig,
    ShipConfig,
    load_ship_config,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project root with .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── AC-1: prelude makes a gate executable resolvable ────────────────────────

class TestPathPreludeApplied:
    """run_local_checks.run_one prepends path_prelude when configured."""

    def test_gate_with_prelude_resolves_executable(self, tmp_path: Path) -> None:
        """AC-1: A gate whose executable lives only in a prelude dir runs."""
        # Create a throwaway executable in a custom bin dir.
        custom_bin = tmp_path / "custom_bin"
        custom_bin.mkdir()
        helper = custom_bin / "my-helper"
        helper.write_text('#!/bin/sh\necho "hello from helper"\n', encoding="utf-8")
        helper.chmod(0o755)

        # Configure path_prelude to add that dir.
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "path_prelude": f'export PATH="{custom_bin}:$PATH"',
                },
            },
        })

        # The gate command uses the helper — should resolve via prelude.
        result = rlc.run_one(
            {"command": "my-helper", "timeout": 10},
            "step", project,
        )
        assert result.passed is True, f"expected pass, got: exit={result.exit_code} stderr={result.stderr_tail}"


# ── AC-2: prelude is read from config (regression guard) ────────────────────

class TestNoPreludeUnchanged:
    """When no prelude is configured, run_one reads the config and finds none."""

    def test_run_one_reads_prelude_from_config(self, tmp_path: Path) -> None:
        """AC-2: run_one reads path_prelude from .ilk-launch.json.

        Regression guard — run_one must consult the project config for
        path_prelude. Today it does not (no config reading at all), so a
        command whose executable is ONLY in the prelude dir fails.
        """
        # Create a custom executable that's NOT on the default PATH.
        custom_bin = tmp_path / "custom_bin"
        custom_bin.mkdir()
        helper = custom_bin / "prelude-cmd"
        helper.write_text('#!/bin/sh\necho "prelude works"\n', encoding="utf-8")
        helper.chmod(0o755)

        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "path_prelude": f'export PATH="{custom_bin}:$PATH"',
                },
            },
        })

        # This should pass once run_one reads the prelude; fails today.
        result = rlc.run_one(
            {"command": "prelude-cmd", "timeout": 10},
            "step", project,
        )
        assert result.passed is True, (
            f"expected prelude-cmd to resolve via path_prelude, "
            f"got exit={result.exit_code} stderr={result.stderr_tail}"
        )


# ── AC-3: ship_config validates path_prelude ────────────────────────────────

class TestPathPreludeSchema:
    """ship_config validates path_prelude as optional string."""

    def test_path_prelude_non_string_is_malformed(self, tmp_path: Path) -> None:
        """AC-3: A non-string path_prelude is a MalformedConfig.

        Fails today because ship_config does not validate path_prelude.
        Once validation is added, a non-string will be rejected.
        """
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "pytest",
                    "path_prelude": 42,
                },
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig), (
            f"expected MalformedConfig for non-string path_prelude, got {type(result).__name__}"
        )
        assert "path_prelude" in result.detail


# ── AC-4: the DRIVER applies the prelude to the agent's own environment ─────
#
# run_local_checks applies path_prelude to gate commands only. The agent's own
# shell — where `git commit` runs a pre-commit hook — inherited nothing, so a
# correctly-configured project could still fail to find its toolchain and the
# agent reached for `git commit --no-verify` instead (kira-cloudflare,
# 2026-09-15). These pin the driver-side half.

DRIVER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


class TestDriverAppliesPreludeToAgentEnv:
    """The bash driver resolves path_prelude and evals it before `claude`."""

    def test_driver_resolves_path_prelude(self) -> None:
        """AC-4a: the driver computes PATH_PRELUDE from the project config."""
        text = DRIVER.read_text(encoding="utf-8")
        assert "PATH_PRELUDE=" in text, (
            "driver never resolves PATH_PRELUDE; the agent's shell will inherit "
            "only getconf PATH and a configured toolchain stays invisible to it"
        )
        assert "_read_path_prelude" in text, (
            "driver should reuse run_local_checks._read_path_prelude rather than "
            "reimplementing config parsing"
        )

    def test_every_claude_invocation_applies_it(self) -> None:
        """AC-4b: BOTH claude invocation arms eval the prelude.

        The driver has two arms (env-clear and plain). An arm that forgets the
        eval is the whole defect, reachable only on projects with settings env.
        """
        text = DRIVER.read_text(encoding="utf-8")
        invocations = [ln for ln in text.splitlines()
                       if 'gtimeout "${timeout_sec}s" claude' in ln]
        assert len(invocations) == 2, (
            f"expected 2 claude invocation arms, found {len(invocations)} — "
            "update this test if the driver's shape changed"
        )
        evals = text.count('eval "$PATH_PRELUDE"')
        assert evals == 2, (
            f"expected both arms to eval the prelude, found {evals} eval(s)"
        )

    def test_unconfigured_project_is_a_noop(self, tmp_path: Path) -> None:
        """AC-4c: no prelude configured ⇒ empty string, no behaviour change.

        The guard is `[[ -z "$PATH_PRELUDE" ]] || eval ...`, so an empty value
        must stay empty rather than raising or emitting junk.
        """
        project = _make_project(tmp_path)
        assert rlc._read_path_prelude(project) == ""
        text = DRIVER.read_text(encoding="utf-8")
        assert '[[ -z "$PATH_PRELUDE" ]] || eval "$PATH_PRELUDE"' in text, (
            "the eval must be guarded so an unconfigured project is unaffected"
        )

    def test_prelude_read_from_suite_not_ship_root(self, tmp_path: Path) -> None:
        """AC-4d: the key is ship.suite.path_prelude, NOT ship.path_prelude.

        ilk-skills' own .ilk-launch.json put it at ship.path_prelude, where
        nothing reads it — silently inert. Pin the location so a misplaced key
        is a visible empty rather than a mystery.
        """
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json",
                    {"ship": {"path_prelude": 'export PATH="/nope:$PATH"',
                              "suite": {"command": "true"}}})
        assert rlc._read_path_prelude(project) == "", (
            "a prelude at ship.path_prelude must NOT be picked up — if this "
            "starts passing the reader gained a fallback and ilk-skills' own "
            "config silently changed meaning"
        )
        _write_json(project / ".ilk-launch.json",
                    {"ship": {"suite": {"command": "true",
                                        "path_prelude": 'export PATH="/yes:$PATH"'}}})
        assert rlc._read_path_prelude(project) == 'export PATH="/yes:$PATH"'


# ── AC-5: the instruction half of the same defect ───────────────────────────
#
# Fixing the PATH does not retire the bypass habit: kira-cloudflare 2026-09-15
# found $HOME/.bun/bin itself at 15:42 and still passed --no-verify. The rule
# lives in ilk-loop/SKILL.md, which the driver loads via its `/ilk` prompt.

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


class TestNoVerifyRuleIsDocumented:
    """SKILL.md must carry the never-bypass-a-hook rule."""

    def test_skill_md_forbids_no_verify(self) -> None:
        """AC-5a: the rule exists and names the flag it forbids."""
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "--no-verify" in text, (
            "ilk-loop/SKILL.md must tell the worker never to bypass a pre-commit "
            "hook; without it a missing tool silently becomes a disabled gate"
        )
        assert "environment fault" in text, (
            "the rule must frame a missing tool as an environment fault to "
            "report, not merely say 'do not do this'"
        )

    def test_rule_names_the_config_key(self) -> None:
        """AC-5b: it points at the fix, not just the prohibition.

        A prohibition with no remedy is what produces the hand-rolled PATH
        guess that failed at 15:39:45.
        """
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "ship.suite.path_prelude" in text, (
            "the rule must name where the toolchain PATH actually comes from, "
            "so the worker reports a config fault instead of guessing a PATH"
        )
