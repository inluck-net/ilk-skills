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

# ── Log source / destination map ─────────────────────────────────────────────
#
# Writers (which script produces which log):
#
#   run_ilk_loop_claude.sh / .ps1   (runner scripts)
#     JSONL summary    → <skill-root>/ilk-loop/logs/.ilk-loop.log
#     Per-iter log     → <skill-root>/ilk-loop/logs/ilk-claude-<run-id>/iter-NN.log
#     Per-iter JSONL   → <skill-root>/ilk-loop/logs/ilk-claude-<run-id>/iter-NN.log.jsonl
#     Sentinel         → ~/.ilk-data/projects/<key>/runtime/last-exit.json
#
#   launch.sh / launch.ps1          (launcher scripts)
#     PID file         → ~/.ilk-data/projects/<key>/runtime/launcher/running.pid
#     Launch metadata  → ~/.ilk-data/projects/<key>/runtime/launcher/last-launch.json
#     Launcher log     → <skill-root>/ilk-loop/logs/launcher/<project-key>-<run-id>.log
#
#   collect.py (this file — reader + postmortem writer)
#     Postmortem       → ~/.ilk-data/projects/<key>/runtime/launcher/postmortems/<run-id>.md
#
# Readers:
#
#   collect.py reads:
#     1. JSONL summary   (for iteration records + classification)
#     2. Per-iter logs   (for tail extraction and keyword classification)
#     3. last-launch.json (for max_iterations, iteration_timeout_min, started_at)
#     4. last-exit.json  (indirectly via loop_status_exit)
#
# Canonical log roots (ordered by preference):
#   1. last-launch.json → log_file, log_dir fields (if present)
#   2. ilk_paths.external_logs_dir(project_key) → ~/.ilk-data/projects/<key>/logs/
#   3. Legacy <skill-root>/ilk-loop/logs/ (pre-externalisation location)
#
# ─────────────────────────────────────────────────────────────────────────────

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
        external_logs_dir,
        find_project_root as _find_project_root,
        project_key,
    )  # type: ignore
except ImportError:
    _find_project_root = None  # type: ignore
    external_launcher_dir = None  # type: ignore
    external_logs_dir = None  # type: ignore
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


def _jsonl_log_candidates(project_path: Path, last_launch: dict | None = None) -> list[Path]:
    """Return ordered candidate paths for the JSONL summary log.

    Resolution order:
      1. last-launch.json → log_file / log_dir fields
      2. ilk_paths.external_logs_dir(project_key)
      3. Legacy <skill-root>/ilk-loop/logs/.ilk-loop.log
    """
    candidates: list[Path] = []

    # 1. last-launch.json hints
    if last_launch:
        log_file = last_launch.get("log_file")
        if log_file:
            p = Path(log_file)
            if p not in candidates:
                candidates.append(p)
        log_dir = last_launch.get("log_dir")
        if log_dir:
            p = Path(log_dir) / ".ilk-loop.log"
            if p not in candidates:
                candidates.append(p)

    # 2. External logs dir
    if external_logs_dir is not None and project_key is not None:
        p = external_logs_dir(project_key(project_path)) / ".ilk-loop.log"
        if p not in candidates:
            candidates.append(p)

    # 3. Legacy skill-root logs dir
    if LOOP_LOG_DIR not in [c.parent for c in candidates]:
        candidates.append(LOOP_LOG_DIR / ".ilk-loop.log")

    return candidates


def _iter_log_root_candidates(project_path: Path, last_launch: dict | None = None) -> list[Path]:
    """Return ordered candidate directories that may contain iter-NN.log files.

    Each candidate is a directory that may contain
    ``ilk-claude-<run-id>/iter-NN.log`` subdirectories.

    Resolution order:
      1. last-launch.json → log_dir field
      2. ilk_paths.external_logs_dir(project_key)
      3. Legacy <skill-root>/ilk-loop/logs/
    """
    candidates: list[Path] = []

    # 1. last-launch.json hint
    if last_launch:
        log_dir = last_launch.get("log_dir")
        if log_dir:
            p = Path(log_dir)
            if p not in candidates:
                candidates.append(p)

    # 2. External logs dir
    if external_logs_dir is not None and project_key is not None:
        p = external_logs_dir(project_key(project_path))
        if p not in candidates:
            candidates.append(p)

    # 3. Legacy skill-root logs dir
    if LOOP_LOG_DIR not in candidates:
        candidates.append(LOOP_LOG_DIR)

    return candidates


