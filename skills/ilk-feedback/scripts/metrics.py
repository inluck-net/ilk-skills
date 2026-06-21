"""Cross-run KPI aggregator for the ilk feedback loop.

Reads one or more projects' .ilk-loop.log JSONL (+ available postmortems)
and emits a structured aggregate (JSON to stdout, plus a --text table view).

Pure read-only — never writes into the log/postmortem trees.

CLI:
  python metrics.py --project <path> --json    # JSON output for one project
  python metrics.py --project <path> --text    # human-readable table
  python metrics.py --all --json               # aggregate across all projects
  python metrics.py --all --text               # aggregate table
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Resolve sibling modules
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from collect import CLASSIFICATION_LABELS  # noqa: E402

# Import ilk_paths for project key resolution and path helpers
_ILK_LOOP_SCRIPTS = _SCRIPT_DIR.parent.parent / "ilk-loop" / "scripts"
sys.path.insert(0, str(_ILK_LOOP_SCRIPTS))
try:
    from ilk_paths import (
        external_logs_dir,
        jsonl_summary_path,
        project_key,
    )
except ImportError:
    # Fallback: when running outside installed skill context
    external_logs_dir = None  # type: ignore[assignment]
    jsonl_summary_path = None  # type: ignore[assignment]
    project_key = None  # type: ignore[assignment]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, tolerating BOM (utf-8-sig). Returns list of dicts."""
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records


def _resolve_jsonl_path(project_path: Path) -> Path | None:
    """Find the .ilk-loop.log for a project, using ilk_paths if available."""
    if jsonl_summary_path is not None and project_key is not None:
        try:
            key = project_key(project_path)
            p = jsonl_summary_path(key)
            if p.exists():
                return p
        except Exception:
            pass
    # Fallback: check legacy location
    legacy = project_path / ".ilk-loop.log"
    if legacy.exists():
        return legacy
    return None


def _discover_projects(data_root: Path) -> list[Path]:
    """Discover project paths from the data root's projects/ directory."""
    projects: list[Path] = []
    proj_dir = data_root / "projects"
    if not proj_dir.is_dir():
        return projects
    for entry in sorted(proj_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Each project dir has a logs/ subdir with .ilk-loop.log
        logs_dir = entry / "logs"
        if logs_dir.is_dir():
            projects.append(entry)
    return projects


# ── KPI Computations ─────────────────────────────────────────────────────────


def compute_classification_distribution(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many runs ended with each classification label.

    For multi-iteration runs, uses the last iteration's classification.
    Falls back to counting individual iteration records if no run-level
    classification is present.

    Returns a dict keyed by CLASSIFICATION_LABELS values (zero-filled).
    """
    dist = {label: 0 for label in CLASSIFICATION_LABELS}

    # Group by run_id to find last iteration per run
    by_run: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        rid = rec.get("run_id", "")
        by_run.setdefault(rid, []).append(rec)

    for rid, iters in by_run.items():
        # Sort by iteration number
        iters.sort(key=lambda r: r.get("iteration", 0))
        last = iters[-1]
        # Look for classification in the record
        label = last.get("classification") or last.get("label")
        if label and label in dist:
            dist[label] += 1
        # If no explicit classification, count as "no-evidence"
        # (the run completed but didn't classify)
        elif not label:
            dist["no-evidence"] += 1

    return dist


def _compute_all_kpis(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute all KPIs from the JSONL records. Returns a metrics dict."""
    dist = compute_classification_distribution(records)

    result: dict[str, Any] = {
        "classification_distribution": dist,
        "total_runs": len(set(r.get("run_id", "") for r in records)),
        "total_iterations": len(records),
        # KPIs that need instrumentation — honest nulls
        "time_to_ship_by_tier": None,
        "blacklist_thrash_count": None,
        "human_touch_count": None,
        "escaped_bug_rate": None,
        "needs_instrumentation": {
            "time_to_ship_by_tier": True,
            "blacklist_thrash_count": True,
            "human_touch_count": True,
            "escaped_bug_rate": True,
        },
    }

    return result


# ── Text rendering ───────────────────────────────────────────────────────────


def _render_text(metrics: dict[str, Any]) -> str:
    """Render metrics as a human-readable text table."""
    lines: list[str] = []
    lines.append("=== ilk feedback metrics ===")
    lines.append("")

    dist = metrics.get("classification_distribution", {})
    lines.append("Classification distribution:")
    max_label_len = max((len(k) for k in dist), default=10)
    for label, count in sorted(dist.items(), key=lambda x: -x[1]):
        bar = "#" * count
        lines.append(f"  {label:<{max_label_len}}  {count:>4}  {bar}")

    lines.append("")
    lines.append(f"Total runs:       {metrics.get('total_runs', '?')}")
    lines.append(f"Total iterations: {metrics.get('total_iterations', '?')}")
    lines.append("")

    # Honest null KPIs
    lines.append("Not yet instrumented:")
    needs = metrics.get("needs_instrumentation", {})
    for kpi, needed in needs.items():
        if needed:
            lines.append(f"  {kpi}: null (needs_instrumentation)")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cross-run KPI aggregator for the ilk feedback loop."
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--project",
        type=str,
        help="Project path to analyze.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Aggregate across all projects under the data root.",
    )
    fmt = p.add_mutually_exclusive_group(required=True)
    fmt.add_argument("--json", action="store_true", help="JSON output.")
    fmt.add_argument("--text", action="store_true", help="Human-readable table.")
    p.add_argument(
        "--data-root",
        type=str,
        default=None,
        help="Override ILK_DATA_HOME (default: ~/.ilk-data).",
    )
    return p


def _resolve_data_root(override: str | None = None) -> Path:
    """Resolve the ILK data root directory."""
    if override:
        return Path(override)
    env = os.environ.get("ILK_DATA_HOME") or os.environ.get("ILK_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".ilk-data"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.project:
        project_path = Path(args.project).resolve()
        jsonl_path = _resolve_jsonl_path(project_path)
        if jsonl_path is None:
            print(
                f"Error: no .ilk-loop.log found for {project_path}",
                file=sys.stderr,
            )
            return 1
        records = _read_jsonl(jsonl_path)
    else:
        # --all mode
        data_root = _resolve_data_root(args.data_root)
        records = []
        for proj_dir in _discover_projects(data_root):
            jsonl_path = jsonl_summary_path(project_key(proj_dir)) if (jsonl_summary_path and project_key) else None
            if jsonl_path and jsonl_path.exists():
                records.extend(_read_jsonl(jsonl_path))

    metrics = _compute_all_kpis(records)

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(_render_text(metrics))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
