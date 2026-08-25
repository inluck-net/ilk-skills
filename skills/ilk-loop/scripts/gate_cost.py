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

Usage:  gate_cost.py [--since YYYYMMDD] [--json]
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


def _iter_runs(since: str | None):
    # A missing data root is a fact about the environment, not an empty
    # corpus — say so instead of raising, and never report it as "0 found".
    if not DATA.is_dir():
        raise SystemExit(
            f"gate_cost: no project data at {DATA} — "
            f"check $ILK_DATA_HOME / $ILK_DATA_DIR (resolved via ilk_paths)."
        )
    for proj in sorted(DATA.iterdir()):
        runs = proj / "logs" / "runs"
        if not runs.is_dir():
            continue
        for run in sorted(runs.iterdir()):
            if not run.is_dir():
                continue
            if since and run.name[:8] < since:
                continue
            yield proj.name, run


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only runs with id >= YYYYMMDD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    per_project: dict[str, dict] = {}
    tot = dict(iters=0, gate_iters=0, broad_s=0.0, ceiling_hits=0,
               repeats=0, broad_calls=0)

    for proj, run in _iter_runs(a.since):
        p = per_project.setdefault(proj, dict(iters=0, gate_iters=0, broad_s=0.0,
                                              ceiling_hits=0, repeats=0,
                                              broad_calls=0, runs=0))
        p["runs"] += 1
        for f in sorted(run.glob("iter-*.log.jsonl")):
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
        "ceiling_s": CEILING_S,
        "total": _fmt(tot),
        "per_project": {k: _fmt(v) for k, v in sorted(per_project.items())
                        if v["iters"]},
    }

    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    t = out["total"]
    print(f"measured {t['iters']} iterations"
          + (f" (runs since {a.since})" if a.since else " (all runs)"))
    print(f"  gate-bearing iterations : {t['gate_iters']}"
          f"  ({100*t['gate_iters']/t['iters']:.0f}% of {t['iters']})"
          if t["iters"] else "  no iterations")
    print(f"  broad-gate seconds      : {t['broad_s']:.0f}s total")
    print(f"  per gate-bearing iter   : {t['broad_s_per_gate_iter']}s   <-- the number to move")
    print(f"  broad calls issued      : {t['broad_calls']}")
    print(f"  ceiling hits (>={CEILING_S:.0f}s) : {t['ceiling_hits']}   <-- each produces NO output")
    print(f"  in-iteration repeats    : {t['repeats']}")
    print()
    print(f"{'project':<48} {'iters':>6} {'gate':>5} {'broad_s':>9} {'per_gate':>9} {'ceil':>5} {'rep':>4}")
    for k, v in out["per_project"].items():
        print(f"{k:<48} {v['iters']:>6} {v['gate_iters']:>5} {v['broad_s']:>9.0f} "
              f"{str(v['broad_s_per_gate_iter']):>9} {v['ceiling_hits']:>5} {v['repeats']:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
