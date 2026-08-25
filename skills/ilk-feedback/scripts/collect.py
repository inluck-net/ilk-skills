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
#     JSONL summary    → ~/.ilk-data/projects/<key>/logs/.ilk-loop.log
#     Per-iter log     → ~/.ilk-data/projects/<key>/logs/runs/<run-id>/iter-NN.log
#     Per-iter JSONL   → ~/.ilk-data/projects/<key>/logs/runs/<run-id>/iter-NN.log.jsonl
#     Sentinel         → ~/.ilk-data/projects/<key>/runtime/last-exit.json
#
#   launch.sh / launch.ps1          (launcher scripts)
#     PID file         → ~/.ilk-data/projects/<key>/runtime/launcher/running.pid
#     Launch metadata  → ~/.ilk-data/projects/<key>/runtime/launcher/last-launch.json
#     Launcher log     → ~/.ilk-data/projects/<key>/logs/launcher/<project-key>-<run-id>.log
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

# Import loop_status for master-resolution logic (extract_master_order,
# pick_active_master) so batch_unverified_tiers can scope to the run's
# active master rather than scanning every shipped sub-plan in the dir.
_LOOP_STATUS_DIR = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts"
if _LOOP_STATUS_DIR.is_dir():
    sys.path.insert(0, str(_LOOP_STATUS_DIR))
try:
    import loop_status as _loop_status  # type: ignore
except ImportError:
    _loop_status = None  # type: ignore


def _subplan_ref_re():
    """Canonical sub-plan-reference regex from the sibling ilk-loop skill.

    Resolved relative to this file so it works from a repo clone as well as
    an installed symlink (the ``~/.cursor`` constants above predate
    multi-host installs).  Falls back to a local build of the same shape if
    plan_slug is unreachable — keep the two in sync via plan_slug.py.
    """
    try:
        _sibling = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
        if _sibling.is_dir() and str(_sibling) not in sys.path:
            sys.path.insert(0, str(_sibling))
        from plan_slug import SUBPLAN_REF_RE  # type: ignore
        return SUBPLAN_REF_RE
    except Exception:
        return re.compile(
            r"(?:^|(?<=[\s(\[|]))(?:\./)?(\d{4}-\d{2}-\d{2}[a-z]?-[a-z0-9][a-z0-9-]*\.md)",
            re.MULTILINE,
        )

# Pull in ilk_paths from the sibling ilk-loop skill so meta-project
# detection is consistent across the suite. Falls back to the legacy
# walk-up in resolve_by_cwd() if the import fails (e.g. running from a
# repo clone before install.sh symlinks are in place).
_ILK_PATHS_DIR = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts"
if _ILK_PATHS_DIR.is_dir():
    sys.path.insert(0, str(_ILK_PATHS_DIR))
try:
    from ilk_paths import (
        archive_run_dir,
        external_launcher_dir,
        external_logs_dir,
        external_runtime_dir,
        find_plans_dir as _find_plans_dir,
        find_project_root as _find_project_root,
        project_key,
        skill_root as _skill_root,
    )  # type: ignore
except ImportError:
    _find_project_root = None  # type: ignore
    _find_plans_dir = None  # type: ignore
    external_launcher_dir = None  # type: ignore
    external_logs_dir = None  # type: ignore
    external_runtime_dir = None  # type: ignore
    archive_run_dir = None  # type: ignore
    project_key = None  # type: ignore
    _skill_root = None  # type: ignore

# How many lines of the last problematic iter's log to embed in the report.
TAIL_LINES = 80

# Import improvement_backlog for upstream-candidate emission (step 2 of
# selfimprove-backlog-and-feedback-candidates sub-plan).
_COLLECT_DIR = Path(__file__).resolve().parent
if str(_COLLECT_DIR) not in sys.path:
    sys.path.insert(0, str(_COLLECT_DIR))
try:
    import improvement_backlog as _improvement_backlog
except ImportError:
    _improvement_backlog = None  # type: ignore

# Import iteration_timing for ceiling-hit detection (sub-plan
# a-gate-that-produces-nothing-is-a-hang → a-wasted-gate-is-named).
# analyze_iteration reads per-iteration JSONL and returns a
# ceiling_hit_no_output list when a broad test command hits the harness
# ceiling, was backgrounded, and produced no captured output.
_TIMING_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
if _TIMING_SCRIPTS_DIR.is_dir() and str(_TIMING_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TIMING_SCRIPTS_DIR))
try:
    from iteration_timing import analyze_iteration as _analyze_iteration  # type: ignore
except ImportError:
    _analyze_iteration = None  # type: ignore

try:
    import autoclose_tracker as _autoclose_tracker
except ImportError:
    _autoclose_tracker = None  # type: ignore


# ---------- autoclose helper --------------------------------------------------


def _maybe_autoclose(project_path: Path, quiet: bool) -> None:
    """Best-effort auto-close tracker/backlog entries resolved by shipped sub-plans.

    Called after each postmortem write.  Never raises — a failure here must
    not break the postmortem pipeline (AC-4).
    """
    if _autoclose_tracker is None or _find_plans_dir is None:
        return
    try:
        plans_dir, _src = _find_plans_dir(project_path)
        if plans_dir is None:
            return
        closed = _autoclose_tracker.autoclose(plans_dir, project=project_path)
        if closed and not quiet:
            print(f"[ilk-feedback] autoclose: {closed} tracker/backlog entries closed")
    except Exception:
        pass


# ---------- project resolution (mirror launcher logic) -----------------------


def read_projects_registry() -> list[dict]:
    if not PROJECTS_JSON.exists():
        return []
    try:
        data = json.loads(PROJECTS_JSON.read_text(encoding="utf-8-sig"))
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
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def read_sentinel(project_path: Path) -> dict | None:
    """Read last-exit.json sentinel written by the runner.

    Returns the parsed dict if the sentinel exists, None otherwise.
    The sentinel proves a run *started* (even if it died before iter 1
    wrote any JSONL record).
    """
    if external_runtime_dir is None or project_key is None:
        return None
    f = external_launcher_dir(project_key(project_path)) / "last-exit.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def _jsonl_log_candidates(project_path: Path, last_launch: dict | None = None) -> list[Path]:
    """Return ordered candidate paths for the JSONL summary log.

    Resolution order:
      1. last-launch.json → jsonl_log field (canonical, set by new launchers)
      2. last-launch.json → log_file / log_dir fields (older metadata)
      3. ilk_paths.external_logs_dir(project_key)
      4. Legacy <skill-root>/ilk-loop/logs/.ilk-loop.log
    """
    candidates: list[Path] = []

    # 1. last-launch.json canonical jsonl_log field
    if last_launch:
        jsonl_log = last_launch.get("jsonl_log")
        if jsonl_log:
            p = Path(jsonl_log)
            if p not in candidates:
                candidates.append(p)

    # 2. last-launch.json older hints
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

    # 3. External logs dir
    if external_logs_dir is not None and project_key is not None:
        p = external_logs_dir(project_key(project_path)) / ".ilk-loop.log"
        if p not in candidates:
            candidates.append(p)

    # 4. Legacy skill-root logs dir
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
            with candidate.open("r", encoding="utf-8-sig") as fh:
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


def read_per_iter_jsonl(
    run_id: str,
    project_path: Path,
    last_launch: dict | None = None,
) -> list[dict]:
    """Return iteration records from per-iter JSONL for a specific run.

    Reads ``<log-root>/runs/<run-id>/iter-*.jsonl`` from all candidate log
    directories.  These files are written by the runner during execution and
    are the fallback evidence when the summary JSONL has no records for this
    run (e.g. claude-worker runs that wrote per-iter logs but no summary).

    De-duplicates by iteration number.  Returns records sorted by iteration.
    """
    seen: set[int] = set()
    records: list[dict] = []
    for root in _iter_log_root_candidates(project_path, last_launch):
        runs_dir = root / "runs" / run_id
        if not runs_dir.is_dir():
            continue
        for jsonl_file in sorted(runs_dir.glob("iter-*.jsonl")):
            try:
                with jsonl_file.open("r", encoding="utf-8-sig") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        it = rec.get("iteration", 0)
                        if it in seen:
                            continue
                        seen.add(it)
                        records.append(rec)
            except OSError:
                continue
    records.sort(key=lambda r: r.get("iteration", 0))
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


