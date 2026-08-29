#!/usr/bin/env python3
"""Tests for plan_lint e2e/device-poll local_check without env_prereq guard.

Covers:
  AC-1  e2e command + no env_prereqs -> 1 finding
  AC-2  same command + non-empty env_prereqs -> 0
  AC-3  same command + body references docs/loop/preflight.sh -> 0
  AC-4  plain unit-test command + no env_prereqs -> 0 (conservative)
  AC-5  message is ASCII; function in ALL_CHECKS
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
    # Seed a pytest.ini so the broad-suite-in-unbounded-project lint
    # (a-bare-pytest-is-bounded-by-config) doesn't fire on every fixture.
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --timeout=60\n", encoding="utf-8",
    )
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


# --- AC-1a: e2e command + no env_prereqs -> 1 finding (frontmatter local_check) ---

_SUBPLAN_E2E_NO_ENV = """\
---
plan: test-e2e-no-env
local_checks:
  - command: node e2e/home-routing.mjs
    timeout: 300
---

# Sub-plan: e2e command with no env_prereqs

An e2e local_check with no env_prereqs reachability probe.
"""


def test_e2e_command_no_env_prereqs(tmp_path):
    """AC-1a: frontmatter e2e command + no env_prereqs -> 1 finding."""
    result = _run_lint(tmp_path, "test-e2e-no-env.md", _SUBPLAN_E2E_NO_ENV)
    assert result.returncode == 1, (
        f"Expected non-zero exit, got {result.returncode}.\nstdout={result.stdout}"
    )
    assert "WARN" in result.stdout
    assert "env_prereqs" in result.stdout.lower() or "env_prereq" in result.stdout.lower()


# --- AC-1b: e2e command in per-step local_checks -> 1 finding ---

_SUBPLAN_E2E_STEP_CHECK = """\
---
plan: test-e2e-step-check
---

# Sub-plan: e2e in per-step local_checks

### Step 0 -- Run e2e
```yaml
local_checks:
  - command: npx playwright test tests/e2e/
    timeout: 120
```
"""


def test_e2e_in_step_local_checks(tmp_path):
    """AC-1b: per-step e2e command + no env_prereqs -> 1 finding."""
    result = _run_lint(tmp_path, "test-e2e-step.md", _SUBPLAN_E2E_STEP_CHECK)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- AC-1c: playwright command -> 1 finding ---

_SUBPLAN_PLAYWRIGHT = """\
---
plan: test-playwright
local_checks:
  - command: npx playwright test
    timeout: 120
---

# Sub-plan: playwright test
"""


def test_playwright_command(tmp_path):
    """AC-1c: playwright command + no env_prereqs -> 1 finding."""
    result = _run_lint(tmp_path, "test-playwright.md", _SUBPLAN_PLAYWRIGHT)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- AC-1d: e2e script with localhost URL -> 1 finding ---

_SUBPLAN_E2E_LOCALHOST = """\
---
plan: test-e2e-localhost
local_checks:
  - command: node e2e/smoke.mjs http://localhost:3000
    timeout: 30
---

# Sub-plan: e2e script hitting localhost
"""


def test_e2e_with_localhost_url(tmp_path):
    """AC-1d: e2e script + localhost URL + no env_prereqs -> 1 finding."""
    result = _run_lint(tmp_path, "test-e2e-localhost.md", _SUBPLAN_E2E_LOCALHOST)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- AC-1e: devtools command -> 1 finding ---

_SUBPLAN_DEVTOOLS = """\
---
plan: test-devtools
local_checks:
  - command: python check_devtools.py --browserUrl http://127.0.0.1:9222
    timeout: 30
---

# Sub-plan: devtools command
"""


def test_devtools_command(tmp_path):
    """AC-1e: devtools command + no env_prereqs -> 1 finding."""
    result = _run_lint(tmp_path, "test-devtools.md", _SUBPLAN_DEVTOOLS)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- AC-2: same command + non-empty env_prereqs -> 0 ---

_SUBPLAN_E2E_WITH_ENV = """\
---
plan: test-e2e-with-env
env_prereqs:
  - description: dev server reachable
    verify_cmd: curl -sf http://localhost:3000
local_checks:
  - command: /opt/homebrew/bin/node e2e/home-routing.mjs
    timeout: 300
---

# Sub-plan: e2e with env_prereqs

This sub-plan correctly declares env_prereqs.
"""


def test_e2e_with_env_prereqs(tmp_path):
    """AC-2: e2e command + env_prereqs declared -> no finding."""
    result = _run_lint(tmp_path, "test-e2e-env.md", _SUBPLAN_E2E_WITH_ENV)
    assert result.returncode == 0, (
        f"Expected clean exit, got {result.returncode}.\nstdout={result.stdout}"
    )
    assert "WARN" not in result.stdout


# --- AC-3: same command + body references docs/loop/preflight.sh -> 0 ---

_SUBPLAN_E2E_PREFLIGHT = """\
---
plan: test-e2e-preflight
local_checks:
  - command: /opt/homebrew/bin/node e2e/home-routing.mjs
    timeout: 300
---

# Sub-plan: e2e with preflight reference

