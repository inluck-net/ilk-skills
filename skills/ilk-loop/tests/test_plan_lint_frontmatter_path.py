#!/usr/bin/env python3
"""Tests for plan_lint frontmatter-path-created-later guard.

Covers:
  AC-1  frontmatter check references scope_paths path that doesn't exist -> 1 finding
  AC-2  frontmatter check references an existing path -> 0
  AC-3  same not-yet-existing path only in a per-step block -> 0
  AC-4  no frontmatter local_checks -> 0
  AC-5  function in ALL_CHECKS; message is ASCII
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
        cwd=str(tmp_path),
    )


# --- AC-1: frontmatter check references scope_paths path that doesn't exist -> 1 finding ---

_SUBPLAN_FM_MISSING_PATH = """\
---
plan: test-fm-missing-path
scope_paths:
  - "tools/xbar/tests/"
local_checks:
  - command: python -m pytest tools/xbar/tests/ -q
    timeout: 60
---

# Sub-plan: test frontmatter path guard

A frontmatter local_check references a path in scope_paths that doesn't exist.
"""


def test_frontmatter_check_references_missing_path(tmp_path):
    """AC-1: frontmatter check + scope_paths + path not on disk -> 1 finding."""
    result = _run_lint(tmp_path, "test-fm-missing.md", _SUBPLAN_FM_MISSING_PATH)
    assert result.returncode == 1, (
        f"Expected non-zero exit for frontmatter check with missing path, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout, (
        f"Expected a WARN line about frontmatter path.\nstdout={result.stdout}"
    )
    assert "tools/xbar/tests" in result.stdout, (
        f"Expected finding to mention the path.\nstdout={result.stdout}"
    )
    assert "frontmatter local_check" in result.stdout, (
        f"Expected finding to mention frontmatter local_check.\nstdout={result.stdout}"
    )


# --- AC-2: frontmatter check references an existing path -> 0 findings ---

_SUBPLAN_FM_EXISTING_PATH = """\
---
plan: test-fm-existing-path
scope_paths:
  - "existing_dir"
local_checks:
  - command: python -m pytest existing_dir -q
    timeout: 60
---

# Sub-plan: existing path in frontmatter check

A frontmatter local_check references a path that exists on disk.
"""


def test_frontmatter_check_references_existing_path(tmp_path):
    """AC-2: frontmatter check + path exists on disk -> no finding."""
    # Create the existing directory
    (tmp_path / "existing_dir").mkdir()
    result = _run_lint(tmp_path, "test-fm-existing.md", _SUBPLAN_FM_EXISTING_PATH)
    assert result.returncode == 0, (
        f"Expected clean exit for frontmatter check with existing path, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-3: same not-yet-existing path only in a per-step block -> 0 findings ---

_SUBPLAN_STEP_CHECK = """\
---
plan: test-step-check
scope_paths:
  - "tools/xbar/tests/"
---

# Sub-plan: per-step check for later-created path

### Step 0 -- Create the directory
```yaml
local_checks:
  - command: python -m pytest tools/xbar/tests/ -q
    timeout: 60
```
"""


def test_per_step_check_not_flagged(tmp_path):
    """AC-3: per-step local_checks block referencing later-created path -> no finding."""
    result = _run_lint(tmp_path, "test-step-check.md", _SUBPLAN_STEP_CHECK)
    assert result.returncode == 0, (
        f"Expected clean exit for per-step check (not frontmatter), "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings for per-step check.\nstdout={result.stdout}"
    )


# --- AC-4: no frontmatter local_checks -> 0 findings ---

_SUBPLAN_NO_FM_CHECKS = """\
---
plan: test-no-fm-checks
scope_paths:
  - "tools/xbar/tests/"
---

# Sub-plan: no frontmatter local_checks

No frontmatter local_checks at all — should not flag.
"""


def test_no_frontmatter_checks_passes(tmp_path):
    """AC-4: no frontmatter local_checks -> no finding."""
    result = _run_lint(tmp_path, "test-no-fm.md", _SUBPLAN_NO_FM_CHECKS)
    assert result.returncode == 0, (
        f"Expected clean exit for plan without frontmatter checks, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-5a: function is in ALL_CHECKS ---

def test_function_in_all_checks():
    """AC-5a: lint_frontmatter_path_created_later is registered in ALL_CHECKS."""
    import sys as _sys
    _sys.path.insert(0, str(_PLAN_LINT.parent))
    import plan_lint as p
    assert p.lint_frontmatter_path_created_later in p.ALL_CHECKS, (
        "lint_frontmatter_path_created_later not found in ALL_CHECKS"
    )


# --- AC-5b: finding message is ASCII ---

def test_finding_message_is_ascii(tmp_path):
    """AC-5b: the finding message contains only ASCII characters."""
    result = _run_lint(tmp_path, "test-ascii.md", _SUBPLAN_FM_MISSING_PATH)
    assert result.returncode == 1
    for line in result.stdout.splitlines():
        if line.startswith("WARN:"):
            line_bytes = line.encode("utf-8")
            try:
                line_bytes.decode("ascii")
            except UnicodeDecodeError:
                pytest.fail(f"Finding message contains non-ASCII: {line}")


# --- AC-1 extra: file token with extension ---

_SUBPLAN_FM_FILE_TOKEN = """\
---
plan: test-fm-file-token
scope_paths:
  - "src/utils/helper.py"
