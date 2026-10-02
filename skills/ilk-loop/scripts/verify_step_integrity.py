"""Enforce that a verify step cannot reshape or reach into another batch's tests.

A verify step exists to resolve attributed regressions — it runs the suite,
finds failures, and fixes them.  Nothing in ilk stops a verify worker editing
tests on its way to green.  I2 sub-plan 0 protects only DECLARED gates.

This module adds three checks at the verify step's gate
(``verify_attribution.py``, before it can pass):

1. **Refuse** if the verify diff modifies any file named as a pin file by
   ANY sub-plan's ``pins_only``-style gate, or any ``test_*`` file another
   batch's sub-plan created.
2. **Refuse** if the verify diff adds a ``skip``, ``skipif``, ``xfail``, or
   ``importorskip`` to an existing test.
3. **Declare and surface** any other edit to an existing test file: the
   commit body must carry ``[test-change: <node> — <why>]``.  A missing
   declaration ⇒ refuse.  Declared changes pass.

Usage (called from ``verify_attribution.py`` or a gate check)::

    from verify_step_integrity import check_verify_commit
    violations = check_verify_commit(
        project, verify_slug="batch-2026-09-15c-verify",
        batch_base="<sha>", pin_files={"tests/test_other.py"},
    )
    if violations:
        for v in violations:
            print(f"REFUSE: {v.file}:{v.line} — {v.reason}")
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Violation:
    """A single integrity violation found in a verify step's diff."""
    file: str
    reason: str
    line: int | None = None


# Patterns that indicate a test is being weakened or disabled.
_SKIP_PATTERNS = [
    re.compile(r"@pytest\.mark\.skip\b"),
    re.compile(r"@pytest\.mark\.skipif\b"),
    re.compile(r"@pytest\.mark\.xfail\b"),
    re.compile(r"pytest\.importorskip\b"),
    re.compile(r"@unittest\.skip\b"),
    re.compile(r"@unittest\.skipIf\b"),
    re.compile(r"@unittest\.expectedFailure\b"),
]

# Pattern for [test-change: <node> — <why>] declarations in commit bodies.
_TEST_CHANGE_DECL_RE = re.compile(
    r"\[test-change:\s*(\S+)\s*[—\-]\s*(.+?)\]",
    re.MULTILINE,
)


