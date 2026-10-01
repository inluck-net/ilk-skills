"""Tests for lint_gate_misses_a_test_that_pins_its_file.

Part of sub-plan: a-gate-names-the-tests-that-pin-its-files
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBPLAN_TEMPLATE = textwrap.dedent("""\
    ---
    plan: {slug}
    status: pending
    current_step: 0
    estimated_steps: 2
    scope_paths:
    {scope_paths}
    local_checks:
      - command: "{gate_cmd}"
    ---

    # Sub-plan: {slug}

    ## Steps

    ### Step 0 — test
    ```yaml
    local_checks:
      - command: "{gate_cmd}"
    ```
    """)


def _write_subplan(
    tmp_path: Path,
    slug: str = "test-plan",
    scope_paths: list[str] | None = None,
    gate_cmd: str = "python3 -m pytest tests/test_other.py -q",
) -> str:
    """Return sub-plan text with the given scope_paths and gate."""
    sp_lines = "\n".join(f"    - {p}" for p in (scope_paths or []))
    return _SUBPLAN_TEMPLATE.format(
        slug=slug,
        scope_paths=sp_lines,
        gate_cmd=gate_cmd,
    )


def _run_lint(
    tmp_path: Path,
    subplan_text: str,
    project_root: Path | None = None,
    slug: str = "test-plan",
) -> list[str]:
    """Run the lint and return findings."""
    from importlib import import_module

    mod = import_module("skills.ilk-loop.scripts.plan_lint")

    # Override _PROJECT_ROOT for the test.
    if project_root is not None:
        old = getattr(mod, "_PROJECT_ROOT", None)
        mod._PROJECT_ROOT = project_root
        try:
            return mod.lint_gate_misses_a_test_that_pins_its_file(subplan_text, slug)
        finally:
            if old is not None:
                mod._PROJECT_ROOT = old
            else:
                delattr(mod, "_PROJECT_ROOT")
    return mod.lint_gate_misses_a_test_that_pins_its_file(subplan_text, slug)


# ---------------------------------------------------------------------------
# AC-1: the case
# ---------------------------------------------------------------------------


def test_ac1_data_file_pinned_by_test_not_in_gate(tmp_path: Path) -> None:
    """scope_paths has a data file; a test pins it; no gate selects that test."""
    # Create project structure.
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    # Data file referenced in scope_paths.
    provisioning = proj / "provisioning"
    provisioning.mkdir()
    (provisioning / "config.template.json").write_text("{}")

    # Test file that pins the data file.
    (tests / "test_pin.py").write_text(
        'import json\njson.load(open("provisioning/config.template.json"))\n'
    )

    # Gate runs a different test.
    (tests / "test_other.py").write_text("def test_ok(): pass\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    assert "config.template.json" in findings[0]
    assert "test_pin.py" in findings[0]


# ---------------------------------------------------------------------------
# AC-2: controls — gate covers the test
# ---------------------------------------------------------------------------


def test_ac2_gate_names_test_file(tmp_path: Path) -> None:
    """Gate names the test file directly ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    provisioning = proj / "provisioning"
    provisioning.mkdir()
    (provisioning / "config.template.json").write_text("{}")
    (tests / "test_pin.py").write_text(
        'import json\njson.load(open("provisioning/config.template.json"))\n'
    )

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest tests/test_pin.py -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


def test_ac2_gate_names_test_directory(tmp_path: Path) -> None:
    """Gate names the tests/ directory ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    provisioning = proj / "provisioning"
    provisioning.mkdir()
    (provisioning / "config.template.json").write_text("{}")
    (tests / "test_pin.py").write_text(
        'import json\njson.load(open("provisioning/config.template.json"))\n'
    )

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest tests/ -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


def test_ac2_whole_suite_gate(tmp_path: Path) -> None:
    """Whole-suite gate ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    provisioning = proj / "provisioning"
    provisioning.mkdir()
    (provisioning / "config.template.json").write_text("{}")
    (tests / "test_pin.py").write_text(
        'import json\njson.load(open("provisioning/config.template.json"))\n'
    )

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


# ---------------------------------------------------------------------------
# AC-3: .py, docs/, glob ⇒ no finding
# ---------------------------------------------------------------------------


def test_ac3_py_scope_path(tmp_path: Path) -> None:
    """A .py scope path is covered by shared-module lint, not this one."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (proj / "mymod.py").write_text("x = 1\n")
    (tests / "test_mymod.py").write_text("def test_ok(): pass\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["mymod.py"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


def test_ac3_docs_scope_path(tmp_path: Path) -> None:
    """A docs/ scope path ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    docs = proj / "docs"
    docs.mkdir()
    (docs / "design.md").write_text("# design\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["docs/design.md"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


def test_ac3_glob_scope_path(tmp_path: Path) -> None:
    """A glob scope path ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/*.json"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_lint(tmp_path, text, project_root=proj)
    assert findings == []


# ---------------------------------------------------------------------------
# AC-4: no project root ⇒ no finding
# ---------------------------------------------------------------------------


def test_ac4_no_project_root(tmp_path: Path) -> None:
    """No project root resolvable ⇒ no finding and no exception."""
    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    # Pass a non-existent project root.
    fake_root = tmp_path / "nonexistent"
    findings = _run_lint(tmp_path, text, project_root=fake_root)
    assert findings == []