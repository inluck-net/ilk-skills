"""Guard against rmtree of live data directories.

Provides:
- ``live_roots()``: the real user's ``.ilk-data`` and ``.ilk`` directories.
- ``allowed_roots(rootpath)``: the pytest rootpath and the system temp dir.
- ``refuses(path, *, live_roots, allowed_roots)``: pure predicate.
- ``LiveDataRmtreeRefused``: exception raised by the conftest fixture.
- ``scan_source(text, filename)``: AST scan flagging rmtree of real-home paths.
"""
from __future__ import annotations

import ast
import os
import pwd
import tempfile
from pathlib import Path


class LiveDataRmtreeRefused(PermissionError):
    """Raised when a test attempts to rmtree a live data directory."""


def live_roots() -> list[Path]:
    """Return the real user's ``.ilk-data`` and ``.ilk`` directories.

    Uses ``pwd.getpwuid`` to find the real home, never ``$HOME`` (tests pin
    ``HOME``).  Returns ``[]`` where ``pwd`` is unavailable (Windows).
    """
    try:
        pw_dir = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, AttributeError):
        return []
    return [pw_dir / ".ilk-data", pw_dir / ".ilk"]


def allowed_roots(rootpath: Path) -> list[Path]:
    """Return directories that are always safe to rmtree.

    *rootpath* is the pytest rootpath (``request.config.rootpath``).
    """
    return [
        rootpath.resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]


def refuses(
    path: Path | str,
    *,
    live_roots: list[Path],
    allowed_roots: list[Path],
) -> bool:
    """True when *path* is under a live root and not under an allowed root.

    Pure: every input is injected.
    """
    resolved = Path(path).resolve()
    under_live = any(
        resolved == root or resolved.is_relative_to(root)
        for root in live_roots
    )
    if not under_live:
        return False
    under_allowed = any(
        resolved == root or resolved.is_relative_to(root)
        for root in allowed_roots
    )
    return not under_allowed


_REAL_HOME_SOURCES = (
    "getpwuid",
    "pw_dir",
    "Path.home",
    "expanduser",
)

_ENV_HOME_KEYS = ("HOME",)

_STRING_LIVE_MARKERS = (".ilk-data", "/.ilk/")


def scan_source(text: str, filename: str) -> list[str]:
    """AST scan: flag ``shutil.rmtree`` calls whose path derives from the real home.

    Returns a list of ``"<file>:<line>: rmtree of a path derived from the real home"``
    strings.  A line ending in ``# rmtree-guard: ok <reason>`` is waived; a waiver
    with an empty reason is itself a finding.
    """
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError:
        return []

    findings: list[str] = []

    # Collect module-level Name nodes assigned from real-home sources.
    module_level_real_names: set[str] = set()

    def _is_real_home_derived(node: ast.expr) -> bool:
        """Check if an AST node's source code mentions a real-home derivation."""
        # Check for getpwuid, pw_dir, Path.home, expanduser, os.environ["HOME"], ".ilk-data", "/.ilk/"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("home", "expanduser"):
                    return True
                if func.attr == "getpwuid":
                    return True
            if isinstance(func, ast.Name):
                if func.id == "getpwuid":
                    return True
            # Check for os.environ.get("HOME") or os.environ["HOME"]
            if isinstance(func, ast.Attribute):
                if func.attr in ("get", "__getitem__"):
                    if isinstance(func.value, ast.Attribute):
                        if func.value.attr == "environ":
                            return True
        if isinstance(node, ast.Attribute):
            if node.attr in _REAL_HOME_SOURCES:
                return True
        if isinstance(node, ast.Name):
            if node.id in module_level_real_names:
                return True
        # Check string constants for .ilk-data or /.ilk/
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in _STRING_LIVE_MARKERS):
                return True
        # Check joined paths (e.g., pw_dir / ".ilk-data")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            # Recursively check both sides
            return _is_real_home_derived(node.left) or _is_real_home_derived(node.right)
        return False

    def _is_real_home_name(node: ast.expr) -> bool:
        """Check if a Name node is in the set of module-level real-home names."""
        if isinstance(node, ast.Name):
            return node.id in module_level_real_names
        return False

    class _ModuleLevelCollector(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if _is_real_home_derived(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module_level_real_names.add(target.id)
            self.generic_visit(node)

    _ModuleLevelCollector().visit(tree)

    class _RmtreeChecker(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Check if this is shutil.rmtree(...) or bare rmtree(...)
            is_rmtree = False
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "rmtree" and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "shutil":
                        is_rmtree = True
            elif isinstance(node.func, ast.Name):
                if node.func.id == "rmtree":
                    is_rmtree = True

            if is_rmtree and node.args:
                arg = node.args[0]
                # Check waiver comment on the same line.
                line = text.splitlines()[node.lineno - 1] if node.lineno <= len(text.splitlines()) else ""
                if "# rmtree-guard:" in line:
                    parts = line.split("# rmtree-guard:", 1)[1].strip()
                    if parts.startswith("ok"):
                        reason = parts[2:].strip()
                        if not reason:
                            findings.append(
                                f"{filename}:{node.lineno}: rmtree of a path derived from the real home (waiver with empty reason)"
                            )
                        return  # waived

                # Check if the argument is derived from a real-home source.
                if _is_real_home_derived(arg) or _is_real_home_name(arg):
                    findings.append(
                        f"{filename}:{node.lineno}: rmtree of a path derived from the real home"
                    )
            self.generic_visit(node)

    _RmtreeChecker().visit(tree)
    return findings