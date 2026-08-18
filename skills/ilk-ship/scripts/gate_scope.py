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

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Contract-governed files (from plan_lint.py:123-136) ────────────────────

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
