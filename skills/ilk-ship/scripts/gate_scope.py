#!/usr/bin/env python3
"""Select a gate tier from the consumer set.

Part of sub-plan `the-gate-scope-follows-the-risk`.  Pure-function tier
selection (no I/O in the decision function); resolution happens outside it.

Tier table (from MASTER):
  0 — docs/changelog only, no code
  1 — changed symbol has zero resolved consumers
  2 — N resolved consumers
  3 — contract-governed file OR a shared path/schema

AC-3: pure function of (changed_paths, resolved_consumers, contract_governed_set).
AC-4: a path or schema change selects tier 3, not tier 1.
AC-5: when the oracle cannot run, result is tier 3, not tier 1.
AC-9: a change touching any .py/.sh/.ps1 can never be tier 0.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Contract-governed files (from plan_lint.py:123-136) ────────────────────

# Directories holding artifacts THIS tool writes. Changes inside them must never
# influence tier selection — see `_is_path_or_schema_change`. `baseline_diff`
# imports BASELINE_DIR_NAME from here so the name has exactly one definition.
BASELINE_DIR_NAME = ".ilk-baselines"
TOOL_ARTIFACT_DIRS = frozenset({BASELINE_DIR_NAME})

CONTRACT_GOVERNED_FILES: frozenset[str] = frozenset({
    "collect.py",
    "watchdog.ps1",
    "watchdog.sh",
    "scheduler.ps1",
    "scheduler.sh",
    "run_ilk_loop_claude.ps1",
    "run_ilk_loop_claude.sh",
    "loop_status.py",
    "promote_next_master.py",
    "plan_status.py",
    "status_all.py",
    "render_tray.py",
})

# Code file extensions — tier 0 is impossible when any changed path has one.
_CODE_EXTENSIONS = frozenset({".py", ".sh", ".ps1"})

# Path/schema indicators — files with no import graph that the consumer
# oracle would return zero for, but which are high-risk.  A sentinel case:
# one identifier at 2 call sites broke 12 test fixtures across 7 files.
_PATH_OR_SCHEMA_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml"})


# ── Consumer result — distinguishes zero from unknown (AC-5) ──────────────

class OracleStatus(Enum):
    """Whether the consumer oracle succeeded."""
    OK = "ok"           # grep ran and returned results (possibly zero)
    FAILED = "failed"   # grep missing, timed out, or errored


@dataclass(frozen=True)
class ConsumerResult:
    """Result of resolving consumers for a changed symbol.

    ``status == FAILED`` means the oracle could not run — this is distinct
    from "zero consumers" (status == OK, importers == []).  AC-5 requires
    the distinction: an oracle that fails silently to "no consumers"
    reproduces the sentinel bug with a machine's authority.
    """
    status: OracleStatus
    importers: tuple[str, ...]  # relative paths of production importers

    @property
    def count(self) -> int:
        return len(self.importers)

    @property
    def is_zero(self) -> bool:
        """True only when the oracle ran successfully and found nothing."""
        return self.status == OracleStatus.OK and len(self.importers) == 0

    @property
    def is_unknown(self) -> bool:
        """True when the oracle could not run (AC-5)."""
        return self.status == OracleStatus.FAILED


# ── Consumer resolution (I/O lives here, not in the tier function) ─────────

def resolve_consumers(
    module_name: str,
    project_root: Path,
    timeout: float = 15,
) -> ConsumerResult:
    """Find production Python files that import *module_name*.

    Reuses the same grep-based approach as ``plan_lint._find_importers``
    (the existing caller-aware detector).  Returns ``ConsumerResult`` with
    ``status == FAILED`` when grep cannot run, rather than silently returning
    zero — AC-5.

    This is the I/O boundary.  The tier decision function is pure.
    """
    pattern = f"from {module_name} import|import {module_name}"
    try:
        result = subprocess.run(
            ["grep", "-Ern", "--include=*.py", "-l", pattern, str(project_root)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode not in (0, 1):
            # grep exit 1 = no matches (valid); exit 2 = error.
            return ConsumerResult(status=OracleStatus.FAILED, importers=())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ConsumerResult(status=OracleStatus.FAILED, importers=())

    importers: list[str] = []
    root_str = str(project_root)
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        abs_path = line.strip()
        if abs_path.startswith(root_str):
            rel = abs_path[len(root_str):].lstrip("/")
        else:
            rel = abs_path
        # Exclude test files — same as plan_lint._find_importers.
        if _is_test_path(rel):
            continue
        importers.append(rel)

    return ConsumerResult(status=OracleStatus.OK, importers=tuple(importers))


def _is_test_path(rel_path: str) -> bool:
    """True if *rel_path* looks like a test file or lives in a test directory.

    Mirrors plan_lint._is_test_path exactly — a second oracle that disagrees
    with the first is worse than no second oracle.
    """
    parts = rel_path.replace("\\", "/").split("/")
    _TEST_DIR_NAMES = {"test", "tests", "spec", "specs"}
    for part in parts[:-1]:
        if part in _TEST_DIR_NAMES:
            return True
    filename = parts[-1]
    if filename.startswith("test_") and filename.endswith(".py"):
        return True
    if filename.endswith("_test.py"):
        return True
    return False


# ── Path/schema detection helpers ──────────────────────────────────────────

def _is_path_or_schema_change(path: str) -> bool:
    """True if *path* is a path or schema file (no import graph).

    These are the files where the consumer oracle returns zero consumers
    because there is nothing to import — but the change is high-risk.
    The sentinel case: a writer path moved at 2 call sites broke 12 test
    fixtures across 7 files.
    """
    p = path.replace("\\", "/")
    basename = p.rsplit("/", 1)[-1] if "/" in p else p
    # This tool's OWN artifacts are not risk signals. `store_baseline` commits
    # .ilk-baselines/<tag>__<hash>.json on every release; because `.json` is a
    # path/schema extension, that artifact forced the NEXT release to tier 3
    # regardless of what changed. Measured 2026-08-19: a docs-only diff plus a
    # stored baseline selected tier 3, the same diff without it selected tier 0
    # — the release process poisoned its own gate decision, and 8 of the 10
    # releases v0.9.57..v0.9.67 selected tier 3. A baseline is host-specific
    # anyway (rezmac 745 passed/16 skipped vs this Mac 746/15 at v0.9.66), so it
    # is never shared state worth widening a gate over.
    if any(seg in TOOL_ARTIFACT_DIRS for seg in p.split("/")):
        return False
    # Explicit path/config files
    if basename in {
        ".ilk-launch.json", "pytest.ini", "setup.cfg", "pyproject.toml",
        "Makefile", "Dockerfile",
    }:
        return True
    # Extensions that have no import graph
    for ext in _PATH_OR_SCHEMA_EXTENSIONS:
        if basename.endswith(ext):
            return True
    return False


def _is_contract_governed(path: str, contract_set: frozenset[str]) -> bool:
    """True if *path* ends with a contract-governed filename."""
    p = path.replace("\\", "/")
    return any(p.endswith("/" + name) or p == name for name in contract_set)


def _has_code_files(changed_paths: list[str]) -> bool:
    """True if any changed path is a code file (.py/.sh/.ps1)."""
    for p in changed_paths:
        basename = p.replace("\\", "/").rsplit("/", 1)[-1] if "/" in p else p
        for ext in _CODE_EXTENSIONS:
            if basename.endswith(ext):
                return True
    return False


# ── Tier selection (pure function — AC-3) ──────────────────────────────────

@dataclass(frozen=True)
class TierDecision:
    """The gate tier and the reason for selecting it."""
    tier: int           # 0, 1, 2, or 3
    reason: str         # human-readable reason
    consumer_count: int  # number of resolved consumers (0 if unknown)

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "consumer_count": self.consumer_count,
        }


def select_tier(
    changed_paths: list[str],
    consumer_result: ConsumerResult,
    contract_governed_set: frozenset[str] = CONTRACT_GOVERNED_FILES,
) -> TierDecision:
    """Select a gate tier from the changed paths and consumer result.

    Pure function — no I/O.  Resolution happens in ``resolve_consumers``.

    Tier table:
      0 — docs/changelog only, no code (.py/.sh/.ps1)
      1 — changed symbol has zero resolved consumers
      2 — N resolved consumers
      3 — contract-governed file OR a shared path/schema OR oracle failed

    AC-4: a path or schema change selects tier 3, not tier 1.
    AC-5: when the oracle cannot run, result is tier 3, not tier 1.
    AC-9: a change touching any .py/.sh/.ps1 can never be tier 0.
    """
    # Tier 3 triggers (highest priority — checked first)
    # AC-5: oracle failed → tier 3
    if consumer_result.is_unknown:
        return TierDecision(
            tier=3,
            reason="consumer oracle could not run — defaulting to widest gate",
            consumer_count=0,
        )

    # AC-4: path/schema change → tier 3
    for p in changed_paths:
        if _is_path_or_schema_change(p):
            return TierDecision(
                tier=3,
                reason=f"path/schema change ({p}) has no import graph — tier 3",
                consumer_count=consumer_result.count,
            )

    # Contract-governed file → tier 3
    for p in changed_paths:
        if _is_contract_governed(p, contract_governed_set):
            return TierDecision(
                tier=3,
                reason=f"contract-governed file ({p}) — tier 3",
                consumer_count=consumer_result.count,
            )

    # AC-9: any code file → cannot be tier 0
    has_code = _has_code_files(changed_paths)

    # Tier 0: docs/changelog only (AC-9: no .py/.sh/.ps1)
    if not has_code:
        return TierDecision(
            tier=0,
            reason="docs/changelog only, no code files",
            consumer_count=0,
        )

    # Tier 1: zero consumers (oracle ran, found nothing)
    if consumer_result.is_zero:
        return TierDecision(
            tier=1,
            reason="changed symbol has zero resolved consumers",
            consumer_count=0,
        )

    # Tier 2: N consumers
    return TierDecision(
        tier=2,
        reason=f"{consumer_result.count} resolved consumer(s)",
        consumer_count=consumer_result.count,
    )


# ── Complement subtraction (AC-6, AC-8, AC-10) ─────────────────────────────

# The two floors that can NEVER be subtracted, regardless of complement.
# AC-8: "a complement that would empty the gate still runs both floors."
# Each floor has a primary keyword and optional aliases for matching.
FLOOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "baseline-compare": ("baseline-compare", "baseline"),
    "collection": ("collection", "collect-only", "--collect-only"),
}
FLOOR_COMMANDS: frozenset[str] = frozenset(FLOOR_KEYWORDS.keys())


@dataclass(frozen=True)
class SubtractionResult:
    """What was subtracted and why (AC-6: auditable after the fact)."""
    selected: tuple[str, ...]      # commands the gate would run
    already_run: tuple[str, ...]   # commands found in JSONL
    subtracted: tuple[str, ...]    # commands removed (overlap)
    kept: tuple[str, ...]          # commands that will actually run
    floors_protected: tuple[str, ...]  # floor commands that were kept

    def to_dict(self) -> dict:
        return {
            "selected": list(self.selected),
            "already_run": list(self.already_run),
            "subtracted": list(self.subtracted),
            "kept": list(self.kept),
            "floors_protected": list(self.floors_protected),
        }


def _extract_test_path(command: str) -> str | None:
    """Extract the test path from a pytest command for comparison.

    Returns the path argument (e.g. "skills/ilk-loop/tests/") or None
    if the command doesn't look like a pytest invocation.
    """
    import shlex
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    # Find the pytest path argument (first non-flag arg after "pytest")
    try:
        pytest_idx = next(i for i, p in enumerate(parts) if p.endswith("pytest"))
    except StopIteration:
        return None
    for part in parts[pytest_idx + 1:]:
        if not part.startswith("-"):
            return part
    return None


def _commands_match(selected_cmd: str, recorded_cmd: str) -> bool:
    """True if the recorded command covers the same work as the selected one.

    Reconciliation: a differently-flagged invocation of the same path is NOT
    the same work.  This repo's ``--timeout-method=signal`` requirement makes
    that a live concern — ``pytest tests/ --timeout-method=thread`` is NOT
    equivalent to ``pytest tests/ --timeout-method=signal``.
    """
    sel_path = _extract_test_path(selected_cmd)
    rec_path = _extract_test_path(recorded_cmd)
    if sel_path is None or rec_path is None:
        return False
    # Paths must match (normalize trailing slash)
    if sel_path.rstrip("/") != rec_path.rstrip("/"):
        return False
    # Flags must also match — different flags = different work
    sel_flags = sorted(p for p in shlex.split(selected_cmd) if p.startswith("-"))
    rec_flags = sorted(p for p in shlex.split(recorded_cmd) if p.startswith("-"))
    return sel_flags == rec_flags


def read_jsonl_commands(jsonl_path: Path) -> list[str]:
    """Read all recorded gate commands from a JSONL log.

    Returns a deduplicated list of commands that were recorded for any
    outcome (pass, fail, error, inconclusive).  Historical records without
    a ``command`` field are skipped (AC-2: readers tolerate absence).
    """
    import json as _json
    commands: list[str] = []
    seen: set[str] = set()
    try:
        with open(jsonl_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                # Gate records are nested in local_checks
                checks = rec.get("local_checks", [])
                if isinstance(checks, dict):
                    checks = [checks]
                for check in checks:
                    if isinstance(check, dict):
                        cmd = check.get("command", "")
                        if cmd and cmd not in seen:
                            seen.add(cmd)
                            commands.append(cmd)
    except (FileNotFoundError, OSError):
        pass
    return commands


def subtract_complement(
    selected_commands: list[str],
    recorded_commands: list[str],
    floor_commands: frozenset[str] = FLOOR_COMMANDS,
) -> SubtractionResult:
    """Subtract already-run commands from the selected gate.

    AC-6: reports what it subtracted and why, so the decision is auditable.
    AC-8: subtraction can never shrink the two floors (baseline-compare,
    collection) — those are applied after scoping, at whatever scope was
    chosen.

    ``floor_commands`` are matched by substring: if a selected command
    contains a floor keyword, it is always kept regardless of complement.
    """
    subtracted: list[str] = []
    kept: list[str] = []
    floors_protected: list[str] = []

    for sel_cmd in selected_commands:
        # AC-8: floors are never subtracted
        is_floor = any(
            kw in sel_cmd
            for keywords in FLOOR_KEYWORDS.values()
            for kw in keywords
        )
        if is_floor:
            kept.append(sel_cmd)
            floors_protected.append(sel_cmd)
            continue

        # Check if this command was already run
        already_covered = any(_commands_match(sel_cmd, rec) for rec in recorded_commands)
        if already_covered:
            subtracted.append(sel_cmd)
        else:
            kept.append(sel_cmd)

    return SubtractionResult(
        selected=tuple(selected_commands),
        already_run=tuple(recorded_commands),
        subtracted=tuple(subtracted),
        kept=tuple(kept),
        floors_protected=tuple(floors_protected),
    )
