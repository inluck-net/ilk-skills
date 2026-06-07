"""All-projects + slots self-refreshing dashboard for ilk-loop.

Renders the output of ``status_all.py --json`` as a human-readable table.
Designed for ``/ilk-status --watch`` — refreshes every *-n* seconds until
interrupted.  Dependency-free (ANSI clear + redraw, no curses).

Modes
-----
``--once``           render one frame and exit (for CI / tests)
``--watch``          refresh loop (default cadence 5 s via ``-n``)
``--json-from FILE`` read sample JSON from *FILE* instead of calling
                     ``status_all.py`` (for deterministic test fixtures)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── sibling imports ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── data acquisition ────────────────────────────────────────────────

def _status_all_json() -> list[dict]:
    """Call status_all.py --json and return parsed list."""
    status_all = Path(__file__).resolve().parent / "status_all.py"
    result = subprocess.run(
        [sys.executable, str(status_all), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"status_all.py failed (exit {result.returncode}): {result.stderr}")
    return json.loads(result.stdout)


def _load_json_from(path: str) -> list[dict]:
    """Load status JSON from a file path."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── rendering ───────────────────────────────────────────────────────

def _clear_screen() -> None:
    """ANSI clear + cursor home.  Works on all terminals."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def render_frame(entries: list[dict]) -> str:
    """Render one dashboard frame.  Returns the string to print.

    AC-1 table columns: project, master, next_subplan (cur/est), sentinel, class.
    AC-3 header shows ``live N / slots M`` when >=1 sentinel alive.
    """
    lines: list[str] = []

    # ── header: live / slots count (AC-3) ────────────────────────────
    total = len(entries)
    alive_count = sum(1 for e in entries if e.get("sentinel", {}).get("alive"))
    lines.append(f"ilk status — live {alive_count} / slots {total}")
    lines.append("")

    if not entries:
        lines.append("No projects found.")
        return "\n".join(lines)

    # ── column widths ────────────────────────────────────────────────
    col_proj = max(len("project"), max(len(e.get("project_key", "")) for e in entries))
    col_master = max(len("master"), max(len(e.get("active_master", "") or "-") for e in entries))
    col_next = max(len("next"), max(len(e.get("next_subplan", "") or "-") for e in entries))
    col_step = max(len("step"), 6)
    col_sent = max(len("sentinel"), 12)

    # header row
    lines.append(
        f"{'project'.ljust(col_proj)}  "
        f"{'master'.ljust(col_master)}  "
        f"{'next'.ljust(col_next)}  "
        f"{'step'.ljust(col_step)}  "
        f"{'sentinel'.ljust(col_sent)}  "
        f"class"
    )
    lines.append(
        f"{'-' * col_proj}  "
        f"{'-' * col_master}  "
        f"{'-' * col_next}  "
        f"{'-' * col_step}  "
        f"{'-' * col_sent}  "
        f"-----"
    )

    # ── data rows ────────────────────────────────────────────────────
    for e in entries:
        proj = e.get("project_key", "?")
        master = e.get("active_master", "") or "-"
        next_sp = e.get("next_subplan", "") or "-"
        step = e.get("step", "") or "-"
        sent = e.get("sentinel", {})
        alive_str = "alive" if sent.get("alive") else "dead"
        sent_display = f"{sent.get('state', '?')}:{alive_str}"
        last_class = e.get("last_class") or "-"

        lines.append(
            f"{proj.ljust(col_proj)}  "
            f"{master.ljust(col_master)}  "
            f"{next_sp.ljust(col_next)}  "
            f"{step.ljust(col_step)}  "
            f"{sent_display.ljust(col_sent)}  "
            f"{last_class}"
        )

    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="ilk all-projects dashboard")
    ap.add_argument("--once", action="store_true",
                    help="Render one frame and exit (for tests / CI)")
    ap.add_argument("--watch", action="store_true",
                    help="Refresh loop (default every 5 s)")
    ap.add_argument("-n", type=float, default=5.0,
                    help="Refresh interval in seconds (default 5)")
    ap.add_argument("--json-from", type=str, default=None,
                    help="Read status JSON from file instead of calling status_all.py")
    args = ap.parse_args()

    # Default to --once if neither --once nor --watch given.
    if not args.watch:
        args.once = True

    # Data source
    if args.json_from:
        def _get_entries() -> list[dict]:
            return _load_json_from(args.json_from)
    else:
        def _get_entries() -> list[dict]:
            return _status_all_json()

    if args.once:
        entries = _get_entries()
        print(render_frame(entries))
        return 0

    # --watch mode: refresh loop until interrupted
    try:
        while True:
            _clear_screen()
            entries = _get_entries()
            print(render_frame(entries))
            time.sleep(args.n)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
