#!/usr/bin/env python3
"""
Postmortem generator for ilk-loop runs.

Reads the JSONL summary at ~/.cursor/skills/ilk-loop/logs/.ilk-loop.log
that `run_ilk_loop_claude.ps1` writes, plus the per-iteration text logs
in `<LogDir>\\ilk-claude-<run-id>\\`, and produces:

  - Classification (one of 8 taxonomy labels — see SKILL.md)
  - Parameter recommendations for next launch
  - Tail of the last problematic iteration's log
  - Markdown report saved to ~/.ilk-data/projects/<key>/runtime/launcher/postmortems/<run-id>.md
    with YAML front-matter so launcher Step 1.5 can read it cheaply.

CLI:
  python collect.py                                # cwd walk-up
  python collect.py -ProjectName es_api
  python collect.py -ProjectPath C:\\path\\to\\proj
  python collect.py -ProjectName es_api -RunId 20260523-110800
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


HOME = Path(os.path.expanduser("~"))
LAUNCHER_DIR = HOME / ".cursor" / "skills" / "ilk-launcher"
PROJECTS_JSON = LAUNCHER_DIR / "projects.json"
LOOP_LOG_DIR = HOME / ".cursor" / "skills" / "ilk-loop" / "logs"
JSONL_LOG = LOOP_LOG_DIR / ".ilk-loop.log"
LOOP_STATUS_SCRIPT = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts" / "loop_status.py"

# Pull in ilk_paths from the sibling ilk-loop skill so meta-project
# detection is consistent across the suite. Falls back to the legacy
# walk-up in resolve_by_cwd() if the import fails (e.g. running from a
# repo clone before install.sh symlinks are in place).
_ILK_PATHS_DIR = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts"
if _ILK_PATHS_DIR.is_dir():
    sys.path.insert(0, str(_ILK_PATHS_DIR))
try:
    from ilk_paths import (
        external_launcher_dir,
        find_project_root as _find_project_root,
        project_key,
    )  # type: ignore
except ImportError:
    _find_project_root = None  # type: ignore
    external_launcher_dir = None  # type: ignore
    project_key = None  # type: ignore

# How many lines of the last problematic iter's log to embed in the report.
TAIL_LINES = 80


# ---------- project resolution (mirror launcher logic) -----------------------


def read_projects_registry() -> list[dict]:
    if not PROJECTS_JSON.exists():
        return []
    try:
        data = json.loads(PROJECTS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data.get("projects", []) or []


def resolve_by_name(name: str) -> Path:
    for entry in read_projects_registry():
        if entry.get("name") == name:
            return Path(entry["path"])
    known = ", ".join(p.get("name", "?") for p in read_projects_registry())
    raise SystemExit(f"Project '{name}' not in projects.json. Known: {known}")


def resolve_by_cwd() -> Path:
    cur = Path.cwd()
    # Prefer ilk_paths' authoritative resolver — it recognises both
    # single-repo (.git ancestor) and meta-project (.ilk-meta.json
    # ancestor) layouts. Falls back to the legacy walk-up only if the
    # ilk-loop skill isn't installed yet.
    if _find_project_root is not None:
        root, _kind = _find_project_root(cur)
        if root is not None:
            return Path(root)
    for ancestor in [cur, *cur.parents]:
        plans = ancestor / "docs" / "plans"
        if plans.is_dir() and any(plans.glob("MASTER-*.md")):
            return ancestor
    raise SystemExit(
        f"No project root (.git or .ilk-meta.json) and no "
        f"docs/plans/MASTER-*.md found walking up from {cur}. "
        "Pass -ProjectName or -ProjectPath, or cd into a project."
    )


def project_name_for(path: Path) -> str:
    for entry in read_projects_registry():
        if Path(entry.get("path", "")) == path:
            return entry.get("name", path.name)
    return path.name


# ---------- log reading ------------------------------------------------------


def read_last_launch(project_path: Path) -> dict | None:
    if external_launcher_dir is None or project_key is None:
        return None
    f = external_launcher_dir(project_key(project_path)) / "last-launch.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_jsonl_iters(project_path: Path) -> list[dict]:
    """
    Return ALL iteration records for this project across all runs.
    Each record is a dict matching Write-JsonlRecord in run_ilk_loop_claude.ps1.
    """
    if not JSONL_LOG.exists():
        return []
    records = []
    project_path_str = str(project_path)
    project_path_norm = project_path_str.replace("/", "\\").lower()
    with JSONL_LOG.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_proj = str(rec.get("project", "")).replace("/", "\\").lower()
            if rec_proj == project_path_norm:
                records.append(rec)
    return records


def runs_index(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by run_id, preserving iteration order within each."""
    by_run: dict[str, list[dict]] = {}
    for rec in records:
        rid = rec.get("run_id")
        if not rid:
            continue
        by_run.setdefault(rid, []).append(rec)
    for rid, lst in by_run.items():
        lst.sort(key=lambda r: r.get("iteration", 0))
    return by_run


