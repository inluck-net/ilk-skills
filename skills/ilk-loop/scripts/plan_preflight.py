"""Preflight validation for an authored batch before release.

Runs the loop's own readers against a master + sub-plans and refuses release
when any of them disagrees with the authoring.  This catches structural defects
at authoring time — before the loop burns a run discovering them.

Called from ``/ilk-plan`` step 8b: a failing preflight leaves the master
``draft`` and names which reader disagreed.

ACs (from judged-by-the-readers-that-will-run-it):

1. **Registry parity.** ``extract_subplan_files(master)`` must agree with the
   master's registry table.  The backticked-filename anchor case (0 parsed vs
   2 in the table) is the measured failure this catches.
2. **Declared paths exist.** Every ``unit_test_targets`` entry and every test
   file named in a gate command resolves on disk.
3. **Step-commit feasibility.** Every authored step either names a repo artifact
   or declares ``--allow-empty``.
4. **Gate resolvability.** Every gate command's leading executable resolves on
   the effective driver PATH.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from plan_lint import (
    _strip_frontmatter,
    _extract_step_sections,
    _step_has_repo_diff,
    _COMMIT_LINE_RE,
    _FINDINGS_ONLY_RE,
    _EXTERNAL_DATA_ROOT_RE,
)
from plan_status import extract_subplan_files, parse_frontmatter


# ── Result container ────────────────────────────────────────────────────────


@dataclass
class PreflightResult:
    """Outcome of a preflight validation run."""
    failures: list[str] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return bool(self.failures)


# ── Registry parity ─────────────────────────────────────────────────────────

# Regex to extract filenames from a markdown table row.
# Matches: ``| N | 2026-09-22-slug.md | status |`` or backticked variants.
_TABLE_FILENAME_RE = re.compile(
    r"\|\s*\d+\s*\|"     # row number
    r"\s*`?([^|]+?)`?\s*\|"  # filename (possibly backticked)
    r"[^|]*\|",          # rest of row (no DOTALL — one line at a time)
)

# A filename that looks like a sub-plan reference.
_SUBPLAN_FILENAME_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2}[a-z]?-[a-z0-9][a-z0-9-]*\.md)"
)


def _extract_table_filenames(master_text: str) -> list[str]:
    """Extract sub-plan filenames from the registry table, including backticked.

    This is the reader that must agree with ``extract_subplan_files``.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for row_match in _TABLE_FILENAME_RE.finditer(master_text):
        cell = row_match.group(1).strip()
        # The cell might be a backticked filename, a link, or bare.
        # Strip backticks and link syntax.
        cell = cell.strip('`').strip()
        # Extract the actual filename from the cell.
        fn_match = _SUBPLAN_FILENAME_RE.search(cell)
        if fn_match:
            fn = fn_match.group(1)
            if fn not in seen and not fn.startswith("MASTER"):
                seen.add(fn)
                ordered.append(fn)
    return ordered


def _check_registry_parity(master_text: str) -> list[str]:
    """AC1: extract_subplan_files must agree with the registry table."""
    findings: list[str] = []

    # The loop's parser (extract_subplan_files) — what loop_status,
    # scheduler_scan, promote_next_master, and ship_integrity use.
    parsed = extract_subplan_files(master_text)

    # The table reader — what the planner writes.
    table_filenames = _extract_table_filenames(master_text)

    parsed_set = set(parsed)
    table_set = set(table_filenames)

    if parsed_set != table_set:
        in_table_not_parsed = table_set - parsed_set
        in_parsed_not_table = parsed_set - table_set
        parts = []
        if in_table_not_parsed:
            parts.append(f"in table but not parsed: {sorted(in_table_not_parsed)}")
        if in_parsed_not_table:
            parts.append(f"parsed but not in table: {sorted(in_parsed_not_table)}")
        findings.append(
            f"Registry parity failure: extract_subplan_files returns "
            f"{len(parsed)} sub-plan(s) {sorted(parsed)} but the table names "
            f"{len(table_filenames)} {sorted(table_filenames)}. "
            f"Mismatch: {'; '.join(parts)}. "
            f"The loop reads extract_subplan_files; a table the parser cannot "
            f"see produces 'no runnable sub-plans' and halts. "
            f"Measured: backticked filenames parse as 0 (backtick is not in "
            f"_REF_LEAD lookbehind). Use bare filenames or [name](./name) links."
        )

    return findings


