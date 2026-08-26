#!/usr/bin/env python3
"""gate_cost — measure what broad-suite gates actually cost per iteration.

The corpus aggregate (iteration_timing --baseline) is dominated by history and
cannot detect a recent change. This reports the cost driver directly, with an
explicit denominator, so a before/after comparison is meaningful.

Definitions (stated so "after" means the same thing):
  broad command      — is_broad_test_command() from iteration_timing
  ceiling hit        — a tool call whose duration >= CEILING_S (harness
                       auto-background boundary); these produce no output
  repeat             — the same normalised command issued more than once
                       within ONE iteration
  gate-bearing iter  — an iteration that issued >= 1 broad command.  This is
                       the denominator that matters: iterations which never
                       ran a broad gate say nothing about gate cost.

  cut point         — --after <ISO8601> filters by each ITERATION's own start
                       time (its first record's timestamp), not by run id.  A
                       run can straddle a change: 20260825-115525 began 11:55
                       and the hook it measures landed 12:00, so run-level
                       filtering would have to include or exclude it whole and
                       both are wrong.

Usage:  gate_cost.py [--since YYYYMMDD] [--after ISO8601] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Resolve siblings relative to THIS file, never to a conventional home path —
# a hardcoded clone location breaks on any second host (rezmac).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import iteration_timing as it  # noqa: E402
from ilk_paths import ilk_data_root  # noqa: E402

CEILING_S = 590.0  # harness auto-backgrounds at 600s; allow jitter

# Honours $ILK_DATA_HOME / $ILK_DATA_DIR / ~/.ilk-data via the canonical
# resolver (SKILL.md "Data-home location & override"). Never re-derive it.
DATA = ilk_data_root() / "projects"


def _iter_runs(since: str | None, root: Path | None = None):
    """Yield (project_name, run_dir). *root* overrides DATA for tests."""
    data = root if root is not None else DATA
    # A missing data root is a fact about the environment, not an empty
    # corpus — say so instead of raising, and never report it as "0 found".
    if not data.is_dir():
        raise SystemExit(
            f"gate_cost: no project data at {data} — "
            f"check $ILK_DATA_HOME / $ILK_DATA_DIR (resolved via ilk_paths)."
        )
    for proj in sorted(data.iterdir()):
        runs = proj / "logs" / "runs"
        if not runs.is_dir():
            continue
        for run in sorted(runs.iterdir()):
            if not run.is_dir():
                continue
            if since and run.name[:8] < since:
                continue
            yield proj.name, run


def _iter_start_ts(path: Path):
    """First record timestamp in an iteration log, or None if unreadable.

    This is the iteration's start, which is what a cut point must compare
    against — the file's mtime is when it was last WRITTEN, i.e. the end.
    """
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("timestamp")
                if ts:
                    try:
                        return it._parse_ts(ts)
                    except Exception:
                        return None
    except OSError:
        return None
    return None


def _calls(path: Path):
    """Yield (duration_sec, command) for every paired Bash call."""
    starts, res = {}, {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = r.get("timestamp")
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, list) or not ts:
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                starts[b["id"]] = (ts, (b.get("input") or {}).get("command", ""))
            elif b.get("type") == "tool_result":
                res[b.get("tool_use_id")] = ts
    for tid, (ts, cmd) in starts.items():
        if tid in res:
            try:
                d = (it._parse_ts(res[tid]) - it._parse_ts(ts)).total_seconds()
            except Exception:
                continue
            yield d, cmd


def _parse_test_files(cmd: str) -> list[str]:
    """Extract test-file paths from a pytest command string.

    Handles the forms this repo actually produces:
      bare paths, -q/-v before or after paths, -k selections, pipes to
      tail/grep.  Returns [] for non-pytest commands or commands with no
      recognisable test-file arguments.
    """
    # Strip pipes — only the left side names test files
    left = cmd.split("|")[0].strip()
    tokens = left.split()
    if not tokens:
        return []

    # Must be a pytest invocation (python -m pytest, or bare pytest)
    pytest_idx = None
    for i, t in enumerate(tokens):
        if t == "pytest" or t.endswith("/pytest"):
            pytest_idx = i
            break
    if pytest_idx is None:
        return []

    # Everything after the pytest binary is args
    args = tokens[pytest_idx + 1:]
    files = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("-"):
            # -k, -m, --timeout, --timeout-method, etc. take a value
            # unless they use = form
            if "=" not in a and a in ("-k", "-m", "--timeout", "--timeout-method",
                                      "-x", "--maxfail", "-p", "--override-ini"):
                skip_next = True
            continue
        # Not a flag — treat as a path if it looks like a test path
        if "/" in a or a.startswith("test") or a.endswith(".py"):
            files.append(a)
    return files


def per_file_report(
    root: Path,
    since: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    as_json: bool = False,
) -> dict:
    """Produce per-test-file wall-clock report.

    Returns the data dict; prints text to stdout unless *as_json* is True.
    """
    per_project: dict[str, dict] = {}

    for proj, run in _iter_runs(since, root=root):
        p = per_project.setdefault(proj, {
            "per_file": {},       # file -> {invocations, total_s, max_s}
            "multi_file": {},     # frozenset(files) -> {invocations, total_s}
            "total_pytest": 0,
            "single_file_pytest": 0,
            "iters_searched": 0,
            "runs_searched": 0,
        })
        p["runs_searched"] += 1
        for f in sorted(run.glob("iter-*.log.jsonl")):
            if after is not None or before is not None:
                start = _iter_start_ts(f)
                if start is None:
                    continue
                if after is not None and start <= after:
                    continue
                if before is not None and start > before:
                    continue
            p["iters_searched"] += 1
            for d, cmd in _calls(f):
                files = _parse_test_files(cmd)
                if not files:
                    continue
                p["total_pytest"] += 1
                if len(files) == 1:
                    p["single_file_pytest"] += 1
                    entry = p["per_file"].setdefault(files[0], {
                        "invocations": 0, "total_s": 0.0, "max_s": 0.0,
                    })
                    entry["invocations"] += 1
                    entry["total_s"] += d
                    entry["max_s"] = max(entry["max_s"], d)
                else:
                    key = frozenset(files)
                    entry = p["multi_file"].setdefault(key, {
                        "invocations": 0, "total_s": 0.0,
                    })
                    entry["invocations"] += 1
                    entry["total_s"] += d

    # Build output
    out: dict = {
        "schema": 1,
        "since": since,
        "after": after.isoformat() if after else None,
        "before": before.isoformat() if before else None,
        "per_project": {},
    }

    for proj_name, p in sorted(per_project.items()):
        per_file_list = sorted(
            [{"file": f, **v} for f, v in p["per_file"].items()],
            key=lambda e: e["total_s"], reverse=True,
        )
        multi_list = sorted(
            [{"files": sorted(k), **v} for k, v in p["multi_file"].items()],
            key=lambda e: e["total_s"], reverse=True,
        )
        # Round floats
        for entry in per_file_list:
            entry["total_s"] = round(entry["total_s"], 1)
            entry["max_s"] = round(entry["max_s"], 1)
        for entry in multi_list:
            entry["total_s"] = round(entry["total_s"], 1)
        out["per_project"][proj_name] = {
            "per_file": per_file_list,
            "multi_file": multi_list,
            "total_pytest_invocations": p["total_pytest"],
            "single_file_invocations": p["single_file_pytest"],
            "iters_searched": p["iters_searched"],
            "runs_searched": p["runs_searched"],
        }

    if as_json:
        print(json.dumps(out, indent=2))
        return out

    # Text output
    window = (f"iterations starting at or before {before}" if before and not after
              else f"iterations starting after {after}" if after
              else f"runs since {since}" if since else "all runs")

    any_data = any(p["per_file"] for p in out["per_project"].values())
    if not any_data:
        total_runs = sum(p["runs_searched"] for p in out["per_project"].values())
        total_iters = sum(p["iters_searched"] for p in out["per_project"].values())
        if not out["per_project"]:
            print(f"no measurements ({window}; 0 runs searched)")
        else:
            print(f"no measurements ({window}; {total_runs} runs, "
                  f"{total_iters} iterations searched)")
        return out

    for proj_name, proj in out["per_project"].items():
        print(f"\n  {proj_name}  ({window})")
        denom = proj["single_file_invocations"]
        total = proj["total_pytest_invocations"]
        print(f"  per-file cost from {denom} of {total} invocations")
        print(f"  iters searched: {proj['iters_searched']}")
        print()
        if proj["per_file"]:
            print(f"  {'file':<55} {'n':>4} {'total_s':>9} {'max_s':>9}")
            for e in proj["per_file"]:
                print(f"  {e['file']:<55} {e['invocations']:>4} "
                      f"{e['total_s']:>9.1f} {e['max_s']:>9.1f}")
        else:
            print("  (no single-file invocations)")
        if proj["multi_file"]:
            print()
            print("  multi-file invocations (not in per-file totals):")
            for e in proj["multi_file"]:
                print(f"  {' + '.join(e['files'])}  "
                      f"({e['invocations']}x, {e['total_s']:.1f}s)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only runs with id >= YYYYMMDD (run-level, coarse)")
    ap.add_argument("--after", help="only ITERATIONS starting after this ISO8601 "
                                    "timestamp (precise; handles a run that "
                                    "straddles the change being measured)")
    ap.add_argument("--before", help="only ITERATIONS starting at or before this "
                                     "ISO8601 timestamp. The mirror of --after: "
                                     "needed to re-derive a BEFORE number once "
                                     "post-change data has entered the corpus.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--by-test-file", action="store_true",
                    help="report per-test-file wall-clock (single-file invocations only)")
    a = ap.parse_args()

    if a.by_test_file:
        after_ts = None
        if a.after:
            try:
                after_ts = it._parse_ts(a.after)
            except Exception:
                raise SystemExit(f"gate_cost: --after {a.after!r} is not an ISO8601 timestamp")
        before_ts = None
        if a.before:
            try:
                before_ts = it._parse_ts(a.before)
            except Exception:
                raise SystemExit(f"gate_cost: --before {a.before!r} is not an ISO8601 timestamp")
        per_file_report(root=DATA, since=a.since, after=after_ts, before=before_ts,
                        as_json=a.json)
        return 0

    after_ts = None
    if a.after:
        try:
            after_ts = it._parse_ts(a.after)
        except Exception:
            raise SystemExit(f"gate_cost: --after {a.after!r} is not an ISO8601 timestamp")

    before_ts = None
    if a.before:
        try:
            before_ts = it._parse_ts(a.before)
        except Exception:
            raise SystemExit(f"gate_cost: --before {a.before!r} is not an ISO8601 timestamp")

    skipped_no_ts = 0
    per_project: dict[str, dict] = {}
    tot = dict(iters=0, gate_iters=0, broad_s=0.0, ceiling_hits=0,
               repeats=0, broad_calls=0)

    for proj, run in _iter_runs(a.since):
        p = per_project.setdefault(proj, dict(iters=0, gate_iters=0, broad_s=0.0,
                                              ceiling_hits=0, repeats=0,
                                              broad_calls=0, runs=0))
        p["runs"] += 1
        for f in sorted(run.glob("iter-*.log.jsonl")):
            if after_ts is not None or before_ts is not None:
                start = _iter_start_ts(f)
                if start is None:
                    # Undatable iteration: EXCLUDE and count it.  Silently
                    # including it would let pre-cut data into an after-window.
                    skipped_no_ts += 1
                    continue
                if after_ts is not None and start <= after_ts:
                    continue
                if before_ts is not None and start > before_ts:
                    continue
            p["iters"] += 1
            tot["iters"] += 1
            seen: dict[str, int] = {}
            had_broad = False
            for d, cmd in _calls(f):
                if not it.is_broad_test_command(cmd):
                    continue
                had_broad = True
                norm = it.normalise_command(cmd)
                seen[norm] = seen.get(norm, 0) + 1
                for bucket in (p, tot):
                    bucket["broad_s"] += d
                    bucket["broad_calls"] += 1
                    if d >= CEILING_S:
                        bucket["ceiling_hits"] += 1
            reps = sum(n - 1 for n in seen.values() if n > 1)
            p["repeats"] += reps
            tot["repeats"] += reps
            if had_broad:
                p["gate_iters"] += 1
                tot["gate_iters"] += 1

    def _fmt(d: dict) -> dict:
        gi = d["gate_iters"]
        return {
            **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()},
            "broad_s_per_gate_iter": round(d["broad_s"] / gi, 1) if gi else None,
        }

    out = {
        "measured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "since": a.since,
        "after": a.after,
        "before": a.before,
        "excluded_no_timestamp": skipped_no_ts,
        "ceiling_s": CEILING_S,
        "total": _fmt(tot),
        "per_project": {k: _fmt(v) for k, v in sorted(per_project.items())
                        if v["iters"]},
    }

    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    t = out["total"]
    window = (f"iterations starting at or before {a.before}" if a.before and not a.after
              else f"iterations starting after {a.after}" if a.after
              else f"runs since {a.since}" if a.since else "all runs")
    print(f"measured {t['iters']} iterations ({window})")
    if not t["iters"]:
        print("  no iterations in this window — nothing to report.")
        if skipped_no_ts:
            print(f"  ({skipped_no_ts} excluded: no parseable start timestamp)")
        return 0
    if skipped_no_ts:
        print(f"  excluded (undatable): {skipped_no_ts}")

    # HEADLINE: ceiling hits, per project. A ceiling hit is a broad command
    # that ran to the harness boundary and returned ZERO bytes -- near-binary,
    # and robust to which work happened to run. `per_gate` is an average over
    # projects with structurally different gate costs (ilk-skills ~106s vs
    # gh-resolve ~770s measured 2026-08-25), so adding cheap iterations drags
    # the blend down without anything improving. It is context, not the claim.
    print()
    print(f"  CEILING HITS (>={CEILING_S:.0f}s, zero output) : {t['ceiling_hits']}   <-- headline")
    print(f"  in-iteration repeats                     : {t['repeats']}   <-- headline")
    print(f"  gate-bearing iterations                  : {t['gate_iters']} of {t['iters']}")
    print(f"  broad-gate seconds                       : {t['broad_s']:.0f}s")
    print(f"  per gate-bearing iter (BLENDED, context) : {t['broad_s_per_gate_iter']}s")
    print()
    print("  Per project — compare a project against ITSELF; the blend across")
    print("  projects is not a like-for-like number.")
    print()
    print(f"  {'project':<44} {'iters':>6} {'gate':>5} {'CEIL':>5} {'REP':>5} {'broad_s':>9} {'per_gate':>9}")
    for k, v in out["per_project"].items():
        print(f"  {k:<44} {v['iters']:>6} {v['gate_iters']:>5} {v['ceiling_hits']:>5} "
              f"{v['repeats']:>5} {v['broad_s']:>9.0f} {str(v['broad_s_per_gate_iter']):>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