def newest_run_id(by_run: dict[str, list[dict]]) -> str | None:
    if not by_run:
        return None
    # run_id format is YYYYMMDD-HHMMSS, lexicographic sort = chronological
    return sorted(by_run.keys())[-1]


# ---------- classification ---------------------------------------------------

LOCAL_CHECK_RE = re.compile(
    r"tsc\b|typecheck|vitest|pytest|\bruff\b|eslint|bun run|cargo build|\bmake\b|npm test|npm run|mypy\b|pre-push|pre-commit|TS\d{4}|SyntaxError|ImportError",
    re.IGNORECASE,
)

API_RE = re.compile(
    r"connection timed out|socket hang up|"
    r"\b50[023]\b(?:\s+(?:Bad\s+Gateway|Service\s+Unavailable|Internal\s+Server\s+Error|error)\b|\s*$)|"
    r"ECONNRESET|rate limit(?!s\b)|\b429\b|Anthropic API error|anthropic\s+API\s+error|"
    r"connection reset|timeout exceeded",
    re.IGNORECASE,
)


def classify_log_keywords(lines: list[str]) -> str:
    """Return 'local-check', 'api', or 'unknown' based on keyword counts."""
    if not lines:
        return "unknown"
    local_count = sum(1 for line in lines if LOCAL_CHECK_RE.search(line))
    api_count = sum(1 for line in lines if API_RE.search(line))
    if local_count > api_count:
        return "local-check"
    if api_count > local_count:
        return "api"
    return "unknown"


