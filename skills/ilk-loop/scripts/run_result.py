#!/usr/bin/env python3
"""run_result.py — owns the unattended run's result file.

Called by the runner on every exit path when the master carries
``ilk_profile: unattended``.  Writes an atomic (tmp + os.replace) JSON
file whose schema is versioned and whose check outcomes use the tri-state
vocabulary ``pass | fail | unmeasured``.

Usage:
  python run_result.py write --result-file P --run-id ID --master SLUG \
      --exit-state STATE [--checks-jsonl F ...] [--integrity-jsonl F ...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Internal → result-file outcome mapping.
_OUTCOME_MAP = {
    "pass": "pass",
    "fail": "fail",
    "error": "unmeasured",
    "skipped": "unmeasured",
    "inconclusive": "unmeasured",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _classify_check(raw: dict[str, Any]) -> dict[str, Any]:
    """Map an internal check record to the result-file schema.

    Rules (the tests pin each one):
    - ``pass`` ⇒ ``pass``; ``fail`` ⇒ ``fail``; ``error`` / ``skipped`` /
      ``inconclusive`` ⇒ ``unmeasured``.  A timeout is never ``fail``.
    - ``exit_code`` is present on every row; it is null only when no process
      exit exists.
    - ``fail`` rows require ``failed_count`` (int or null) AND keep
      ``exit_code``.
    - ``unmeasured`` rows require a non-empty ``reason``.
    """
    internal = raw.get("outcome")
    outcome = _OUTCOME_MAP.get(internal, "unmeasured")
    evidence_reason = None
    if internal is None:
        evidence_reason = 'missing check outcome'
    elif internal == 'pass' and (type(raw.get('exit_code')) is not int or raw['exit_code'] != 0):
        outcome = 'unmeasured'
        evidence_reason = 'passing check has no measured zero exit'


    row: dict[str, Any] = {
        "slug": raw.get("slug", ""),
        "step": raw.get("step", 0),
        "cmd": raw.get("cmd", raw.get("command", "")),
        "cwd": raw.get("cwd", ""),
        "outcome": outcome,
        "exit_code": raw.get("exit_code"),
        "duration_s": raw.get("duration_s", 0.0),
        "path_prelude_applied": raw.get("path_prelude_applied", False),
    }

    if outcome == "fail":
        # failed_count is required; null when the tool gives no count.
        row["failed_count"] = raw.get("failed_count")
        # exit_code already set above.
        row["reason"] = raw.get("reason")
    elif outcome == "unmeasured":
        reason = evidence_reason or raw.get("reason") or raw.get("error") or ""
        if not reason:
            # Provide a default reason for skipped/inconclusive without
            # explicit reason — the tool opted out of measuring.
            if internal in ("skipped", "inconclusive"):
                reason = f"check {internal}"
            else:
                # Refused: unmeasured without a reason.
                raise ValueError(
                    f"unmeasured row for slug={raw.get('slug')!r} has no reason — "
                    "refused (gh-resolve distinguishes 'exit 1, 0 tests failed' "
                    "from a counted red)"
                )
        row["reason"] = reason
        row["failed_count"] = raw.get("failed_count")
    else:
        # pass
        row["reason"] = raw.get("reason")
        row["failed_count"] = raw.get("failed_count")

    return row


def _read_checks_jsonl(paths: list[str]) -> list[dict[str, Any]]:
    """Read and classify checks from one or more JSONL files."""
    checks: list[dict[str, Any]] = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                checks.append(_classify_check(raw))
            except ValueError:
                # Re-raise so the caller sees the refusal.
                raise
    return checks


def _read_integrity_jsonl(paths: list[str]) -> list[dict[str, Any]]:
    """Read integrity violations from JSONL files."""
    violations: list[dict[str, Any]] = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            violations.append({
                "slug": raw.get("slug", ""),
                "violation": raw.get("violation", ""),
                "enforced": raw.get("enforced", False),
            })
    return violations


def _resolve_project_key() -> str:
    """Best-effort project key resolution."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ilk_paths import project_key as _pk, find_project_root
        root, _ = find_project_root(Path.cwd())
        if root:
            return _pk(root)
    except Exception:
        pass
    return ""


