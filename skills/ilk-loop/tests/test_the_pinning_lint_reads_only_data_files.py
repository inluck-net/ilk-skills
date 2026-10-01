"""Tests for the pinning lint and shared-module lint changes.

Part of sub-plan: the-pinning-lint-reads-only-data-files
Covers AC-1, AC-2, AC-3, AC-4, AC-6, AC-7, AC-8.
"""
from __future__ import annotations

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


def _run_pinning_lint(
    tmp_path: Path,
    subplan_text: str,
    project_root: Path | None = None,
    slug: str = "test-plan",
) -> list[str]:
    """Run the pinning lint and return findings."""
    from importlib import import_module

    mod = import_module("skills.ilk-loop.scripts.plan_lint")

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


def _run_shared_module_lint(
    tmp_path: Path,
    subplan_text: str,
    project_root: Path | None = None,
    slug: str = "test-plan",
) -> list[str]:
    """Run the shared-module lint and return findings."""
    from importlib import import_module

    mod = import_module("skills.ilk-loop.scripts.plan_lint")

    if project_root is not None:
        old = getattr(mod, "_PROJECT_ROOT", None)
        mod._PROJECT_ROOT = project_root
        try:
            return mod.lint_shared_module_gate(subplan_text, slug)
        finally:
            if old is not None:
                mod._PROJECT_ROOT = old
            else:
                delattr(mod, "_PROJECT_ROOT")
    return mod.lint_shared_module_gate(subplan_text, slug)


