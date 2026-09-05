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
import re
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


def _iter_runs(since: str | None, root: Path | None = None,
               project: str | None = None):
    """Yield (project_name, run_dir). *root* overrides DATA for tests.

    *project* restricts the scan to one project key.  Scanning every
    project is right for the cross-project diagnostic and wrong for a
    per-project consumer: measured 2026-09-05, the unscoped scan reads
    the whole corpus in ~11.9s and grows with it, and plan_lint pays that
    on every invocation.
    """
    data = root if root is not None else DATA
    # A missing data root is a fact about the environment, not an empty
    # corpus — say so instead of raising, and never report it as "0 found".
    if not data.is_dir():
        raise SystemExit(
            f"gate_cost: no project data at {data} — "
            f"check $ILK_DATA_HOME / $ILK_DATA_DIR (resolved via ilk_paths)."
        )
    if project is not None and not (data / project).is_dir():
        # An unknown key would otherwise scan nothing and report an empty
        # corpus, which is indistinguishable from a project with no runs.
        raise SystemExit(
            f"gate_cost: --project {project!r} not found under {data} "
            f"({len(list(data.iterdir()))} projects present)."
        )
    for proj in sorted(data.iterdir()):
        if project is not None and proj.name != project:
            continue
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


def _calls_detailed(path: Path):
    """Yield (gap_sec, command, result_text) for every paired Bash call.

    *gap_sec* is the transcript gap: assistant record timestamp -> tool_result
    record timestamp.  It is an UPPER BOUND on the command's runtime, not a
    measurement of it — anything that delays the result record being written
    is inside it.  The dominant contaminant is a concurrently running
    backgrounded tool call: the transcript serialises, so a 0.06s command
    issued while a 158s background suite is in flight gets the background
    task's elapsed time stamped on it.  Measured 2026-09-05 on the real
    corpus: 342 of 2668 single-file pytest invocations had a gap >= 10x the
    duration pytest itself reported, worst case 3912x.

    *result_text* carries pytest's own summary line, which is the actual
    instrument — see _pytest_reported_seconds.
    """
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
                body = b.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        x.get("text", "") for x in body if isinstance(x, dict)
                    )
                res[b.get("tool_use_id")] = (ts, body if isinstance(body, str) else "")
    for tid, (ts, cmd) in starts.items():
        if tid in res:
            rts, body = res[tid]
            try:
                d = (it._parse_ts(rts) - it._parse_ts(ts)).total_seconds()
            except Exception:
                continue
            yield d, cmd, body


def _calls(path: Path):
    """Yield (gap_sec, command) for every paired Bash call.

    Retained for the broad-gate report in main(), whose headline (ceiling
    hits) is a claim about the harness boundary — the gap is the right
    quantity there.  Per-file cost must not use it; see _calls_detailed.
    """
    for d, cmd, _ in _calls_detailed(path):
        yield d, cmd


# pytest's own summary line: "3 passed in 0.06s", "1 failed, 2 passed in 12.34s",
# "no tests ran in 0.31s", "2 passed, 1 skipped in 1.02s (0:00:01)".
_PYTEST_SUMMARY = re.compile(
    r"\b(?:passed|failed|error|errors|skipped|xfailed|xpassed|deselected|"
    r"no tests ran)\b[^\n]*?\bin\s+([0-9]+(?:\.[0-9]+)?)s",
    re.IGNORECASE,
)


def _pytest_reported_seconds(result_text: str) -> float | None:
    """Seconds pytest reported for itself, or None if it did not report.

    This is the measurement.  Unlike the transcript gap it is produced by the
    process being measured, so it cannot absorb harness queueing or an
    unrelated background task's elapsed time.  None means "this invocation
    was not measured" — a ceiling-hit call returns zero output and lands
    here — and a None must never be silently replaced by the gap.
    """
    if not result_text:
        return None
    # Take the FIRST summary.  A chained command (`pytest a.py && pytest
    # tests/`) prints one per run, and the target we parsed is the first
    # pytest in the string — _parse_pytest_targets stops at the separator.
    # The later summaries belong to the later commands.  Measured on the
    # real corpus 2026-09-05: 15 of 2369 single-target result bodies carry
    # more than one summary, and in each the first is the parsed target's.
    m = _PYTEST_SUMMARY.search(result_text)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# Tokens that end the pytest invocation.  Everything after one of these
# belongs to a DIFFERENT command and must not be read as a pytest target:
# `pytest a.py; cat b.py` names one test file, not two.
_SHELL_BREAK = re.compile(r"[;&|<>]")

# A token carrying any of these is shell syntax, a glob, or a variable — never
# a path pytest was handed literally.  `2>/dev/null;`, `tests/test_*.py` and
# `"$D/run.txt"` all reached the per-file report through the old parser.
_SHELL_CHARS = re.compile(r"[;&|<>$`(){}*?\[\]!\\]")


