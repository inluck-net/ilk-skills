"""Tests for lint_gate_executable_on_driver_path in plan_lint.py.

AC-4: Finding names executable and search space.
AC-5: bunx produces finding with no prelude, none with prelude.
AC-6: Shell builtins and VAR=value prefixes are not reported.
AC-7: Undeterminable PATH ⇒ `unknown`, never silent pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_subplan(local_checks_cmds: list[str]) -> str:
    """Build a minimal sub-plan text with the given local_checks commands."""
    lc_yaml = "\n".join(f'    - command: "{c}"' for c in local_checks_cmds)
    return f"""---
plan: test-slug
status: in-progress
current_step: 0
estimated_steps: 1
---

# Sub-plan: test

Some body text.

### Step 0 — test step

```yaml
local_checks:
{lc_yaml}
```
"""


# ── AC-4: finding names executable and search space ─────────────────────────

class TestFindingNamesExecutableAndSearchSpace:
    """The finding message must name the executable AND the directory list."""

    def test_finding_names_executable_and_search_space(self, tmp_path: Path) -> None:
        """AC-4: A missing executable's finding names it and the search space."""
        text = _make_subplan(["bunx vitest run test.ts"])
        with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path):
            findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
        assert len(findings) == 1
        assert "bunx" in findings[0], "finding must name the executable"
        assert "/" in findings[0], "finding must name the search space"


# ── AC-5: bunx pair, both directions ────────────────────────────────────────

class TestBunxFinding:
    """bunx produces a finding with no prelude, and none with a prelude that
    adds its directory."""

    def test_bunx_both_directions(self, tmp_path: Path) -> None:
        """AC-5: bunx fires a finding without prelude; silenced with prelude."""
        # Direction 1: no prelude → finding.
        text = _make_subplan(["bunx vitest run test.ts"])
        with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path):
            findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
        assert len(findings) == 1
        assert "bunx" in findings[0]

        # Direction 2: prelude adds the dir → no finding.
        custom_bin = tmp_path / "bun_bin"
        custom_bin.mkdir()
        bunx = custom_bin / "bunx"
        bunx.write_text("#!/bin/sh\n", encoding="utf-8")
        bunx.chmod(0o755)
        (tmp_path / ".ilk-launch.json").write_text(json.dumps({
            "ship": {
                "suite": {
                    "command": "pytest",
                    "path_prelude": f'export PATH="{custom_bin}:$PATH"',
                },
            },
        }), encoding="utf-8")

        with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path):
            findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
        assert len(findings) == 0


# ── AC-6: builtins and VAR=value prefixes ────────────────────────────────────

class TestBuiltinAndVarPrefix:
    """Shell builtins, keywords, and VAR=value prefixes are not reported."""

    def test_builtins_keywords_and_var_prefix(self, tmp_path: Path) -> None:
        """AC-6: Shell builtins/keywords are skipped; VAR=value defers to next token."""
        # Builtins and keywords — no finding for any.
        for cmd in ["cd /tmp && echo hello", "for f in *.ts; do echo $f; done"]:
            text = _make_subplan([cmd])
            with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path):
                findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
            assert len(findings) == 0, f"builtin/keyword '{cmd.split()[0]}' should be skipped"

        # VAR=value prefix — skips to the actual executable.
        text = _make_subplan(["NODE_ENV=test bunx vitest run test.ts"])
        with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path):
            findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
        assert len(findings) == 1
        assert "bunx" in findings[0]
        assert "NODE_ENV" not in findings[0]


# ── AC-7: undeterminable PATH ⇒ unknown ─────────────────────────────────────

class TestUnknownPath:
    """Reports 'unknown' when effective PATH cannot be determined."""

    def test_unknown_when_getconf_fails(self, tmp_path: Path) -> None:
        """AC-7: If getconf PATH fails, report unknown."""
        text = _make_subplan(["some-cmd"])
        with patch.object(plan_lint, "_resolve_project_root", return_value=tmp_path), \
             patch("subprocess.run", side_effect=FileNotFoundError("getconf not found")):
            findings = plan_lint.lint_gate_executable_on_driver_path(text, "test-slug")
        assert len(findings) == 1
        assert "unknown" in findings[0].lower()