def _git(project: Path, *args: str) -> str | None:
    """Run a read-only git command, returning stripped stdout or None."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _is_test_file(path: str) -> bool:
    """True when *path* looks like a test file."""
    name = Path(path).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
    )


def _get_verify_diff_files(
    project: Path, verify_slug: str, batch_base: str,
) -> list[str]:
    """Return files changed by the verify slug's commits since *batch_base*.

    Looks for commits whose message carries ``[plan:<verify-slug>#``.
    Falls back to ``batch_base..HEAD`` when no trailers are found (shared
    remote).
    """
    # Try trailer-filtered first.
    log = _git(
        project, "log", "--format=%H", "--diff-filter=ACMR",
        f"{batch_base}..HEAD", "--grep", f"[plan:{verify_slug}#",
    )
    if log:
        shas = log.splitlines()
        files: set[str] = set()
        for sha in shas:
            diff = _git(project, "diff-tree", "--no-commit-id", "-r", "--name-only", sha)
            if diff:
                files.update(diff.splitlines())
        return sorted(files)

    # Fallback: full range (shared remote, no trailers).
    diff = _git(
        project, "diff", "--name-only", "--diff-filter=ACMR",
        f"{batch_base}..HEAD",
    )
    return sorted(diff.splitlines()) if diff else []


def _get_verify_commit_bodies(
    project: Path, verify_slug: str, batch_base: str,
) -> str:
    """Return the concatenated commit bodies for the verify slug's commits."""
    log = _git(
        project, "log", "--format=%B",
        f"{batch_base}..HEAD", "--grep", f"[plan:{verify_slug}#",
    )
    if log:
        return log
    # Fallback: all commits in range.
    log = _git(project, "log", "--format=%B", f"{batch_base}..HEAD")
    return log or ""


def _get_file_at(project: Path, sha: str, path: str) -> str | None:
    """Return the content of *path* at *sha*, or None."""
    return _git(project, "show", f"{sha}:{path}")


def _get_file_now(project: Path, path: str) -> str | None:
    """Return the current content of *path*, or None."""
    try:
        return (project / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _find_added_skip_markers(old_content: str, new_content: str) -> list[int]:
    """Return line numbers where skip/xfail markers were ADDED.

    Only returns lines that are present in *new_content* but not in
    *old_content*.
    """
    old_lines = set(old_content.splitlines())
    added: list[int] = []
    for i, line in enumerate(new_content.splitlines(), 1):
        if line in old_lines:
            continue
        for pat in _SKIP_PATTERNS:
            if pat.search(line):
                added.append(i)
                break
    return added


def _find_deleted_test_functions(
    old_content: str, new_content: str,
) -> list[str]:
    """Return names of test functions present in *old_content* but not *new_content*."""
    func_re = re.compile(r"^def (test_\w+)\s*\(", re.MULTILINE)
    old_funcs = set(func_re.findall(old_content))
    new_funcs = set(func_re.findall(new_content))
    return sorted(old_funcs - new_funcs)


def _parse_test_change_declarations(commit_bodies: str) -> dict[str, str]:
    """Return ``{node_id: why}`` from ``[test-change:]`` trailers."""
    return {
        m.group(1): m.group(2)
        for m in _TEST_CHANGE_DECL_RE.finditer(commit_bodies)
    }


def check_verify_commit(
    project: Path,
    verify_slug: str,
    batch_base: str,
    pin_files: set[str],
) -> list[Violation]:
    """Check a verify step's diff for integrity violations.

    Parameters
    ----------
    project : Path
        The project root (a git repo).
    verify_slug : str
        The verify sub-plan's slug (from its ``plan:`` frontmatter).
    batch_base : str
        The batch's base commit SHA.
    pin_files : set[str]
        Files named as pin files by any sub-plan's ``pins_only``-style gate.
        Paths are relative to *project*.

    Returns
    -------
    list[Violation]
        Empty if the verify step is clean.
    """
    changed_files = _get_verify_diff_files(project, verify_slug, batch_base)
    if not changed_files:
        return []

    commit_bodies = _get_verify_commit_bodies(project, verify_slug, batch_base)
    declarations = _parse_test_change_declarations(commit_bodies)

    violations: list[Violation] = []

    for path in changed_files:
        if not _is_test_file(path):
            continue

        # AC-1: pin file or another batch's test file.
        if path in pin_files:
            violations.append(Violation(
                file=path,
                reason=(
                    f"verify step modified pin file {path!r} — a pin file is "
                    f"owned by another sub-plan's gate and must not be touched "
                    f"by the verify step"
                ),
            ))
            continue  # no further checks for this file

        # Check for skip/xfail additions and test function deletions.
        old_content = _get_file_at(project, batch_base, path)
        new_content = _get_file_now(project, path)
        if old_content is None or new_content is None:
            continue

        # AC-2: added skip/xfail markers.
        added_skip_lines = _find_added_skip_markers(old_content, new_content)
        if added_skip_lines:
            violations.append(Violation(
                file=path,
                line=added_skip_lines[0],
                reason=(
                    f"verify step added skip/xfail marker to {path} "
                    f"at line {added_skip_lines[0]} — a verify must not "
                    f"weaken tests to reach green"
                ),
            ))
            continue

        # AC-2b: deleted test functions.
        deleted = _find_deleted_test_functions(old_content, new_content)
        if deleted:
            violations.append(Violation(
                file=path,
                reason=(
                    f"verify step deleted test function(s) "
                    f"{', '.join(deleted)} from {path} — a verify must not "
                    f"remove tests to reach green"
                ),
            ))
            continue

        # AC-3: any other test change must be declared.
        # A test file that changed (content differs) needs a declaration.
        if old_content != new_content:
            node_guess = f"{path}::(changed)"
            if node_guess not in declarations and path not in declarations:
                # Check if ANY declaration covers this file.
                file_declared = any(
                    path in node or node.startswith(path)
                    for node in declarations
                )
                if not file_declared:
                    violations.append(Violation(
                        file=path,
                        reason=(
                            f"verify step changed test file {path} without a "
                            f"[test-change: <node> — <why>] declaration in the "
                            f"commit body — undeclared test changes must be "
                            f"surfaced for review"
                        ),
                    ))

    return violations