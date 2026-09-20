"""Resolve the effective iteration window at launch time.

The effective window is ``max(config, max declarations)`` across the active
master's sub-plans, hard-capped at :data:`ILK_MAX_ITERATION_TIMEOUT_MIN`.
Used by ``launch.sh`` so that a sub-plan's declared
``recommended_iteration_timeout_min`` actually sizes the runner window —
the guarantee from retro-2026-09-20-a-step-that-outgrew-its-window.

Exit codes:
  0  — prints the resolved value (minutes)
  2  — error (no plans dir, no config, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Resolve sibling modules via the scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))

from ilk_paths import find_plans_dir  # noqa: E402
from plan_status import extract_subplan_files, parse_frontmatter  # noqa: E402

#: Hard ceiling so a typo'd declaration cannot pin a slot forever.
ILK_MAX_ITERATION_TIMEOUT_MIN = 120

#: Fallback when config is absent and no declarations exist.
DEFAULT_ITERATION_TIMEOUT_MIN = 30


# ── core resolution ──────────────────────────────────────────────────────────

def resolve_effective_timeout(project_path: Path) -> int:
    """Return the effective iteration timeout (minutes) for *project_path*.

    Resolution order:
      1. Read ``iteration_timeout_min`` from ``.ilk-launch.json`` (config).
      2. For the active master's non-shipped sub-plans, collect all
         ``recommended_iteration_timeout_min`` declarations.
      3. The effective window is ``max(config, max(declarations))``, capped
         at :data:`ILK_MAX_ITERATION_TIMEOUT_MIN`.

    Raises ``FileNotFoundError`` if no plans directory or master is found
    (the caller should handle this gracefully).
    """
    # 1. Config value
    config_timeout = _read_config_timeout(project_path)

    # 2. Maximum declaration from active master's sub-plans
    max_declaration = _read_max_declaration(project_path)

    # 3. Resolve
    raw = max(config_timeout, max_declaration)
    return min(raw, ILK_MAX_ITERATION_TIMEOUT_MIN)


def _read_config_timeout(project_path: Path) -> int:
    """Read ``iteration_timeout_min`` from ``.ilk-launch.json``."""
    cfg_path = project_path / ".ilk-launch.json"
    if not cfg_path.exists():
        return DEFAULT_ITERATION_TIMEOUT_MIN
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        val = cfg.get("iteration_timeout_min", "")
        return int(val) if val else DEFAULT_ITERATION_TIMEOUT_MIN
    except (json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_ITERATION_TIMEOUT_MIN


def _read_max_declaration(project_path: Path) -> int:
    """Return the highest ``recommended_iteration_timeout_min`` across
    non-shipped sub-plans of the active master, or 0 if none declare it.
    """
    plans_dir, _ = find_plans_dir(project_path)
    if plans_dir is None:
        return 0

    master_path = _pick_master(plans_dir)
    if master_path is None:
        return 0

    master_text = master_path.read_text(encoding="utf-8-sig")
    subplan_files = extract_subplan_files(master_text)

    best = 0
    for fname in subplan_files:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue
        text = sub_path.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        if fm.get("status", "pending") == "shipped":
            continue
        raw = fm.get("recommended_iteration_timeout_min", "")
        if raw:
            try:
                val = int(raw)
                best = max(best, val)
            except (ValueError, TypeError):
                pass
    return best


def _pick_master(plans_dir: Path) -> Path | None:
    """Pick the active master (same logic as _detect_local_checks._pick_master)."""
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        return None

    best: Path | None = None
    best_status = ""
    for p in masters:
        text = p.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        st = (fm.get("status") or "").strip().lower()
        if st == "active":
            return p
        if st in ("queued", "active") and best_status not in ("active",):
            best = p
            best_status = st
        if best is None:
            best = p
            best_status = st or "(none)"
    return best


# ── banner support ───────────────────────────────────────────────────────────

def resolve_with_source(project_path: Path) -> tuple[int, str]:
    """Return ``(effective_timeout, source_label)`` for the launch banner.

    Source labels: ``config``, ``declaration``, ``default``.
    """
    config_timeout = _read_config_timeout(project_path)
    max_declaration = _read_max_declaration(project_path)

    if max_declaration > config_timeout:
        effective = min(max_declaration, ILK_MAX_ITERATION_TIMEOUT_MIN)
        return effective, "declaration"
    elif _config_file_exists(project_path):
        effective = min(config_timeout, ILK_MAX_ITERATION_TIMEOUT_MIN)
        return effective, "config"
    else:
        effective = min(DEFAULT_ITERATION_TIMEOUT_MIN, ILK_MAX_ITERATION_TIMEOUT_MIN)
        return effective, "default"


def _config_file_exists(project_path: Path) -> bool:
    return (project_path / ".ilk-launch.json").exists()


# ── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve effective iteration window.")
    parser.add_argument("project_path", type=Path, help="Project root directory")
    parser.add_argument("--source", action="store_true",
                        help="Print source label alongside the value")
    args = parser.parse_args()

    try:
        if args.source:
            value, label = resolve_with_source(args.project_path)
            print(f"{value} {label}")
        else:
            value = resolve_effective_timeout(args.project_path)
            print(value)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