Run docs/loop/preflight.sh before this sub-plan to ensure the dev server is up.
"""


def test_e2e_with_preflight_ref(tmp_path):
    """AC-3: e2e command + preflight.sh reference -> no finding."""
    result = _run_lint(tmp_path, "test-e2e-preflight.md", _SUBPLAN_E2E_PREFLIGHT)
    assert result.returncode == 0, (
        f"Expected clean exit, got {result.returncode}.\nstdout={result.stdout}"
    )
    assert "WARN" not in result.stdout


# --- AC-4: plain unit-test command + no env_prereqs -> 0 (conservative) ---

_SUBPLAN_PLAIN_PYTEST = """\
---
plan: test-plain-pytest
local_checks:
  - command: python3 -m pytest tests/test_x.py -q
    timeout: 60
---

# Sub-plan: plain pytest

A unit-test command should not trigger the e2e guard.
"""


def test_plain_pytest_no_env(tmp_path):
    """AC-4: plain pytest command + no env_prereqs -> no finding (conservative)."""
    result = _run_lint(tmp_path, "test-plain-pytest.md", _SUBPLAN_PLAIN_PYTEST)
    assert result.returncode == 0, (
        f"Expected clean exit, got {result.returncode}.\nstdout={result.stdout}"
    )
    assert "WARN" not in result.stdout


_SUBPLAN_VITEST = """\
---
plan: test-vitest
local_checks:
  - command: /opt/homebrew/bin/npx vitest run
    timeout: 60
---

# Sub-plan: vitest run

Baseline-green on all platforms 2026-06-28. Full vitest suite as gate.
"""


def test_vitest_no_env(tmp_path):
    """AC-4 extra: vitest run + no env_prereqs -> no finding."""
    result = _run_lint(tmp_path, "test-vitest.md", _SUBPLAN_VITEST)
    assert result.returncode == 0
    assert "WARN" not in result.stdout


_SUBPLAN_TSC = """\
---
plan: test-tsc
local_checks:
  - command: /opt/homebrew/bin/npx tsc --noEmit
    timeout: 60
---

# Sub-plan: tsc check
"""


def test_tsc_no_env(tmp_path):
    """AC-4 extra: tsc --noEmit + no env_prereqs -> no finding."""
    result = _run_lint(tmp_path, "test-tsc.md", _SUBPLAN_TSC)
    assert result.returncode == 0
    assert "WARN" not in result.stdout


# --- AC-5a: function is in ALL_CHECKS ---

def test_function_in_all_checks():
    """AC-5a: lint_e2e_check_without_env_prereq is registered in ALL_CHECKS."""
    import sys as _sys
    _sys.path.insert(0, str(_PLAN_LINT.parent))
    import plan_lint as p
    assert p.lint_e2e_check_without_env_prereq in p.ALL_CHECKS, (
        "lint_e2e_check_without_env_prereq not found in ALL_CHECKS"
    )


# --- AC-5b: finding message is ASCII ---

def test_finding_message_is_ascii(tmp_path):
    """AC-5b: the finding message contains only ASCII characters."""
    result = _run_lint(tmp_path, "test-ascii.md", _SUBPLAN_E2E_NO_ENV)
    assert result.returncode == 1
    for line in result.stdout.splitlines():
        if line.startswith("WARN:"):
            line_bytes = line.encode("utf-8")
            try:
                line_bytes.decode("ascii")
            except UnicodeDecodeError:
                pytest.fail(f"Finding message contains non-ASCII: {line}")


# --- Edge: no local_checks at all -> 0 ---

_SUBPLAN_NO_CHECKS = """\
---
plan: test-no-checks
---

# Sub-plan: no local_checks at all
"""


def test_no_local_checks(tmp_path):
    """No local_checks anywhere -> no finding."""
    result = _run_lint(tmp_path, "test-no-checks.md", _SUBPLAN_NO_CHECKS)
    assert result.returncode == 0
    assert "WARN" not in result.stdout


# --- Edge: empty env_prereqs list -> still flags ---

_SUBPLAN_EMPTY_ENV = """\
---
plan: test-empty-env
env_prereqs: []
local_checks:
  - command: node e2e/home-routing.mjs
    timeout: 300
---

# Sub-plan: empty env_prereqs list
"""


def test_empty_env_prereqs_list(tmp_path):
    """Empty env_prereqs list -> still flags (no probe declared)."""
    result = _run_lint(tmp_path, "test-empty-env.md", _SUBPLAN_EMPTY_ENV)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- Edge: .spec. file pattern -> 1 finding ---

_SUBPLAN_SPEC = """\
---
plan: test-spec
local_checks:
  - command: npx playwright test tests/login.spec.ts
    timeout: 120
---

# Sub-plan: spec file
"""


def test_spec_file_pattern(tmp_path):
    """.spec. file in command -> 1 finding."""
    result = _run_lint(tmp_path, "test-spec.md", _SUBPLAN_SPEC)
    assert result.returncode == 1
    assert "WARN" in result.stdout


# --- Back-compat: existing plan_lint test files still pass ---

def test_existing_plan_lint_tests_still_pass():
    """Back-compat: the existing plan_lint test files pass."""
    test_files = [
        _HERE / "test_plan_lint.py",
        _HERE / "test_plan_lint_brittle_assertion.py",
        _HERE / "test_plan_lint_contract_change.py",
        _PLAN_LINT.parent.parent / "tests" / "test_plan_lint_escaped_bug.py",
        _HERE / "test_plan_lint_frontmatter_path.py",
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
