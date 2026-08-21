#!/usr/bin/env python3
"""
iteration_timing.py -- attribute an iteration's wall-clock from its JSONL record stream.

Parses `iter-*.log.jsonl` files, pairs `tool_use` → `tool_result` by
`tool_use_id`, and reports per-iteration wall-clock attribution:
  - tool time (per tool name)
  - test time (broad vs targeted)
  - model remainder (span − tool time)
  - unpaired count
  - backgrounded call count

Data source: `assistant` and `user` records carry `timestamp` fields;
`system` and `stream_event` records do not. Model time is therefore a
remainder, not a measured bucket — the reader names it accordingly.

Usage:
    iteration_timing.py --run <path>          # single iteration (file or dir)
    iteration_timing.py --run <path> --json   # JSON output
    iteration_timing.py --baseline            # corpus-wide summary
    iteration_timing.py --baseline --json     # JSON output
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── broad-vs-targeted classifier (AC-5) ──────────────────────────────────────

_TEST_CMD_RE = re.compile(r"pytest|py\.test|unittest|nose|nosetests", re.IGNORECASE)

# Patterns that narrow the run to a specific subset.
_TARGETED_PATTERNS = [
    re.compile(r"tests?[\\/]\S+"),          # path like tests/test_x.py
    re.compile(r"::\w+"),                    # node id like ::TestY
    re.compile(r"-k\s+\S+"),                # -k selector
    re.compile(r"--lf\b"),                   # --lf (last failed)
    re.compile(r"--last-failed\b"),          # --last-failed
]


def is_broad_test_command(command: str) -> bool:
    """Return True if *command* is a broad (full-suite) test invocation.

    A command is *targeted* if it names a path, ``::`` node id, ``-k``
    selector, or ``--lf``/``--last-failed``; otherwise *broad*.
    """
    if not _TEST_CMD_RE.search(command):
        return False  # not a test command at all
    for pat in _TARGETED_PATTERNS:
        if pat.search(command):
            return False
    return True


def is_test_command(command: str) -> bool:
    """Return True if *command* looks like a test runner invocation."""
    return bool(_TEST_CMD_RE.search(command))


# ── analysis ─────────────────────────────────────────────────────────────────

_BACKGROUND_RE = re.compile(r"moved to the background \(ID:")


def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime."""
    # Handle both 'Z' and '+00:00' suffixes.
    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def analyze_iteration(path: Path) -> dict:
    """Analyze a single iteration's JSONL and return timing attribution.

    *path* may be a ``.jsonl`` file or a directory containing one.
    Returns a dict with keys: ``span_sec``, ``tool_sec``, ``test_sec``,
    ``broad_test_sec``, ``model_remainder_sec``, ``paired``, ``unpaired``,
    ``backgrounded_calls``.
    """
    if path.is_dir():
        jsonl_files = sorted(path.glob("iter-*.log.jsonl"))
        if not jsonl_files:
            raise FileNotFoundError(f"no iter-*.log.jsonl in {path}")
        path = jsonl_files[0]

    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Collect tool_use and tool_result blocks with their parent timestamps.
    tool_uses = {}    # tool_use_id → {name, command, ts}
    tool_results = {}  # tool_use_id → {ts, content}

    for rec in records:
        rec_type = rec.get("type")
        ts_str = rec.get("timestamp")
        if not ts_str:
            continue
        ts = _parse_ts(ts_str)

        if rec_type == "assistant":
            msg = rec.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "tool_use":
                    uid = block["id"]
                    inp = block.get("input", {})
                    tool_uses[uid] = {
                        "name": block.get("name", "unknown"),
                        "command": inp.get("command", ""),
                        "ts": ts,
                    }
        elif rec_type == "user":
            msg = rec.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "tool_result":
                    uid = block.get("tool_use_id")
                    if uid:
                        tool_results[uid] = {
                            "ts": ts,
                            "content": block.get("content", ""),
                        }

    # Pair and compute durations.
    tool_sec = 0.0
    test_sec = 0.0
    broad_test_sec = 0.0
    paired = 0
    unpaired = 0
    backgrounded_calls = 0

    all_timestamps = []

    for uid, tu in tool_uses.items():
        tr = tool_results.get(uid)
        if tr is None:
            unpaired += 1
            all_timestamps.append(tu["ts"])
            continue

        paired += 1
        duration = (tr["ts"] - tu["ts"]).total_seconds()
        tool_sec += duration
        all_timestamps.extend([tu["ts"], tr["ts"]])

        # Check if this is a test command.
        if is_test_command(tu["command"]):
            test_sec += duration
            if is_broad_test_command(tu["command"]):
                broad_test_sec += duration

        # Check if auto-backgrounded.
        if _BACKGROUND_RE.search(str(tr["content"])):
            backgrounded_calls += 1

    # Also collect timestamps from non-tool records.
    for rec in records:
        ts_str = rec.get("timestamp")
        if ts_str:
            all_timestamps.append(_parse_ts(ts_str))

    # Compute span.
    span_sec = 0.0
    if all_timestamps:
        span_sec = (max(all_timestamps) - min(all_timestamps)).total_seconds()

    model_remainder_sec = max(0.0, span_sec - tool_sec)

    return {
        "span_sec": round(span_sec, 3),
        "tool_sec": round(tool_sec, 3),
        "test_sec": round(test_sec, 3),
        "broad_test_sec": round(broad_test_sec, 3),
        "model_remainder_sec": round(model_remainder_sec, 3),
        "paired": paired,
        "unpaired": unpaired,
        "backgrounded_calls": backgrounded_calls,
    }