def count_rate_limit_events(
    run_id: str,
    project_path: Path,
    last_launch: dict | None = None,
) -> int:
    """Count rate-limit event records for a specific run_id in the JSONL stream.

    Rate-limit events are separate records with ``type: "rate_limit_event"``.
    This count is independently useful even without the ``throttled`` label:
    it tells the operator how much of the run's wall-clock was spent waiting.
    """
    total = 0
    project_path_norm = _normalize_path_for_compare(project_path)
    for candidate in _jsonl_log_candidates(project_path, last_launch):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "rate_limit_event":
                        continue
                    rec_proj = _normalize_path_for_compare(rec.get("project", ""))
                    if rec_proj != project_path_norm:
                        continue
                    if rec.get("session_id") == run_id or rec.get("run_id") == run_id:
                        total += 1
        except OSError:
            continue
    return total


# ---------- classification ---------------------------------------------------

# Single source of truth for the final classification label vocabulary.
# Every label that classify() / _classify_core() or the sentinel failure map
# can emit MUST be listed here. The totality test
# (test_label_action_totality.py) reads this constant and asserts
# Resolve-WatchdogAction maps every entry to a known action — so adding a
# label here without a watchdog branch will fail that gate automatically.
#
# Internal labels returned by classify_log_keywords() ("local-check", "api",
# "unknown") are NOT final classification labels and are intentionally excluded.
CLASSIFICATION_LABELS: tuple[str, ...] = (
    "interrupted",
    "local-checks-stuck",
    "local-checks-broken",
    "timeout-bound",
    "budget-exhausted",
    "clean-success",
    "dependency-unreachable",
    "model-incapability",
    "api-blocked",
    "stuck-no-progress",
    "api-flaky",
    "max-iter-bound",
    "self-hosting-drift",
    "shipped-unverified",
    "no-evidence",
    "never-ran",
    "throttled",
)

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

# Signals that the gate COMMAND could not execute (not that the code failed).
# Exit 4/5 = pytest collection/config errors; exit 127 = command not found.
BROKEN_GATE_STDERR_RE = re.compile(
    r"no such file|file or directory not found|command not found|"
    r"not recognized|No module named|SyntaxError",
    re.IGNORECASE,
)


def _is_broken_gate_result(check: dict) -> bool:
    """Return True if a failing local_checks result indicates the gate
    COMMAND could not execute (exit_code in {4,5,127} or stderr matches
    a 'couldn't execute' pattern).  The product code is NOT implicated;
    a blind resume re-fails identically."""
    ec = check.get("exit_code")
    if ec in (4, 5, 127):
        return True
    stderr = check.get("stderr_tail") or ""
    if stderr and BROKEN_GATE_STDERR_RE.search(stderr):
        return True
    return False


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


# Signals that a run stalled because a required runtime dependency was
# UNREACHABLE — the loop worker is missing an MCP, the dev server is down, or
# an env_prereq probe failed. This is distinct from a genuine no-progress
# stall: a restart will not help, the fix is a config/reachability change
# (e.g. `ilk-worker-mcp add figma`). See detect_unreachable_dependency.
DEPENDENCY_RE = re.compile(
    r"MCP not connected|no MCP servers|"
    r"claude\s+mcp\s+list[^\n]*\bgrep\b|"
    r"blocked:\s*dependency unreachable|dependency unreachable|"
    r"env[_ ]?prereq[^\n]*(?:unreachable|not connected|failed)",
    re.IGNORECASE,
)

# Extract a named MCP/dependency from the unreachable signal, if present.
_DEP_NAME_RE = re.compile(
    r"grep\s+-q\s+([A-Za-z0-9_.-]+)|"
    r"([A-Za-z0-9_.-]+)\s+MCP not connected",
    re.IGNORECASE,
)

# --- Never-ran detection ---
#
# A run that never invoked the model (zero turns, zero tokens) is an
# environment/startup fault, not a stalled agent.  The runner still writes
# stop_reason=no-progress because it sees zero new commits, but the cause
# is distinguishable: num_turns==0, both token counts 0, and the result
# string is a startup-failure error (e.g. "Unknown command: /ilk").
#
# See detect_never_ran and the 2026-08-10-never-ran-classification sub-plan.

STARTUP_FAILURE_RE = re.compile(
    r"Unknown command|"
    r"command not found|"
    r"not recognized as an internal or external command|"
    r"No such file or directory",
    re.IGNORECASE,
)


def _is_never_ran_iter(rec: dict) -> bool:
    """Return True if this iteration record shows a zero-turn, zero-token
    startup failure — the model was never invoked."""
    num_turns = rec.get("num_turns")
    input_tokens = rec.get("input_tokens") or rec.get("usage", {}).get("input_tokens")
    output_tokens = rec.get("output_tokens") or rec.get("usage", {}).get("output_tokens")
    result = rec.get("result") or ""

    # Must have zero turns AND zero tokens to qualify — a run that took
    # even one turn is a genuine stall, not a startup failure.
    if num_turns != 0:
        return False
    if (input_tokens or 0) != 0 or (output_tokens or 0) != 0:
        return False
    # Match a startup-failure shape in the result string.
    return bool(STARTUP_FAILURE_RE.search(result))


def detect_never_ran(lines: list[str]) -> bool:
    """Return True when the log lines show a startup failure — the model
    was never invoked.  Follows the detect_unreachable_dependency pattern:
    scan lines for a startup-failure signal."""
    if not lines:
        return False
    return any(STARTUP_FAILURE_RE.search(line) for line in lines)


# --- Vision / model-incapability detection ---
#
# When MCP tool calls (e.g. chrome-devtools) succeed but the model cannot
# process the results (e.g. a text-only model receiving an image), the stall
# is a model capability gap, not a dependency failure.  A restart won't help;
# the fix is a model that can handle the output modality.

# Detect MCP tool call invocations in the log (e.g. "mcp__chrome-devtools__click").
_MCP_CALL_RE = re.compile(
    r"mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_-]+",
    re.IGNORECASE,
)

# Detect tool call result indicators suggesting the tool executed.
# Matches "Tool ran without output or errors", "[{...tool_result...}]", etc.
_MCP_RESULT_RE = re.compile(
    r"Tool ran without output or errors|tool_result|<tool_result",
    re.IGNORECASE,
)

# Detect a blank image Read result — a Read of an image file that returned
# an empty source (text-only model cannot see images).
_BLANK_IMAGE_RE = re.compile(
    r"Read\s*\([^)]*\.(?:png|jpg|jpeg|gif|webp|bmp)\b[^)]*\).*source\s*=\s*[\])}\"]",
    re.IGNORECASE,
)


def _has_mcp_success_evidence(lines: list[str]) -> bool:
    """Return True if the log shows MCP tool calls that executed successfully.

    Checks for MCP call invocations (mcp__<server>__<tool>) AND result
    indicators. Both must be present — seeing only calls without results
    means the tool may have been invoked but failed.
    """
    if not lines:
        return False
    has_calls = False
    has_results = False
    for line in lines:
        if not has_calls and _MCP_CALL_RE.search(line):
            has_calls = True
        if not has_results and _MCP_RESULT_RE.search(line):
            has_results = True
        if has_calls and has_results:
            return True
    return False


def _has_blank_image_source(lines: list[str]) -> bool:
    """Return True if the log shows a Read of an image with empty source."""
    if not lines:
        return False
    return any(_BLANK_IMAGE_RE.search(line) for line in lines)


def _detect_model_incapability(
    dep_log_path: str | None,
    jsonl_path: str | None,
    iter_logs: list[str],
) -> bool:
    """Return True when the stall is a model capability gap, not dependency failure.

    A model-incapability stall has:
    - MCP tool calls that executed successfully (e.g. chrome-devtools calls)
    - No commits made (caller already checked this via stop_reason == "no-progress")
    - Either: blank image source in a Read result, OR no dependency-unreachable
      signal in the logs despite the DEPENDENCY_RE match in the outer check.
    """
    # Check main log for MCP success evidence
    if dep_log_path:
        lines = tail_log(dep_log_path)
        if _has_mcp_success_evidence(lines):
            return True

    # Fallback: check per-iter JSONL
    if jsonl_path and Path(jsonl_path).exists():
        lines = tail_log(jsonl_path, max_lines=5000)
        if _has_mcp_success_evidence(lines):
            return True

    # Check iter logs from JSONL record for blank image + MCP calls
    if iter_logs:
        if _has_blank_image_source(iter_logs):
            return True

    return False


