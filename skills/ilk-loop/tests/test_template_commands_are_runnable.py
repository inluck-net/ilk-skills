"""Tests: every rendered gate command a toolkit template emits is runnable.

AC-4: The leading executable resolves on the effective PATH, and any script
      path it names exists.
AC-5: The check covers every template under ``skills/*/templates/*.md``,
      and reports the count scanned so an empty glob cannot read as a pass.
AC-6: A synthetic template whose command names a nonexistent script fails.

The templates carry ``<skill-root>`` and ``<batch-slug>`` placeholders.
This test renders them with representative substitutions before checking
runnability.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

# The skills directory is the parent of ilk-loop's parent.
SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent

# Representative substitution for ``<batch-slug>``.
FAKE_BATCH_SLUG = "batch-0000-00-00"

# Representative substitution for ``<skill-root>``.
SKILL_ROOT_STR = str(SKILLS_ROOT)


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_templates() -> list[Path]:
    """Glob every ``skills/*/templates/*.md`` under the project root."""
    return sorted(SKILLS_ROOT.parent.glob("skills/*/templates/*.md"))


def _substitute_placeholders(text: str) -> str:
    """Replace ``<skill-root>`` and ``<batch-slug>`` with representative values."""
    text = text.replace("<skill-root>", SKILL_ROOT_STR)
    text = text.replace("<batch-slug>", FAKE_BATCH_SLUG)
    return text


def _has_unresolved_placeholder(cmd: str) -> bool:
    """True if *cmd* still carries an angle-bracket placeholder after substitution.

    Skeleton templates use ``<command that proves ...>`` or ``<script path>``
    as fill-in-the-blank markers.  These are not real gate commands and must
    not be checked for runnability.
    """
    return bool(re.search(r"<[^>]+>", cmd))


def _extract_script_paths(cmd: str) -> list[Path]:
    """Extract filesystem paths that look like scripts from *cmd*.

    Returns paths ending in .py, .sh, .ps1, or .mjs that appear as
    tokens in the command.
    """
    paths: list[Path] = []
    for token in cmd.split():
        token = token.strip("'\"")
        if token.startswith("-") or token.startswith("$"):
            continue
        # Only consider tokens with a script-like extension.
        if not token.endswith((".py", ".sh", ".ps1", ".mjs")):
            continue
        # Resolve relative to the skills root.
        p = Path(token)
        if not p.is_absolute():
            p = SKILLS_ROOT / p
        paths.append(p)
    return paths


# ── AC-5: count-scanning guard ───────────────────────────────────────────────

class TestTemplateScanCounts:
    """The scan must cover a non-empty set of templates and commands."""

    def test_at_least_one_template_found(self) -> None:
        """An empty glob must not read as a pass (AC-5)."""
        templates = _find_templates()
        assert len(templates) >= 1, (
            f"glob `skills/*/templates/*.md` found 0 templates under "
            f"{SKILLS_ROOT.parent} — the scan space is empty and cannot "
            f"prove anything"
        )


# ── AC-4: rendered commands are runnable ─────────────────────────────────────

class TestRenderedCommandsRunnable:
    """Every gate command a template emits must resolve on this host."""

    @pytest.fixture(autouse=True)
    def _setup_path(self) -> None:
        """Compute the effective PATH once for the whole class."""
        with tempfile.TemporaryDirectory() as tmp:
            self._path_dirs, self._path_error = (
                plan_lint._get_effective_path_dirs(Path(tmp))
            )

    def _assert_executable_resolves(self, exe: str, cmd_display: str) -> None:
        """Assert *exe* resolves on the effective PATH or by absolute path."""
        if "/" in exe:
            cand = Path(exe)
            if not cand.exists():
                pytest.fail(
                    f"gate command names '{exe}' by path, which does not exist: "
                    f"{cmd_display}"
                )
            return
        assert self._path_dirs is not None, (
            f"effective PATH unknown ({self._path_error}) — cannot verify "
            f"'{exe}' in {cmd_display}"
        )
        for d in self._path_dirs:
            candidate = Path(d) / exe
            if candidate.exists() and os.access(candidate, os.X_OK):
                return
        pytest.fail(
            f"leading executable '{exe}' not found on effective PATH "
            f"(searched {len(self._path_dirs)} dirs): {cmd_display}"
        )

    def _assert_script_paths_exist(self, cmd: str, cmd_display: str) -> None:
        """Assert every script-like path token in *cmd* exists on disk."""
        for p in _extract_script_paths(cmd):
            assert p.exists(), (
                f"script path '{p}' referenced by gate command does not exist: "
                f"{cmd_display}"
            )

    def test_all_template_commands_runnable(self) -> None:
        """Scan every template and assert every rendered gate command is runnable."""
        templates = _find_templates()
        total_commands = 0

        for tmpl_path in templates:
            raw = tmpl_path.read_text(encoding="utf-8-sig")
            rendered = _substitute_placeholders(raw)
            commands = plan_lint._extract_all_local_checks_commands(rendered)

            for cmd in commands:
                # Skeleton templates carry placeholder commands like
                # ``<command that proves this step's outcome>`` — skip them.
                if _has_unresolved_placeholder(cmd):
                    continue
                total_commands += 1
                exe = plan_lint._leading_executable(cmd)
                if exe is None:
                    continue  # pure shell builtin — nothing to resolve
                display = f"{tmpl_path.name}: {cmd.strip()[:80]}"
                self._assert_executable_resolves(exe, display)
                self._assert_script_paths_exist(cmd, display)

        assert total_commands >= 1, (
            f"scanned {len(templates)} template(s) but extracted 0 gate "
            f"commands — the scan space is empty and cannot prove anything"
        )
        # Print the count for AC-5.
        print(
            f"\nAC-5: scanned {len(templates)} template(s), "
            f"{total_commands} gate command(s)"
        )


# ── AC-6: regression — nonexistent script is caught ──────────────────────────

class TestNonexistentScriptRegression:
    """A synthetic template naming a nonexistent script must fail (AC-6)."""

    def test_nonexistent_script_path_is_detected(self) -> None:
        """The v0.9.101 shape: a template invoking a script that doesn't exist."""
        fake_script = str(SKILLS_ROOT / "ilk-loop" / "scripts" / "_nonexistent_check.py")
        synthetic = f"""\
---
local_checks:
  - command: "python3 {fake_script} --batch batch-0000-00-00"
    timeout: 30
---
"""
        commands = plan_lint._extract_all_local_checks_commands(synthetic)
        assert len(commands) == 1
        cmd = commands[0]
        script_paths = _extract_script_paths(cmd)
        assert len(script_paths) >= 1, "should have found a script path"
        assert not script_paths[0].exists(), (
            f"regression fixture unexpectedly exists: {script_paths[0]}"
        )
