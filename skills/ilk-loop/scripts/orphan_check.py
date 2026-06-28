#!/usr/bin/env python3
"""Detect exported symbols whose only call sites are test files.

Given a repo root and one or more symbol names, scan the source tree for
references to each symbol.  If every reference (excluding the definition
itself) lives in a test file, the symbol is "built but unwired" — a
GRIDLOCK Gap-A orphaned capability.

Uses ``rg`` (ripgrep) when available for speed, with a pure-Python
``os.walk`` + ``re`` fallback so the tool works on boxes without ripgrep.

CLI:
    python orphan_check.py --root <repo> --symbol foo [--symbol bar] [--json]
        prints ``WARN: <sym>: built but unwired (only test call sites)``
        for each orphaned symbol; exit 1 if any finding.

Reads files with ``utf-8-sig`` (zh-CN Windows configs may carry a BOM).
Subprocess capture pins ``encoding="utf-8", errors="replace"``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Test-file classification ────────────────────────────────────────────────

# Directories whose entire subtree is test code.
_TEST_DIR_NAMES = frozenset({
    "tests", "test", "__tests__", "spec", "specs", "e2e", "integration",
})

# File-name patterns that indicate test files.
_TEST_FILE_RE = re.compile(
    r"""
    (?:^test_[^/\\]*$           #  test_*.py, test_*.ts
    |(?:^|[/\\])test_[^/\\]+$   #  same with path prefix
    |_test\.[^.]+$              #  *_test.py, *_test.go
    |\.spec\.[^.]+$             #  *.spec.ts, *.spec.js
    |\.test\.[^.]+$             #  *.test.ts, *.test.js
    |\.tests\.[^.]+$            #  *.tests.cs
    |_spec\.[^.]+$              #  *_spec.rb
    |Test\.[^.]+$               #  Test.java, Test.cs (PascalCase)
    |Tests\.[^.]+$              #  Tests.cs
    |test_[^/\\]*\.[^.]+$)      #  test_foo.py (with extension)
    """,
    re.VERBOSE,
)

# Paths to skip entirely (binary, build output, node_modules, etc.)
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "*.egg-info",
})

# File extensions to scan (text source files).
_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php",
    ".swift", ".dart", ".lua", ".sh", ".bash", ".ps1",
    ".vue", ".svelte", ".html", ".css", ".scss",
})


def is_test_file(path: str) -> bool:
    """True if *path* looks like a test file by common conventions."""
    p = path.replace("\\", "/")
    parts = p.split("/")
    # Check if any path component is a test directory name.
    for part in parts[:-1]:  # skip filename
        if part.lower() in _TEST_DIR_NAMES:
            return True
    # Check filename pattern.
    filename = parts[-1]
    return bool(_TEST_FILE_RE.search(filename))


def _should_skip_dir(dirname: str) -> bool:
    """True if *dirname* should be skipped during traversal."""
    return dirname in _SKIP_DIRS or dirname.endswith(".egg-info")


def _is_source_file(path: str) -> bool:
    """True if *path* has a text-source extension."""
    ext = Path(path).suffix.lower()
    return ext in _SOURCE_EXTS


# ── Symbol reference scanning ───────────────────────────────────────────────

# A reference to a symbol: word-boundary match (not part of a longer identifier).
def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    """Build a regex that matches *symbol* as a whole word."""
    return re.compile(r"\b" + re.escape(symbol) + r"\b")


def _scan_file_python(filepath: str, symbol: str) -> list[tuple[int, str]]:
    """Pure-Python fallback: scan one file for symbol references."""
    pattern = _symbol_pattern(symbol)
    hits: list[tuple[int, str]] = []
    try:
        text = Path(filepath).read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, UnicodeDecodeError):
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            hits.append((i, line.strip()))
    return hits


def _find_rg() -> str | None:
    """Return the path to ``rg`` if available, else None."""
    return shutil.which("rg")


def _scan_with_rg(root: str, symbol: str) -> dict[str, list[tuple[int, str]]]:
    """Use ripgrep to find all references to *symbol* under *root*."""
    rg = _find_rg()
    if rg is None:
        return {}
    try:
        result = subprocess.run(
            [rg, "--line-number", "--no-heading", "--word-regexp",
             "--glob", "!.git", "--glob", "!node_modules",
             "--glob", "!__pycache__", "--glob", "!*.pyc",
             symbol, root],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    hits: dict[str, list[tuple[int, str]]] = {}
    for line in result.stdout.splitlines():
        # rg output: file:line:text
        parts = line.split(":", 2)
        if len(parts) >= 2:
            filepath = parts[0]
            try:
                lineno = int(parts[1])
            except ValueError:
                continue
            text = parts[2] if len(parts) > 2 else ""
            hits.setdefault(filepath, []).append((lineno, text.strip()))
    return hits


def _scan_with_python(root: str, symbol: str) -> dict[str, list[tuple[int, str]]]:
    """Pure-Python fallback: walk the tree and scan each source file."""
    hits: dict[str, list[tuple[int, str]]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place.
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in filenames:
            filepath = os.path.join(dirpath, fname)
            if not _is_source_file(filepath):
                continue
            file_hits = _scan_file_python(filepath, symbol)
            if file_hits:
                hits[filepath] = file_hits
    return hits


def scan_references(root: str, symbol: str) -> dict[str, list[tuple[int, str]]]:
    """Find all references to *symbol* under *root*, preferring rg."""
    rg_hits = _scan_with_rg(root, symbol)
    if rg_hits:
        return rg_hits
    return _scan_with_python(root, symbol)


# ── Orphan detection ────────────────────────────────────────────────────────

def _is_definition_line(line: str, symbol: str) -> bool:
    """True if *line* looks like a definition of *symbol* (not a call site)."""
    s = re.escape(symbol)
    return bool(re.search(
        rf"\b(?:def|class|function|const|let|var|export\s+(?:function|const|class))\s+{s}\b",
        line,
    ))


def check_symbol(root: str, symbol: str) -> dict:
    """Check whether *symbol* is orphaned (only test call sites).

    Returns a dict with keys:
        symbol: str
        orphaned: bool
        total_refs: int
        test_refs: int
        prod_refs: int
        test_files: list[str]
        prod_files: list[str]
    """
    refs = scan_references(root, symbol)
    test_files: list[str] = []
    prod_files: list[str] = []
    total = 0
    for filepath, hits in refs.items():
        relpath = os.path.relpath(filepath, root)
        # Filter out definition lines — only count actual usage/call sites.
        usage_hits = [(ln, text) for ln, text in hits
                      if not _is_definition_line(text, symbol)]
        if not usage_hits:
            continue
        total += len(usage_hits)
        if is_test_file(relpath):
            test_files.append(relpath)
        else:
            prod_files.append(relpath)
    return {
        "symbol": symbol,
        "orphaned": len(prod_files) == 0 and len(test_files) > 0,
        "total_refs": total,
        "test_refs": sum(
            len([(ln, t) for ln, t in refs[f]
                 if not _is_definition_line(t, symbol)])
            for f in refs
            if is_test_file(os.path.relpath(f, root))
        ),
        "prod_refs": sum(
            len([(ln, t) for ln, t in refs[f]
                 if not _is_definition_line(t, symbol)])
            for f in refs
            if not is_test_file(os.path.relpath(f, root))
        ),
        "test_files": sorted(test_files),
        "prod_files": sorted(prod_files),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect exported symbols whose only call sites are test files."
    )
    parser.add_argument("--root", required=True, help="Repo root to scan.")
    parser.add_argument(
        "--symbol", action="append", required=True,
        help="Symbol name to check (repeat for multiple).",
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output as JSON.")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"ERROR: root '{root}' is not a directory", file=sys.stderr)
        return 2

    results = []
    findings = 0
    for symbol in args.symbol:
        result = check_symbol(root, symbol)
        results.append(result)
        if result["orphaned"]:
            findings += 1
            if not args.json_output:
                print(
                    f"WARN: {symbol}: built but unwired "
                    f"(only test call sites: {', '.join(result['test_files'])})"
                )

    if args.json_output:
        print(json.dumps(results, indent=2))

    if findings == 0 and not args.json_output:
        print("OK: no orphaned symbols found")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