def _normalize_path_for_compare(p: str | os.PathLike) -> str:
    """Normalize a path for cross-platform equality comparison.

    JSONL `project` records are written verbatim by whichever loop
    runner produced them (PowerShell uses backslashes; bash uses
    forward slashes). We want a query on either platform to match
    records produced on either platform when they refer to the same
    logical path.

    Strategy: lowercase + collapse separators to forward slashes.
    That's sufficient because path equality here is checked against
    records this runner *itself* wrote — we never need to resolve
    symlinks or canonicalise drive letters.
    """
    return str(p).replace("\\", "/").lower()


def read_jsonl_iters(project_path: Path, last_launch: dict | None = None) -> list[dict]:
    """Return ALL iteration records for this project across all runs.

    Scans all candidate JSONL files (external, last-launch.json hint,
    legacy) and de-duplicates by (run_id, iteration).
    """
    project_path_norm = _normalize_path_for_compare(project_path)
    seen: set[tuple[str, int]] = set()
    records: list[dict] = []

    for candidate in _jsonl_log_candidates(project_path, last_launch):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec_proj = _normalize_path_for_compare(rec.get("project", ""))
                    if rec_proj != project_path_norm:
                        continue
                    rid = rec.get("run_id", "")
                    it = rec.get("iteration", 0)
                    key = (rid, it)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(rec)
        except OSError:
            continue

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
                resolved = resolve_iter_log(
                    last.get("run_id"), last.get("iteration", 0), project_path, last_launch
                )
                if resolved:
                    last_log_path = str(resolved)
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


def resolve_iter_log(
    run_id: str, iteration: int, project_path: Path | None = None, last_launch: dict | None = None
) -> Path | None:
    """Return the path to a specific iteration log if it exists on disk.

    Searches all candidate log root directories (external, last-launch.json
    hint, legacy) for ``ilk-claude-<run_id>/iter-NN.log``.
    """
    if project_path is not None:
        roots = _iter_log_root_candidates(project_path, last_launch)
    else:
        roots = [LOOP_LOG_DIR]
    rel = f"ilk-claude-{run_id}" / f"iter-{iteration:02d}.log"
    for root in roots:
        p = root / rel
        if p.exists():
            return p
    return None


