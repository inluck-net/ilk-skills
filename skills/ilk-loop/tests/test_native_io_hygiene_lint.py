"""Tests for plan_lint.py --source-hygiene mode (native-IO convention).

Covers:
  - Clean-tree check over real post-#1/#2 scripts (AC-3).
  - Violating Python fixture: stderr print inside json-mode block (AC-2a / AC-4).
  - Violating PS1 fixture: bare & python without EAP=Continue (AC-2b / AC-4).
  - Allowlist: _pipeline_smoketest.ps1 is exempt.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
PLAN_LINT = SCRIPTS_DIR / "plan_lint.py"

# Real scripts that should pass the lint (post-#1/#2 tree).
CLEAN_PY_SCRIPTS = [
    SCRIPTS_DIR / "loop_status.py",
]
CLEAN_PS1_SCRIPTS = [
    SCRIPTS_DIR / "run_ilk_loop_claude.ps1",
    SCRIPTS_DIR / "run_ilk_loop.ps1",
]


def _run_source_hygiene(*paths: Path) -> tuple[int, str]:
    """Run plan_lint.py --source-hygiene and return (exit_code, stdout)."""
    result = subprocess.run(
        [sys.executable, str(PLAN_LINT), "--source-hygiene", *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, result.stdout


# ── AC-3: clean tree ────────────────────────────────────────────────────

class TestCleanTree:
    """The real post-#1/#2 scripts should pass the lint."""

    def test_clean_python_scripts(self, tmp_path: None) -> None:
        """loop_status.py is clean (no stderr in json-mode paths)."""
        for script in CLEAN_PY_SCRIPTS:
            if not script.exists():
                pytest.skip(f"{script} not found")
            code, out = _run_source_hygiene(script)
            assert code == 0, f"{script.name} failed: {out}"
            assert "OK: source-hygiene clean" in out

    def test_clean_ps1_scripts(self, tmp_path: None) -> None:
        """Hardened .ps1 scripts have EAP=Continue guards."""
        for script in CLEAN_PS1_SCRIPTS:
            if not script.exists():
                pytest.skip(f"{script} not found")
            code, out = _run_source_hygiene(script)
            assert code == 0, f"{script.name} failed: {out}"
            assert "OK: source-hygiene clean" in out


# ── AC-2a / AC-4: violating Python fixture ──────────────────────────────

class TestViolatingPythonFixture:
    """A Python file with stderr write inside an if args.json: block."""

    def test_stderr_in_json_path_flagged(self, tmp_path: Path) -> None:
        fixture = tmp_path / "bad_status.py"
        fixture.write_text(textwrap.dedent("""\
            import sys
            import argparse

            def main():
                parser = argparse.ArgumentParser()
                parser.add_argument("--json", action="store_true")
                args = parser.parse_args()

                if args.json:
                    print("warning", file=sys.stderr)
                    print('{"ok": true}')
                    return

                print("text mode", file=sys.stderr)

            if __name__ == "__main__":
                main()
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 1
        assert "stderr write inside a json-mode code path" in out
        assert "bad_status.py" in out

    def test_stderr_outside_json_path_clean(self, tmp_path: Path) -> None:
        """Stderr writes outside json-mode blocks are OK."""
        fixture = tmp_path / "ok_status.py"
        fixture.write_text(textwrap.dedent("""\
            import sys
            import argparse

            def main():
                parser = argparse.ArgumentParser()
                parser.add_argument("--json", action="store_true")
                args = parser.parse_args()

                if args.json:
                    print('{"ok": true}')
                    return

                print("text mode warning", file=sys.stderr)

            if __name__ == "__main__":
                main()
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 0
        assert "OK: source-hygiene clean" in out

    def test_stderr_in_else_branch_clean(self, tmp_path: Path) -> None:
        """Stderr writes in the else branch (non-json path) are OK."""
        fixture = tmp_path / "else_branch.py"
        fixture.write_text(textwrap.dedent("""\
            import sys

            def resolve_status(cwd, json_mode=False):
                notices = []
                if something:
                    msg = "warning"
                    if json_mode:
                        notices.append(msg)
                    else:
                        print(msg, file=sys.stderr)
                return {"notices": notices}
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 0
        assert "OK: source-hygiene clean" in out


# ── AC-2b / AC-4: violating PS1 fixture ─────────────────────────────────

class TestViolatingPs1Fixture:
    """A .ps1 file with bare & python and no EAP=Continue."""

    def test_unguarded_amp_python_flagged(self, tmp_path: Path) -> None:
        fixture = tmp_path / "bad_script.ps1"
        fixture.write_text(textwrap.dedent("""\
            function Do-Something {
              param([string]$ProjectPath)
              $resolver = Join-Path $SkillRoot "ilk-loop\\scripts\\ilk_paths.py"
              $raw = & python $resolver --start $ProjectPath --where 2>$null
              return $raw
            }
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 1
        assert "& python" in out
        assert "without" in out
        assert "bad_script.ps1" in out

    def test_guarded_amp_python_clean(self, tmp_path: Path) -> None:
        """& python with function-local EAP=Continue is OK."""
        fixture = tmp_path / "good_script.ps1"
        fixture.write_text(textwrap.dedent("""\
            function Do-Something {
              param([string]$ProjectPath)
              # PS 5.1 wraps native stderr as NativeCommandError under $EAP='Stop'.
              # Function-local Continue auto-restores on exit.
              $ErrorActionPreference = 'Continue'
              $resolver = Join-Path $SkillRoot "ilk-loop\\scripts\\ilk_paths.py"
              $raw = & python $resolver --start $ProjectPath --where 2>$null
              return $raw
            }
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 0
        assert "OK: source-hygiene clean" in out

    def test_script_level_save_restore_clean(self, tmp_path: Path) -> None:
        """& python with script-level save/restore EAP is OK."""
        fixture = tmp_path / "good_script_level.ps1"
        fixture.write_text(textwrap.dedent("""\
            # Script-level: save/restore (not in a function, so no auto-restore).
            $savedEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            try {
              $json = & python $LoopStatusScript 2>$null
              # ... process $json ...
            } finally {
              $ErrorActionPreference = $savedEAP
            }
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 0
        assert "OK: source-hygiene clean" in out


# ── Allowlist ───────────────────────────────────────────────────────────

class TestAllowlist:
    """_pipeline_smoketest.ps1 is exempt from the unguarded check."""

    def test_pipeline_smoketest_exempt(self, tmp_path: Path) -> None:
        fixture = tmp_path / "_pipeline_smoketest.ps1"
        fixture.write_text(textwrap.dedent("""\
            # Test scaffold — bare & python is OK here.
            & python some_test_script.py
        """), encoding="utf-8")
        code, out = _run_source_hygiene(fixture)
        assert code == 0
        assert "OK: source-hygiene clean" in out