# ── Declared paths exist ────────────────────────────────────────────────────


def _check_declared_paths(
    subplan_texts: dict[str, str],
    project_root: Path,
) -> list[str]:
    """AC2: every unit_test_targets entry and gate test path resolves on disk."""
    findings: list[str] = []

    for slug, text in subplan_texts.items():
        fm = parse_frontmatter(text)

        # Check unit_test_targets from frontmatter.
        targets_str = fm.get("unit_test_targets", "")
        if targets_str:
            # Parse the inline list: [a, b, c] or just a single value.
            targets_str = targets_str.strip("[]")
            targets = [t.strip().strip('"').strip("'")
                       for t in targets_str.split(",") if t.strip()]
            for target in targets:
                target_path = project_root / target
                if not target_path.exists():
                    findings.append(
                        f"{slug}: unit_test_targets entry '{target}' "
                        f"does not exist at {target_path}. "
                        f"A declared test path that the loop cannot find "
                        f"produces a collection error, not a test failure."
                    )

        # Check test paths in gate commands.
        body = _strip_frontmatter(text)
        gate_cmd_re = re.compile(
            r"command:\s*['\"](.+?)['\"]", re.MULTILINE
        )
        for m in gate_cmd_re.finditer(body):
            cmd = m.group(1)
            # Extract file paths from pytest commands.
            pytest_path_re = re.compile(
                r"pytest\s+([\w/._-]+\.py)", re.IGNORECASE
            )
            for pm in pytest_path_re.finditer(cmd):
                test_file = pm.group(1)
                test_path = project_root / test_file
                if not test_path.exists():
                    findings.append(
                        f"{slug}: gate command references test file '{test_file}' "
                        f"which does not exist at {test_path}."
                    )

    return findings


# ── Step-commit feasibility ─────────────────────────────────────────────────


def _check_step_commit_feasibility(subplan_texts: dict[str, str]) -> list[str]:
    """AC3: every step names a repo artifact or declares --allow-empty."""
    findings: list[str] = []

    for slug, text in subplan_texts.items():
        body = _strip_frontmatter(text)

        for step_no, heading, section in _extract_step_sections(body):
            # Check if the step describes a non-repo deliverable.
            has_findings = bool(_FINDINGS_ONLY_RE.search(section))
            has_external_data = bool(_EXTERNAL_DATA_ROOT_RE.search(section))
            has_repo_file = _step_has_repo_diff(section)

            is_non_repo = (has_findings or has_external_data) and not has_repo_file
            if not is_non_repo:
                continue

            # Extract the commit line for this step.
            commit_match = _COMMIT_LINE_RE.search(section)
            if not commit_match:
                continue

            commit_cmd = commit_match.group(1)
            if "--allow-empty" in commit_cmd:
                continue

            findings.append(
                f"{slug}: {heading} describes a non-repo deliverable "
                f"(Findings prose or external data root path) but its commit "
                f"line lacks --allow-empty. A step with no repo diff needs "
                f"'git commit --allow-empty -m \"... [plan:<slug>#step-N]\"' "
                f"to satisfy ship_integrity."
            )

    return findings


# ── Gate resolvability ──────────────────────────────────────────────────────