def _git_info(cwd: Path | None) -> tuple[str, str, list[dict[str, str]]]:
    """Return (base_sha, head_sha, commits) from git log."""
    import subprocess

    if cwd is None:
        return "", "", []
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", timeout=10,
        )
        head_sha = head.stdout.strip() if head.returncode == 0 else ""

        # Without an explicit legacy run base, the current parent says
        # nothing about the run's starting point. Leave the range unknown.
        base_sha = os.environ.get("ILK_RUN_BASE_SHA", "")

        commits: list[dict[str, str]] = []
        if base_sha and head_sha and base_sha != head_sha:
            log = subprocess.run(
                ["git", "log", "--format=%H %s", f"{base_sha}..{head_sha}"],
                cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            if log.returncode == 0:
                for line in log.stdout.strip().splitlines():
                    parts = line.split(" ", 1)
                    commits.append({
                        "sha": parts[0],
                        "subject": parts[1] if len(parts) > 1 else "",
                    })

        return base_sha, head_sha, commits
    except Exception:
        return "", "", []


def _recorded_git_info(log: Path, run_id: str, master: str, project: str):
    """Project a contiguous closed range; never query the mutable Git tree."""
    provenance = dict(source='closed-iteration-records', observation_complete=False)
    def unknown(reason):
        return '', '', [], dict(provenance, reason=reason)
    try:
        closes = {}; latest = 0
        with log.open(encoding='utf-8-sig') as source:
            for line in source:
                if not line.strip(): continue
                row = json.loads(line)
                if not isinstance(row, dict) or row.get('run_id') != run_id: continue
                if row.get('record_phase') not in {'open', 'close'}: continue
                iteration = row.get('iteration')
                if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
                    return unknown('invalid iteration identity')
                latest = max(latest, iteration)
                if row['record_phase'] == 'open': continue
                if iteration in closes and closes[iteration] != row:
                    return unknown('contradictory close records')
                closes[iteration] = row
        if not closes or latest not in closes:
            return unknown('latest iteration has no close record')
        if sorted(closes) != list(range(1, latest+1)):
            return unknown('closed iteration range is incomplete')
        base = ''; head = ''; commits = []
        for iteration in sorted(closes):
            row = closes[iteration]; context = row.get('context') or {}
            execution = row.get('execution') or {}
            if (not isinstance(context, dict) or not isinstance(execution, dict)
                    or context.get('run_id') != run_id or context.get('iteration') != iteration):
                return unknown('close and frozen context disagree')
            selected = context.get('dispatch') or {}
            if (not isinstance(selected, dict) or not selected.get('master_path')
                    or Path(selected['master_path']).stem != Path(master).stem):
                return unknown('close master differs from result master')
            if project and Path(project).resolve() not in {
                    Path(context.get(role, '')).resolve() for role in ('clone_tree', 'exec_tree')}:
                return unknown('close project differs from result project')
            start = context.get('base_commit'); end = execution.get('head_commit')
            recorded = execution.get('commits')
            sha = lambda value: isinstance(value, str) and bool(re.fullmatch('(?:[0-9a-f]{40}|[0-9a-f]{64})', value))
            if (execution.get('capture_error') or not sha(start) or not sha(end)
                    or not isinstance(recorded, list) or any(not sha(c) for c in recorded)):
                return unknown('execution capture unavailable')
            if head and start != head:
                return unknown('execution range is discontinuous')
            if (start == end and recorded) or (start != end and (not recorded or recorded[-1] != end)):
                return unknown('execution range contradicts captured head')
            if not base: base = start
            head = end
            commits.extend(dict(sha=c, subject='') for c in recorded)
        return base, head, commits, dict(provenance, observation_complete=True,
                                        closed_iterations=latest)
    except (OSError, ValueError, TypeError):
        return unknown('iteration records unavailable or malformed')


def write_result(
    result_file: Path,
    *,
    run_id: str,
    master_slug: str,
    exit_state: str,
    checks: list[dict[str, Any]] | None = None,
    integrity: list[dict[str, Any]] | None = None,
    pinned: bool = False,
    iterations: int = 0,
    started_at: str = "",
    ended_at: str = "",
    project_path: str = "",
    worktree: str = "",
    gate_resolution: str = "none",
    proof: dict[str, Any] | None = None,
    iteration_log: Path | None = None,
) -> None:
    """Write the result file atomically (tmp + os.replace)."""
    if iteration_log is not None:
        base_sha, head_sha, commits, git_provenance = _recorded_git_info(
            iteration_log, run_id, master_slug, project_path)
    else:
        base_sha, head_sha, commits = _git_info(
            Path(project_path) if project_path else None)
        git_provenance = dict(source='legacy-current-git',
                              observation_complete=bool(base_sha and head_sha))

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provenance": "runner",
        "pinned": pinned,
        "run_id": run_id,
        "master_slug": master_slug,
        "project_key": _resolve_project_key(),
        "worktree": worktree,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "commits": commits,
        "git_provenance": git_provenance,
        "checks": checks or [],
        "gate_resolution": gate_resolution,
        "proof": proof or {
            "trailer_found": False,
            "ledger_rows": 0,
            "verification_record": None,
            "record_suite_failed": None,
        },
        "integrity": integrity or [],
        "exit_state": exit_state,
        "iterations": iterations,
        "started_at": started_at,
        "ended_at": ended_at or _now_iso(),
    }

    # Atomic write: tmp + os.replace.
    result_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(result_file.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(result_file))
    except BaseException:
        # Clean up tmp on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Write an unattended run's result file.",
    )
    sub = ap.add_subparsers(dest="command")
    write_cmd = sub.add_parser("write", help="Write the result file.")
    write_cmd.add_argument("--result-file", required=True, type=Path)
    write_cmd.add_argument("--run-id", required=True)
    write_cmd.add_argument("--master", required=True, dest="master_slug")
    write_cmd.add_argument("--exit-state", required=True)
    write_cmd.add_argument(
        "--checks-jsonl", nargs="*", default=[],
        help="Paths to per-iteration local_checks JSONL files.",
    )
    write_cmd.add_argument(
        "--integrity-jsonl", nargs="*", default=[],
        help="Paths to integrity violation JSONL files.",
    )
    write_cmd.add_argument("--iterations", type=int, default=0)
    write_cmd.add_argument("--started-at", default="")
    write_cmd.add_argument("--project-path", default="")
    write_cmd.add_argument("--worktree", default="")
    write_cmd.add_argument("--iteration-log", type=Path)
    write_cmd.add_argument("--gate-resolution", default="none")
    write_cmd.add_argument(
        "--pinned", action="store_true", default=False,
        help="Set pinned=true (ILK_MASTER was set).",
    )

    args = ap.parse_args(argv)
    if args.command != "write":
        ap.print_help()
        return 2

    try:
        checks = _read_checks_jsonl(args.checks_jsonl) if args.checks_jsonl else []
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    integrity = (
        _read_integrity_jsonl(args.integrity_jsonl) if args.integrity_jsonl else []
    )

    try:
        write_result(
            args.result_file,
            run_id=args.run_id,
            master_slug=args.master_slug,
            exit_state=args.exit_state,
            checks=checks,
            integrity=integrity,
            pinned=args.pinned,
            iterations=args.iterations,
            started_at=args.started_at,
            project_path=args.project_path,
            worktree=args.worktree,
            gate_resolution=args.gate_resolution,
            iteration_log=args.iteration_log,
        )
    except Exception as exc:
        print(f"error: failed to write result: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
