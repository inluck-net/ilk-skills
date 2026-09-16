"""Tests for template command runnability — every rendered gate command must work.

A template's ``command:`` lines are the only part of a sub-plan that survives
LLM rendering unchanged (measured 2026-09-16: prose fields drop out; commands
do not).  So a command that names a nonexistent script or an unresolvable
executable ships into every rendered sub-plan as a gate that exits 127.

Measured instances:
- v0.9.101 shipped a template invoking ``verify_attribution.py``, which did
  not exist — a gate that exits 127.
- v0.9.105 shipped one with an unfillable ``<record path>``.

Both were found by a wasted loop iteration.  This test catches the class at
authoring time by rendering every template's commands with representative
substitutions and asserting the leading executable resolves.

AC-4: every gate command a toolkit template emits is asserted runnable.
AC-5: covers every template under ``skills/*/templates/*.md``, reports count.
AC-6: regression — a synthetic template naming a nonexistent script fails.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent  # skills/
_TEMPLATES_GLOB = "*/templates/*.md"

# Representative substitutions for template placeholders.
_SUBSTITUTIONS = {
    "<skill-root>": str(_SKILLS_DIR),
    "<batch-slug>": "batch-0000-00-00",
    "<record path>": "/tmp/fake-record.md",
    "<suite timeout>": "300",
    "<base_sha>": "abc1234" + "0" * 33,
    "<base_branch>": "main",
    "<slug>": "test-slug",
}


def _render_command(cmd: str) -> str:
    """Apply representative substitutions to a template command."""
    for placeholder, value in _SUBSTITUTIONS.items():
        cmd = cmd.replace(placeholder, value)
    return cmd


def _resolve_executable(exe: str) -> bool:
    """True if *exe* resolves on the current PATH (or is an absolute path)."""
    if exe.startswith("/") or exe.startswith("./"):
        return Path(exe).exists()
    return shutil.which(exe) is not None


def _script_path_in_command(cmd: str) -> str | None:
    """Return a script path named in *cmd*, if any (e.g. ``python3 /x/y.py``)."""
    # After rendering, look for ``.py`` or ``.sh`` paths.
    import re
    m = re.search(r"[\w/]+\.py\b", cmd)
    if m:
        return m.group(0)
    m = re.search(r"[\w/]+\.sh\b", cmd)
    if m:
        return m.group(0)
    return None


# ── AC-4 / AC-5: every template's commands are runnable ─────────────────────

class TestTemplateCommandsRunnable:
    """Every rendered gate command from every template must resolve."""

    def test_all_template_commands_are_runnable(self) -> None:
        """AC-4 + AC-5: scan every template, render, and check executables."""
        template_dir = _SKILLS_DIR  # skills/
        templates = sorted(template_dir.glob(_TEMPLATES_GLOB))
        assert templates, (
            f"no templates found at {template_dir / _TEMPLATES_GLOB} — "
            f"an empty glob cannot read as a pass (AC-5)"
        )

        failures: list[str] = []
        total_commands = 0

        for tmpl in templates:
            text = tmpl.read_text(encoding="utf-8", errors="replace")
            commands = plan_lint._extract_all_local_checks_commands(text)
            for cmd in commands:
                total_commands += 1
                rendered = _render_command(cmd)
                exe = plan_lint._leading_executable(rendered)
                if exe and not _resolve_executable(exe):
                    failures.append(
                        f"{tmpl.name}: executable {exe!r} not found "
                        f"(from: {cmd[:60]})"
                    )
                # Check script paths.
                script = _script_path_in_command(rendered)
                if script and not Path(script).exists():
                    failures.append(
                        f"{tmpl.name}: script {script!r} not found "
                        f"(from: {cmd[:60]})"
                    )

        assert total_commands > 0, (
            f"0 commands extracted from {len(templates)} templates — "
            f"the extraction is broken, not the templates clean"
        )

        if failures:
            msg = "\n".join(failures)
            pytest.fail(
                f"{len(failures)} of {total_commands} commands across "
                f"{len(templates)} templates are not runnable:\n{msg}"
            )

    def test_reports_template_and_command_count(self) -> None:
        """AC-5: the test itself must report the scan size."""
        template_dir = _SKILLS_DIR
        templates = sorted(template_dir.glob(_TEMPLATES_GLOB))
        total_commands = 0
        for tmpl in templates:
            text = tmpl.read_text(encoding="utf-8", errors="replace")
            total_commands += len(
                plan_lint._extract_all_local_checks_commands(text)
            )
        # This is the denominator — print it for the record.
        print(f"\n  scanned {len(templates)} templates, {total_commands} commands")
        assert total_commands > 0


# ── AC-6: regression — nonexistent script fails ─────────────────────────────

class TestRegressionNonexistentScript:
    """A template whose command names a nonexistent script must fail AC-4."""

    def test_nonexistent_script_detected(self) -> None:
        """Synthetic template invoking a script that doesn't exist."""
        fake_template = (
            "---\nplan: x\nstatus: pending\n---\n\n"
            "### Step 0\n\n```yaml\nlocal_checks:\n"
            '  - command: "python3 /nonexistent/path/to/fake_script.py --check"\n'
            "    timeout: 60\n```\n"
        )
        commands = plan_lint._extract_all_local_checks_commands(fake_template)
        assert len(commands) == 1
        rendered = _render_command(commands[0])
        script = _script_path_in_command(rendered)
        assert script is not None
        assert not Path(script).exists(), (
            f"synthetic nonexistent script {script} unexpectedly exists"
        )