# ── corpus baseline (AC-4) ──────────────────────────────────────────────────

def _find_corpus_root() -> Path:
    """Resolve the ilk data root from env or default."""
    import os
    root = os.environ.get("ILK_DATA_HOME") or os.environ.get("ILK_DATA_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".ilk-data"


def analyze_baseline() -> dict:
    """Walk the full corpus and return aggregate timing shares.

    Returns a dict with: ``iterations_parsed``, ``iterations_unusable``,
    ``total_hours``, ``shares`` (sub-dict with per-bucket percentages).
    """
    corpus_root = _find_corpus_root()
    run_dirs = sorted(corpus_root.glob("projects/*/logs/runs/*"))

    total_span = 0.0
    total_tool = 0.0
    total_test = 0.0
    total_broad = 0.0
    parsed = 0
    unusable = 0

    for run_dir in run_dirs:
        for jsonl_file in sorted(run_dir.glob("iter-*.log.jsonl")):
            try:
                result = analyze_iteration(jsonl_file)
                if result["span_sec"] <= 0:
                    unusable += 1
                    continue
                parsed += 1
                total_span += result["span_sec"]
                total_tool += result["tool_sec"]
                total_test += result["test_sec"]
                total_broad += result["broad_test_sec"]
            except Exception:
                unusable += 1

    total_hours = round(total_span / 3600, 1)

    shares = {}
    if total_span > 0:
        shares["model_remainder_pct"] = round(
            (total_span - total_tool) / total_span * 100, 1
        )
        shares["tool_pct"] = round(total_tool / total_span * 100, 1)
        shares["test_pct"] = round(total_test / total_span * 100, 1)
        shares["broad_test_pct"] = round(total_broad / total_span * 100, 1)
    else:
        shares["model_remainder_pct"] = 0.0
        shares["tool_pct"] = 0.0
        shares["test_pct"] = 0.0
        shares["broad_test_pct"] = 0.0

    return {
        "iterations_parsed": parsed,
        "iterations_unusable": unusable,
        "total_hours": total_hours,
        "shares": shares,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_table(result: dict, path: Path) -> None:
    """Print a human-readable per-iteration table."""
    print(f"File: {path}")
    print(f"  Span:             {result['span_sec']:.1f}s")
    print(f"  Tool total:       {result['tool_sec']:.1f}s")
    print(f"  Test total:       {result['test_sec']:.1f}s")
    print(f"    Broad tests:    {result['broad_test_sec']:.1f}s")
    print(f"  Model remainder:  {result['model_remainder_sec']:.1f}s")
    print(f"  Paired:           {result['paired']}")
    print(f"  Unpaired:         {result['unpaired']}")
    print(f"  Backgrounded:     {result['backgrounded_calls']}")


def _print_baseline_table(data: dict) -> None:
    """Print a human-readable corpus summary."""
    print(f"Iterations parsed:   {data['iterations_parsed']}")
    print(f"Iterations unusable: {data['iterations_unusable']}")
    print(f"Total hours:         {data['total_hours']}")
    shares = data["shares"]
    print(f"  Model remainder:   {shares['model_remainder_pct']}%")
    print(f"  Tool total:        {shares['tool_pct']}%")
    print(f"  Test total:        {shares['test_pct']}%")
    print(f"  Broad tests:       {shares['broad_test_pct']}%")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attribute an iteration's wall-clock from its JSONL record stream."
    )
    parser.add_argument(
        "--run", type=Path, help="Path to a single iteration JSONL file or directory"
    )
    parser.add_argument(
        "--baseline", action="store_true",
        help="Walk the full corpus and report aggregate shares"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit results as JSON"
    )
    args = parser.parse_args()

    if not args.run and not args.baseline:
        parser.error("either --run or --baseline is required")

    if args.baseline:
        data = analyze_baseline()
        if args.json_output:
            json.dump(data, sys.stdout, indent=2)
            print()
        else:
            _print_baseline_table(data)
        return

    result = analyze_iteration(args.run)
    if args.json_output:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        _print_table(result, args.run)


if __name__ == "__main__":
    main()
