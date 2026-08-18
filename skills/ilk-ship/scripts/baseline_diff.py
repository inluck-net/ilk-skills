#!/usr/bin/env python3
"""Attribute red test results by node id against a named tag baseline.

Part of sub-plan `a-red-result-names-its-baseline`.

Two floors that never shrink:
  1. Baseline-compare by node id against the last tag.
  2. Collection (--collect-only catches the class that voids every other result).

This script implements floors 1 and 2.  Floor 3 (inconclusive/timeout) is
added in step 2.

AC-1: comparison by node id, not count.
AC-2: the ref is resolved (git describe --tags --abbrev=0), never assumed.
AC-3: missing baseline is "could not compare", distinct from "zero regressions".
AC-4: a collection error is a distinct, loud outcome that voids every other result.
AC-5: the collection floor runs at whatever scope was selected, including tier 0.
AC-8: baseline_red entries subtracted by node id; stale entries reported.
AC-9: baselines keyed by (tag, suite-invocation flags).

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple


# ── Types ────────────────────────────────────────────────────────────────────

class BaselineStatus(Enum):
    """Whether a baseline comparison succeeded."""
    FOUND = "found"
    MISSING = "missing"        # no tag, or no stored baseline for that tag
    COULD_NOT_COMPARE = "could_not_compare"  # alias, used in output


@dataclass(frozen=True)
class BaselineRef:
    """The resolved reference for a baseline comparison."""
    tag: str
    resolved: bool             # True if resolved via git describe
    status: BaselineStatus


@dataclass(frozen=True)
class NodeIdDiff:
    """Result of comparing current failures against a baseline by node id."""
    ref: BaselineRef
    new_failures: FrozenSet[str]       # in current, not in baseline
    inherited_failures: FrozenSet[str] # in both
    fixed: FrozenSet[str]              # in baseline, not in current
    current_count: int
    baseline_count: int
    search_space: int                  # total collected tests
    filtered: bool                     # True if -k/--deselect/path arg used

    @property
    def regression_count(self) -> int:
        return len(self.new_failures)

    @property
    def could_not_compare(self) -> bool:
        return self.ref.status == BaselineStatus.COULD_NOT_COMPARE


@dataclass(frozen=True)
class StaleExclusion:
    """A baseline_red entry that no longer fails."""
    node_id: str
    reason: str
    as_of: str


@dataclass(frozen=True)
class BaselineReport:
    """Full report: diff + stale exclusions + collection floor."""
    diff: NodeIdDiff
    stale_exclusions: Tuple[StaleExclusion, ...]
    denominator_statement: str   # "N regressions across M collected tests vs vX.Y.Z"
    collection_floor: Optional[CollectionFloor] = None

    def to_dict(self) -> dict:
        d = {
            "ref": self.diff.ref.tag,
            "ref_resolved": self.diff.ref.resolved,
            "could_not_compare": self.diff.could_not_compare,
            "regression_count": self.diff.regression_count,
            "new_failures": sorted(self.diff.new_failures),
            "inherited_failures": sorted(self.diff.inherited_failures),
            "fixed": sorted(self.diff.fixed),
            "current_count": self.diff.current_count,
            "baseline_count": self.diff.baseline_count,
            "search_space": self.diff.search_space,
            "filtered": self.diff.filtered,
            "stale_exclusions": [
                {"node_id": s.node_id, "reason": s.reason, "as_of": s.as_of}
                for s in self.stale_exclusions
            ],
            "denominator_statement": self.denominator_statement,
        }
        if self.collection_floor is not None:
            d["collection_floor"] = self.collection_floor.to_dict()
        return d


# ── Run verdict (AC-6) ───────────────────────────────────────────────────────

class Verdict(Enum):
    """Run verdict, mirroring the driver's vocabulary.

    run_ilk_loop_claude.sh:1014-1019 already tags all four:
    pass / fail / inconclusive / error.

    AC-6: a timeout is reported as inconclusive, naming the bound.
    A timeout is a fact about the bound, not about the code.
    """
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass(frozen=True)
class RunVerdict:
    """The verdict of a test run, with the bound if inconclusive."""
    verdict: Verdict
    bound_seconds: Optional[int] = None   # only set when verdict is INCONCLUSIVE
    message: str = ""

    @property
    def is_inconclusive(self) -> bool:
        return self.verdict == Verdict.INCONCLUSIVE

    def to_dict(self) -> dict:
        d = {"verdict": self.verdict.value}
        if self.bound_seconds is not None:
            d["bound_seconds"] = self.bound_seconds
        if self.message:
            d["message"] = self.message
        return d


# ── Collection floor (AC-4, AC-5) ────────────────────────────────────────────

@dataclass(frozen=True)
class CollectionError:
    """A single collection error from pytest --collect-only."""
    file_path: str
    error_type: str   # e.g. "TypeError", "ImportError"
    message: str


@dataclass(frozen=True)
class CollectionFloor:
    """Result of the collection floor check.

    AC-4: a collection error voids every other result from that run.
    AC-5: the floor runs at whatever scope was selected, including tier 0.
    A collection error in an unrelated directory does not block a docs-only
    release, but it must still be reported.
    """
    errors: Tuple[CollectionError, ...]
    collected_count: int       # tests successfully collected (0 if errors)
    has_errors: bool

    @property
    def voids_run(self) -> bool:
        """AC-4: a collection error voids every other result."""
        return self.has_errors

    def to_dict(self) -> dict:
        return {
            "has_errors": self.has_errors,
            "voids_run": self.voids_run,
            "collected_count": self.collected_count,
            "errors": [
                {"file_path": e.file_path, "error_type": e.error_type, "message": e.message}
                for e in self.errors
            ],
        }


# Patterns in pytest's --collect-only stderr/output for collection errors.
_COLLECT_ERROR_PREFIX = "ERROR collecting"
_COLLECT_ERROR_TYPE_RE = __import__("re").compile(
    r"ERROR collecting (.+)"
)
_COLLECT_EXCEPTION_RE = __import__("re").compile(
    r"E\s+(\w+(?:\.\w+)*)\s*:\s*(.*)"
)
_COLLECTED_COUNT_RE = __import__("re").compile(
    r"collected (\d+) items?"
)


def parse_collection_output(output: str) -> CollectionFloor:
    """Parse pytest --collect-only output for collection errors.

    AC-4: detect "ERROR collecting" lines and extract the error details.
    Returns a CollectionFloor with any errors found.
    """
    import re

    errors: list[CollectionError] = []
    collected_count = 0

    for line in output.splitlines():
        # Count collected tests
        m = _COLLECTED_COUNT_RE.search(line)
        if m:
            collected_count = int(m.group(1))

        # Detect collection errors
        if _COLLECT_ERROR_PREFIX in line:
            # Extract file path from "ERROR collecting <path>"
            m2 = _COLLECT_ERROR_TYPE_RE.search(line)
            file_path = m2.group(1).strip() if m2 else "unknown"

            # Look for the exception in subsequent lines (already seen or upcoming)
            # For now, record the file; the error type comes from E lines
            errors.append(CollectionError(
                file_path=file_path,
                error_type="CollectionError",
                message=line.strip(),
            ))

        # Detect exception type from "E TypeError: ..." lines
        if errors and line.strip().startswith("E "):
            m3 = _COLLECT_EXCEPTION_RE.match(line.strip())
            if m3:
                # Update the last error with the actual exception type
                last = errors[-1]
                errors[-1] = CollectionError(
                    file_path=last.file_path,
                    error_type=m3.group(1),
                    message=m3.group(2),
                )

    return CollectionFloor(
        errors=tuple(errors),
        collected_count=collected_count,
        has_errors=len(errors) > 0,
    )


def run_with_timeout(
    command: str,
    cwd: Optional[Path] = None,
    timeout: int = 120,
) -> Tuple[RunVerdict, str]:
    """Run a command with a timeout and return the verdict.

    AC-6: a timeout → inconclusive, naming the bound.
    Mirrors run_ilk_loop_claude.sh:997-998 (gtimeout exit 124 → inconclusive).

    Args:
        command: shell command to run.
        cwd: working directory.
        timeout: seconds before the run is killed.

    Returns:
        (RunVerdict, combined stdout+stderr output).
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + "\n" + result.stderr
        if result.returncode == 0:
            return RunVerdict(verdict=Verdict.PASS), output
        elif result.returncode == 124:
            # gtimeout's exit code for killed-by-timeout
            return RunVerdict(
                verdict=Verdict.INCONCLUSIVE,
                bound_seconds=timeout,
                message=f"run hit its timeout of {timeout}s",
            ), output
        elif result.returncode in (126, 127):
            # 126 = permission denied, 127 = command not found
            return RunVerdict(
                verdict=Verdict.ERROR,
                message=f"exit {result.returncode}: command not found or not executable",
            ), output
        else:
            return RunVerdict(verdict=Verdict.FAIL), output
    except subprocess.TimeoutExpired as e:
        output = (e.stdout or "") + "\n" + (e.stderr or "")
        return RunVerdict(
            verdict=Verdict.INCONCLUSIVE,
            bound_seconds=timeout,
            message=f"run hit its timeout of {timeout}s",
        ), output
    except Exception as e:
        return RunVerdict(
            verdict=Verdict.ERROR,
            message=f"{type(e).__name__}: {e}",
        ), ""


