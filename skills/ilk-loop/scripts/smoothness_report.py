"""Smoothness report — classify every run by how it ended.

Scans postmortem files across all projects, classifies each by terminal state,
and prints the clean-finish rate with its denominator.

AC6 from judged-by-the-readers-that-will-run-it:
    "smoothness_report.py classifies every run in a window by terminal state
    from the postmortem corpus and prints clean-finish rate with its denominator.
    It reproduces today's baseline — 2 of 18 over 24h — from the same data."

CLI:
    python smoothness_report.py [--hours N] [--since YYYY-MM-DDTHH:MM]
        Prints a table of terminal states and the clean-finish rate.
        Default window: 24 hours from now.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import ilk_data_root  # noqa: E402


def _parse_postmortem(path: Path) -> tuple[str, str, str] | None:
    """Extract (started_at, classification, project) from a postmortem file.

    Returns None if the file cannot be parsed.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None

    m_class = re.search(r'^classification:\s*"(.+?)"', text, re.MULTILINE)
    m_start = re.search(r'^started_at:\s*"(.+?)"', text, re.MULTILINE)
    m_proj = re.search(r'^project:\s*"(.+?)"', text, re.MULTILINE)

    if not (m_class and m_start and m_proj):
        return None

    return m_start.group(1), m_class.group(1), m_proj.group(1)


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO-8601 timestamp with optional timezone offset."""
    # Normalize common timezone formats.
    s = ts_str.strip()
    # +0800 → +08:00, +0000 → +00:00
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
        # Normalize to naive UTC for comparison.
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def collect_runs(
    since: datetime,
    data_root: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Collect (started_at, classification, project) for runs since *since*.

    Scans all projects under the ilk data root.
    """
    if data_root is None:
        data_root = ilk_data_root()

    runs: list[tuple[str, str, str]] = []
    projects_dir = data_root / "projects"

    if not projects_dir.is_dir():
        return runs

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        pm_dir = proj_dir / "runtime" / "launcher" / "postmortems"
        if not pm_dir.is_dir():
            continue
        for pm_file in pm_dir.glob("*.md"):
            parsed = _parse_postmortem(pm_file)
            if parsed is None:
                continue
            started_str, classification, project = parsed
            dt = _parse_timestamp(started_str)
            if dt is None:
                continue
            if dt < since:
                continue
            runs.append((started_str, classification, project))

    return runs


def classify_runs(
    runs: list[tuple[str, str, str]],
) -> dict[str, int]:
    """Count runs by terminal state classification."""
    counter: Counter[str] = Counter()
    for _, classification, _ in runs:
        counter[classification] += 1
    return dict(counter)


# States that count as "clean finish" — the loop completed its work
# without structural or environmental failure.
_CLEAN_STATES = {"clean-success", "all-shipped"}


def smoothness_report(
    runs: list[tuple[str, str, str]],
) -> tuple[int, int, float, dict[str, int]]:
    """Compute the smoothness metric.

    Returns:
        (total_runs, clean_count, clean_rate, state_counts)
    """
    state_counts = classify_runs(runs)
    total = sum(state_counts.values())
    clean = sum(v for k, v in state_counts.items() if k in _CLEAN_STATES)
    rate = clean / total if total > 0 else 0.0
    return total, clean, rate, state_counts


def format_report(
    total: int,
    clean: int,
    rate: float,
    state_counts: dict[str, int],
    window_label: str,
) -> str:
    """Format the smoothness report as a human-readable string."""
    lines = [
        f"Smoothness report — {window_label}",
        f"",
        f"| Terminal state       | Count |",
        f"|----------------------|-------|",
    ]
    for state, count in sorted(state_counts.items(), key=lambda x: -x[1]):
        marker = " ✓" if state in _CLEAN_STATES else ""
        lines.append(f"| {state:<20s} | {count:>5d}{marker} |")
    lines.append(f"|----------------------|-------|")
    lines.append(f"| Total                | {total:>5d} |")
    lines.append(f"")
    lines.append(f"Clean finish: {clean} of {total} ({rate:.1%})")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoothness report — classify runs by terminal state."
    )
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Look back this many hours (default: 24).",
    )
    parser.add_argument(
        "--since", default=None,
        help="ISO-8601 start time (overrides --hours).",
    )
    parser.add_argument(
        "--data-root", default=None,
        help="ilk data root (default: auto-resolve).",
    )
    args = parser.parse_args()

    if args.since:
        since = _parse_timestamp(args.since)
        if since is None:
            print(f"ERROR: cannot parse --since timestamp: {args.since}",
                  file=sys.stderr)
            return 1
        window_label = f"since {args.since}"
    else:
        since = datetime.utcnow() - timedelta(hours=args.hours)
        window_label = f"last {args.hours}h"

    data_root = Path(args.data_root) if args.data_root else None
    runs = collect_runs(since, data_root)
    total, clean, rate, state_counts = smoothness_report(runs)

    print(format_report(total, clean, rate, state_counts, window_label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())