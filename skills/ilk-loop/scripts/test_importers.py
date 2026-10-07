r"""Shared helper: find test files that import changed modules.

``importer_tests(project, changed)`` returns sorted repo-relative test
files whose ``ast`` imports name the module of any changed non-test
``.py`` file.  Used by:

- ``run_local_checks._synthesize_mention_check`` (step gate)
- ``verification_record.compute_suite_scope`` (verify scope)
- ``plan_lint._discover_tests_by_import`` (lint, thin wrapper)
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path


def _find_testpaths(project_root: Path) -> list[Path]:
    """Return configured test directories, falling back to ``tests/``.

    Reads ``testpaths`` from pytest.ini / setup.cfg / pyproject.toml.
    """
    import re

    for ini_name, section_start, key_re in [
        ("pytest.ini", "[pytest]", r"^testpaths\s*=\s*(.+)"),
        ("setup.cfg", "[tool:pytest]", r"^testpaths\s*=\s*(.+)"),
    ]:
        ini = project_root / ini_name
        if not ini.exists():
            continue
        in_section = False
        for line in ini.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("["):
                in_section = line.strip().startswith(section_start)
            elif in_section:
                m = re.match(key_re, line.strip())
                if m:
                    return [project_root / d.strip()
                            for d in m.group(1).split()]
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8-sig")
        in_section = False
        for line in text.splitlines():
            if line.strip() == "[tool.pytest.ini_options]":
                in_section = True
            elif line.strip().startswith("["):
                in_section = False
            elif in_section:
                m = re.match(r"^testpaths\s*=\s*\[(.+)\]", line.strip())
                if m:
                    return [project_root / d.strip().strip('"').strip("'")
                            for d in m.group(1).split(",")]
    default = project_root / "tests"
    return [default] if default.is_dir() else []


_TEST_DIR_NAMES = {"test", "tests", "spec", "specs"}


def _is_test_file(rel_path: str) -> bool:
    """Heuristic: does *rel_path* look like a test file?"""
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts[:-1]:
        if part in _TEST_DIR_NAMES:
            return True
    filename = parts[-1]
    return filename.startswith("test_") and filename.endswith(".py")


def _module_name(changed_file: str) -> str | None:
    """Derive the importable module name from a changed file.

    Returns the file stem (e.g. ``widget`` from ``src/widget.py``),
    matching ``plan_lint._resolve_module_name`` semantics.

    Returns ``None`` for non-Python files or test files.
    """
    if not changed_file.endswith(".py"):
        return None
    if _is_test_file(changed_file):
        return None
    return Path(changed_file).stem


def importer_tests(project: Path, changed: list[str]) -> list[str]:
    """Return sorted repo-relative test files that import changed modules.

    For each changed non-test ``.py`` file, finds git-tracked ``test_*.py``
    files under the project's configured test directories whose ``ast``
    imports name the module of the changed file (``import x``,
    ``from x import``, ``from pkg.x import``).

    Parameters
    ----------
    project:
        Repo root.
    changed:
        Repo-relative paths of changed files.

    Returns
    -------
    list[str]
        Sorted repo-relative test file paths.  Empty if no changed file
        has importers.
    """
    modules = [(f, _module_name(f)) for f in changed]
    modules = [(f, m) for f, m in modules if m is not None]
    if not modules:
        return []

    # Discover test directories
    test_dirs = _find_testpaths(project)

    # Collect git-tracked test files
    try:
        ls = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if ls.returncode != 0:
        return []

    tracked = set(ls.stdout.split("\0"))

    # Build set of test files to scan (only those under test dirs, tracked)
    test_files: set[str] = set()
    for td in test_dirs:
        if not td.is_dir():
            continue
        for py in td.rglob("test_*.py"):
            rel = str(py.relative_to(project))
            if rel in tracked:
                test_files.add(rel)

    if not test_files:
        return []

    # Parse each test file once, cache its set of imported module segments.
    # For ``import src.widget``, segments are ``{src, widget}``.
    # For ``from src.widget import Widget``, segments are ``{src, widget}``.
    # This matches ``plan_lint._discover_tests_by_import`` which checks
    # ``top == module_name`` where ``top`` is the first segment, but also
    # handles sub-package imports like ``from pkg.widget import X`` where
    # the changed file is ``pkg/widget.py`` (stem ``widget``).
    test_imports: dict[str, set[str]] = {}
    for rel in test_files:
        try:
            src = (project / rel).read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src, filename=rel)
        except (SyntaxError, OSError):
            continue
        segments: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    segments.update(alias.name.split("."))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    segments.update(node.module.split("."))
        test_imports[rel] = segments

    # Find tests that import any changed module (matching on any segment)
    result: set[str] = set()
    for _changed_file, mod_name in modules:
        for rel, segments in test_imports.items():
            if mod_name in segments:
                result.add(rel)

    return sorted(result)