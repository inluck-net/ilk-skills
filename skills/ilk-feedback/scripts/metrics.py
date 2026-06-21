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
import re
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


def compute_time_to_ship_by_tier(
    records: list[dict[str, Any]],
    project_path: Path | None = None,
) -> dict[str, Any] | None:
    """Compute time-to-ship grouped by verification_tier.

    Returns a dict keyed by tier with avg/min/max seconds, or None when
    the needed fields (started_at, shipped_at, verification_tier) are
    absent from the records.  Never crashes on missing data.
    """
    # Group by run_id, collect per-run aggregates
    by_run: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        rid = rec.get("run_id", "")
        by_run.setdefault(rid, []).append(rec)

    # We need records that carry started_at + duration_sec + verification_tier.
    # Current JSONL doesn't have these — check and return None if absent.
    has_needed = False
    for rid, iters in by_run.items():
        iters.sort(key=lambda r: r.get("iteration", 0))
        last = iters[-1]
        if last.get("verification_tier") and (last.get("started_at") or last.get("duration_sec")):
            has_needed = True
            break

    if not has_needed:
        return None

    # Compute per-tier stats
    tier_data: dict[str, list[float]] = {}
    for rid, iters in by_run.items():
        iters.sort(key=lambda r: r.get("iteration", 0))
        last = iters[-1]
        tier = last.get("verification_tier")
        if not tier:
            continue
        # Sum duration_sec across iterations (total wall-clock proxy)
        total_dur = sum(r.get("duration_sec", 0) for r in iters if r.get("duration_sec"))
        if total_dur <= 0:
            continue
        tier_data.setdefault(tier, []).append(total_dur)

    if not tier_data:
        return None

    result: dict[str, Any] = {}
    for tier, durations in tier_data.items():
        result[tier] = {
            "avg_sec": round(sum(durations) / len(durations), 1),
            "min_sec": round(min(durations), 1),
            "max_sec": round(max(durations), 1),
            "count": len(durations),
        }
    return result


def compute_blacklist_thrash_count(
    records: list[dict[str, Any]],
    project_path: Path | None = None,
) -> int | None:
    """Count blacklist-thrash events from scheduler log (best-effort).

    Returns the count, or None when the scheduler log is unreachable.
    """
    # Look for scheduler log in the project's runtime dir
    if project_path is None:
        return None

    if external_logs_dir is not None and project_key is not None:
        try:
            key = project_key(project_path)
            runtime_dir = external_logs_dir(key).parent / "runtime"
            scheduler_log = runtime_dir / "watchdog" / "scheduler.log"
            if scheduler_log.exists():
                count = 0
                try:
                    with scheduler_log.open("r", encoding="utf-8-sig") as fh:
                        for line in fh:
                            if "blacklist" in line.lower() and "thrash" in line.lower():
                                count += 1
                except OSError:
                    return None
                return count
        except Exception:
            pass
    return None


def compute_comprehension_debt(
    project_path: Path | None = None,
    git_log_runner: Any | None = None,
) -> dict[str, Any] | None:
    """Compute comprehension-debt proxy: loop-authored commit ratio.

    Honest-proxy constraint: ilk records no explicit "human reviewed this
    merge" signal yet, so a *true* un-reviewed-merge ratio isn't computable.
    The defensible proxy is **loop-authored-commit ratio**: commits bearing
    a loop signature (`[plan:<slug>#step-N]` trailer, or the worker
    `Co-Authored-By`) over total commits in a window — high ratio = code
    merged with little human authorship, the comprehension-debt direction.

    Args:
        project_path: Project directory to run git log in.
        git_log_runner: Injectable callable ``(cwd, n) -> list[str]`` where
            each string is a raw commit message body.  Defaults to
            ``_default_git_log_runner`` which calls ``git log``.

    Returns:
        Dict with loop_authored_ratio, total_commits, loop_commits,
        is_proxy, needs_signal — or None when project_path is None.
    """
    if project_path is None:
        return None

    if git_log_runner is None:
        git_log_runner = _default_git_log_runner

    try:
        messages = git_log_runner(str(project_path), 100)
    except Exception:
        return None

    total = len(messages)
    if total == 0:
        return {
            "loop_authored_ratio": None,
            "total_commits": 0,
            "loop_commits": 0,
            "is_proxy": True,
            "needs_signal": "explicit human-review record",
        }

    loop_count = 0
    for msg in messages:
        if _is_loop_signed(msg):
            loop_count += 1

    return {
        "loop_authored_ratio": round(loop_count / total, 4),
        "total_commits": total,
        "loop_commits": loop_count,
        "is_proxy": True,
        "needs_signal": "explicit human-review record",
    }