def loop_status_exit(project_path: Path) -> int:
    if not LOOP_STATUS_SCRIPT.exists():
        return -1
    try:
        proc = subprocess.run(
            [sys.executable, str(LOOP_STATUS_SCRIPT)],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode
    except (subprocess.TimeoutExpired, OSError):
        return -1


def classify(
    iters: list[dict],
    last_launch: dict | None,
    project_path: Path,
) -> tuple[str, dict[str, Any]]:
    """
    Return (label, facts_dict) where facts_dict has the metrics used in the
    report and recommendations.
    """
    if not iters:
        return "interrupted", {
            "reason": "no JSONL records for this run — possibly failed before first iter completed",
        }

    last = iters[-1]
    last_stop = last.get("stop_reason")
    error_count = sum(1 for r in iters if r.get("exit_code") not in (0, None))
    err_rate = error_count / len(iters) if iters else 0.0
    new_commits_total = sum((r.get("new_commits_total") or 0) for r in iters)

    iter_count = len(iters)
    max_iter_configured = (last_launch or {}).get("max_iterations") or 0
    timeout_configured_min = (last_launch or {}).get("iteration_timeout_min") or 0

    # Override: local_checks have been failing repeatedly. Diagnostic
    # signal that the agent is committing work but cannot satisfy the
    # sub-plan's machine-checkable acceptance — typically means the
    # sub-plan's AC was wrong, the contract is impossible to satisfy
    # mechanically, or there's a real bug the agent can't resolve.
    #
    # Guarded by `last iter ended with a fail` so that clean runs
    # (last iter passes or has no checks) are never mis-classified.
    # Only fires when the loop was launched with -RunLocalChecks.
    last_checks = last.get("local_checks") or []
    last_failed = any(c.get("outcome") == "fail" for c in last_checks)
    if last_failed:
        recent = iters[-5:] if len(iters) >= 5 else iters
        fail_iters = sum(
            1 for r in recent
            if any((c.get("outcome") == "fail") for c in (r.get("local_checks") or []))
        )
        pass_iters = sum(
            1 for r in recent
            if (r.get("local_checks") or [])
            and all((c.get("outcome") == "pass") for c in (r.get("local_checks") or []))
        )
        if fail_iters >= 3 and fail_iters > pass_iters:
            return "local-checks-stuck", {
                "iter_at_stop": last.get("iteration"),
                "fail_iters_in_window": fail_iters,
                "pass_iters_in_window": pass_iters,
                "window_size": len(recent),
            }

    # explicit JSONL-recorded stop reasons
    if last_stop == "timeout":
        return "timeout-bound", {
            "iter_at_stop": last.get("iteration"),
            "duration_sec": last.get("duration_sec"),
            "configured_timeout_min": timeout_configured_min,
            "error_rate": err_rate,
        }
    if last_stop == "budget-exhausted":
        return "budget-exhausted", {
            "iter_at_stop": last.get("iteration"),
            "max_budget_usd": last.get("max_budget_usd"),
        }
    if last_stop == "already-shipped":
        return "clean-success", {
            "iters": iter_count,
            "commits": new_commits_total,
        }
    if last_stop == "no-progress":
        # split by error pattern
        last3 = iters[-3:]
        last3_errs = sum(1 for r in last3 if r.get("exit_code") not in (0, None))
        if last3_errs >= 2:
            # Disambiguate API errors from local-check failures via log keywords.
            kw = "unknown"
            last_log_path = last.get("log")
            if not last_log_path and last.get("run_id"):
                constructed = (
                    LOOP_LOG_DIR
                    / f"ilk-claude-{last.get('run_id')}"
                    / f"iter-{last.get('iteration', 0):02d}.log"
                )
                if constructed.exists():
                    last_log_path = str(constructed)
            if last_log_path:
                kw = classify_log_keywords(tail_log(last_log_path))
                if kw == "unknown":
                    # Fallback: raw JSONL stream often has more content than
                    # the human-readable .log file (especially older runs).
                    jsonl_path = Path(last_log_path).with_suffix(".log.jsonl")
                    if jsonl_path.exists():
                        kw = classify_log_keywords(
                            tail_log(str(jsonl_path), max_lines=5000)
                        )
            if kw == "local-check":
                fail_iters = sum(
                    1 for r in last3 if r.get("exit_code") not in (0, None)
                )
                pass_iters = sum(
                    1 for r in last3 if r.get("exit_code") in (0, None)
                )
                return "local-checks-stuck", {
                    "iter_at_stop": last.get("iteration"),
                    "fail_iters_in_window": fail_iters,
                    "pass_iters_in_window": pass_iters,
                    "window_size": len(last3),
                }
            return "api-blocked", {
                "iter_at_stop": last.get("iteration"),
                "last3_errors": last3_errs,
                "error_rate": err_rate,
            }
        return "stuck-no-progress", {
            "iter_at_stop": last.get("iteration"),
            "error_rate": err_rate,
        }

    # last_stop is null → loop didn't break inside the iter loop on a
    # known reason. Could be all-shipped (post-iter break) or max-iter
    # (post-loop) or external interruption.
    #
    # For *historical* runs, loop_status_exit() reflects the CURRENT project
    # state, not the state at run time. When the last iter was clean and made
    # progress, treat it as clean-success rather than interrupted.
    is_clean = last.get("exit_code") in (0, None) and (last.get("new_commits_total") or 0) > 0
    if loop_status_exit(project_path) == 0 or is_clean:
        # api-flaky overrides clean-success only if a lot of upstream errors
        if err_rate >= 0.30 and iter_count >= 3:
            return "api-flaky", {
                "error_rate": err_rate,
                "error_count": error_count,
                "note": "shipped despite endpoint instability",
            }
        return "clean-success", {
            "iters": iter_count,
            "commits": new_commits_total,
        }

    if max_iter_configured and iter_count >= max_iter_configured:
        return "max-iter-bound", {
            "iters": iter_count,
            "max_iter_configured": max_iter_configured,
            "error_rate": err_rate,
        }

    return "interrupted", {
        "iters": iter_count,
        "max_iter_configured": max_iter_configured,
        "note": "no JSONL stop_reason; loop_status not 0; iter < max — likely external kill (stop.ps1, window closed, or process crash)",
    }


# ---------- recommendations --------------------------------------------------


def recommend_params(
    label: str,
    iters: list[dict],
    last_launch: dict | None,
) -> tuple[int, int, str]:
    """Return (max_iter, timeout_min, rationale)."""
    cur_max = (last_launch or {}).get("max_iterations") or 30
    cur_to = (last_launch or {}).get("iteration_timeout_min") or 30

    durations_min = [(r.get("duration_sec") or 0) / 60.0 for r in iters]
    max_dur = max(durations_min) if durations_min else 0
    avg_dur = sum(durations_min) / len(durations_min) if durations_min else 0

    if label == "clean-success":
        return cur_max, cur_to, "kept previous params; run shipped clean"

    if label == "timeout-bound":
        # bump timeout to ~1.5x the highest observed iter (rounded to 5 min,
        # capped at 120, floored to 15)
        suggested = int(((max_dur * 1.5) // 5 + 1) * 5)
        suggested = max(15, min(120, suggested))
        return cur_max, suggested, (
            f"longest iter was {max_dur:.1f}m vs configured {cur_to}m timeout; "
            f"bumped timeout to {suggested}m (~1.5x the longest observed)"
        )

    if label == "max-iter-bound":
        # bump iter cap by ~50% (capped at 60)
        suggested = min(60, int(cur_max * 1.5))
        return suggested, cur_to, (
            f"hit MaxIterations={cur_max} without shipping; bumped to {suggested}. "
            "Also consider whether sub-plans need finer steps."
        )

    if label == "api-blocked":
        return cur_max, cur_to, (
            "API was the blocker — params unchanged. Check ANTHROPIC_BASE_URL, "
            "credentials, or switch model before relaunching."
        )

    if label == "api-flaky":
        return cur_max, cur_to, (
            "endpoint unstable but loop made progress. Params unchanged. "
            "Watch this trend across 2-3 more runs before switching endpoints."
        )

    if label == "stuck-no-progress":
        return cur_max, cur_to, (
            "agent stalled — likely sub-plan ambiguity or a real bug. Read the "
            "tail before relaunching with same params; consider splitting the "
            "current step or filing a bug as out-of-scope."
        )

    if label == "local-checks-stuck":
        return cur_max, cur_to, (
            "agent kept committing but local_checks kept failing — sub-plan AC "
            "may be wrong/unachievable, or a real bug. Params unchanged. **Do "
            "not auto-relaunch**: read the failing check output and decide "
            "whether to fix the AC, split the step, or file out-of-scope."
        )

    if label == "budget-exhausted":
        return cur_max, cur_to, (
            "hit --max-budget-usd cap. Either raise the cap or accept stopping "
            "here. Params unchanged."
        )

    if label == "interrupted":
        return cur_max, cur_to, (
            "loop didn't reach a natural stop (window killed externally). "
            "Params unchanged; resume when ready."
        )

    return cur_max, cur_to, "no specific recommendation"


# ---------- tail extraction --------------------------------------------------


def tail_log(log_path_str: str | None, max_lines: int = TAIL_LINES) -> list[str]:
    if not log_path_str:
        return []
    p = Path(log_path_str)
    if not p.exists():
        return [f"<log file missing: {log_path_str}>"]
    try:
        # could be large; use efficient tail
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return [f"<error reading log: {e}>"]
    return [ln.rstrip("\n") for ln in lines[-max_lines:]]


# ---------- report rendering -------------------------------------------------


def render_report(
    project_path: Path,
    project_name: str,
    run_id: str,
    iters: list[dict],
    last_launch: dict | None,
    label: str,
    facts: dict[str, Any],
    rec_max: int,
    rec_to: int,
    rationale: str,
    tail: list[str],
) -> str:
    iter_count = len(iters)
    max_iter_cfg = (last_launch or {}).get("max_iterations") or 0
    to_cfg = (last_launch or {}).get("iteration_timeout_min") or 0
    total_elapsed = sum((r.get("duration_sec") or 0) for r in iters)
    new_commits_total = sum((r.get("new_commits_total") or 0) for r in iters)
    err_count = sum(1 for r in iters if r.get("exit_code") not in (0, None))
    durations_min = [(r.get("duration_sec") or 0) / 60.0 for r in iters]
    avg_dur = sum(durations_min) / len(durations_min) if durations_min else 0
    max_dur = max(durations_min) if durations_min else 0

    started_at = (last_launch or {}).get("started_at", "?")
    model = next((r.get("model") for r in iters if r.get("model")), "?")
    base_url = next((r.get("base_url") for r in iters if r.get("base_url")), "?")

    last = iters[-1] if iters else {}
    last_iter_log = last.get("log") if last else None

    fm = {
        "project": project_name,
        "project_path": str(project_path),
        "run_id": run_id,
        "classification": label,
        "recommended_max_iterations": rec_max,
        "recommended_iteration_timeout_min": rec_to,
        "iterations": iter_count,
        "iterations_max_configured": max_iter_cfg,
        "iteration_timeout_min_configured": to_cfg,
        "new_commits_total": new_commits_total,
        "total_elapsed_sec": total_elapsed,
        "transient_error_count": err_count,
        "started_at": started_at,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    fm_yaml = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) else v}\n" for k, v in fm.items()) + "---\n"

    body_lines: list[str] = []
    body_lines.append(f"# ilk postmortem — {project_name} — run {run_id}\n")
    body_lines.append(f"**Classification:** `{label}`\n")
    body_lines.append("## Quick stats\n")
    body_lines.append("| Metric | Value |")
    body_lines.append("|---|---|")
    body_lines.append(f"| Started | {started_at} |")
    body_lines.append(f"| Iterations | {iter_count} / {max_iter_cfg if max_iter_cfg else 'unknown'} |")
    body_lines.append(f"| Total elapsed | {total_elapsed/60:.1f} min |")
    body_lines.append(f"| Avg iter | {avg_dur:.1f} min |")
    to_disp = f"{to_cfg} min" if to_cfg else "unknown (last-launch.json missing)"
    body_lines.append(f"| Max iter | {max_dur:.1f} min (configured timeout: {to_disp}) |")
    body_lines.append(f"| New commits | {new_commits_total} |")
    body_lines.append(f"| Transient API errors | {err_count} |")
    body_lines.append(f"| Model | {model} |")
    body_lines.append(f"| Endpoint | {base_url} |")
    body_lines.append("")

    body_lines.append("## What happened\n")
    body_lines.append(_label_narrative(label, facts))
    body_lines.append("")

    body_lines.append("## Recommendation for next launch\n")
    body_lines.append(f"- `MaxIterations`: **{rec_max}** (was {max_iter_cfg if max_iter_cfg else 'unknown'})")
    body_lines.append(f"- `IterationTimeoutMin`: **{rec_to}** (was {to_cfg if to_cfg else 'unknown'})")
    body_lines.append(f"- Rationale: {rationale}")
    body_lines.append("")

    body_lines.append("## Suggested next action\n")
    body_lines.append("Pick one (the agent will surface this as a 3-way question):")
    body_lines.append(f"- (a) Resume now with `-MaxIterations {rec_max} -IterationTimeoutMin {rec_to}`")
    body_lines.append("- (b) Investigate the tail below first; resume later")
    body_lines.append("- (c) Manual handling — leave the loop idle, decide outside this skill")
    body_lines.append("")

    if last_iter_log:
        body_lines.append(f"## Tail of last iter (iter {last.get('iteration')}, log: `{last_iter_log}`)\n")
    else:
        body_lines.append("## Tail of last iter\n")
    body_lines.append("```")
    body_lines.extend(tail or ["<no tail available>"])
    body_lines.append("```")
    body_lines.append("")

    body_lines.append("## Per-iteration breakdown\n")
    body_lines.append("| # | Duration (min) | Commits | Exit | Stop reason |")
    body_lines.append("|---|---|---|---|---|")
    for r in iters:
        dur = (r.get("duration_sec") or 0) / 60.0
        body_lines.append(
            f"| {r.get('iteration', '?')} "
            f"| {dur:.1f} "
            f"| {r.get('new_commits_total', 0)} "
            f"| {r.get('exit_code', '?')} "
            f"| {r.get('stop_reason') or '-'} |"
        )

    return fm_yaml + "\n" + "\n".join(body_lines) + "\n"


def _label_narrative(label: str, facts: dict[str, Any]) -> str:
    if label == "clean-success":
        return "All sub-plans shipped. Loop ended naturally on `all-shipped`."
    if label == "timeout-bound":
        cfg_to = facts.get("configured_timeout_min")
        cfg_disp = f"{cfg_to} min" if cfg_to else "unknown (no last-launch.json — likely a manual launch before ilk-launcher existed; the loop infers timeout from script defaults)"
        actual_min = (facts.get("duration_sec") or 0) / 60
        return (
            f"Iteration {facts.get('iter_at_stop')} hit the configured "
            f"`IterationTimeoutMin` ({cfg_disp}). Observed duration: "
            f"{actual_min:.1f} min. The agent was killed mid-iter; partial "
            "work in that iter is lost (anything committed earlier in the "
            "iter survives)."
        )
    if label == "max-iter-bound":
        return (
            f"Loop ran out of iterations ({facts.get('iters')} / "
            f"{facts.get('max_iter_configured')}) without shipping all sub-plans. "
            "Either bump MaxIterations or break sub-plans into smaller, faster steps."
        )
    if label == "api-flaky":
        return (
            f"Endpoint instability: {facts.get('error_count')} transient errors "
            f"(rate {facts.get('error_rate'):.0%}). Loop survived because at least "
            "some iters made commits despite errors."
        )
    if label == "api-blocked":
        return (
            f"API errors stalled the loop. Iter {facts.get('iter_at_stop')} was the "
            "third zero-progress iter, with API errors in 2+ of the last 3 iters. "
            "Triage endpoint, credentials, or model before relaunching."
        )
    if label == "stuck-no-progress":
        return (
            f"Agent stalled: 3 consecutive zero-progress iters at "
            f"iter {facts.get('iter_at_stop')}, but exit codes were mostly clean. "
            "Likely sub-plan ambiguity, prompt confusion, or a real bug it can't "
            "solve. **Read the tail below.**"
        )
    if label == "local-checks-stuck":
        return (
            f"Sub-plan `local_checks` failed in {facts.get('fail_iters_in_window')} "
            f"of the last {facts.get('window_size')} iterations (passed in "
            f"{facts.get('pass_iters_in_window')}). The agent kept making commits "
            f"through iter {facts.get('iter_at_stop')}, but the machine-checkable "
            "acceptance criteria did not converge. Either the AC is wrong/over-"
            "specified, the step is too coarse for the agent to land in one go, "
            "or there's a real bug it can't fix. **Read the failing check output "
            "in the iteration log before deciding what to do.**"
        )
    if label == "budget-exhausted":
        return (
            f"Hit `--max-budget-usd` ({facts.get('max_budget_usd')}). Loop stopped "
            "to protect spend."
        )
    if label == "interrupted":
        return facts.get("note") or "Loop did not reach a natural stop."
    return "(no narrative for this label)"


# ---------- main -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a postmortem for a ilk-loop run.")
    parser.add_argument("-ProjectPath", "--project-path", dest="project_path", default=None)
    parser.add_argument("-ProjectName", "--project-name", dest="project_name", default=None)
    parser.add_argument("-RunId", "--run-id", dest="run_id", default=None,
                        help="Specific run_id (YYYYMMDD-HHMMSS). Default: most recent.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress prints; only output report path.")
    args = parser.parse_args()

    if args.project_path:
        project_path = Path(args.project_path).resolve()
    elif args.project_name:
        project_path = resolve_by_name(args.project_name).resolve()
    else:
        project_path = resolve_by_cwd().resolve()

    if not project_path.exists():
        raise SystemExit(f"Project path does not exist: {project_path}")

    project_name = args.project_name or project_name_for(project_path)
    last_launch = read_last_launch(project_path)

    all_records = read_jsonl_iters(project_path)
    if not all_records:
        print(f"[ilk-feedback] No JSONL records for project {project_path}.")
        print(f"[ilk-feedback] Has ilk ever run for this project? Looked at {JSONL_LOG}")
        return 1

    by_run = runs_index(all_records)
    if args.run_id:
        if args.run_id not in by_run:
            available = ", ".join(sorted(by_run.keys())[-5:])
            raise SystemExit(f"run_id '{args.run_id}' not found. Recent: {available}")
        target_run = args.run_id
    else:
        target_run = newest_run_id(by_run)

    iters = by_run[target_run]

    label, facts = classify(iters, last_launch, project_path)
    rec_max, rec_to, rationale = recommend_params(label, iters, last_launch)
    tail = tail_log((iters[-1].get("log") if iters else None))

    report = render_report(
        project_path=project_path,
        project_name=project_name,
        run_id=target_run,
        iters=iters,
        last_launch=last_launch,
        label=label,
        facts=facts,
        rec_max=rec_max,
        rec_to=rec_to,
        rationale=rationale,
        tail=tail,
    )

    if external_launcher_dir is None or project_key is None:
        print("ilk_paths not available; cannot resolve external launcher dir.", file=sys.stderr)
        return 1
    out_dir = external_launcher_dir(project_key(project_path)) / "postmortems"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target_run}.md"
    out_path.write_text(report, encoding="utf-8")

    # stdout summary for the calling agent
    if not args.quiet:
        print(f"[ilk-feedback] project: {project_name}  run: {target_run}")
        print(f"[ilk-feedback] classification: {label}")
        print(f"[ilk-feedback] iters: {len(iters)} / {(last_launch or {}).get('max_iterations', '?')}")
        print(f"[ilk-feedback] new_commits_total: {sum((r.get('new_commits_total') or 0) for r in iters)}")
        print(f"[ilk-feedback] recommendation: MaxIterations={rec_max} IterationTimeoutMin={rec_to}")
        print(f"[ilk-feedback] rationale: {rationale}")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