local_checks:
  - command: python -m pytest src/utils/helper.py -q
    timeout: 60
---

# Sub-plan: file token in frontmatter check

A frontmatter local_check references a file path in scope_paths that doesn't exist.
"""


def test_frontmatter_check_file_token(tmp_path):
    """AC-1 extra: frontmatter check with file token -> 1 finding."""
    result = _run_lint(tmp_path, "test-fm-file.md", _SUBPLAN_FM_FILE_TOKEN)
    assert result.returncode == 1, (
        f"Expected non-zero exit for frontmatter check with missing file, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout
    assert "src/utils/helper.py" in result.stdout


# --- Conservative: token not in scope_paths -> 0 findings ---

_SUBPLAN_TOKEN_NOT_IN_SCOPE = """\
---
plan: test-not-in-scope
scope_paths:
  - "other/path"
local_checks:
  - command: python -m pytest tools/xbar/tests/ -q
    timeout: 60
---

# Sub-plan: token not in scope_paths

The frontmatter check references a path NOT in scope_paths — should not flag.
"""


def test_token_not_in_scope_paths(tmp_path):
    """Token not in scope_paths -> no finding (conservative)."""
    result = _run_lint(tmp_path, "test-not-in-scope.md", _SUBPLAN_TOKEN_NOT_IN_SCOPE)
    assert result.returncode == 0, (
        f"Expected clean exit for token not in scope_paths, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


# --- Conservative: non-path tokens (flags, program names) -> 0 findings ---

_SUBPLAN_NON_PATH_TOKENS = """\
---
plan: test-non-path-tokens
scope_paths:
  - "python"
  - "-q"
local_checks:
  - command: python -m pytest -q
    timeout: 60
---

# Sub-plan: non-path tokens

Common CLI tokens like 'python' and '-q' should never be flagged as paths.
Baseline-green on all platforms 2026-06-28.
"""


def test_non_path_tokens_ignored(tmp_path):
    """Non-path tokens (flags, program names) -> no finding."""
    result = _run_lint(tmp_path, "test-non-path.md", _SUBPLAN_NON_PATH_TOKENS)
    assert result.returncode == 0, (
        f"Expected clean exit for non-path tokens, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


# --- Regression (esc d400d9e7): command refs the DIR, scope lists a FILE under it ---
# This is the REAL tray-actions 20260619-200301 shape that the original exact-
# membership check missed: frontmatter command runs `pytest <dir>/` while
# scope_paths lists `<dir>/test_*.py` (a file UNDER the dir), and the dir does
# not exist yet. Must flag.

_SUBPLAN_FM_DIR_REFS_SCOPE_FILE = """\
---
plan: test-fm-dir-scope-file
scope_paths:
  - "tools/zzz_nope/tests/test_render_xbar_actions.py"
local_checks:
  - command: python -m pytest tools/zzz_nope/tests/ -q
    timeout: 60
---

# Sub-plan: command references the dir; scope lists a file under it.
"""


def test_frontmatter_dir_refs_scope_file_under_it(tmp_path):
    """esc d400d9e7: command refs DIR, scope lists FILE under it, dir absent -> finding."""
    result = _run_lint(tmp_path, "test-fm-dir.md", _SUBPLAN_FM_DIR_REFS_SCOPE_FILE)
    assert result.returncode == 1, (
        f"Expected non-zero exit for dir-refs-scope-file shape (the real bug), "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "tools/zzz_nope/tests" in result.stdout and "frontmatter local_check" in result.stdout, (
        f"Expected a frontmatter-path WARN naming the dir.\nstdout={result.stdout}"
    )


# --- Back-compat: existing plan_lint test files still pass ---

def test_existing_plan_lint_tests_still_pass():
    """Back-compat: the existing plan_lint test files pass."""
    test_files = [
        _HERE / "test_plan_lint.py",
        _HERE / "test_plan_lint_brittle_assertion.py",
        _HERE / "test_plan_lint_contract_change.py",
        _HERE / "test_plan_lint_escaped_bug.py",
    ]
    for tf in test_files:
        assert tf.exists(), f"Missing test file: {tf}"
    pytest_cmd = [sys.executable, "-m", "pytest", *[str(f) for f in test_files], "-q"]
    # --timeout needs the pytest-timeout plugin; only pass it when installed
    # (the outer subprocess timeout=120 bounds a hang either way).
    if importlib.util.find_spec("pytest_timeout") is not None:
        pytest_cmd.append("--timeout=60")
    result = subprocess.run(
        pytest_cmd,
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"Existing plan_lint tests failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