def _default_git_log_runner(cwd: str, n: int = 100) -> list[str]:
    """Default git-log runner: returns the last *n* commit message bodies."""
    import subprocess as _sp

    result = _sp.run(
        ["git", "log", f"--max-count={n}", "--format=%B"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    # Split on blank lines — each commit body is separated by a blank line
    raw = result.stdout.strip()
    if not raw:
        return []
    # git log --format=%B separates commits with blank lines
    bodies: list[str] = []
    current: list[str] = []
    for line in raw.split("\n"):
        if line.strip() == "" and current:
            bodies.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if current:
        bodies.append("\n".join(current))
    return bodies


# Signature patterns for loop-authored commits
_LOOP_SIGNATURE_PATTERNS = [
    re.compile(r"^\[plan:", re.MULTILINE),                     # [plan:slug#step-N] trailer
    re.compile(r"Co-Authored-By:.*worker", re.IGNORECASE),     # worker co-author
]


def _is_loop_signed(commit_message: str) -> bool:
    """Return True if the commit message carries a loop signature."""
    for pat in _LOOP_SIGNATURE_PATTERNS:
        if pat.search(commit_message):
            return True
    return False


def _compute_all_kpis(
    records: list[dict[str, Any]],
    project_path: Path | None = None,
    git_log_runner: Any | None = None,
) -> dict[str, Any]:
    """Compute all KPIs from the JSONL records. Returns a metrics dict."""
    dist = compute_classification_distribution(records)
    time_to_ship = compute_time_to_ship_by_tier(records, project_path)
    thrash_count = compute_blacklist_thrash_count(records, project_path)
    comprehension_debt = compute_comprehension_debt(project_path, git_log_runner)

    result: dict[str, Any] = {
        "classification_distribution": dist,
        "total_runs": len(set(r.get("run_id", "") for r in records)),
        "total_iterations": len(records),
        # Now-computable KPIs (null when needed fields absent)
        "time_to_ship_by_tier": time_to_ship,
        "blacklist_thrash_count": thrash_count,
        "comprehension_debt": comprehension_debt,
        # KPIs that need instrumentation — honest nulls
        "human_touch_count": None,
        "escaped_bug_rate": None,
        "needs_instrumentation": {
            "time_to_ship_by_tier": time_to_ship is None,
            "blacklist_thrash_count": thrash_count is None,
            "human_touch_count": True,
            "escaped_bug_rate": True,
            "comprehension_debt": comprehension_debt is None,
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

    # Now-computable KPIs
    tts = metrics.get("time_to_ship_by_tier")
    if tts:
        lines.append("Time-to-ship by tier:")
        for tier, stats in sorted(tts.items()):
            lines.append(
                f"  {tier}: avg={stats['avg_sec']}s "
                f"min={stats['min_sec']}s max={stats['max_sec']}s "
                f"(n={stats['count']})"
            )
    else:
        lines.append("Time-to-ship by tier: null (needs_instrumentation)")

    thrash = metrics.get("blacklist_thrash_count")
    if thrash is not None:
        lines.append(f"Blacklist thrash count: {thrash}")
    else:
        lines.append("Blacklist thrash count: null (needs_instrumentation)")

    # Comprehension debt (proxy)
    cd = metrics.get("comprehension_debt")
    if cd is not None:
        ratio = cd.get("loop_authored_ratio")
        ratio_str = f"{ratio:.2%}" if ratio is not None else "null"
        lines.append(
            f"Comprehension debt: {ratio_str} loop-authored "
            f"({cd['loop_commits']}/{cd['total_commits']}) [proxy]"
        )
    else:
        lines.append("Comprehension debt: null (needs_instrumentation)")

    lines.append("")

    # Honest null KPIs
    lines.append("Not yet instrumented:")
    needs = metrics.get("needs_instrumentation", {})
    for kpi, needed in needs.items():
        if needed and kpi not in ("time_to_ship_by_tier", "blacklist_thrash_count"):
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
        metrics = _compute_all_kpis(records, project_path)
    else:
        # --all mode: read JSONL from each project's logs/ dir directly
        data_root = _resolve_data_root(args.data_root)
        records = []
        for proj_dir in _discover_projects(data_root):
            jsonl_path = proj_dir / "logs" / ".ilk-loop.log"
            if jsonl_path.exists():
                records.extend(_read_jsonl(jsonl_path))
        metrics = _compute_all_kpis(records)

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(_render_text(metrics))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
