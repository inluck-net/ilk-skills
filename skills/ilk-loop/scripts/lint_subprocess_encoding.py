#!/usr/bin/env python3
"""Subprocess capture-encoding lint (FM-0003 guard).

AST-based detector that flags any ``subprocess.run`` / ``subprocess.Popen``
call that **captures** output (``capture_output=True``, ``stdout=PIPE``,
``text=True``, or ``universal_newlines=True``) without pinning an explicit
``encoding=`` kwarg.  Such calls decode child output via the locale codec
(cp936/GBK on zh-CN Windows) which can produce ``None`` stdout and crash
downstream consumers — the FM-0003 failure shape.

Usage::

    # lint a single file
    python lint_subprocess_encoding.py path/to/file.py

    # lint all .py files under one or more directories
    python lint_subprocess_encoding.py --scan skills/ tools/
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    path: str
    lineno: int
    func_name: str
    reason: str


# ── helpers ───────────────────────────────────────────────────────────

_SUBPROCESS_FUNCS = {"subprocess.run", "subprocess.Popen"}

# Keywords that indicate output capture.
_CAPTURE_KEYWORDS = {"capture_output"}

# Keywords whose presence means "text mode" (string, not bytes).
_TEXT_KEYWORDS = {"text", "universal_newlines"}

# The keyword we require when capturing.
_ENCODING_KEYWORD = "encoding"


def _is_subprocess_call(call: ast.Call) -> str | None:
    """Return the fully-qualified name if *call* targets subprocess.run/Popen, else None."""
    func = call.func
    # subprocess.run(...)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        qn = f"{func.value.id}.{func.attr}"
        if qn in _SUBPROCESS_FUNCS:
            return qn
    # from subprocess import run; run(...)
    if isinstance(func, ast.Name) and func.id in ("run", "Popen"):
        return func.id
    return None


def _has_kwarg(call: ast.Call, name: str) -> bool:
    """True if the call has keyword argument *name*."""
    return any(kw.arg == name for kw in call.keywords)


def _kwarg_is_truthy(call: ast.Call, name: str) -> bool:
    """True if keyword *name* is present and its value is truthy (True literal)."""
    for kw in call.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
            # non-constant (variable) — assume it could be True
            return True
    return False


def _is_pipe_value(node: ast.expr) -> bool:
    """Heuristic: does *node* look like subprocess.PIPE?"""
    # subprocess.PIPE
    if isinstance(node, ast.Attribute) and node.attr == "PIPE":
        return True
    # from subprocess import PIPE; PIPE
    if isinstance(node, ast.Name) and node.id == "PIPE":
        return True
    return False


def _check_call(call: ast.Call, func_name: str) -> str | None:
    """Return a reason string if *call* violates the lint, else None."""
    captures = _kwarg_is_truthy(call, "capture_output")
    captures = captures or any(
        kw.arg in ("stdout", "stderr") and _is_pipe_value(kw.value)
        for kw in call.keywords
    )
    text_mode = _kwarg_is_truthy(call, "text") or _kwarg_is_truthy(
        call, "universal_newlines"
    )

    # A call is "capturing" if it uses capture_output, PIPE, or text mode.
    if not (captures or text_mode):
        return None

    if _has_kwarg(call, _ENCODING_KEYWORD):
        return None

    if captures:
        return (
            f"{func_name} captures output without encoding= — "
            "child output will decode via locale codec (FM-0003)"
        )
    # text=True but no capture — not a violation (text=True alone is harmless
    # when there's no pipe).  Only flag if there's actual capture.
    return None


# ── file-level scan ───────────────────────────────────────────────────

def lint_source(source: str, path: str = "<unknown>") -> list[Violation]:
    """Lint one python source string.  Returns a list of violations."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _is_subprocess_call(node)
        if func_name is None:
            continue
        reason = _check_call(node, func_name)
        if reason:
            violations.append(
                Violation(path=path, lineno=node.lineno, func_name=func_name, reason=reason)
            )
    return violations


def lint_file(path: Path) -> list[Violation]:
    """Lint one file on disk."""
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    return lint_source(source, str(path))


# ── directory scan ────────────────────────────────────────────────────

def scan_paths(globs: list[str]) -> list[Violation]:
    """Scan all .py files under each glob/dir path.  Returns all violations."""
    violations: list[Violation] = []
    seen: set[str] = set()
    for pattern in globs:
        p = Path(pattern)
        if p.is_file():
            if str(p) not in seen:
                seen.add(str(p))
                violations.extend(lint_file(p))
        elif p.is_dir():
            for py in sorted(p.rglob("*.py")):
                if str(py) not in seen:
                    seen.add(str(py))
                    violations.extend(lint_file(py))
    return violations


# ── CLI ───────────────────────────────────────────────────────────────

def _format_violation(v: Violation) -> str:
    return f"{v.path}:{v.lineno}: {v.reason}"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: lint_subprocess_encoding.py [--scan] PATH [PATH ...]", file=sys.stderr)
        return 2

    scan_mode = False
    paths = []
    for arg in args:
        if arg == "--scan":
            scan_mode = True
        else:
            paths.append(arg)

    if not paths:
        print("error: no paths provided", file=sys.stderr)
        return 2

    if scan_mode:
        violations = scan_paths(paths)
    else:
        violations = []
        for p in paths:
            violations.extend(lint_file(Path(p)))

    for v in violations:
        print(_format_violation(v))

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
