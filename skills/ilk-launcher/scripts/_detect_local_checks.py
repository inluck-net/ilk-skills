"""Detect whether any queued sub-plan of the active master declares local_checks.

Used by launch.sh to decide whether --run-local-checks should default ON.

Reuse: ilk_paths (plans-dir resolution), plan_status (sub-plan extraction +
frontmatter parsing). Stdlib only.

Exit codes:
  0  — at least one non-shipped sub-plan declares local_checks  (prints "true")
  1  — no non-shipped sub-plan declares local_checks             (prints "false")
  2  — error (no plans dir, no master, etc.)                     (prints "false")

When --reason is given, prints a human-readable explanation instead of
bare true/false (used for the launch banner).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve sibling modules via the scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))

from ilk_paths import find_plans_dir  # noqa: E402
from plan_status import (  # noqa: E402
    extract_subplan_files,
    parse_frontmatter,
)


def _has_local_checks(text: str) -> bool:
    """Return True if *text* contains a non-empty ``local_checks`` declaration.

    Detects both frontmatter-level declarations AND per-step declarations in
    the body. The canonical subplan-template style puts each step's gate in a
    fenced ``yaml`` block (``local_checks:`` + ``- command:`` items) under a
    ``### Step N`` heading, with frontmatter ``local_checks: []``. Scanning
    only the frontmatter (the prior behavior) silently reported gates OFF for
    every plan authored in that canonical style.
    """
    if not text.startswith("---"):
        # No frontmatter — scan the whole document for per-step blocks.
        return _block_has_local_checks(text)
    end = text.find("\n---", 3)
    if end < 0:
        return _block_has_local_checks(text)
    fm_block = text[3:end]
    if _block_has_local_checks(fm_block):
        return True
    # Per-step gates live in fenced yaml blocks in the body, below the
    # frontmatter — scan there too.
    return _block_has_local_checks(text[end + 4:])


def _block_has_local_checks(block: str) -> bool:
    """Check a YAML block (frontmatter body) for a non-empty local_checks."""
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip()
        # Top-level or indented local_checks key
        if stripped.lstrip().startswith("local_checks:"):
            after_colon = stripped.split("local_checks:", 1)[1].strip()
            if after_colon and after_colon != "[]":
                return True
            # Check if next lines are indented list items
            indent = len(stripped) - len(stripped.lstrip())
            j = i + 1
            while j < len(lines):
                next_line = lines[j].rstrip()
                if not next_line.strip():
                    j += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    break
                # Any indented content under local_checks means gates exist
                if next_line.lstrip().startswith("- "):
                    return True
                j += 1
            i = j
            continue
        i += 1
    return False


def _pick_master(plans_dir: Path) -> tuple[Path, str]:
    """Pick the active master (same logic as loop_status.pick_active_master)."""
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        raise FileNotFoundError(f"No MASTER-*.md in {plans_dir}")

    best: Path | None = None
    best_status = ""
    for p in masters:
        text = p.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        st = (fm.get("status") or "").strip().lower()
        if st == "active":
            return p, st
        if st == "queued" and best_status != "active":
            best = p
            best_status = st
        if best is None:
            best = p
            best_status = st or "(none)"
    assert best is not None
    return best, best_status


def _non_shipped_subplans(master_path: Path, plans_dir: Path) -> list[str]:
    """Return sub-plan filenames that are not shipped, in master order."""
    master_text = master_path.read_text(encoding="utf-8-sig")
    all_files = extract_subplan_files(master_text)
    result = []
    for fname in all_files:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue
        sub_text = sub_path.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(sub_text)
        if fm.get("status", "pending") != "shipped":
            result.append(fname)
    return result


def detect(project_path: Path, *, reason: bool = False) -> bool:
    """Return True iff any non-shipped sub-plan declares local_checks.

    When *reason* is True, also prints a human-readable explanation.
    """
    plans_dir, src = find_plans_dir(project_path)
    if plans_dir is None:
        if reason:
            print("Gates: OFF (no plans directory found)")
        return False

    try:
        master, master_status = _pick_master(plans_dir)
    except FileNotFoundError:
        if reason:
            print("Gates: OFF (no master plan found)")
        return False

    queued = _non_shipped_subplans(master, plans_dir)
    if not queued:
        if reason:
            print("Gates: OFF (all sub-plans shipped)")
        return False

    for fname in queued:
        sub_text = (plans_dir / fname).read_text(encoding="utf-8-sig")
        if _has_local_checks(sub_text):
            if reason:
                print(f"Gates: ON (declared in {fname})")
            return True

    if reason:
        print("Gates: OFF (no queued sub-plan declares local_checks)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect local_checks on queued sub-plans")
    ap.add_argument("project_path", type=Path, help="Absolute path to project root")
    ap.add_argument("--reason", action="store_true",
                    help="Print human-readable explanation instead of true/false")
    args = ap.parse_args()

    found = detect(args.project_path, reason=args.reason)
    if not args.reason:
        print("true" if found else "false")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
