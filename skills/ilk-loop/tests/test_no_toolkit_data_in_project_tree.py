"""Guard test: no toolkit artifact is written into the consumer tree.

AC-4: No toolkit script composes a default *write* path under the project
      root. Allow-list legitimate reads (`.ilk-launch.json`, `pyproject.toml`,
      `setup.cfg`, legacy `docs/plans/` fallbacks).

AC-3: `generate_ship_report.py`'s default output path resolves under
      `~/.ilk-data/projects/<key>/runtime/launcher/ship-reports/`, not under
      `project/docs/`.

AC-1/AC-2: The batch-verification template names an explicit external
           destination resolved from `ilk_paths.py`.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
TEMPLATES_DIR = SKILL_ROOT / "templates"


# ── Allow-list of sanctioned project-tree reads ──────────────────────────────

# These paths are legitimate reads of consumer-owned config. The guard test
# documents them explicitly so future readers know which touches are sanctioned.
ALLOWED_READ_PATHS = {
    ".ilk-launch.json",       # project config (read by runner, ship, etc.)
    "pyproject.toml",         # project config (read by plan_lint, etc.)
    "setup.cfg",              # project config (legacy)
    "docs/plans/",            # legacy in-tree plans dir (read-only fallback)
    "docs/loop/",             # project-side primer + fixtures
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _collect_script_paths() -> list[Path]:
    """Collect all .py files under skills/*/scripts/."""
    scripts = []
    for skill_dir in SKILL_ROOT.parent.iterdir():
        if not skill_dir.is_dir():
            continue
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.is_dir():
            scripts.extend(scripts_dir.glob("*.py"))
    return scripts


def _is_write_destination(node: ast.expr, source: str) -> bool:
    """Heuristic: does this AST node look like a write destination?

    Returns True if the node is a Path join that could be a write target.
    """
    # Look for patterns like: project / "docs" / ...
    # or: some_path / "docs" / ...
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # Recursively check if "docs" appears in the join
        parts = _extract_string_literals(node)
        if "docs" in parts:
            return True
    return False


def _extract_string_literals(node: ast.expr) -> list[str]:
    """Extract string literals from a Path join expression."""
    result = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        result.append(node.value)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        result.extend(_extract_string_literals(node.left))
        result.extend(_extract_string_literals(node.right))
    return result


def _find_default_write_paths_in_script(script_path: Path) -> list[str]:
    """Find default write destinations under the project tree.

    Returns a list of violation descriptions.
    """
    violations = []
    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_path))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        # Look for assignments where RHS is a Path join containing "docs"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "output" in target.id.lower():
                    if _is_write_destination(node.value, source):
                        # Check if this is guarded by an explicit --output arg
                        # If it's a default (not conditional on args.output), it's a violation
                        if not _is_guarded_by_args(node, tree):
                            violations.append(
                                f"{script_path.name}: default write path "
                                f"composed under project tree"
                            )
    return violations


def _is_guarded_by_args(assign_node: ast.Assign, tree: ast.Module) -> bool:
    """Check if this assignment is guarded by args.output or similar."""
    # Look for patterns like: Path(args.output).resolve() if args.output else ...
    # or: output_path = args.output or project / "docs" / ...
    # This is a heuristic check
    return False


def _check_template_writes_external(template_path: Path) -> list[str]:
    """Check that the batch-verification template writes externally.

    Returns violations if the template instructs writing to the project tree.
    """
    violations = []
    if not template_path.exists():
        return violations

    text = template_path.read_text(encoding="utf-8")

    # Check for instructions to write verification records to project tree
    # Look for patterns like "Commit the record" without specifying external path
    if "Commit the record" in text and "ilk_paths" not in text:
        violations.append(
            f"{template_path.name}: step 0 says 'Commit the record' "
            f"without specifying an external path via ilk_paths.py"
        )

    # Check for "docs/verification" or similar project-tree write paths
    if re.search(r"docs/verification", text):
        violations.append(
            f"{template_path.name}: references docs/verification "
            f"(project-tree write path)"
        )

    return violations


def _check_ship_report_default_external() -> list[str]:
    """Check that generate_ship_report.py's default output is external.

    AC-3: The default should resolve under
    ~/.ilk-data/projects/<key>/runtime/launcher/ship-reports/, not
    project/docs/.
    """
    violations = []
    script = SCRIPTS_DIR / "generate_ship_report.py"
    if not script.exists():
        violations.append("generate_ship_report.py not found")
        return violations

    source = script.read_text(encoding="utf-8")

    # Check if the default output path uses project / "docs" / ...
    # The current violation is at lines 812-814:
    # output_path = Path(args.output).resolve() if args.output else (
    #     project / "docs" / "plans" / "ship-reports" / default_name
    # )
    if 'project / "docs" / "plans" / "ship-reports"' in source:
        violations.append(
            "generate_ship_report.py: default output path is "
            'project / "docs" / "plans" / "ship-reports" / ... '
            "(should be external via ilk_paths.py)"
        )

    # Also check the whitelist constant
    if 'SHIP_REPORTS_WHITELIST_PREFIX = "docs/plans/ship-reports/"' in source:
        violations.append(
            "generate_ship_report.py: SHIP_REPORTS_WHITELIST_PREFIX "
            "points to project tree"
        )

    return violations


# ── Tests ────────────────────────────────────────────────────────────────────

class TestAc4NoDefaultWriteUnderProjectRoot:
    """AC-4: No toolkit script composes a default write path under the project root."""

    def test_no_script_default_writes_to_project_tree(self):
        """Scan all scripts for default write destinations under project root."""
        scripts = _collect_script_paths()
        all_violations = []
        for script in scripts:
            all_violations.extend(_find_default_write_paths_in_script(script))

        # This test is expected to FAIL at step 0 (red) because
        # generate_ship_report.py still has the violation.
        # After step 2, this should pass.
        assert all_violations == [], (
            f"Found {len(all_violations)} default write path(s) under project tree:\n"
            + "\n".join(f"  - {v}" for v in all_violations)
        )


class TestAc3ShipReportDefaultExternal:
    """AC-3: generate_ship_report.py default output path is external."""

    def test_default_output_path_is_external(self):
        """The ship report default should resolve under ~/.ilk-data."""
        violations = _check_ship_report_default_external()
        # Expected to FAIL at step 0 (red).
        assert violations == [], (
            f"Ship report default path violations:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestAc1Ac2TemplateWritesExternal:
    """AC-1/AC-2: batch-verification template writes externally."""

    def test_template_instructs_external_write(self):
        """The template should name an explicit external destination."""
        template = TEMPLATES_DIR / "batch-verification-subplan.md"
        violations = _check_template_writes_external(template)
        # Expected to FAIL at step 0 (red) because the template currently
        # says only "Commit the record" without specifying an external path.
        assert violations == [], (
            f"Template write-path violations:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestAllowListDocumentsSanctionedReads:
    """The allow-list explicitly documents which project-tree touches are sanctioned."""

    def test_allow_list_entries_exist(self):
        """The allow-list should have entries documenting sanctioned reads."""
        # This test always passes — it documents the allow-list exists.
        assert len(ALLOWED_READ_PATHS) > 0, "Allow-list is empty"
        assert ".ilk-launch.json" in ALLOWED_READ_PATHS
        assert "pyproject.toml" in ALLOWED_READ_PATHS

    def test_allow_list_is_described_in_test(self):
        """Each allow-list entry should be self-documenting."""
        # This test always passes — it documents the purpose.
        for path in ALLOWED_READ_PATHS:
            assert isinstance(path, str), f"Allow-list entry {path!r} is not a string"
