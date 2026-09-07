#!/usr/bin/env python3
"""suite_timing — compare pytest configurations by outcome set, then wall-clock.

The rule: outcome-set equality gates wall-clock.  A configuration that flips
any test is disqualified regardless of speed.  Only among configurations with
an identical node-id outcome set does wall-clock decide.

The runner is injectable so tests never launch a real suite.

Usage:
  python3 suite_timing.py [--project .] [--configs serial,-n4] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# ── Domain types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunOutcome:
    """Result of a single test node under a configuration."""
    node_id: str
    passed: bool  # True=PASSED, False=FAILED/SKIPPED/ERROR


@dataclass(frozen=True)
class ConfigResult:
    """Result of running a suite under one configuration."""
    name: str              # e.g. "serial", "-n 4"
    wall_clock: float      # seconds
    outcomes: frozenset[tuple[str, bool]]  # (node_id, passed) pairs
    invocations: tuple[str, ...]  # the pytest invocations used
    load_start: dict[str, float] | None = None  # load avg at run start
    load_end: dict[str, float] | None = None    # load avg at run end

    @classmethod
    def from_outcomes(
        cls,
        name: str,
        wall_clock: float,
        outcomes: Sequence[RunOutcome],
        invocations: Sequence[str] = (),
        load_start: dict[str, float] | None = None,
        load_end: dict[str, float] | None = None,
    ) -> "ConfigResult":
        return cls(
            name=name,
            wall_clock=wall_clock,
            outcomes=frozenset((o.node_id, o.passed) for o in outcomes),
            invocations=tuple(invocations),
            load_start=load_start,
            load_end=load_end,
        )


@dataclass(frozen=True)
class Comparison:
    """Result of comparing two configurations."""
    faster: str
    slower: str
    faster_wall_clock: float
    slower_wall_clock: float
    outcome_sets_equal: bool
    flips: frozenset[str]  # node-ids that differ in pass/fail


@dataclass(frozen=True)
class Recommendation:
    """The recommended configuration and why."""
    config: ConfigResult
    reason: str
    disqualified: tuple[str, ...]  # names of configs with flips


# ── Pure comparators ────────────────────────────────────────────────────────


def compare_configs(a: ConfigResult, b: ConfigResult) -> Comparison:
    """Compare two configurations by outcome set and wall-clock.

    Returns a Comparison indicating which is faster, whether their outcome
    sets are equal, and which node-ids flipped.
    """
    # Find node-ids present in both but with different pass/fail
    a_set = a.outcomes
    b_set = b.outcomes

    # Node-ids in both sets with different outcomes
    a_by_id = dict(a_set)
    b_by_id = dict(b_set)
    all_ids = set(a_by_id.keys()) | set(b_by_id.keys())

    flips: set[str] = set()
    for nid in all_ids:
        a_passed = a_by_id.get(nid)
        b_passed = b_by_id.get(nid)
        if a_passed is not None and b_passed is not None and a_passed != b_passed:
            flips.add(nid)

    outcome_sets_equal = len(flips) == 0

    if a.wall_clock <= b.wall_clock:
        faster, slower = a.name, b.name
        faster_wc, slower_wc = a.wall_clock, b.wall_clock
    else:
        faster, slower = b.name, a.name
        faster_wc, slower_wc = b.wall_clock, a.wall_clock

    return Comparison(
        faster=faster,
        slower=slower,
        faster_wall_clock=faster_wc,
        slower_wall_clock=slower_wc,
        outcome_sets_equal=outcome_sets_equal,
        flips=frozenset(flips),
    )


def recommend(configs: Sequence[ConfigResult]) -> Recommendation:
    """Recommend the best configuration.

    Rule: outcome-set equality gates wall-clock.
    1. Find the largest group of configs with identical outcome sets.
    2. Among that group, pick the fastest.
    3. If all groups have size 1 (no two configs share outcomes), pick the
       one with fewest flips relative to the first config (then fastest).
    """
    if not configs:
        raise ValueError("no configurations to compare")

    # Group configs by outcome set.
    # Two configs are in the same group if their outcome sets are identical.
    groups: list[list[ConfigResult]] = []
    assigned: set[str] = set()

    for cfg in configs:
        if cfg.name in assigned:
            continue
        group = [cfg]
        assigned.add(cfg.name)
        for other in configs:
            if other.name in assigned:
                continue
            if compare_configs(cfg, other).outcome_sets_equal:
                group.append(other)
                assigned.add(other.name)
        groups.append(group)

    # Pick the largest group (ties broken by fastest member).
    groups.sort(key=lambda g: (-len(g), min(c.wall_clock for c in g)))
    best_group = groups[0]

    # Check if the best group is "clean" — its outcomes match the first config
    # (which should be serial if present).  If the first config is itself in
    # the best group, the group is clean by definition.
    reference = configs[0]
    group_matches_reference = any(
        compare_configs(reference, c).outcome_sets_equal for c in best_group
    )

    if len(best_group) > 1 and group_matches_reference:
        # Multiple configs with identical outcomes matching reference — pick fastest.
        best = min(best_group, key=lambda c: c.wall_clock)
        disqualified = tuple(
            c.name for g in groups[1:] for c in g
        )
        return Recommendation(
            config=best,
            reason=f"fastest among {len(best_group)} config(s) with identical outcome sets",
            disqualified=disqualified,
        )

    # No group matches the reference, or all groups have size 1.
    # Pick fewest flips relative to reference, then fastest.
    scored: list[tuple[int, float, ConfigResult]] = []
    for cfg in configs:
        if cfg.name == reference.name:
            scored.append((0, cfg.wall_clock, cfg))
        else:
            comp = compare_configs(reference, cfg)
            scored.append((len(comp.flips), cfg.wall_clock, cfg))

    scored.sort(key=lambda t: (t[0], t[1]))
    best = scored[0][2]
    return Recommendation(
        config=best,
        reason=f"fewest flips ({scored[0][0]}) relative to reference; no clean group found",
        disqualified=tuple(c.name for _, _, c in scored[1:]),
    )

    if clean:
        best = min(clean, key=lambda c: c.wall_clock)
        return Recommendation(
            config=best,
            reason=f"fastest among {len(clean)} config(s) with identical outcome sets",
            disqualified=tuple(c.name for _, _, c in dirty),
        )

    # All dirty — pick fewest flips, then fastest
    dirty.sort(key=lambda t: (t[0], t[1]))
    fewest_flips = dirty[0][0]
    best = dirty[0][2]
    return Recommendation(
        config=best,
        reason=f"fewest flips ({fewest_flips}) among all configs; all have outcome differences",
        disqualified=tuple(c.name for _, _, c in dirty[1:]),
    )


# ── Environment capture ────────────────────────────────────────────────────


def capture_environment(project_path: Path) -> dict[str, Any]:
    """Capture the environment for the timing artifact."""
    env: dict[str, Any] = {
        "host": platform.node(),
        "platform": f"{platform.system()} {platform.release()}",
    }

    # hw.ncpu (macOS) or nproc (Linux)
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.ncpu"],
                capture_output=True, text=True, timeout=5,
            )
            env["ncpu"] = int(result.stdout.strip()) if result.returncode == 0 else None
        else:
            result = subprocess.run(
                ["nproc"],
                capture_output=True, text=True, timeout=5,
            )
            env["ncpu"] = int(result.stdout.strip()) if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        env["ncpu"] = None

    # Python version
    env["python"] = sys.version.split()[0]

    # pytest / xdist versions
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        env["pytest_version"] = result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        env["pytest_version"] = None

    try:
        import pytest_xdist  # type: ignore[import-untyped]
        env["xdist_version"] = getattr(pytest_xdist, "__version__", "unknown")
    except ImportError:
        env["xdist_version"] = None

    # HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        env["head"] = result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        env["head"] = None

    # Load average
    try:
        load1, load5, load15 = [round(x, 2) for x in platform.getloadavg()]
        env["load_avg"] = {"1m": load1, "5m": load5, "15m": load15}
    except OSError:
        env["load_avg"] = None

    return env


# ── Invocation resolution ───────────────────────────────────────────────────


def resolve_serial_invocation(project_path: Path) -> str:
    """Resolve the serial invocation from ship.suite via ship_audit.

    Reuses the same construction path as batch_gate._run_gate_inner so
    the validator and the writer cannot drift.
    """
    try:
        _scripts_dir = str(Path(__file__).resolve().parent)
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        from ship_audit import _resolve_expected_invocation
        invocation = _resolve_expected_invocation(project_path)
        if invocation:
            return invocation
    except (ImportError, Exception):
        pass

    # Fallback: default pytest invocation
    return "python3 -m pytest --timeout=60 --timeout-method=signal"


def build_default_configs(
    serial_invocation: str,
) -> list[tuple[str, str, list[str]]]:
    """Build the default configurations to compare.

    Returns list of (name, description, extra_args) tuples.
    The serial invocation is the base; extra_args are appended.
    """
    return [
        ("serial", "serial (no xdist)", []),
        ("-n 2", "2 workers", ["-n", "2"]),
        ("-n 4", "4 workers", ["-n", "4"]),
        ("-n auto", "auto workers", ["-n", "auto"]),
        ("-n 2 --dist loadfile", "2 workers, loadfile scheduler", ["-n", "2", "--dist", "loadfile"]),
        ("-n 4 --dist loadfile", "4 workers, loadfile scheduler", ["-n", "4", "--dist", "loadfile"]),
    ]


# ── Runner protocol ─────────────────────────────────────────────────────────

# The runner is a callable that takes (invocation: str, cwd: Path) and returns
# (exit_code: int, stdout: str, wall_clock: float).
#
# For tests, inject a fake runner.  For real runs, use _default_runner.


Runner = Callable[[str, Path], tuple[int, str, float]]


def _default_runner(invocation: str, cwd: Path) -> tuple[int, str, float]:
    """Run a pytest invocation and capture output."""
    import time
    start = time.monotonic()
    try:
        result = subprocess.run(
            invocation.split(),
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=3600,  # 1 hour max per run
        )
        elapsed = time.monotonic() - start
        return result.returncode, result.stdout, elapsed
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return -1, "", elapsed


def _capture_load() -> dict[str, float] | None:
    """Capture current load average."""
    try:
        load1, load5, load15 = [round(x, 2) for x in platform.getloadavg()]
        return {"1m": load1, "5m": load5, "15m": load15}
    except OSError:
        return None


def parse_outcomes(stdout: str) -> list[RunOutcome]:
    """Parse pytest -v output into RunOutcome objects.

    Looks for lines like:
      tests/test_foo.py::test_bar PASSED
      tests/test_foo.py::test_baz FAILED
    """
    outcomes: list[RunOutcome] = []
    for line in stdout.splitlines():
        line = line.strip()
        if " PASSED" in line:
            node_id = line.split(" PASSED")[0].strip()
            outcomes.append(RunOutcome(node_id=node_id, passed=True))
        elif " FAILED" in line:
            node_id = line.split(" FAILED")[0].strip()
            outcomes.append(RunOutcome(node_id=node_id, passed=False))
    return outcomes


def run_config(
    name: str,
    base_invocation: str,
    extra_args: list[str],
    cwd: Path,
    runner: Runner = _default_runner,
) -> ConfigResult:
    """Run one configuration and return its result."""
    invocation = base_invocation
    if extra_args:
        invocation = f"{base_invocation} {' '.join(extra_args)}"

    load_start = _capture_load()
    exit_code, stdout, wall_clock = runner(invocation, cwd)
    load_end = _capture_load()
    outcomes = parse_outcomes(stdout)

    return ConfigResult.from_outcomes(
        name=name,
        wall_clock=wall_clock,
        outcomes=outcomes,
        invocations=[invocation],
        load_start=load_start,
        load_end=load_end,
    )


# ── Artifact writing ────────────────────────────────────────────────────────


def write_artifact(
    results: list[ConfigResult],
    recommendation: Recommendation,
    env: dict[str, Any],
    output_path: Path,
) -> None:
    """Write a dated timing artifact."""
    lines = [
        "# Suite timing — configuration comparison",
        "",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Environment",
        "",
        f"| field | value |",
        f"|---|---|",
        f"| host | `{env.get('host', 'unknown')}` |",
        f"| platform | {env.get('platform', 'unknown')} |",
        f"| ncpu | {env.get('ncpu', 'unknown')} |",
        f"| python | {env.get('python', 'unknown')} |",
        f"| pytest | {env.get('pytest_version', 'unknown')} |",
        f"| xdist | {env.get('xdist_version', 'not installed')} |",
        f"| HEAD | `{env.get('head', 'unknown')}` |",
    ]

    load = env.get("load_avg")
    if load:
        lines.append(f"| load avg | {load['1m']} / {load['5m']} / {load['15m']} (1m/5m/15m) |")

    lines.extend([
        "",
        "## Results",
        "",
        "| config | wall-clock (s) | vs serial | outcome set | load start (1m/5m/15m) | load end (1m/5m/15m) |",
        "|---|---|---|---|---|---|",
    ])

    serial_wc = results[0].wall_clock if results else 0
    serial_outcomes = results[0].outcomes if results else frozenset()

    for r in results:
        ratio = f"{r.wall_clock / serial_wc:.2f}×" if serial_wc > 0 else "—"
        if r.name == "serial":
            ratio = "—"
        eq = "identical" if r.outcomes == serial_outcomes else "DIFFERS"
        ls = r.load_start
        le = r.load_end
        ls_str = f"{ls['1m']}/{ls['5m']}/{ls['15m']}" if ls else "—"
        le_str = f"{le['1m']}/{le['5m']}/{le['15m']}" if le else "—"
        lines.append(f"| {r.name} | {r.wall_clock:.2f} | {ratio} | {eq} | {ls_str} | {le_str} |")

    lines.extend([
        "",
        "## Recommendation",
        "",
        f"**{recommendation.config.name}** — {recommendation.reason}",
    ])

    if recommendation.disqualified:
        lines.append("")
        lines.append(f"Disqualified: {', '.join(recommendation.disqualified)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Compare pytest configurations by outcome set, then wall-clock.",
    )
    ap.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="project root (default: cwd)",
    )
    ap.add_argument(
        "--configs",
        type=str,
        default=None,
        help="comma-separated config names to run (default: all built-in)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output artifact path (default: tests/baselines/suite-timing-<date>.md)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be run without running it",
    )
    args = ap.parse_args(argv)

    project = args.project.resolve()

    # Resolve serial invocation
    serial_invocation = resolve_serial_invocation(project)
    print(f"Serial invocation: {serial_invocation}")

    # Build configs
    all_configs = build_default_configs(serial_invocation)

    if args.configs:
        names = {n.strip() for n in args.configs.split(",")}
        all_configs = [(n, d, a) for n, d, a in all_configs if n in names]

    if args.dry_run:
        print("Would run:")
        for name, desc, extra in all_configs:
            inv = f"{serial_invocation} {' '.join(extra)}" if extra else serial_invocation
            print(f"  {name}: {inv}")
        return 0

    # Capture environment
    env = capture_environment(project)
    print(f"Host: {env['host']}, ncpu: {env['ncpu']}")

    # Run each config
    results: list[ConfigResult] = []
    for name, desc, extra in all_configs:
        print(f"\nRunning {name} ({desc})...")
        result = run_config(name, serial_invocation, extra, project)
        results.append(result)
        print(f"  wall-clock: {result.wall_clock:.2f}s, "
              f"outcomes: {len(result.outcomes)}")

    # Compare and recommend
    rec = recommend(results)
    print(f"\nRecommendation: {rec.config.name}")
    print(f"  {rec.reason}")

    # Write artifact
    output = args.output
    if output is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output = project / "tests" / "baselines" / f"suite-timing-{date_str}.md"

    write_artifact(results, rec, env, output)
    print(f"\nArtifact written to: {output}")

    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