def _check_gate_executables(
    subplan_texts: dict[str, str],
) -> list[str]:
    """AC4: every gate command's leading executable resolves on PATH."""
    findings: list[str] = []
    builtin_skip = {"set", "cd", "export", "if", "then", "else", "fi",
                    "for", "while", "do", "done", "echo", "true", "false",
                    "test", "[", "]", "bash", "sh", "zsh", "exec"}

    for slug, text in subplan_texts.items():
        body = _strip_frontmatter(text)
        gate_cmd_re = re.compile(
            r"command:\s*['\"](.+?)['\"]", re.MULTILINE
        )
        for m in gate_cmd_re.finditer(body):
            cmd = m.group(1).strip()
            # Skip VAR=value prefixes.
            parts = cmd.split()
            if not parts:
                continue
            exe = parts[0]
            # Skip shell builtins, keywords, and env prefixes.
            if exe in builtin_skip or "=" in exe:
                continue
            # Skip if it starts with $ (env var expansion).
            if exe.startswith("$"):
                continue
            # Resolve on PATH.
            resolved = shutil.which(exe)
            if resolved is None:
                findings.append(
                    f"{slug}: gate executable '{exe}' not found on PATH. "
                    f"The gate will fail with 'command not found' at run time."
                )

    return findings


# ── Step gates extractable ─────────────────────────────────────────────────


def _check_step_gates_extract(
    subplan_texts: dict[str, str],
) -> list[str]:
    """AC5: every step that declares local_checks is extractable by the loop's locator.

    Uses :func:`run_local_checks.step_gate_fence` — the same locator the
    runtime uses — so the preflight and the gate runner always agree.
    """
    from run_local_checks import step_gate_fence  # noqa: E811

    findings: list[str] = []
    step_heading_re = re.compile(r"^###\s+Step\s+(\d+)", re.MULTILINE)

    for slug, text in subplan_texts.items():
        body = _strip_frontmatter(text)
        seen_steps: set[int] = set()
        for shm in step_heading_re.finditer(body):
            sn = int(shm.group(1))
            if sn in seen_steps:
                continue
            seen_steps.add(sn)
            gate = step_gate_fence(body, sn)
            if gate.declares_local_checks and gate.fence_text is None:
                findings.append(
                    f"FAIL {slug}: ### Step {sn} declares local_checks in a "
                    f"shape the runtime locator cannot extract "
                    f"(e.g. a json fence)."
                )

    return findings


# ── Main entry point ────────────────────────────────────────────────────────


def preflight_batch(
    master_text: str,
    plans_dir: Path,
    project_root: Path,
    subplan_texts: dict[str, str] | None = None,
) -> PreflightResult:
    """Run all preflight checks against an authored batch.

    Args:
        master_text: The MASTER plan's full text.
        plans_dir: The directory containing sub-plan files.
        project_root: The project root for path resolution.
        subplan_texts: Optional dict of {filename: text} for sub-plans.
            When provided, these are used instead of reading from plans_dir.

    Returns:
        A ``PreflightResult`` with any failures found.
    """
    result = PreflightResult()

    # AC1: Registry parity.
    result.failures.extend(_check_registry_parity(master_text))

    # Load sub-plan texts if not provided.
    if subplan_texts is None:
        subplan_texts = {}
        for fname in extract_subplan_files(master_text):
            fpath = plans_dir / fname
            if fpath.exists():
                subplan_texts[fname] = fpath.read_text(encoding="utf-8-sig")

    # AC2: Declared paths exist.
    result.failures.extend(_check_declared_paths(subplan_texts, project_root))

    # AC3: Step-commit feasibility.
    result.failures.extend(_check_step_commit_feasibility(subplan_texts))

    # AC4: Gate resolvability.
    result.failures.extend(_check_gate_executables(subplan_texts))

    # AC5: Step gates extractable.
    result.failures.extend(_check_step_gates_extract(subplan_texts))

    return result


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Preflight validation for an authored batch."
    )
    parser.add_argument("master", help="MASTER plan file path.")
    parser.add_argument(
        "--plans-dir", default=None,
        help="Plans directory (default: same as master file's parent).",
    )
    parser.add_argument(
        "--project-root", default=None,
        help="Project root for path resolution (default: cwd).",
    )
    args = parser.parse_args()

    master_path = Path(args.master)
    master_text = master_path.read_text(encoding="utf-8-sig")

    plans_dir = Path(args.plans_dir) if args.plans_dir else master_path.parent
    project_root = Path(args.project_root) if args.project_root else Path.cwd()

    result = preflight_batch(master_text, plans_dir, project_root)

    if result.failures:
        for f in result.failures:
            print(f"FAIL: {f}")
        return 1
    else:
        print("OK: preflight clean")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())