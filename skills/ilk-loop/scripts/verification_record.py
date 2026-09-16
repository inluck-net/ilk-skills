#!/usr/bin/env python3
"""Write machine-readable record fields that the batch-verification runtime depends on.

The batch-verification template's step 0 runs a test suite and writes a
verification record.  Two fields the runtime reads — ``verified_head`` (the
HEAD the suite ran on) and ``suite_scope`` (scoped vs full) — historically
lived only in prose bullets.  A planner that paraphrases prose drops them;
measured on gh-resolve 2026-09-16: both fields survived in 0 of 2 rendered
sub-plans.

This script is invoked **from a gate command** so it cannot be paraphrased
away.  It reads HEAD from git (never accepts it as an argument — a caller
that can pass it can pass the wrong one, which is the class of defect this
batch is about) and writes it to the record file.

Usage::

    python3 verification_record.py --project <dir> --record <path>
    python3 verification_record.py --project . --record /abs/path/to/record.md

Idempotent: re-running on an existing record updates the fields rather than
appending a second copy.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def read_head_from_git(project: Path) -> str | None:
    """Return the full 40-char HEAD sha, or None if unresolvable."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return sha
    return None


_VERIFIED_HEAD_RE = re.compile(
    r"^[-*\s]*\**\s*head[^:\n]*:\**[ \t]*`?[0-9a-fA-F]{7,40}`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def inject_verified_head(text: str, sha: str) -> str:
    """Return *text* with ``verified_head: <sha>`` present (updated or appended).

    Idempotent: if the field already exists, replaces it.  If absent, appends
    after the last non-blank line.
    """
    field_line = f"**verified_head:** `{sha}`"
    if _VERIFIED_HEAD_RE.search(text):
        return _VERIFIED_HEAD_RE.sub(field_line, text)
    # Append after the last non-blank line.
    stripped = text.rstrip("\n")
    if stripped:
        return stripped + "\n\n" + field_line + "\n"
    return field_line + "\n"


_SUITE_SCOPE_RE = re.compile(
    r"^[-*\s]*\**\s*suite_scope[ \t]*:\**[ \t]*`?\w+`?.*$",
    re.MULTILINE | re.IGNORECASE,
)

# Global/config/fixture patterns whose change invalidates any narrow scope.
_GLOBAL_PATTERNS = (
    "conftest.py",
    "fixtures",
    "pytest.ini",
    "setup.cfg",
    "pyproject.toml",
    "tox.ini",
    ".github",
    "Makefile",
)


def _is_global_change(path: str) -> bool:
    """True if *path* touches conftest, fixtures, or build config."""
    norm = path.replace("\\", "/").lower()
    return any(pat in norm for pat in _GLOBAL_PATTERNS)


def _module_test_name(module_stem: str) -> str:
    """Derive the expected test-file name for a Python module stem."""
    if module_stem.startswith("test_"):
        return module_stem + ".py"
    return f"test_{module_stem}.py"


def compute_suite_scope(project: Path, base_sha: str) -> dict:
    """Derive the suite scope from ``base_sha..HEAD``.

    Returns ``{"mode": "scoped"|"full", "count": N, "reason": str}``.

    Widen to ``full`` (AC-2) when:
    - the importer set cannot be computed;
    - the diff touches conftest, fixtures, or build config;
    - no test files are in the changed set and no changed module maps to an
      existing test file.
    """
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..HEAD"],
            cwd=project, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {"mode": "full", "count": 0,
                "reason": "git diff failed — importer set uncomputable"}

    if r.returncode != 0:
        return {"mode": "full", "count": 0,
                "reason": f"git diff exited {r.returncode}"}

    changed = [p for p in r.stdout.strip().splitlines() if p.strip()]
    if not changed:
        return {"mode": "scoped", "count": 0, "reason": "empty diff"}

    # Direct test-file changes.
    test_files = {p for p in changed
                  if "/test" in p.replace("\\", "/")
                  or p.replace("\\", "/").endswith("_test.py")}

    # Non-test Python files: try to map to their test counterparts.
    non_test_py = [p for p in changed
                   if p.endswith(".py") and p not in test_files]
    has_global = any(_is_global_change(p) for p in changed)

    if has_global:
        return {"mode": "full", "count": 0,
                "reason": "diff touches conftest/fixtures/build config"}

    # Map each changed module to its test file.  If ANY module has no test,
    # the importer set is incomplete → widen.
    mapped: set[str] = set()
    for py_path in non_test_py:
        stem = Path(py_path).stem
        test_name = _module_test_name(stem)
        candidates = list(project.rglob(test_name))
        if candidates:
            mapped.add(str(candidates[0].relative_to(project)))
        else:
            # Module with no discoverable test file — importer set incomplete.
            return {"mode": "full", "count": 0,
                    "reason": f"no test file found for changed module {py_path}"}

    combined = test_files | mapped
    if not combined:
        # Only non-Python files changed (docs, configs not in _GLOBAL_PATTERNS).
        return {"mode": "scoped", "count": 0, "reason": "no test-eligible changes"}

    return {"mode": "scoped", "count": len(combined),
            "reason": f"{len(combined)} test file(s) selected"}


def inject_suite_scope(text: str, mode: str, count: int) -> str:
    """Return *text* with ``suite_scope: <mode> (N files)`` present (idempotent)."""
    field_line = f"**suite_scope:** `{mode}` ({count} file{'s' if count != 1 else ''})"
    if _SUITE_SCOPE_RE.search(text):
        return _SUITE_SCOPE_RE.sub(field_line, text)
    stripped = text.rstrip("\n")
    if stripped:
        return stripped + "\n" + field_line + "\n"
    return field_line + "\n"


def resolve_batch_record(project: Path, batch_slug: str) -> Path:
    """Resolve ``<ext logs>/verification/<batch_slug>-batch.md`` for a project.

    Same resolution logic as ``verify_attribution.resolve_batch_record``.
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ilk_paths import external_logs_dir, resolve_project_key  # type: ignore[import-untyped]
    except ImportError as exc:
        raise FileNotFoundError(f"ilk_paths unavailable: {exc}")

    key = resolve_project_key(project)
    if not key:
        raise FileNotFoundError(
            f"no ilk project key resolves from {project}"
        )
    verification_dir = external_logs_dir(key) / "verification"
    record = verification_dir / f"{batch_slug}-batch.md"
    if record.is_file():
        return record
    if verification_dir.is_dir():
        siblings = sorted(p.name for p in verification_dir.glob("*-batch.md"))
        found = ", ".join(siblings) if siblings else "(0 *-batch.md files)"
        raise FileNotFoundError(
            f"no record for batch {batch_slug!r} at {record}. "
            f"{verification_dir} holds: {found}"
        )
    raise FileNotFoundError(
        f"no record for batch {batch_slug!r}: {verification_dir} does not exist"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write verified_head (and soon suite_scope) to a verification record."
    )
    ap.add_argument(
        "--project", default=".", help="project root whose git HEAD to read"
    )
    ap.add_argument(
        "--record", default=None, help="path to the verification record (.md)"
    )
    ap.add_argument(
        "--batch", default=None, metavar="BATCH_SLUG",
        help="resolve the record as <ext logs>/verification/<BATCH_SLUG>-batch.md",
    )
    ap.add_argument(
        "--compute-scope", action="store_true",
        help="compute suite_scope from base_sha..HEAD and inject it",
    )
    ap.add_argument(
        "--base-sha", default=None, metavar="SHA",
        help="batch base commit for scope computation (required with --compute-scope)",
    )
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()

    if bool(args.record) == bool(args.batch):
        print(
            "ERROR: pass exactly one of --record or --batch",
            file=sys.stderr,
        )
        return 2

    if args.batch:
        try:
            record = resolve_batch_record(project, args.batch)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        record = Path(args.record)

    sha = read_head_from_git(project)
    if sha is None:
        print(
            f"ERROR: cannot read HEAD from git in {project}",
            file=sys.stderr,
        )
        return 1

    if not record.is_file():
        print(f"ERROR: record not found: {record}", file=sys.stderr)
        return 1

    text = record.read_text(encoding="utf-8-sig", errors="replace")
    updated = inject_verified_head(text, sha)

    if args.compute_scope:
        if not args.base_sha:
            print(
                "ERROR: --compute-scope requires --base-sha",
                file=sys.stderr,
            )
            return 2
        scope = compute_suite_scope(project, args.base_sha)
        updated = inject_suite_scope(updated, scope["mode"], scope["count"])
        print(f"suite_scope: {scope['mode']} ({scope['count']} files) — {scope['reason']}")

    record.write_text(updated, encoding="utf-8")
    print(f"verified_head: {sha} → {record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