def _is_test_target(tok: str) -> bool:
    """True if *tok* is a pytest target naming a FILE (optionally a node id).

    Deliberately narrow.  The old rule ("has a slash, or starts with 'test',
    or ends with .py") admitted directories, globs, redirections and a gate's
    own output file as "test files"; measured 2026-09-05, 44 of gh-resolve's
    547 per-file entries were not files at all.  A per-FILE report may only
    contain things that are files.
    """
    if not tok or tok.startswith("-"):
        return False
    if _SHELL_CHARS.search(tok):
        return False
    path = tok.split("::", 1)[0]  # tests/test_x.py::Class::test_y -> tests/test_x.py
    return path.endswith(".py") and not path.endswith("/")


def _parse_pytest_targets(cmd: str) -> tuple[list[str], list[str]]:
    """Split a pytest command's positional targets into (files, other).

    *files* are targets naming a .py file (optionally a node id).  *other* is
    every remaining positional — a directory, a glob, `.`.  Both are needed:
    a duration may only be attributed to a file when that file was the SOLE
    target of the invocation, so a caller must be able to see that
    `pytest a.py tests/ -q` had a second target even though the second one is
    not a file.  Dropping the non-file target and calling the rest a
    single-file measurement is the attribution bug in a new place — measured:
    it moved test_data_home_sandbox.py from 4.6s to 86.8s by claiming a
    two-target run's time for one file.

    Returns ([], []) for non-pytest commands.
    """
    tokens = cmd.strip().split()
    if not tokens:
        return [], []

    # Must be a pytest invocation (python -m pytest, or bare pytest)
    pytest_idx = None
    for i, t in enumerate(tokens):
        if t == "pytest" or t.endswith("/pytest"):
            pytest_idx = i
            break
    if pytest_idx is None:
        return [], []

    # Args run from the pytest binary to the end of THIS command
    args = tokens[pytest_idx + 1:]
    files: list[str] = []
    other: list[str] = []
    skip_next = False
    for a in args:
        # A separator anywhere in the token ends this command: `-q;`, `2>&1`
        # and `2>/dev/null;` all mean the pytest invocation is over.
        if _SHELL_BREAK.search(a):
            break
        if skip_next:
            skip_next = False
            continue
        if a.startswith("-"):
            # -k, -m, --timeout, --timeout-method, etc. take a value
            # unless they use = form
            if "=" not in a and a in ("-k", "-m", "--timeout", "--timeout-method",
                                      "-x", "--maxfail", "-p", "--override-ini",
                                      "-n", "--dist", "--rootdir", "-c"):
                skip_next = True
            continue
        # Strip shell quoting before judging the token: a quoted duplicate of
        # a real path is the tell that quoting was never stripped.
        a = a.strip("'\"")
        if not a:
            continue
        if _is_test_target(a):
            files.append(a)
        else:
            other.append(a)
    return files, other


def _parse_test_files(cmd: str) -> list[str]:
    """The .py-file targets of a pytest command, ignoring any others.

    Callers that attribute a duration must use _parse_pytest_targets instead
    and check that `other` is empty — see its docstring.
    """
    return _parse_pytest_targets(cmd)[0]