def parse_postmortem_frontmatter(path: Path) -> dict[str, Any]:
    """Read YAML-like frontmatter from a postmortem .md file."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    block = text[4:end]
    result: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Strip surrounding quotes (render_report uses json.dumps for strings)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        # Cast integer-looking values
        elif re.fullmatch(r"-?\d+", value):
            value = int(value)
        result[key] = value
    return result


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
    last_log_path: str | None = None,
) -> str:
    iter_count = len(iters)
    if last_launch is not None:
        max_iter_cfg = last_launch.get("max_iterations") or 0
        to_cfg = last_launch.get("iteration_timeout_min") or 0
    else:
        max_iter_cfg = next(
            (r.get("max_iterations") for r in iters if r.get("max_iterations") is not None), "unknown"
        )
        to_cfg = next(
            (r.get("iteration_timeout_min") for r in iters if r.get("iteration_timeout_min") is not None), "unknown"
        )
    total_elapsed = sum((r.get("duration_sec") or 0) for r in iters)
    new_commits_total = sum((r.get("new_commits_total") or 0) for r in iters)
    err_count = sum(1 for r in iters if r.get("exit_code") not in (0, None))
    durations_min = [(r.get("duration_sec") or 0) / 60.0 for r in iters]
    avg_dur = sum(durations_min) / len(durations_min) if durations_min else 0
    max_dur = max(durations_min) if durations_min else 0

    started_at = (last_launch or {}).get("started_at") if last_launch is not None else None
    if not started_at and iters:
        started_at = iters[0].get("timestamp")
    if not started_at:
        started_at = "unknown"
    model = next((r.get("model") for r in iters if r.get("model")), "?")
    base_url = next((r.get("base_url") for r in iters if r.get("base_url")), "?")

    last = iters[-1] if iters else {}
    last_iter_log = last_log_path if last_log_path else last.get("log") if last else None

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
    to_disp = f"{to_cfg} min" if to_cfg and to_cfg != "unknown" else "unknown (last-launch.json missing)"
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


def split_manual_tail(body: str) -> tuple[str, str]:
    """Split a postmortem body into (auto_part, manual_tail) at the Manual analysis boundary."""
    marker = "\n---\n\n## Manual "
    idx = body.find(marker)
    if idx == -1:
        return (body, "")
    return (body[:idx], body[idx:])


# ---------- index mode -------------------------------------------------------


def run_index(args) -> int:
    projects_dir = HOME / ".ilk-data" / "projects"
    if not projects_dir.exists():
        print("no postmortems found", file=sys.stderr)
        return 0
    rows: list[dict[str, Any]] = []
    for postmortem_path in projects_dir.rglob("runtime/launcher/postmortems/*.md"):
        fm = parse_postmortem_frontmatter(postmortem_path)
        if not fm:
            continue
        parts = postmortem_path.parts
        try:
            proj_idx = parts.index("projects")
            project_key_name = parts[proj_idx + 1]
        except (ValueError, IndexError):
            project_key_name = "unknown"
        rows.append({
            "project": project_key_name,
            "run_id": fm.get("run_id", "unknown"),
            "classification": fm.get("classification", "unknown"),
            "iterations": fm.get("iterations", 0),
            "new_commits_total": fm.get("new_commits_total", 0),
            "generated_at": fm.get("generated_at", ""),
            "path": str(postmortem_path),
        })
    # filters
    if args.project:
        proj_filter = args.project.lower()
        rows = [r for r in rows if proj_filter in r["project"].lower()]
    if args.label:
        rows = [r for r in rows if r["classification"] == args.label]
    if args.since:
        m = re.fullmatch(r"(\d+)([hd])", args.since)
        num = int(m.group(1))
        unit = m.group(2)
        delta = dt.timedelta(hours=num) if unit == "h" else dt.timedelta(days=num)
        cutoff = (dt.datetime.now() - delta).isoformat()
        rows = [r for r in rows if r["generated_at"] >= cutoff]
    if not rows:
        print("no postmortems found", file=sys.stderr)
        return 0
    rows.sort(key=lambda r: r["generated_at"], reverse=True)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print("| date | project | classification | iters | commits | run_id |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        date = r["generated_at"][:16] if len(r["generated_at"]) >= 16 else r["generated_at"]
        print(
            f"| {date} | {r['project']} | {r['classification']} | "
            f"{r['iterations']} | {r['new_commits_total']} | {r['run_id']} |"
        )
    return 0


# ---------- reclassify mode --------------------------------------------------


def run_reclassify(args) -> int:
    dry_run = args.dry_run
    postmortem_paths: list[Path] = []

    if args.reclassify_all:
        projects_dir = HOME / ".ilk-data" / "projects"
        if projects_dir.exists():
            postmortem_paths = list(projects_dir.rglob("runtime/launcher/postmortems/*.md"))
    else:
        proj_str = args.reclassify
        project_path = None
        try:
            project_path = resolve_by_name(proj_str).resolve()
        except SystemExit:
            pass
        if project_path is None:
            p = Path(proj_str).resolve()
            if p.exists():
                project_path = p
        if project_path is None:
            # Try matching as a project key under ~/.ilk-data/projects/
            projects_dir = HOME / ".ilk-data" / "projects"
            key_dir = projects_dir / proj_str
            if key_dir.is_dir():
                pm_dir = key_dir / "runtime" / "launcher" / "postmortems"
                if pm_dir.is_dir():
                    postmortem_paths = list(pm_dir.glob("*.md"))
                # We don't know the project_path for JSONL lookup yet;
                # fall through to frontmatter-based resolution below.
                if not postmortem_paths:
                    print(f"no postmortems found for project key: {proj_str}", file=sys.stderr)
                    return 0
                # Derive project_path from the first postmortem's frontmatter
                fm0 = parse_postmortem_frontmatter(postmortem_paths[0])
                project_path = Path(fm0.get("project_path", "")) if fm0 else None
                if not project_path or not project_path.exists():
                    # Can't resolve project_path; still show diff lines but skip JSONL reclassification
                    for p in sorted(postmortem_paths):
                        fm = parse_postmortem_frontmatter(p)
                        if not fm:
                            continue
                        run_id = fm.get("run_id")
                        old_label = fm.get("classification", "unknown")
                        print(f"{proj_str} {run_id}: {old_label} → {old_label} (no change)")
                    return 0
            else:
                print(f"Project not found: {proj_str}", file=sys.stderr)
                return 1
        if project_path is None:
            print(f"Project not found: {proj_str}", file=sys.stderr)
            return 1
        if not postmortem_paths:
            if external_launcher_dir is not None and project_key is not None:
                pm_dir = external_launcher_dir(project_key(project_path)) / "postmortems"
                if pm_dir.is_dir():
                    postmortem_paths = list(pm_dir.glob("*.md"))
            else:
                projects_dir = HOME / ".ilk-data" / "projects"
                if projects_dir.exists():
                    for p in projects_dir.rglob("runtime/launcher/postmortems/*.md"):
                        fm = parse_postmortem_frontmatter(p)
                        if fm.get("project_path") == str(project_path):
                            postmortem_paths.append(p)

    if not postmortem_paths:
        print("no postmortems found", file=sys.stderr)
        return 0

    postmortem_paths.sort()

    changed = 0
    skipped = 0
    unchanged = 0

    for pm_path in postmortem_paths:
        fm = parse_postmortem_frontmatter(pm_path)
        if not fm:
            continue
        run_id = fm.get("run_id")
        old_label = fm.get("classification", "unknown")
        proj_path_str = fm.get("project_path")
        if not run_id or not proj_path_str:
            continue

        proj_path = Path(proj_path_str)
        parts = pm_path.parts
        try:
            proj_idx = parts.index("projects")
            display_name = parts[proj_idx + 1]
        except (ValueError, IndexError):
            display_name = proj_path.name

        all_records = read_jsonl_iters(proj_path)
        by_run = runs_index(all_records)
        iters = by_run.get(run_id)
        if not iters:
            print(f"[skip] {run_id}: no JSONL records")
            skipped += 1
            continue

        new_label, _ = classify(iters, None, proj_path)
        if new_label == old_label:
            print(f"{display_name} {run_id}: {old_label} → {new_label} (no change)")
            unchanged += 1
            continue

        print(f"{display_name} {run_id}: {old_label} → {new_label} (CHANGE)")
        changed += 1
        if not dry_run:
            try:
                existing_body = pm_path.read_text(encoding="utf-8")
            except OSError:
                continue
            _, manual_tail = split_manual_tail(existing_body)

            new_facts = classify(iters, None, proj_path)[1]
            rec_max, rec_to, rationale = recommend_params(new_label, iters, None)

            last = iters[-1] if iters else {}
            last_log = last.get("log")
            if not last_log:
                resolved = resolve_iter_log(run_id, last.get("iteration", 0), proj_path)
                if resolved:
                    last_log = str(resolved)
            tail = tail_log(last_log)

            proj_name = project_name_for(proj_path)
            report = render_report(
                project_path=proj_path,
                project_name=proj_name,
                run_id=run_id,
                iters=iters,
                last_launch=None,
                label=new_label,
                facts=new_facts,
                rec_max=rec_max,
                rec_to=rec_to,
                rationale=rationale,
                tail=tail,
                last_log_path=last_log,
            )
            new_body = report + manual_tail
            pm_path.write_text(new_body, encoding="utf-8")

    return 0


# ---------- main -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a postmortem for a ilk-loop run.")
    parser.add_argument("-ProjectPath", "--project-path", dest="project_path", default=None)
    parser.add_argument("-ProjectName", "--project-name", dest="project_name", default=None)
    parser.add_argument("-RunId", "--run-id", dest="run_id", default=None,
                        help="Specific run_id (YYYYMMDD-HHMMSS). Default: most recent.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress prints; only output report path.")
    parser.add_argument("--index", action="store_true", help="List all postmortems across projects.")
    parser.add_argument("--since", default=None, help="Filter by age: e.g. 7d, 24h")
    parser.add_argument("--label", default=None, help="Filter by classification label")
    parser.add_argument("--project", default=None, help="Filter by project name/substring")
    parser.add_argument("--json", action="store_true", help="Output as JSON list")
    parser.add_argument("--reclassify", default=None, help="Reclassify historical postmortems for a project")
    parser.add_argument("--reclassify-all", action="store_true", help="Reclassify all historical postmortems")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    if args.since and not re.fullmatch(r"\d+[hd]", args.since):
        parser.error("--since must be <int>h or <int>d (e.g. 7d, 24h)")

    if args.reclassify and args.reclassify_all:
        parser.error("--reclassify and --reclassify-all are mutually exclusive")

    if args.reclassify or args.reclassify_all:
        if args.index:
            parser.error("--index is mutually exclusive with --reclassify / --reclassify-all")
        if args.project_path or args.project_name or args.run_id:
            parser.error("--reclassify / --reclassify-all is mutually exclusive with single-run args")
        return run_reclassify(args)

    if args.index:
        return run_index(args)

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

    all_records = read_jsonl_iters(project_path, last_launch)
    if not all_records:
        candidates = _jsonl_log_candidates(project_path, last_launch)
        looked_at = ", ".join(str(c) for c in candidates)
        print(f"[ilk-feedback] No JSONL records for project {project_path}.")
        print(f"[ilk-feedback] Has ilk ever run for this project? Looked at {looked_at}")
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
    last_log = iters[-1].get("log") if iters else None
    if not last_log and iters:
        resolved = resolve_iter_log(target_run, iters[-1]["iteration"], project_path, last_launch)
        if resolved:
            last_log = str(resolved)
    tail = tail_log(last_log)

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
        last_log_path=last_log,
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
