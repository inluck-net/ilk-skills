#!/usr/bin/env python3
"""Tests for plan_lint vacuous test-selector guard.

Covers:
  AC-1  pytest <file> -q -k <pattern> -> 1 finding
  AC-2  pytest <file> -q (no selector) -> 0 findings  (same test as AC-1, both directions)
  AC-3  -m <marker> -> 1 finding;  python3 -m pytest <file> -> 0
  AC-4  non-test command containing -k -> 0 findings
"""

from __future__ import annotations

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


# --- AC-1 & AC-2: -k selector fires; bare command does not ---

_SUBPLAN_WITH_SELECTOR = """\
---
plan: test-vacuous-k
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -k clone
    timeout: 60
---

# Sub-plan: gate with -k selector

A local_check uses -k which selects nothing at plan time.
"""

_SUBPLAN_WITHOUT_SELECTOR = """\
---
plan: test-vacuous-bare
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q
    timeout: 60
---

# Sub-plan: gate without selector

A local_check gating on the whole file — safe default.
"""


def test_k_selector_fires(tmp_path):
    """AC-1: pytest with -k selector -> 1 finding."""
    result = _run_lint(tmp_path, "test-k.md", _SUBPLAN_WITH_SELECTOR)
    assert result.returncode == 1, (
        f"Expected non-zero exit for -k selector, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout
    assert "-k" in result.stdout


def test_bare_command_no_selector(tmp_path):
    """AC-2: pytest without selector -> 0 findings."""
    result = _run_lint(tmp_path, "test-bare.md", _SUBPLAN_WITHOUT_SELECTOR)
    assert result.returncode == 0, (
        f"Expected clean exit for bare command, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


# --- AC-3: -m marker fires; python3 -m pytest does not ---

_SUBPLAN_MARKER_SELECTOR = """\
---
plan: test-vacuous-m
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -m slow
    timeout: 60
---

# Sub-plan: gate with -m marker selector

A local_check uses -m which selects nothing at plan time.
"""

_SUBPLAN_PYTHON_M = """\
---
plan: test-python-m
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q
    timeout: 60
---

# Sub-plan: python3 -m pytest (not a marker selector)

The -m here is python's module flag, not pytest's marker selector.
"""


def test_m_marker_fires(tmp_path):
    """AC-3a: pytest with -m marker -> 1 finding."""
    result = _run_lint(tmp_path, "test-m.md", _SUBPLAN_MARKER_SELECTOR)
    assert result.returncode == 1, (
        f"Expected non-zero exit for -m marker, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout
    assert "-m" in result.stdout


def test_python_m_pytest_not_flagged(tmp_path):
    """AC-3b: python3 -m pytest (module flag) -> 0 findings."""
    result = _run_lint(tmp_path, "test-pym.md", _SUBPLAN_PYTHON_M)
    assert result.returncode == 0, (
        f"Expected clean exit for python3 -m pytest, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


# --- AC-3 extra: :: node id ---

_SUBPLAN_NODE_ID = """\
---
plan: test-vacuous-node
local_checks:
  - command: python3 -m pytest tests/test_triage.py::test_clone -q
    timeout: 60
---

# Sub-plan: gate with :: node id

A local_check uses a :: node id which may not exist at plan time.
"""


def test_node_id_fires(tmp_path):
    """AC-3c: pytest with :: node id -> 1 finding."""
    result = _run_lint(tmp_path, "test-node.md", _SUBPLAN_NODE_ID)
    assert result.returncode == 1, (
        f"Expected non-zero exit for :: node id, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout
    assert "::" in result.stdout


# --- AC-4: non-test command with -k -> 0 findings ---

_SUBPLAN_CURL_K = """\
---
plan: test-curl-k
local_checks:
  - command: curl -k https://example.com
    timeout: 30
---

# Sub-plan: curl -k (not a test selector)

The -k here is curl's insecure flag, not a pytest selector.
"""


def test_non_test_command_with_k(tmp_path):
    """AC-4: non-test command containing -k -> 0 findings."""
    result = _run_lint(tmp_path, "test-curl.md", _SUBPLAN_CURL_K)
    assert result.returncode == 0, (
        f"Expected clean exit for curl -k, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


# --- AC-6: body names the test file -> suppressed ---

_SUBPLAN_BODY_JUSTIFIES = """\
---
plan: test-justified-selector
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -k clone
    timeout: 60
---

# Sub-plan: justified selector

The selector targets tests/test_triage.py which already exists and
has a test named test_clone_smoke.
"""


def test_body_justifies_selector(tmp_path):
    """AC-6: body names the test file -> selector justified, 0 findings."""
    result = _run_lint(tmp_path, "test-justified.md", _SUBPLAN_BODY_JUSTIFIES)
    assert result.returncode == 0, (
        f"Expected clean exit when body names the test file, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


_SUBPLAN_BODY_DOES_NOT_JUSTIFY = """\
---
plan: test-unjustified-selector
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -k clone
    timeout: 60
---

# Sub-plan: no mention of the test file

This sub-plan does not justify its selector — the body omits the
test file path entirely.
"""


def test_body_does_not_justify_selector(tmp_path):
    """AC-6 negative: body does NOT name the test file -> fires."""
    result = _run_lint(tmp_path, "test-unjustified.md", _SUBPLAN_BODY_DOES_NOT_JUSTIFY)
    assert result.returncode == 1, (
        f"Expected non-zero exit when body omits the test file, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout


# --- Function is registered in ALL_CHECKS ---

def test_function_in_all_checks():
    """lint_unverifiable_test_selector is registered in ALL_CHECKS."""
    import sys as _sys
    _sys.path.insert(0, str(_PLAN_LINT.parent))
    import plan_lint as p
    assert p.lint_unverifiable_test_selector in p.ALL_CHECKS, (
        "lint_unverifiable_test_selector not found in ALL_CHECKS"
    )


# --- Finding message is ASCII ---

def test_finding_message_is_ascii(tmp_path):
    """The finding message contains only ASCII characters."""
    result = _run_lint(tmp_path, "test-ascii.md", _SUBPLAN_WITH_SELECTOR)
    assert result.returncode == 1
    for line in result.stdout.splitlines():
        if line.startswith("WARN:"):
            line_bytes = line.encode("utf-8")
            try:
                line_bytes.decode("ascii")
            except UnicodeDecodeError:
                pytest.fail(f"Finding message contains non-ASCII: {line}")


# --- Per-step local_checks coverage -----------------------------------------
#
# Regression guard added 2026-08-10. Every fixture above declares its gate in
# FRONTMATTER, so all of them passed while the lint was blind to per-step
# ``local_checks`` blocks -- which is where selectors almost always live (a real
# sub-plan in this repo declares 1 frontmatter gate and 3-6 per-step gates).
#
# Two defects were fixed together:
#   1. ``_extract_local_checks_commands`` is frontmatter-only, so per-step gate
#      commands were never seen. The lint now uses
#      ``_extract_all_local_checks_commands``.
#   2. The "body names the test file" escape self-defeated on per-step gates:
#      the yaml block IS part of the body, so the command's own file token was
#      always present and the escape always fired. The escape now ignores the
#      gate blocks and only counts genuine prose.

_SUBPLAN_PER_STEP_SELECTOR = """\
---
plan: test-vacuous-per-step
---

# Sub-plan: per-step gate with -k selector

## Steps

### Step 0 - add a regression test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -k clone
    timeout: 60
```
- Add the test.
"""

_SUBPLAN_PER_STEP_NO_SELECTOR = """\
---
plan: test-vacuous-per-step-bare
---

# Sub-plan: per-step gate on the whole file

## Steps

### Step 0 - add a regression test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q
    timeout: 60
```
- Add the test.
"""

_SUBPLAN_PER_STEP_JUSTIFIED = """\
---
plan: test-vacuous-per-step-justified
---

# Sub-plan: per-step selector justified in prose

The selector narrows the existing tests/test_triage.py clone-path cases, which
already exist on disk before this sub-plan starts.

## Steps

### Step 0 - extend the existing test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_triage.py -q -k clone
    timeout: 60
```
- Extend the test.
"""


def test_per_step_selector_fires(tmp_path):
    """A -k selector in a PER-STEP local_checks block must be flagged."""
    result = _run_lint(tmp_path, "test-per-step-k.md", _SUBPLAN_PER_STEP_SELECTOR)
    assert result.returncode == 1, (
        f"Expected non-zero exit for a per-step -k selector, got exit "
        f"{result.returncode}. Per-step gates are where selectors actually "
        f"live; a frontmatter-only lint is effectively silent.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout
    assert "-k" in result.stdout


def test_per_step_bare_command_clean(tmp_path):
    """A per-step gate on the whole file must NOT be flagged."""
    result = _run_lint(tmp_path, "test-per-step-bare.md", _SUBPLAN_PER_STEP_NO_SELECTOR)
    assert result.returncode == 0, (
        f"Expected clean exit for a per-step whole-file gate, got exit "
        f"{result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout


def test_per_step_selector_justified_by_prose(tmp_path):
    """Prose naming the test file still suppresses the finding for a per-step gate.

    Guards the fix from over-correcting: stripping the gate blocks from the body
    must not also strip genuine prose justification.
    """
    result = _run_lint(tmp_path, "test-per-step-just.md", _SUBPLAN_PER_STEP_JUSTIFIED)
    assert result.returncode == 0, (
        f"Expected clean exit when prose names the test file, got exit "
        f"{result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