# ---------------------------------------------------------------------------
# Pinning lint: AC-1, AC-2, AC-3, AC-4
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_shell_file_pinned_by_test_not_in_gate(tmp_path: Path) -> None:
    """A .sh scope_paths entry pinned by a test outside the gates ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()
    skills = proj / "skills" / "x"
    skills.mkdir(parents=True)

    (skills / "run.sh").write_text("#!/bin/bash\necho hello\n")
    (tests / "test_run.py").write_text(
        'import pathlib\npathlib.Path("skills/x/run.sh").exists()\n'
    )
    (tests / "test_other.py").write_text("def test_ok(): pass\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["skills/x/run.sh"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_pinning_lint(tmp_path, text, project_root=proj)
    assert findings == [], f"Expected no finding for .sh file, got {findings}"


def test_ac2_markdown_entry_no_finding(tmp_path: Path) -> None:
    """A .md scope_paths entry ⇒ no finding.

    NOTE: The lint already skips .md files today (they're not in the data-file
    candidate list).  This test verifies the current behavior.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()
    docs = proj / "docs"
    docs.mkdir()

    (docs / "design.md").write_text("# design\n")
    (tests / "test_design.py").write_text(
        'import pathlib\npathlib.Path("docs/design.md").exists()\n'
    )
    (tests / "test_other.py").write_text("def test_ok(): pass\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["docs/design.md"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_pinning_lint(tmp_path, text, project_root=proj)
    assert findings == [], f"Expected no finding for .md file, got {findings}"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_batch_verification_whole_suite_gate(tmp_path: Path) -> None:
    """A batch_verification: true sub-plan with install.json pinned outside gates ⇒ no finding."""
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (proj / "install.json").write_text("{}")
    (tests / "test_install.py").write_text(
        'import json\njson.load(open("install.json"))\n'
    )
    (tests / "test_other.py").write_text("def test_ok(): pass\n")

    text = textwrap.dedent("""\
        ---
        plan: test-batch-verify
        status: pending
        current_step: 0
        estimated_steps: 2
        batch_verification: true
        scope_paths:
            - install.json
        local_checks:
          - command: "python3 skills/ilk-loop/scripts/verification_record.py --run-suite --scope full"
        ---

        # Sub-plan: test-batch-verify

        ## Steps

        ### Step 0 — test
        ```yaml
        local_checks:
          - command: "python3 skills/ilk-loop/scripts/verification_record.py --run-suite --scope full"
        ```
        """)

    findings = _run_pinning_lint(tmp_path, text, project_root=proj)
    assert findings == [], f"Expected no finding for batch_verification sub-plan, got {findings}"


def test_ac4_data_file_pinned_by_test_not_in_gate(tmp_path: Path) -> None:
    """provisioning/config.template.json pinned by tests/test_pin.py with no selecting gate ⇒ one finding."""
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
    (tests / "test_other.py").write_text("def test_ok(): pass\n")

    text = _write_subplan(
        tmp_path,
        scope_paths=["provisioning/config.template.json"],
        gate_cmd="python3 -m pytest tests/test_other.py -q",
    )

    findings = _run_pinning_lint(tmp_path, text, project_root=proj)
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    assert "config.template.json" in findings[0]
    assert "test_pin.py" in findings[0]


# ---------------------------------------------------------------------------
# Shared-module lint: AC-6, AC-7, AC-8
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac6_shared_module_caller_tests_covered_across_gates(tmp_path: Path) -> None:
    """No shared-module finding when caller tests are in the union of all gates.

    Setup: scope_paths has pipeline.py, imported by batch.py and reap.py.
    No tests/test_pipeline.py.  One gate runs tests/test_batch.py, another
    runs tests/test_reap.py.

    Today the lint checks each gate individually, so it fires even though
    the union covers all callers.  The new rule checks the union instead.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (proj / "pipeline.py").write_text("def run(): pass\n")
    (proj / "batch.py").write_text("from pipeline import run\n")
    (proj / "reap.py").write_text("from pipeline import run\n")

    (tests / "test_batch.py").write_text("def test_batch(): pass\n")
    (tests / "test_reap.py").write_text("def test_reap(): pass\n")

    text = textwrap.dedent("""\
        ---
        plan: test-shared-module
        status: pending
        current_step: 0
        estimated_steps: 2
        scope_paths:
            - pipeline.py
        local_checks: []
        ---

        # Sub-plan: test-shared-module

        ## Steps

        ### Step 0 — test
        ```yaml
        local_checks:
          - command: "python3 -m pytest tests/test_batch.py -q"
          - command: "python3 -m pytest tests/test_reap.py -q"
        ```
        """)

    findings = _run_shared_module_lint(tmp_path, text, project_root=proj)
    assert findings == [], f"Expected no shared-module finding, got {findings}"


def test_ac7_shared_module_caller_test_missing_from_all_gates(tmp_path: Path) -> None:
    """One shared-module finding when reap test is missing from every gate.

    Same setup as AC-6 but only test_batch.py is in the gates.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (proj / "pipeline.py").write_text("def run(): pass\n")
    (proj / "batch.py").write_text("from pipeline import run\n")
    (proj / "reap.py").write_text("from pipeline import run\n")

    (tests / "test_batch.py").write_text("def test_batch(): pass\n")
    (tests / "test_reap.py").write_text("def test_reap(): pass\n")

    text = textwrap.dedent("""\
        ---
        plan: test-shared-module
        status: pending
        current_step: 0
        estimated_steps: 2
        scope_paths:
            - pipeline.py
        local_checks: []
        ---

        # Sub-plan: test-shared-module

        ## Steps

        ### Step 0 — test
        ```yaml
        local_checks:
          - command: "python3 -m pytest tests/test_batch.py -q"
        ```
        """)

    findings = _run_shared_module_lint(tmp_path, text, project_root=proj)
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    assert "test_reap.py" in findings[0]


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac8_shared_module_own_test_exists_not_in_gate(tmp_path: Path) -> None:
    """One shared-module finding when module test exists but no gate runs it.

    Same setup as AC-6 but tests/test_pipeline.py exists and neither gate
    runs it.  Today the lint emits a finding but doesn't name the module's
    own test — it only names missed caller tests.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (proj / "pipeline.py").write_text("def run(): pass\n")
    (proj / "batch.py").write_text("from pipeline import run\n")
    (proj / "reap.py").write_text("from pipeline import run\n")

    (tests / "test_batch.py").write_text("def test_batch(): pass\n")
    (tests / "test_reap.py").write_text("def test_reap(): pass\n")
    (tests / "test_pipeline.py").write_text("def test_pipeline(): pass\n")

    text = textwrap.dedent("""\
        ---
        plan: test-shared-module
        status: pending
        current_step: 0
        estimated_steps: 2
        scope_paths:
            - pipeline.py
        local_checks: []
        ---

        # Sub-plan: test-shared-module

        ## Steps

        ### Step 0 — test
        ```yaml
        local_checks:
          - command: "python3 -m pytest tests/test_batch.py -q"
          - command: "python3 -m pytest tests/test_reap.py -q"
        ```
        """)

    findings = _run_shared_module_lint(tmp_path, text, project_root=proj)
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    assert "test_pipeline.py" in findings[0]