def detect_unreachable_dependency(lines: list[str]) -> str | None:
    """Return the missing dependency name when the logs show an unreachable
    env_prereq / missing MCP; else None.

    Falls back to a generic label when the signal is present but no specific
    name can be extracted.
    """
    if not lines:
        return None
    hit = False
    name: str | None = None
    for line in lines:
        if DEPENDENCY_RE.search(line):
            hit = True
            m = _DEP_NAME_RE.search(line)
            if m and not name:
                name = m.group(1) or m.group(2)
            if name:
                break
    if not hit:
        return None
    return name or "a required dependency/MCP"


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
            encoding="utf-8", errors="replace",
        )
        return proc.returncode
    except (subprocess.TimeoutExpired, OSError):
        return -1


def collect_self_hosting_facts(
    project_path: Path,
    last_launch: dict | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Collect facts indicating self-hosting / runtime-path drift.

    Self-hosting means the project being modified is the same repo that
    supplies the installed ``ilk-*`` skills.  Path drift means the log
    or runner paths changed during the run (old path gone, preserved
    archive exists).

    Returns a dict of facts merged into the classification facts.
    """
    facts: dict[str, Any] = {}

    # 1. Is the project the skill source?
    skill_root_path = None
    is_self_hosting = False
    if _skill_root is not None:
        try:
            skill_root_path = _skill_root()
        except (FileNotFoundError, OSError):
            pass
    if skill_root_path is not None:
        proj_norm = _normalize_path_for_compare(project_path)
        skill_norm = _normalize_path_for_compare(skill_root_path)
        # Direct match: project IS the skills dir (legacy layout)
        # or project is a parent of the skills dir (repo-clone layout
        # where skills are installed under ~/.cursor/skills from this repo)
        is_self_hosting = (
            proj_norm == skill_norm
            or skill_norm.startswith(proj_norm + "/")
            or proj_norm.startswith(skill_norm + "/")
        )
    facts["skill_root_path"] = str(skill_root_path) if skill_root_path else None
    facts["is_self_hosting"] = is_self_hosting

    # 2. Does the launch log path still exist?
    launch_log_path = None
    if last_launch:
        launch_log_path = last_launch.get("log_file") or last_launch.get("jsonl_log")
    facts["launch_log_path"] = launch_log_path
    facts["launch_log_exists"] = (
        Path(launch_log_path).exists() if launch_log_path else None
    )

    # 3. Does a preserved archive exist for this run?
    archive_exists = False
    archive_path = None
    if run_id and archive_run_dir is not None and project_key is not None:
        try:
            root, _kind = _find_project_root(project_path) if _find_project_root else (None, "single")
            if root is not None:
                key = project_key(root)
                archive_path = str(archive_run_dir(key, run_id))
                archive_exists = Path(archive_path).is_dir()
        except (OSError, ValueError):
            pass
    facts["preserved_archive_path"] = archive_path
    facts["preserved_archive_exists"] = archive_exists

    # 4. Log path drift: original gone but preserved replacement exists
    facts["log_path_drifted"] = (
        facts["launch_log_exists"] is False and archive_exists
    )

    return facts


# ---------- verification-tier helpers ----------------------------------------

_NON_LOOP_TIERS = {"compile-only", "device-manual"}


def _parse_subplan_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML-like frontmatter from a sub-plan .md file.

    Returns a flat dict of string keys to string values (enough for
    ``status``, ``verification_tier``, and ``plan`` fields).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        line = line.rstrip("\n")
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def batch_unverified_tiers(project_path: Path) -> list[dict[str, str]]:
    """Return shipped sub-plans whose ``verification_tier`` is not ``loop-verified``.

    Scoped to the run's active (or most-recently-shipped) master: only
    sub-plans referenced in that master's registry are considered.  This
    prevents a postmortem for master A from flagging device-manual
    sub-plans that belong to an older, unrelated master B.

    Each entry is ``{"plan": <slug>, "tier": <tier>, "file": <filename>}``.
    Absent ``verification_tier`` is treated as ``loop-verified`` (back-compat).
    Returns an empty list when all shipped sub-plans in the master are
    loop-verified, when no plans directory is found, or when master
    resolution fails (graceful degradation — don't break postmortems if
    loop_status is unavailable).
    """
    if _find_plans_dir is None:
        return []
    plans_dir, _src = _find_plans_dir(project_path)
    if plans_dir is None:
        return []

    # Resolve THE RUN'S master and restrict to its registry. A postmortem
    # classifies a just-finished run, whose master is the most-recently-created
    # one — NOT pick_active_master (that answers "what to run NEXT", and once a
    # batch ships its all-shipped filter excludes every master and falls back to
    # the alphabetically-last master, which mis-attributed a prior batch's
    # device-manual sub-plan, 2026-06-09). Pick newest by `created:` frontmatter,
    # falling back to mtime.
    # Self-contained (no loop_status dependency — it isn't importable in every
    # environment, e.g. CI). Pick newest master by `created:`, fallback mtime;
    # its registry = the sub-plan filenames it references.
    registry_files: set[str] | None = None
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if masters:
        def _created_key(m: Path) -> tuple:
            created = _parse_subplan_frontmatter(m).get("created", "")
            try:
                mtime = m.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (created, mtime)
        chosen_master = max(masters, key=_created_key)
        try:
            master_text = chosen_master.read_text(encoding="utf-8")
        except OSError:
            master_text = ""
        refs = _subplan_ref_re().findall(master_text)
        refs_set = {r for r in refs if not r.startswith("MASTER")}
        # Only scope when the master actually lists sub-plans. A master with no
        # parseable registry (malformed/minimal) → don't scope (scan all) rather
        # than exclude everything.
        registry_files = refs_set if refs_set else None

    unverified: list[dict[str, str]] = []
    for p in sorted(plans_dir.glob("*.md")):
        if p.name.startswith("MASTER-"):
            continue
        # Scope: skip sub-plans not in the run's master registry.
        if registry_files is not None and p.name not in registry_files:
            continue
        fm = _parse_subplan_frontmatter(p)
        if fm.get("status") != "shipped":
            continue
        tier = fm.get("verification_tier") or "loop-verified"
        if tier in _NON_LOOP_TIERS:
            unverified.append({
                "plan": fm.get("plan", p.stem),
                "tier": tier,
                "file": p.name,
            })
    return unverified


def _classify_core(
    iters: list[dict],
    last_launch: dict | None,
    project_path: Path,
) -> tuple[str, dict[str, Any]]:
    """Core classification logic (no self-hosting facts)."""
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
    # `local_checks` may be a per-iteration dict (newer runs: a single summary
    # record with `outcome`) or a list of check dicts (older shape). Normalize
    # to a list of dicts and drop any stray non-dict entries — iterating a dict
    # yields its string keys, and `str.get` does not exist (crash, 2026-06-08).
    def _items(rec):
        lc = rec.get("local_checks")
        if isinstance(lc, dict):
            return [lc]
        if isinstance(lc, list):
            return [c for c in lc if isinstance(c, dict)]
        return []

    # An `error` outcome (the check command itself failed to run — e.g. `grep`
    # missing in the worker's cmd.exe shell) is NOT a pass.
    last_checks = _items(last)
    last_failed = any(c.get("outcome") in ("fail", "error") for c in last_checks)
    if last_failed:
        recent = iters[-5:] if len(iters) >= 5 else iters
        fail_iters = sum(
            1 for r in recent
            if any(c.get("outcome") in ("fail", "error") for c in _items(r))
        )
        pass_iters = sum(
            1 for r in recent
            if _items(r)
            and all(c.get("outcome") == "pass" for c in _items(r))
        )
        if fail_iters >= 3 and fail_iters > pass_iters:
            # Distinguish broken gate (command couldn't execute) from
            # stuck gate (command ran and code failed it).
            broken = any(
                _is_broken_gate_result(c)
                for r in recent
                for c in _items(r)
                if c.get("outcome") in ("fail", "error")
            )
            label = "local-checks-broken" if broken else "local-checks-stuck"
            return label, {
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
        # Never-ran case FIRST: the run never invoked the model (zero turns,
        # zero tokens, startup-failure result).  This is an environment fault,
        # not a stalled agent — a restart won't help until the cause is fixed.
        # Detect it before dependency-unreachable and stuck-no-progress so the
        # project is not blacklisted for a missing directory / typo.
        if _is_never_ran_iter(last):
            worker_home = last.get("worker_home") or (last_launch.get("worker_home") if last_launch else None)
            result_str = last.get("result") or ""
            return "never-ran", {
                "iter_at_stop": last.get("iteration"),
                "worker_home": worker_home,
                "result": result_str,
            }
        # Reachability case SECOND: the stall was a missing MCP / unreachable
        # env_prereq, not a genuine no-progress. A restart won't help; the fix
        # is config/reachability (e.g. `ilk-worker-mcp add <name>`). Detect it
        # before the generic stuck path — the figma stalls had clean exit codes
        # and would otherwise fall through to stuck-no-progress.
        dep_log_path = last.get("log")
        if not dep_log_path and last.get("run_id"):
            resolved = resolve_iter_log(
                last.get("run_id"), last.get("iteration", 0), project_path, last_launch
            )
            if resolved:
                dep_log_path = str(resolved)
        if dep_log_path:
            missing = detect_unreachable_dependency(tail_log(dep_log_path))
            jsonl_dep_path: str | None = None
            if missing is None:
                jsonl_dep_path = str(Path(dep_log_path).with_suffix(".log.jsonl"))
                if Path(jsonl_dep_path).exists():
                    missing = detect_unreachable_dependency(
                        tail_log(jsonl_dep_path, max_lines=5000)
                    )
                else:
                    jsonl_dep_path = None
            if missing:
                # Vision/model-incapability override: when the named MCP's
                # tool calls visibly succeeded in the iteration logs, the
                # dependency IS reachable — the stall is a model capability
                # gap (e.g. text-only model receiving an image).  Detect
                # this before returning dependency-unreachable so the label
                # accurately reflects the root cause.
                if _detect_model_incapability(dep_log_path, jsonl_dep_path, []):
                    return "model-incapability", {
                        "iter_at_stop": last.get("iteration"),
                        "mcp_evidence": "tool calls succeeded despite dependency-unreachable signal",
                    }
                return "dependency-unreachable", {
                    "iter_at_stop": last.get("iteration"),
                    "missing_dependency": missing,
                }
        # Throttled case: the run hit rate limits (non-trivial event count)
        # and produced low output.  An environment fault, not a stall —
        # a restart won't help until the rate-limit window passes.
        run_id = last.get("run_id")
        if run_id:
            rl_count = count_rate_limit_events(run_id, project_path, last_launch)
            if rl_count > 0:
                # Compute output-per-second across the last few iters.
                last3 = iters[-3:]
                total_output = sum((r.get("output_tokens") or 0) for r in last3)
                total_dur = sum((r.get("duration_sec") or 0) for r in last3)
                output_per_sec = total_output / total_dur if total_dur > 0 else 0
                # A throttled run has low output relative to a normal run.
                # Threshold: < 5 tokens/sec is suspiciously low for a
                # functioning model.
                if output_per_sec < 5:
                    return "throttled", {
                        "iter_at_stop": last.get("iteration"),
                        "rate_limit_event_count": rl_count,
                        "output_per_sec": round(output_per_sec, 2),
                    }
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
                # Distinguish broken gate from stuck gate via local_checks
                # exit_code / stderr_tail when available.
                broken = any(
                    _is_broken_gate_result(c)
                    for r in last3
                    for c in _items(r)
                    if c.get("outcome") in ("fail", "error")
                )
                label = "local-checks-broken" if broken else "local-checks-stuck"
                return label, {
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


def _classify_self_hosting_drift(
    core_label: str,
    core_facts: dict[str, Any],
    sh_facts: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(label, facts)`` if self-hosting drift applies, else ``None``.

    Conservative trigger: only fires when the project is the skill source
    AND the launch log path has disappeared (or a preserved archive exists
    for a path that no longer does).  More-specific labels
    (``clean-success``, ``local-checks-stuck``, ``budget-exhausted``,
    ``timeout-bound``) are never overridden — those have intact evidence
    and a clearer diagnosis.
    """
    if not sh_facts.get("is_self_hosting"):
        return None

    has_drift = (
        sh_facts.get("log_path_drifted")
        or sh_facts.get("launch_log_exists") is False
        or (sh_facts.get("preserved_archive_exists") and not sh_facts.get("launch_log_exists"))
    )
    if not has_drift:
        return None

    # Don't override labels that already have strong, intact evidence.
    preserve_labels = {"clean-success", "local-checks-stuck", "budget-exhausted", "timeout-bound"}
    if core_label in preserve_labels:
        return None

    facts = {**core_facts, **sh_facts}
    facts["original_label"] = core_label
    facts["drift_reason"] = (
        "self-hosting project with runtime path drift: "
        f"launch_log_exists={sh_facts.get('launch_log_exists')}, "
        f"preserved_archive_exists={sh_facts.get('preserved_archive_exists')}, "
        f"log_path_drifted={sh_facts.get('log_path_drifted')}"
    )
    return "self-hosting-drift", facts


def classify(
    iters: list[dict],
    last_launch: dict | None,
    project_path: Path,
) -> tuple[str, dict[str, Any]]:
    """Classify a run and include self-hosting drift facts.

    Delegates to ``_classify_core`` for the taxonomy label, then merges
    self-hosting / runtime-path drift facts into the returned dict.
    When self-hosting + path drift is detected and the core label is not
    more specific, overrides to ``self-hosting-drift``.
    """
    run_id = iters[-1].get("run_id") if iters else None
    sh_facts = collect_self_hosting_facts(project_path, last_launch, run_id)

    # L1: sentinel terminal failure state is authoritative.  When the
    # sentinel records a failure, honour it regardless of the iter-count
    # heuristic or the agent narrative.  See
    # orchestration-collaboration.md §L1 and the 2026-06-16 bug
    # (state=local_checks_failed laundered into clean-success).
    #
    # Narrowing (2026-08-14): a one-iteration local_checks_failed is
    # unrunnable (the gate couldn't execute), not stuck (the agent failed).
    # The iter-count heuristic already guards ≥3 correctly; only the <3
    # case needs the different label.  See sub-plan
    # a-one-iteration-gate-failure-is-not-stuck.
    sentinel = read_sentinel(project_path)
    _SENTINEL_FAILURE_MAP: dict[str, str] = {
        "budget_exhausted": "budget-exhausted",
        "max-iterations": "max-iter-bound",
        "interrupted": "interrupted",
    }
    if sentinel is not None:
        sentinel_state = (sentinel.get("state") or "").strip()
        if sentinel_state in _SENTINEL_FAILURE_MAP or sentinel_state == "local_checks_failed":
            if sentinel_state == "local_checks_failed":
                iter_count = len(iters)
                last_iter = iters[-1] if iters else {}
                if iter_count < 3 and last_iter.get("exit_code", 1) == 0:
                    label = "local-checks-broken"
                else:
                    label = "local-checks-stuck"
            else:
                label = _SENTINEL_FAILURE_MAP[sentinel_state]
            facts: dict[str, Any] = {
                "iter_at_stop": sentinel.get("iteration"),
                "reason": "sentinel terminal state",
            }
            # Merge self-hosting facts and return early — the sentinel is
            # authoritative, no further classification needed.
            facts.update(sh_facts)
            assert label in CLASSIFICATION_LABELS, (
                f"Sentinel-derived label '{label}' not in CLASSIFICATION_LABELS — "
                f"add it to the vocabulary constant in collect.py"
            )
            return label, facts

    label, facts = _classify_core(iters, last_launch, project_path)

    # Check for self-hosting drift override
    drift_result = _classify_self_hosting_drift(label, facts, sh_facts)
    if drift_result is not None:
        label, facts = drift_result
    else:
        facts.update(sh_facts)

    # Check for shipped-unverified override: when the label would be
    # clean-success but ≥1 shipped sub-plan has a non-loop-verified tier,
    # downgrade to shipped-unverified so the postmortem is honest.
    if label == "clean-success":
        unverified = batch_unverified_tiers(project_path)
        if unverified:
            label = "shipped-unverified"
            facts["unverified_sub_plans"] = unverified

    assert label in CLASSIFICATION_LABELS, (
        f"Classification label '{label}' not in CLASSIFICATION_LABELS — "
        f"add it to the vocabulary constant in collect.py"
    )
    return label, facts


# ---------- upstream-candidate emission --------------------------------------

# Classification labels that indicate a toolkit/process gap (not project-local).
# Conservative list — only clear toolkit signals where the sub-plan's
# local_checks or the loop machinery itself is the likely root cause.
_TOOLKIT_SIGNAL_LABELS = frozenset({
    "local-checks-stuck",   # sub-plan AC may be wrong/over-specified
    "local-checks-broken",  # gate COMMAND couldn't execute — fix the gate config
})


def maybe_emit_upstream_candidate(
    label: str,
    facts: dict[str, Any],
    project_path: Path,
    run_id: str,
    iters: list[dict],
) -> None:
    """Emit an upstream candidate when the classification is a toolkit signal.

    Conservative: only fires for labels in ``_TOOLKIT_SIGNAL_LABELS``.
    Never emits for project-local findings.
    """
    if _improvement_backlog is None:
        return
    if label not in _TOOLKIT_SIGNAL_LABELS:
        return

    last = iters[-1] if iters else {}
    evidence = {
        "project": str(project_path),
        "run_id": run_id,
        "iter_at_stop": last.get("iteration"),
    }
    # Include failing check commands + exit codes if available
    fail_checks = []
    fail_exit_codes: list[int | None] = []
    for r in iters[-3:]:
        lc = r.get("local_checks")
        if isinstance(lc, dict):
            lc = [lc]
        if isinstance(lc, list):
            for c in lc:
                if isinstance(c, dict) and c.get("outcome") in ("fail", "error"):
                    cmd = c.get("command", "")
                    if cmd and cmd not in fail_checks:
                        fail_checks.append(cmd)
                        fail_exit_codes.append(c.get("exit_code"))
    if fail_checks:
        evidence["failing_checks"] = fail_checks
        if label == "local-checks-broken":
            evidence["exit_codes"] = fail_exit_codes

    # AC-8: when a command was captured, the recommendation names it.
    failing_cmd_str = ""
    if fail_checks:
        failing_cmd_str = f" Failing command: `{fail_checks[0]}`"

    if label == "local-checks-broken":
        gap_desc = (
            f"Loop classified as '{label}': gate COMMAND could not execute "
            f"(exit_code in {{4,5,127}} or 'not found' in stderr). "
            f"Product code is NOT implicated; a blind resume re-fails. "
            f"Fix the gate config (often a path a later step creates)."
            f"{failing_cmd_str}"
        )
        proposed_fix = (
            "Fix the gate config: check if the command references a path "
            "that doesn't exist yet (see plan_lint frontmatter-path rule), "
            "install the missing dependency in the worker, or correct the "
            "command syntax."
        )
        candidate_kind = "toolchain"
    else:
        gap_desc = (
            f"Loop classified as '{label}': agent kept committing but "
            f"local_checks kept failing (fail_iters_in_window="
            f"{facts.get('fail_iters_in_window')}, "
            f"pass_iters_in_window={facts.get('pass_iters_in_window')}). "
            f"Sub-plan AC may be wrong/over-specified or there's a real bug."
            f"{failing_cmd_str}"
        )
        # AC-8: when a command was captured, name it in the recommendation.
        if fail_checks:
            proposed_fix = (
                f"Review the failing command `{fail_checks[0]}` — check if the "
                f"AC is achievable, the command syntax is correct, or there's a "
                f"real bug in the code being tested."
            )
        else:
            proposed_fix = "Review the sub-plan's local_checks AC for achievability; consider splitting the step or adjusting the check."
        candidate_kind = "toolkit"

    try:
        # candidates.json is the contract /ilk-self-improve reads;
        # source="feedback" distinguishes postmortem-emitted entries.
        _improvement_backlog.add_candidate(
            title=f"{label}: {project_path.name}",
            kind=candidate_kind,
            gap=gap_desc,
            evidence=evidence,
            proposed_fix=proposed_fix,
            leverage="medium",
            severity="high",
            source="feedback",
        )
    except Exception:
        # Non-fatal — don't break postmortem generation
        pass


# ---------- suspected-hang detection ----------------------------------------


def detect_suspected_hangs(
    run_id: str,
    project_path: Path,
    last_launch: dict | None = None,
) -> list[dict[str, Any]]:
    """Detect ceiling-hit-with-no-output events from per-iteration JSONL files.

    A ceiling hit is a broad test command that hit the harness ceiling,
    was auto-backgrounded, and produced no captured output.  Reads each
    per-iteration JSONL through ``iteration_timing.analyze_iteration`` and
    collects the ``ceiling_hit_no_output`` entries.

    Returns a list of dicts with keys: ``command``, ``duration_sec``,
    ``iteration``.  Empty when no ceiling hits are found.
    """
    if _analyze_iteration is None:
        return []

    hangs: list[dict[str, Any]] = []
    for root in _iter_log_root_candidates(project_path, last_launch):
        runs_dir = root / "runs" / run_id
        if not runs_dir.is_dir():
            continue
        for jsonl_file in sorted(runs_dir.glob("iter-*.jsonl")):
            # Extract iteration number from filename.
            iter_match = re.match(r"iter-(\d+)\.jsonl", jsonl_file.name)
            iter_num = int(iter_match.group(1)) if iter_match else 0
            try:
                result = _analyze_iteration(jsonl_file)
                for entry in result.get("ceiling_hit_no_output", []):
                    hangs.append({
                        "command": entry["command"],
                        "duration_sec": entry["duration_sec"],
                        "iteration": iter_num,
                    })
            except Exception:
                continue
    return hangs


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

    if label == "shipped-unverified":
        return cur_max, cur_to, (
            "loop shipped but some sub-plans have compile-only or device-manual "
            "verification tiers — need a human + device pass before trusting."
        )

    if label == "no-evidence":
        return cur_max, cur_to, (
            "run started (sentinel present) but left no usable JSONL records. "
            "Possibly crashed before iter 1. Params unchanged; check the "
            "sentinel state and runner logs before relaunching."
        )

    if label == "startup-hang":
        return cur_max, cur_to, (
            "the run hung before iteration 1 and the runner aborted on the "
            "startup timeout. A restart will NOT help until the cause is fixed "
            "(usually a wedged pre-iteration step / branch setup). Read the "
            "runner banner tail, fix the cause, then relaunch. Params unchanged."
        )

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

    if label == "dependency-unreachable":
        return cur_max, cur_to, (
            "the loop stalled because a required runtime dependency was "
            "unreachable (worker missing an MCP, dev server down, or an "
            "env_prereq probe failed) - a restart will NOT help. For a missing "
            "worker MCP: ilk-worker-mcp add <name> then ilk-worker-mcp verify. "
            "For a dev-server/remote source: bring it up. Params unchanged."
        )

    if label == "model-incapability":
        return cur_max, cur_to, (
            "the loop stalled because the model cannot process the tool output "
            "(e.g. text-only model receiving an image). The MCP IS reachable — "
            "tool calls succeeded — but the model lacks the required modality. "
            "A restart will NOT help. Use a vision-capable model or a bridging "
            "tool like vl_describe. Params unchanged."
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

    if label == "local-checks-broken":
        return cur_max, cur_to, (
            "gate COMMAND could not execute (exit 4/5/127 or 'not found' in "
            "stderr) — product code is NOT the issue. Params unchanged. **Do "
            "not auto-relaunch**: a blind resume re-fails identically. Fix the "
            "gate config (often a path a later step creates; see plan_lint "
            "frontmatter-path rule) or install the missing dependency, then "
            "relaunch."
        )

    if label == "budget-exhausted":
        return cur_max, cur_to, (
            "hit --max-budget-usd cap. Either raise the cap or accept stopping "
            "here. Params unchanged."
        )

    if label == "self-hosting-drift":
        return cur_max, cur_to, (
            "self-hosting project experienced runtime path drift — log/runner "
            "paths changed during the run. **Do not auto-relaunch yet.** "
            "1) Preserve evidence (run preserve_active_run.py if not already "
            "archived). 2) Clean the stale sentinel (last-exit.json reports "
            "state=running for a dead PID). 3) Relaunch from a stable/snapshot "
            "runner when available."
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


# Renderer lines may carry a leading wall-clock stamp ("[17:29:48] ...");
# strip it before classifying so the filter keeps working on both the
# stamped format and older unstamped logs.
_TS_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+")


def filter_system_lines(lines: list[str]) -> list[str]:
    """Filter out uninformative [system] lines from a tail.

    Most [system] lines (e.g. thinking_tokens) are technically present but
    entirely uninformative for diagnosis. [system] api_retry is the
    exception — a retry storm is exactly the kind of thing a postmortem
    needs to see — so it is kept. If every line is noise, return a
    sentinel indicating so.
    """
    filtered = [
        ln
        for ln in lines
        if not _TS_PREFIX_RE.sub("", ln).startswith("[system] ")
        or _TS_PREFIX_RE.sub("", ln).startswith("[system] api_retry")
    ]
    if not filtered and lines:
        return ["<all lines were [system] noise — no substantive output>"]
    return filtered


def detect_uncommitted_changes(project_path: Path) -> list[dict[str, Any]]:
    """Detect uncommitted changes in the project repository.

    Returns a list of dicts with 'path' and 'line_count' for each changed file.
    AC-7: on a local_checks_failed stop, name uncommitted paths and line counts.
    """
    import subprocess

    try:
        # Get list of changed files with line counts
        result = subprocess.run(
            ["git", "diff", "--numstat"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        changes = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                added, deleted, path = parts[0], parts[1], parts[2]
                # Only include files with actual changes
                if added != "-" or deleted != "-":
                    total = (int(added) if added != "-" else 0) + (int(deleted) if deleted != "-" else 0)
                    changes.append({"path": path, "line_count": total})
        return changes
    except Exception:
        return []


def resolve_iter_log(
    run_id: str, iteration: int, project_path: Path | None = None, last_launch: dict | None = None
) -> Path | None:
    """Return the path to a specific iteration log if it exists on disk.

    Searches all candidate log root directories (external, last-launch.json
    hint, legacy) for the iteration log.  Current layout
    (``runs/<run_id>/iter-NN.log``) is tried first; legacy layout
    (``ilk-claude-<run_id>/iter-NN.log``) is the fallback.
    """
    if project_path is not None:
        roots = _iter_log_root_candidates(project_path, last_launch)
    else:
        roots = [LOOP_LOG_DIR]
    rel_current = Path("runs") / run_id / f"iter-{iteration:02d}.log"
    rel_legacy = Path(f"ilk-claude-{run_id}") / f"iter-{iteration:02d}.log"
    for rel in (rel_current, rel_legacy):
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
    rl_count = facts.get("rate_limit_event_count", 0)
    if rl_count:
        body_lines.append(f"| Rate-limit events | {rl_count} |")
    body_lines.append(f"| Model | {model} |")
    body_lines.append(f"| Endpoint | {base_url} |")
    body_lines.append("")

    body_lines.append("## What happened\n")
    body_lines.append(_label_narrative(label, facts))
    body_lines.append("")

    # Ceiling hits with zero output: a broad test that hit the ceiling,
    # was backgrounded, and produced no output.  Reported as evidence,
    # not as a new label.
    hang_entries = facts.get("ceiling_hit_no_output", [])
    if hang_entries:
        body_lines.append("## Ceiling hits (zero output)\n")
        body_lines.append(
            "The following broad test commands hit the harness ceiling, were "
            "auto-backgrounded, and produced no captured output.  These are "
            "wasted gate runs — the test suite exceeded the time limit and "
            "the result was discarded.  Reported as evidence, not as a "
            "change to the run's classification.\n"
        )
        for h in hang_entries:
            body_lines.append(
                f"- **iter {h.get('iteration', '?')}**: "
                f"`{h['command']}` — {h['duration_sec']:.1f}s, "
                f"backgrounded, 0 bytes output"
            )
        body_lines.append("")

    # AC-8: extract failing check details from iteration records
    fail_checks = []
    fail_exit_codes: list[int | None] = []
    fail_stdout_tails: list[str] = []
    fail_stderr_tails: list[str] = []
    for r in iters[-3:]:
        lc = r.get("local_checks")
        if isinstance(lc, dict):
            lc = [lc]
        if isinstance(lc, list):
            for c in lc:
                if isinstance(c, dict) and c.get("outcome") in ("fail", "error"):
                    cmd = c.get("command", "")
                    if cmd and cmd not in fail_checks:
                        fail_checks.append(cmd)
                        fail_exit_codes.append(c.get("exit_code"))
                        fail_stdout_tails.append(c.get("stdout_tail", ""))
                        fail_stderr_tails.append(c.get("stderr_tail", ""))

    if fail_checks:
        body_lines.append("## Failing check details\n")
        for i, cmd in enumerate(fail_checks):
            body_lines.append(f"### Check {i+1}\n")
            body_lines.append(f"- **Command:** `{cmd}`")
            if i < len(fail_exit_codes) and fail_exit_codes[i] is not None:
                body_lines.append(f"- **Exit code:** {fail_exit_codes[i]}")
            if i < len(fail_stdout_tails) and fail_stdout_tails[i]:
                body_lines.append(f"- **Stdout tail:**")
                body_lines.append("```")
                body_lines.append(fail_stdout_tails[i][-2048:])  # Cap at 2KB
                body_lines.append("```")
            if i < len(fail_stderr_tails) and fail_stderr_tails[i]:
                body_lines.append(f"- **Stderr tail:**")
                body_lines.append("```")
                body_lines.append(fail_stderr_tails[i][-2048:])  # Cap at 2KB
                body_lines.append("```")
        body_lines.append("")

    if label == "self-hosting-drift":
        body_lines.append("## Self-hosting drift details\n")
        body_lines.append("| Fact | Value |")
        body_lines.append("|---|---|")
        body_lines.append(f"| Skill root | `{facts.get('skill_root_path', 'unknown')}` |")
        body_lines.append(f"| Is self-hosting | {facts.get('is_self_hosting', 'unknown')} |")
        body_lines.append(f"| Launch log path | `{facts.get('launch_log_path', 'unknown')}` |")
        body_lines.append(f"| Launch log exists | {facts.get('launch_log_exists', 'unknown')} |")
        body_lines.append(f"| Preserved archive path | `{facts.get('preserved_archive_path', 'none')}` |")
        body_lines.append(f"| Preserved archive exists | {facts.get('preserved_archive_exists', 'unknown')} |")
        body_lines.append(f"| Log path drifted | {facts.get('log_path_drifted', 'unknown')} |")
        body_lines.append(f"| Original classification | `{facts.get('original_label', 'unknown')}` |")
        body_lines.append("")

    if label == "never-ran":
        worker_home = facts.get("worker_home", "unknown")
        result = facts.get("result", "")
        body_lines.append("## Never-ran details\n")
        body_lines.append("| Fact | Value |")
        body_lines.append("|---|---|")
        body_lines.append(f"| Worker home probed | `{worker_home}` |")
        body_lines.append(f"| Startup failure | `{result}` |")
        body_lines.append("| Remediation | Check the worker home directory and ensure the launcher command is installed and accessible |")
        body_lines.append("")

    # AC-7: on a local_checks_failed stop, name uncommitted paths and line counts.
    if label in ("local-checks-stuck", "local-checks-broken"):
        uncommitted = detect_uncommitted_changes(project_path)
        if uncommitted:
            body_lines.append("## Uncommitted changes at stop\n")
            body_lines.append("| File | Lines changed |")
            body_lines.append("|---|---|")
            for change in uncommitted:
                body_lines.append(f"| `{change['path']}` | {change['line_count']} |")
            body_lines.append("")
            body_lines.append("**Do not commit these** — they represent half-finished work from the last iteration.")
            body_lines.append("")

    body_lines.append("## Recommendation for next launch\n")
    body_lines.append(f"- `MaxIterations`: **{rec_max}** (was {max_iter_cfg if max_iter_cfg else 'unknown'})")
    body_lines.append(f"- `IterationTimeoutMin`: **{rec_to}** (was {to_cfg if to_cfg else 'unknown'})")
    body_lines.append(f"- Rationale: {rationale}")
    body_lines.append("")

    body_lines.append("## Suggested next action\n")
    if label == "self-hosting-drift":
        body_lines.append("**Do not auto-relaunch** until drift is resolved:")
        body_lines.append("- (a) Preserve evidence — run `preserve_active_run.py` if not already archived")
        body_lines.append("- (b) Clean stale sentinel — remove `last-exit.json` with `state=running` for dead PID")
        body_lines.append("- (c) Relaunch from a stable/snapshot runner when available")
    else:
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
    # AC-6: skip [system] lines (e.g. thinking_tokens) — they are technically
    # present but entirely uninformative for diagnosis.
    filtered_tail = filter_system_lines(tail) if tail else ["<no tail available>"]
    body_lines.extend(filtered_tail)
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
    if label == "shipped-unverified":
        subs = facts.get("unverified_sub_plans", [])
        names = ", ".join(
            f"`{s['plan']}` ({s['tier']})" for s in subs
        ) or "(none listed)"
        return (
            "All sub-plans shipped, but some have non-loop-verified tiers "
            "and still need a human + device pass: " + names + ". "
            "The loop cannot confirm these are actually working."
        )
    if label == "timeout-bound":
        cfg_to = facts.get("configured_timeout_min")
        cfg_disp = f"{cfg_to} min" if cfg_to else "unknown (no last-launch.json — likely a manual launch before ilk-launcher existed; the loop infers timeout from script defaults)"
        actual_min = (facts.get("duration_sec") or 0) / 60
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"Iteration {iter_stop} " if iter_stop is not None else "An iteration "
        return (
            f"{iter_clause}hit the configured "
            f"`IterationTimeoutMin` ({cfg_disp}). Observed duration: "
            f"{actual_min:.1f} min. The agent was killed mid-iter; partial "
            "work in that iter is lost (anything committed earlier in the "
            "iter survives)."
        )
    if label == "max-iter-bound":
        return (
            f"Loop ran out of iterations ({facts.get('iters') or '?'} / "
            f"{facts.get('max_iter_configured') or '?'}) without shipping all sub-plans. "
            "Either bump MaxIterations or break sub-plans into smaller, faster steps."
        )
    if label == "api-flaky":
        err_count = facts.get('error_count') or 0
        err_rate = facts.get('error_rate') or 0
        return (
            f"Endpoint instability: {err_count} transient errors "
            f"(rate {err_rate:.0%}). Loop survived because at least "
            "some iters made commits despite errors."
        )
    if label == "api-blocked":
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"Iter {iter_stop} " if iter_stop is not None else "The loop "
        return (
            f"API errors stalled the loop. {iter_clause}was the "
            "third zero-progress iter, with API errors in 2+ of the last 3 iters. "
            "Triage endpoint, credentials, or model before relaunching."
        )
    if label == "dependency-unreachable":
        dep = facts.get("missing_dependency", "a required dependency/MCP")
        fix = (
            f"add it to the worker: `ilk-worker-mcp add {dep}` then "
            f"`ilk-worker-mcp verify`"
            if dep and "dependency" not in dep
            else "restore the missing MCP / dev server / remote source"
        )
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"at iter {iter_stop} " if iter_stop is not None else ""
        return (
            f"The loop stalled {iter_clause}because a "
            f"required runtime dependency was unreachable: **{dep}**. A restart "
            f"will NOT help — this is a config/reachability gap, not a stuck "
            f"agent. Fix: {fix}. Then relaunch."
        )
    if label == "model-incapability":
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"at iter {iter_stop} " if iter_stop is not None else ""
        return (
            f"The loop stalled {iter_clause}because the model cannot process "
            "the tool output (e.g. a text-only model receiving an image from "
            "chrome-devtools). The MCP dependency IS reachable — tool calls "
            "succeeded — but the model lacks the capability to use the results. "
            "A restart will NOT help. Fix: use a model with the required "
            "modality (e.g. vision), or use a tool like `vl_describe` to "
            "bridge the modality gap."
        )

    if label == "never-ran":
        worker_home = facts.get("worker_home", "unknown")
        result = facts.get("result", "")
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"at iter {iter_stop} " if iter_stop is not None else ""
        result_clause = f"\n\nResult: `{result}`" if result else ""
        return (
            f"The run failed {iter_clause}before the model was ever invoked "
            f"(zero turns, zero tokens). Worker home probed: `{worker_home}`. "
            "This is an environment/startup fault — a restart will NOT help "
            "until the cause is fixed (missing command, incomplete worker home, "
            "or misconfigured launcher). Check the worker home and the "
            "command that was attempted." + result_clause
        )
    if label == "throttled":
        rl_count = facts.get("rate_limit_event_count", 0)
        ops = facts.get("output_per_sec", 0)
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f"at iter {iter_stop} " if iter_stop is not None else ""
        return (
            f"The run was rate-limited {iter_clause}— {rl_count} rate-limit "
            f"events detected, output rate {ops:.1f} tokens/sec. "
            "This is a transient condition, not a stall. The rate-limit window "
            "will pass; relaunch after it expires. A restart during the window "
            "will hit the same limit."
        )
    if label == "stuck-no-progress":
        iter_stop = facts.get('iter_at_stop')
        iter_clause = f" at iter {iter_stop}" if iter_stop is not None else ""
        return (
            f"Agent stalled: 3 consecutive zero-progress iters{iter_clause}, "
            "but exit codes were mostly clean. "
            "Likely sub-plan ambiguity, prompt confusion, or a real bug it can't "
            "solve. **Read the tail below.**"
        )
    if label == "local-checks-stuck":
        fail_w = facts.get('fail_iters_in_window')
        pass_w = facts.get('pass_iters_in_window')
        win = facts.get('window_size')
        iter_stop = facts.get('iter_at_stop')
        if fail_w is not None and pass_w is not None and win is not None:
            counts_clause = (
                f"failed in {fail_w} of the last {win} iterations "
                f"(passed in {pass_w}). "
            )
        else:
            counts_clause = ""
        iter_clause = f" through iter {iter_stop}" if iter_stop is not None else ""
        return (
            f"Sub-plan `local_checks` {counts_clause}"
            f"The agent kept making commits{iter_clause}, but the machine-checkable "
            "acceptance criteria did not converge. Either the AC is wrong/over-"
            "specified, the step is too coarse for the agent to land in one go, "
            "or there's a real bug it can't fix. **Read the failing check output "
            "in the iteration log before deciding what to do.**"
        )
    if label == "local-checks-broken":
        fail_w = facts.get('fail_iters_in_window')
        pass_w = facts.get('pass_iters_in_window')
        win = facts.get('window_size')
        if fail_w is not None and pass_w is not None and win is not None:
            counts_clause = (
                f" in {fail_w} of the last {win} iterations "
                f"(passed in {pass_w}). "
            )
        else:
            counts_clause = ". "
        return (
            f"The gate COMMAND could not execute{counts_clause}"
            "The product code is NOT "
            "implicated — a blind resume re-fails identically. The fix is the "
            "gate config itself: often a path a later plan step creates, a missing "
            "dependency, or a command not installed in the worker environment. "
            "See also the plan_lint frontmatter-path rule for prevention. "
            "**Do not auto-relaunch until the gate is fixed.**"
        )
    if label == "budget-exhausted":
        budget = facts.get('max_budget_usd')
        budget_clause = f" ({budget})" if budget is not None else ""
        return (
            f"Hit `--max-budget-usd`{budget_clause}. Loop stopped "
            "to protect spend."
        )
    if label == "self-hosting-drift":
        orig = facts.get("original_label", "unknown")
        launch = facts.get('launch_log_exists')
        archive = facts.get('preserved_archive_exists')
        drifted = facts.get('log_path_drifted')
        return (
            f"This project is the skill source (self-hosting) and runtime "
            f"paths drifted during the run. The original classification was "
            f"`{orig}`, but log evidence is incomplete: "
            f"launch_log_exists={launch if launch is not None else 'unknown'}, "
            f"preserved_archive_exists={archive if archive is not None else 'unknown'}, "
            f"log_path_drifted={drifted if drifted is not None else 'unknown'}. "
            "Preserve evidence, clean the stale sentinel, and relaunch from "
            "a stable/snapshot runner when available."
        )
    if label == "no-evidence":
        return (
            "Run started (sentinel present) but left no usable JSONL records. "
            "The run may have crashed before iter 1 completed, or all records "
            "were for a different project path. " + (facts.get("reason") or "")
        )
    if label == "startup-hang":
        return (
            "The runner hung BEFORE iteration 1 and aborted on the startup "
            "timeout (no progress within the threshold). "
            + (facts.get("reason") or "")
            + " Not auto-relaunchable — fix the cause, then relaunch."
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
        # Per-iter JSONL fallback: claude-worker runs write per-iter logs
        # but may have no summary records.  Check per-iter BEFORE sentinel
        # so we classify from actual iteration data when available.
        check_run = args.run_id or "unknown"
        per_iter = read_per_iter_jsonl(check_run, project_path, last_launch)
        if per_iter:
            all_records = per_iter
            # Fall through to the normal classify path below.
        else:
            sentinel = read_sentinel(project_path)
            if sentinel is not None:
                target_run = sentinel.get("run_id") or args.run_id or "unknown"
                iters: list[dict] = []
                sentinel_state = (sentinel.get("state") or "").strip()
                if sentinel_state == "startup-hang":
                    label = "startup-hang"
                    facts = {
                        "reason": (
                            f"run {target_run} hung before iteration 1 and the runner "
                            "aborted on the startup timeout. A restart will not help "
                            "until the cause is fixed — check the branch-setup / runner "
                            "banner tail (often a wedged pre-iteration step)."
                        ),
                    }
                elif args.run_id and sentinel.get("run_id") == args.run_id:
                    label = "no-evidence"
                    facts = {
                        "reason": (
                            f"run {args.run_id} started (sentinel present) but "
                            "left no usable JSONL records — possibly crashed "
                            "before iter 1 completed"
                        ),
                    }
                else:
                    label, facts = classify(iters, last_launch, project_path)
                rec_max, rec_to, rationale = recommend_params(label, iters, last_launch)
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
                    tail=[],
                )
                if external_launcher_dir is None or project_key is None:
                    print("ilk_paths not available; cannot resolve external launcher dir.", file=sys.stderr)
                    return 1
                out_dir = external_launcher_dir(project_key(project_path)) / "postmortems"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{target_run}.md"
                out_path.write_text(report, encoding="utf-8")
                # Best-effort auto-close tracker entries resolved by shipped sub-plans.
                _maybe_autoclose(project_path, args.quiet)
                if not args.quiet:
                    print(f"[ilk-feedback] project: {project_name}  run: {target_run}")
                    print(f"[ilk-feedback] classification: {label}")
                    print(f"[ilk-feedback] iters: 0 / {(last_launch or {}).get('max_iterations', '?')}")
                print(str(out_path))
                return 0
            # No sentinel either — ilk genuinely never ran here.
            candidates = _jsonl_log_candidates(project_path, last_launch)
            looked_at = ", ".join(str(c) for c in candidates)
            print(f"[ilk-feedback] No JSONL records for project {project_path}.")
            print(f"[ilk-feedback] Has ilk ever run for this project? Looked at {looked_at}")
            return 1

    by_run = runs_index(all_records)
    if args.run_id:
        if args.run_id not in by_run:
            # --run-id R was passed but R has no summary JSONL records.
            # Try per-iter JSONL fallback first (claude-worker runs write
            # per-iter logs but may have no summary records).  Only fall
            # back to sentinel/no-evidence if per-iter also empty.
            per_iter = read_per_iter_jsonl(args.run_id, project_path, last_launch)
            if per_iter:
                target_run = args.run_id
                by_run[target_run] = per_iter
            else:
                sentinel = read_sentinel(project_path)
                sentinel_run = sentinel.get("run_id") if sentinel else None
                if sentinel_run and sentinel_run == args.run_id:
                    target_run = args.run_id
                    iters = []
                    label = "no-evidence"
                    facts: dict[str, Any] = {
                        "reason": (
                            f"run {args.run_id} started (sentinel present) but "
                            "left no usable JSONL records — possibly crashed "
                            "before iter 1 completed"
                        ),
                    }
                    rec_max, rec_to, rationale = recommend_params(label, iters, last_launch)
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
                        tail=[],
                    )
                    if external_launcher_dir is None or project_key is None:
                        print("ilk_paths not available; cannot resolve external launcher dir.", file=sys.stderr)
                        return 1
                    out_dir = external_launcher_dir(project_key(project_path)) / "postmortems"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / f"{target_run}.md"
                    out_path.write_text(report, encoding="utf-8")
                    # Best-effort auto-close tracker entries resolved by shipped sub-plans.
                    _maybe_autoclose(project_path, args.quiet)
                    if not args.quiet:
                        print(f"[ilk-feedback] project: {project_name}  run: {target_run}")
                        print(f"[ilk-feedback] classification: {label}")
                        print(f"[ilk-feedback] iters: 0 / {(last_launch or {}).get('max_iterations', '?')}")
                    print(str(out_path))
                    return 0
                # No JSONL records AND no sentinel match for this run_id.
                _avail = ", ".join(sorted(by_run.keys())[-5:])
                target_run = args.run_id
                iters = []
                label = "no-evidence"
                facts = {
                    "reason": (
                        f"run {args.run_id} has no JSONL records (and no sentinel "
                        f"match). Recent runs with records: {_avail}"
                    ),
                }
                rec_max, rec_to, rationale = recommend_params(label, iters, last_launch)
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
                    tail=[],
                )
                if external_launcher_dir is None or project_key is None:
                    print("ilk_paths not available; cannot resolve external launcher dir.", file=sys.stderr)
                    return 1
                out_dir = external_launcher_dir(project_key(project_path)) / "postmortems"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{target_run}.md"
                out_path.write_text(report, encoding="utf-8")
                # Best-effort auto-close tracker entries resolved by shipped sub-plans.
                _maybe_autoclose(project_path, args.quiet)
                if not args.quiet:
                    print(f"[ilk-feedback] project: {project_name}  run: {target_run}")
                    print(f"[ilk-feedback] classification: {label}")
                print(str(out_path))
                return 0
        target_run = args.run_id
    else:
        target_run = newest_run_id(by_run)
        # Auto-detected run may have no summary records (e.g. claude-worker
        # that wrote per-iter logs but no summary).  Try per-iter fallback
        # before falling through to the summary-only classify path.
        if target_run and target_run not in by_run:
            per_iter = read_per_iter_jsonl(target_run, project_path, last_launch)
            if per_iter:
                by_run[target_run] = per_iter

    iters = by_run[target_run]

    # Guard: when --run-id is explicit and records lack the "iteration" key,
    # emit a clear message instead of crashing with KeyError.
    if args.run_id and iters and not any("iteration" in r for r in iters):
        # Find the run log dir that actually exists on disk.
        run_log_dir = None
        for root in _iter_log_root_candidates(project_path, last_launch):
            candidate = root / "runs" / target_run
            if candidate.is_dir():
                run_log_dir = candidate
                break
        if run_log_dir is None:
            # Fallback: construct the expected path even if it doesn't exist.
            if external_logs_dir is not None and project_key is not None:
                run_log_dir = external_logs_dir(project_key(project_path)) / "runs" / target_run
            else:
                run_log_dir = LOOP_LOG_DIR / "runs" / target_run
        print(
            f"no JSONL records for run {target_run!r} — the runner may have "
            f"died before writing its summary; per-iteration logs are at "
            f"{run_log_dir}",
            file=sys.stderr,
        )
        return 1

    label, facts = classify(iters, last_launch, project_path)

    # Count rate-limit events for this run (independently useful metadata).
    rl_count = count_rate_limit_events(target_run, project_path, last_launch)
    if rl_count > 0:
        facts["rate_limit_event_count"] = rl_count

    # Emit upstream candidate when the classification is a toolkit signal
    # (conservative — only clear toolkit gaps, never project-local findings).
    maybe_emit_upstream_candidate(label, facts, project_path, target_run, iters)

    # Detect ceiling-hit-with-no-output from per-iteration JSONL.  Reported
    # as evidence in the postmortem, not as a classification change.
    hangs = detect_suspected_hangs(target_run, project_path, last_launch)
    if hangs:
        facts["ceiling_hit_no_output"] = hangs

    rec_max, rec_to, rationale = recommend_params(label, iters, last_launch)
    last_log = iters[-1].get("log") if iters else None
    if not last_log and iters:
        iter_num = iters[-1].get("iteration")
        if iter_num is not None:
            resolved = resolve_iter_log(target_run, iter_num, project_path, last_launch)
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

    # Best-effort auto-close tracker entries resolved by shipped sub-plans.
    _maybe_autoclose(project_path, args.quiet)

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