def run_collect_only(
    collect_command: str,
    cwd: Optional[Path] = None,
    timeout: int = 120,
) -> CollectionFloor:
    """Run pytest --collect-only and parse the result.

    AC-5: the collection floor runs at whatever scope was selected.

    Args:
        collect_command: the pytest --collect-only command to run.
        cwd: working directory for the command.
        timeout: seconds before the collection is considered hung.

    Returns:
        CollectionFloor with any errors found.
    """
    try:
        result = subprocess.run(
            collect_command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired:
        return CollectionFloor(
            errors=(CollectionError(
                file_path="<timeout>",
                error_type="TimeoutExpired",
                message=f"collection timed out after {timeout}s",
            ),),
            collected_count=0,
            has_errors=True,
        )
    except Exception as e:
        return CollectionFloor(
            errors=(CollectionError(
                file_path="<error>",
                error_type=type(e).__name__,
                message=str(e),
            ),),
            collected_count=0,
            has_errors=True,
        )

    return parse_collection_output(output)


# ── Ref resolution (AC-2) ───────────────────────────────────────────────────

def resolve_last_tag(cwd: Optional[Path] = None) -> Optional[str]:
    """Resolve the last tag via git describe --tags --abbrev=0.

    Returns None if no tags exist.
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def make_ref(tag: Optional[str]) -> BaselineRef:
    """Build a BaselineRef from a resolved tag."""
    if tag is None:
        return BaselineRef(tag="<no-tag>", resolved=False, status=BaselineStatus.COULD_NOT_COMPARE)
    return BaselineRef(tag=tag, resolved=True, status=BaselineStatus.FOUND)


# ── Baseline storage (AC-9) ─────────────────────────────────────────────────

def baseline_key(tag: str, suite_invocation: str) -> str:
    """Key baselines by (tag, suite-invocation flags).

    AC-9: --timeout-method=thread vs signal changes results, so flags are
    part of the key.
    """
    h = hashlib.sha256(suite_invocation.encode()).hexdigest()[:12]
    return f"{tag}__{h}"


def baseline_dir(project_root: Path) -> Path:
    """Where baselines are stored on disk."""
    return project_root / ".ilk-baselines"


def store_baseline(
    project_root: Path,
    tag: str,
    suite_invocation: str,
    node_ids: FrozenSet[str],
    search_space: int,
) -> Path:
    """Store a baseline for later comparison."""
    d = baseline_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    key = baseline_key(tag, suite_invocation)
    p = d / f"{key}.json"
    data = {
        "tag": tag,
        "suite_invocation": suite_invocation,
        "node_ids": sorted(node_ids),
        "search_space": search_space,
    }
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def load_baseline(
    project_root: Path,
    tag: str,
    suite_invocation: str,
) -> Optional[Tuple[FrozenSet[str], int]]:
    """Load a stored baseline. Returns (node_ids, search_space) or None."""
    d = baseline_dir(project_root)
    key = baseline_key(tag, suite_invocation)
    p = d / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return frozenset(data["node_ids"]), data.get("search_space", 0)
    except (json.JSONDecodeError, KeyError):
        return None


# ── Node-id diff (AC-1) ─────────────────────────────────────────────────────

def diff_by_node_id(
    current_failures: FrozenSet[str],
    baseline_failures: FrozenSet[str],
) -> Tuple[FrozenSet[str], FrozenSet[str], FrozenSet[str]]:
    """Compare by node id, not count.

    AC-1: same count, different node ids must NOT report zero regressions.

    Returns (new_failures, inherited_failures, fixed).
    """
    new = current_failures - baseline_failures
    inherited = current_failures & baseline_failures
    fixed = baseline_failures - current_failures
    return frozenset(new), frozenset(inherited), frozenset(fixed)


# ── Full comparison ──────────────────────────────────────────────────────────

def compare(
    current_failures: FrozenSet[str],
    search_space: int,
    filtered: bool,
    baseline_failures: Optional[FrozenSet[str]],
    baseline_search_space: int,
    ref: BaselineRef,
    suite_invocation: str = "",
) -> NodeIdDiff:
    """Full comparison: node-id diff with ref metadata.

    AC-3: missing baseline → could_not_compare, NOT zero regressions.
    """
    if baseline_failures is None:
        # AC-3: could not compare — distinct from zero regressions
        missing_ref = BaselineRef(
            tag=ref.tag,
            resolved=ref.resolved,
            status=BaselineStatus.COULD_NOT_COMPARE,
        )
        return NodeIdDiff(
            ref=missing_ref,
            new_failures=frozenset(),
            inherited_failures=frozenset(),
            fixed=frozenset(),
            current_count=len(current_failures),
            baseline_count=0,
            search_space=search_space,
            filtered=filtered,
        )

    new, inherited, fixed = diff_by_node_id(current_failures, baseline_failures)
    return NodeIdDiff(
        ref=ref,
        new_failures=new,
        inherited_failures=inherited,
        fixed=fixed,
        current_count=len(current_failures),
        baseline_count=len(baseline_failures),
        search_space=search_space,
        filtered=filtered,
    )


# ── Stale exclusion check (AC-8) ────────────────────────────────────────────

def check_stale_exclusions(
    baseline_red_entries: Sequence[dict],
    current_failures: FrozenSet[str],
) -> Tuple[StaleExclusion, ...]:
    """AC-8: report baseline_red entries that no longer fail.

    Each entry has node_id, reason, as_of.
    """
    stale = []
    for entry in baseline_red_entries:
        nid = entry.get("node_id", "")
        if nid and nid not in current_failures:
            stale.append(StaleExclusion(
                node_id=nid,
                reason=entry.get("reason", ""),
                as_of=entry.get("as_of", ""),
            ))
    return tuple(stale)


# ── Denominator statement (AC-7) ────────────────────────────────────────────

def format_denominator(
    diff: NodeIdDiff,
    filtered: bool,
) -> str:
    """AC-7: every negative carries its denominator.

    "0 regressions across 698 collected tests vs v0.9.63"
    "0 regressions — suite was not fully searched (-k / --deselect / path arg)"
    """
    if diff.could_not_compare:
        return f"could not compare — no baseline for {diff.ref.tag}"

    ref_label = f"vs {diff.ref.tag}"

    if filtered:
        # AC-7: filtered run must say the suite was not fully searched
        if diff.regression_count == 0:
            return (
                f"0 regressions — suite was not fully searched "
                f"(filtered run, {diff.search_space} tests collected) {ref_label}"
            )
        return (
            f"{diff.regression_count} regressions — suite was not fully searched "
            f"(filtered run, {diff.search_space} tests collected) {ref_label}"
        )

    if diff.regression_count == 0:
        return f"0 regressions across {diff.search_space} collected tests {ref_label}"

    return (
        f"{diff.regression_count} regressions across {diff.search_space} "
        f"collected tests {ref_label}"
    )


# ── High-level API ──────────────────────────────────────────────────────────

def run_baseline_diff(
    current_failures: FrozenSet[str],
    search_space: int,
    filtered: bool,
    suite_invocation: str,
    baseline_red_entries: Sequence[dict] = (),
    project_root: Optional[Path] = None,
    tag_override: Optional[str] = None,
    collection_floor: Optional[CollectionFloor] = None,
) -> BaselineReport:
    """Full baseline-diff pipeline.

    Args:
        current_failures: node ids of currently failing tests.
        search_space: total number of collected tests.
        filtered: True if -k/--deselect/path arg was used.
        suite_invocation: the pytest command string (part of baseline key).
        baseline_red_entries: from ship: block's baseline_red list.
        project_root: for loading stored baselines. Cwd if None.
        tag_override: use this tag instead of resolving via git describe.
        collection_floor: pre-computed collection floor result (optional).

    Returns:
        BaselineReport with diff, stale exclusions, denominator statement,
        and collection floor.
    """
    cwd = project_root or Path.cwd()

    # AC-2: resolve the ref
    tag = tag_override or resolve_last_tag(cwd)
    ref = make_ref(tag)

    # Try to load baseline
    baseline_data = None
    if tag is not None:
        baseline_data = load_baseline(cwd, tag, suite_invocation)

    baseline_failures = baseline_data[0] if baseline_data else None
    baseline_search_space = baseline_data[1] if baseline_data else 0

    # Compare
    diff = compare(
        current_failures=current_failures,
        search_space=search_space,
        filtered=filtered,
        baseline_failures=baseline_failures,
        baseline_search_space=baseline_search_space,
        ref=ref,
        suite_invocation=suite_invocation,
    )

    # AC-8: stale exclusions
    stale = check_stale_exclusions(baseline_red_entries, current_failures)

    # AC-7: denominator statement
    denom = format_denominator(diff, filtered)

    return BaselineReport(
        diff=diff,
        stale_exclusions=stale,
        denominator_statement=denom,
        collection_floor=collection_floor,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attribute red test results by node id against a named tag baseline."
    )
    parser.add_argument(
        "--failures-json",
        help="JSON array of current failure node ids (or - for stdin)",
    )
    parser.add_argument(
        "--search-space",
        type=int,
        required=True,
        help="Total number of collected tests",
    )
    parser.add_argument(
        "--filtered",
        action="store_true",
        help="Suite was not fully searched (-k / --deselect / path arg)",
    )
    parser.add_argument(
        "--suite-invocation",
        default="",
        help="The pytest command string (part of baseline key)",
    )
    parser.add_argument(
        "--baseline-red-json",
        help="JSON array of baseline_red entries from ship: block",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Project root (default: cwd)",
    )
    parser.add_argument(
        "--tag",
        help="Override tag instead of resolving via git describe",
    )
    parser.add_argument(
        "--store-baseline",
        action="store_true",
        help="Store the current failures as a baseline for this tag",
    )
    parser.add_argument(
        "--collect-command",
        help="pytest --collect-only command to check for collection errors",
    )
    parser.add_argument(
        "--collect-timeout",
        type=int,
        default=120,
        help="Timeout for --collect-command (default: 120s)",
    )
    args = parser.parse_args(argv)

    # Load failures
    if args.failures_json == "-":
        raw = sys.stdin.read()
    elif args.failures_json:
        raw = args.failures_json
    else:
        parser.error("--failures-json is required (or use - for stdin)")

    try:
        failures_list = json.loads(raw)
        current_failures = frozenset(failures_list)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON in --failures-json: {e}", file=sys.stderr)
        return 1

    # Load baseline_red entries
    baseline_red = []
    if args.baseline_red_json:
        try:
            baseline_red = json.loads(args.baseline_red_json)
        except json.JSONDecodeError as e:
            print(f"error: invalid JSON in --baseline-red-json: {e}", file=sys.stderr)
            return 1

    project_root = args.project_root or Path.cwd()

    # AC-4, AC-5: collection floor (optional)
    coll_floor = None
    if args.collect_command:
        coll_floor = run_collect_only(
            args.collect_command,
            cwd=project_root,
            timeout=args.collect_timeout,
        )
        if coll_floor.voids_run:
            print(f"collection error: {len(coll_floor.errors)} error(s) found", file=sys.stderr)
            for err in coll_floor.errors:
                print(f"  {err.file_path}: {err.error_type}: {err.message}", file=sys.stderr)

    # Optionally store baseline
    if args.store_baseline:
        tag = args.tag or resolve_last_tag(project_root)
        if tag:
            store_baseline(
                project_root, tag, args.suite_invocation,
                current_failures, args.search_space,
            )
            print(f"stored baseline for {tag}", file=sys.stderr)

    report = run_baseline_diff(
        current_failures=current_failures,
        search_space=args.search_space,
        filtered=args.filtered,
        suite_invocation=args.suite_invocation,
        baseline_red_entries=baseline_red,
        project_root=project_root,
        tag_override=args.tag,
        collection_floor=coll_floor,
    )

    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