def _run_date(run_id: str) -> datetime | None:
    """The date encoded in a run id (``20260825-234253``), or None."""
    try:
        return datetime.strptime(run_id[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return None


def _age_days(run_id: str | None, now: datetime) -> int | None:
    """Whole days between *run_id*'s date and *now*, or None if undatable."""
    d = _run_date(run_id) if run_id else None
    if d is None:
        return None
    return max(0, (now.date() - d.date()).days)


def _median(xs: list[float]) -> float | None:
    """Median of *xs*, or None when empty. Stdlib-only, no statistics import."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def per_file_report(
    root: Path,
    since: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    as_json: bool = False,
    project: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Produce per-test-file cost report.

    Returns the data dict; prints text to stdout unless *as_json* is True.

    Each file carries its own dispersion, not just a single number.  `max_s`
    is a conservative CEILING and a poor summary of what a gate costs: on the
    real corpus 2026-09-05, tests/test_drain.py had max 198.6s against a
    median of 1.2s over 44 samples, with the max 11 days old and a fresh
    whole-file measurement of 14.0s.  A reader given only the max cannot tell
    "slow now" from "was slow once".  A percentile ALONE would not fix that
    either — the samples span different commits with different test counts
    ("26 passed in 0.06s" and "79 passed in 24.35s" are the same file), so no
    single statistic is the answer.  Reporting n, median, max and the max's
    age lets the reader see the spread and judge.

    *now* is injectable so the age fields are deterministic under test.
    """
    if now is None:
        now = datetime.now()
    per_project: dict[str, dict] = {}

    for proj, run in _iter_runs(since, root=root, project=project):
        p = per_project.setdefault(proj, {
            "per_file": {},       # file -> {invocations, measured_invocations,
                                  #          total_s, max_s, max_gap_s}
            "multi_file": {},     # frozenset(files) -> {invocations, total_s}
            "total_pytest": 0,
            "single_file_pytest": 0,
            "unmeasured": 0,      # single-file calls pytest did not time
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
            for d, cmd, body in _calls_detailed(f):
                files, other = _parse_pytest_targets(cmd)
                if not files:
                    continue
                p["total_pytest"] += 1
                # A non-file target (a directory, a glob) makes this a
                # multi-target run even when only one .py is named.
                if len(files) == 1 and not other:
                    p["single_file_pytest"] += 1
                    entry = p["per_file"].setdefault(files[0], {
                        "invocations": 0, "measured_invocations": 0,
                        "total_s": 0.0, "max_s": None, "max_gap_s": 0.0,
                        "max_run": None, "samples": [],
                    })
                    entry["invocations"] += 1
                    entry["max_gap_s"] = max(entry["max_gap_s"], d)
                    # The duration comes from pytest, never from the gap.
                    # An invocation pytest did not time is NOT a measurement
                    # of zero — it is counted and excluded.
                    self_s = _pytest_reported_seconds(body)
                    if self_s is None:
                        p["unmeasured"] += 1
                    else:
                        entry["measured_invocations"] += 1
                        entry["total_s"] += self_s
                        entry["samples"].append(self_s)
                        if entry["max_s"] is None or self_s > entry["max_s"]:
                            entry["max_s"] = self_s
                            # Which run produced the max is the whole point of
                            # keeping it: a ceiling from months ago and one
                            # from this morning are not the same claim.
                            entry["max_run"] = run.name
                else:
                    key = frozenset(files) | frozenset(other)
                    entry = p["multi_file"].setdefault(key, {
                        "invocations": 0, "total_s": 0.0,
                    })
                    entry["invocations"] += 1
                    self_s = _pytest_reported_seconds(body)
                    entry["total_s"] += self_s if self_s is not None else 0.0

    # Build output.  schema 2: `total_s`/`max_s` are pytest's own reported
    # seconds, not the transcript gap, and `max_s` is null for a file no
    # invocation of which was timed.  Consumers keyed on schema 1 were
    # reading gap seconds under these names — see _calls_detailed.
    out: dict = {
        "schema": 3,
        "project": project,
        "measured_at": now.strftime("%Y-%m-%d %H:%M"),
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
        # Derive dispersion, then round.  `samples` is working state and is
        # dropped: a consumer that wants the distribution should get the
        # summary, not re-derive one from a list whose length is unbounded.
        for entry in per_file_list:
            samples = entry.pop("samples", [])
            p50 = _median(samples)
            entry["p50_s"] = None if p50 is None else round(p50, 2)
            entry["max_age_days"] = _age_days(entry.get("max_run"), now)
            entry["total_s"] = round(entry["total_s"], 2)
            entry["max_gap_s"] = round(entry["max_gap_s"], 1)
            if entry["max_s"] is not None:
                entry["max_s"] = round(entry["max_s"], 2)
        for entry in multi_list:
            entry["total_s"] = round(entry["total_s"], 2)
        out["per_project"][proj_name] = {
            "per_file": per_file_list,
            "multi_file": multi_list,
            "total_pytest_invocations": p["total_pytest"],
            "single_file_invocations": p["single_file_pytest"],
            "unmeasured_invocations": p["unmeasured"],
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
        unmeasured = proj["unmeasured_invocations"]
        print(f"  of those, {denom - unmeasured} were timed by pytest itself; "
              f"{unmeasured} returned no summary line and are excluded")
        print(f"  iters searched: {proj['iters_searched']}")
        print()
        if proj["per_file"]:
            print(f"  {'file':<50} {'n':>4} {'p50_s':>8} {'max_s':>9} "
                  f"{'age':>6} {'gap_s':>8}")
            for e in proj["per_file"]:
                max_s = " unmeas." if e["max_s"] is None else f"{e['max_s']:>9.2f}"
                p50 = "       -" if e["p50_s"] is None else f"{e['p50_s']:>8.2f}"
                age = ("     -" if e["max_age_days"] is None
                       else f"{e['max_age_days']:>4}d ")
                print(f"  {e['file'][:50]:<50} {e['measured_invocations']:>4} "
                      f"{p50} {max_s} {age} {e['max_gap_s']:>8.1f}")
            print()
            print("  n is measured invocations. max_s is a CEILING, not the "
                  "cost — compare it against")
            print("  p50_s and age before budgeting from it: a 199s max at a "
                  "1.2s median over 44 samples,")
            print("  11 days old, describes one slow run, not a slow file. "
                  "gap_s is the transcript gap")
            print("  (inflated by any concurrent backgrounded call) — shown "
                  "for contrast, never for budgeting.")
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
    ap.add_argument("--project", help="restrict the scan to one project key "
                                     "(as under <data root>/projects/). Without "
                                     "it every project is scanned.")
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
                        as_json=a.json, project=a.project)
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

    for proj, run in _iter_runs(a.since, project=a.project):
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
