#!/usr/bin/env python3
"""ilk-doctor: diagnose why nothing is running for a project.

Read-only diagnostic tool that walks a sequence of gates in order and prints
the FIRST blocker with its evidence, then stops.  A gate that cannot be
evaluated reports ``unknown``, never ``pass``.

Usage:
    python3 doctor.py --project-path <path>
    python3 doctor.py --project-path <path> --json
    python3 doctor.py --project-path <path> --sample-interval 5
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Gate result status values.
GateStatus = Literal["pass", "blocked", "unknown"]


@dataclass
class GateResult:
    """Result of a single gate check."""
    name: str
    status: GateStatus
    evidence: str
    artifact: str = ""  # what was consulted (path / command / count)


@dataclass
class DoctorReport:
    """Full report from a doctor run."""
    project_path: str
    gates: list[GateResult] = field(default_factory=list)
    verdict: str = ""

    def to_dict(self) -> dict:
        return {
            "project_path": self.project_path,
            "gates": [
                {
                    "name": g.name,
                    "status": g.status,
                    "evidence": g.evidence,
                    "artifact": g.artifact,
                }
                for g in self.gates
            ],
            "verdict": self.verdict,
        }


# ── Gate definitions ────────────────────────────────────────────────────────

def _gate_progress_over_time(project_data: Path, sample_interval: float) -> GateResult:
    """Gate 0: sample newest iter-NN.log twice, report byte/line deltas."""
    return GateResult(
        name="progress-over-time",
        status="unknown",
        evidence="not implemented",
        artifact="(no iter log sampled)",
    )


def _gate_master_status(plans_dir: Path) -> GateResult:
    """Gate 1: master status — draft / paused / shipped / none found."""
    return GateResult(
        name="master-status",
        status="unknown",
        evidence="not implemented",
        artifact=str(plans_dir),
    )


def _gate_subplan_statuses(plans_dir: Path) -> GateResult:
    """Gate 2: sub-plan statuses — all shipped / all blocked / nothing runnable."""
    return GateResult(
        name="subplan-statuses",
        status="unknown",
        evidence="not implemented",
        artifact=str(plans_dir),
    )


def _gate_blacklist(project_data: Path) -> GateResult:
    """Gate 3: blacklist / backoff via blacklist_status.py."""
    return GateResult(
        name="blacklist",
        status="unknown",
        evidence="not implemented",
        artifact=str(project_data / "runtime" / "launcher"),
    )


def _gate_lock_holders(project_data: Path) -> GateResult:
    """Gate 4: lock holders — lsof on runtime/launcher/run.lock."""
    return GateResult(
        name="lock-holders",
        status="unknown",
        evidence="not implemented",
        artifact=str(project_data / "runtime" / "launcher" / "run.lock"),
    )


def _gate_process_set(project_path: Path) -> GateResult:
    """Gate 5: process set — runners matching the project path."""
    return GateResult(
        name="process-set",
        status="unknown",
        evidence="not implemented",
        artifact=f"ps -eo pid,command | grep {project_path}",
    )


def _gate_sentinel_vs_reality(project_data: Path) -> GateResult:
    """Gate 6: sentinel vs reality — last-exit.json state compared against gate 5."""
    return GateResult(
        name="sentinel-vs-reality",
        status="unknown",
        evidence="not implemented",
        artifact=str(project_data / "runtime" / "launcher" / "last-exit.json"),
    )


def _gate_config_resolution(project_path: Path) -> GateResult:
    """Gate 7: resolved config vs .ilk-launch.json."""
    return GateResult(
        name="config-resolution",
        status="unknown",
        evidence="not implemented",
        artifact=str(project_path / ".ilk-launch.json"),
    )


# ── Gate walk ───────────────────────────────────────────────────────────────

GATE_ORDER = [
    "progress-over-time",
    "master-status",
    "subplan-statuses",
    "blacklist",
    "lock-holders",
    "process-set",
    "sentinel-vs-reality",
    "config-resolution",
]


def run_doctor(project_path: Path, sample_interval: float = 20.0) -> DoctorReport:
    """Walk gates in order; stop at the first blocker.

    Returns a DoctorReport with the gate results and a verdict line.
    """
    project_data = _resolve_project_data(project_path)
    plans_dir = _resolve_plans_dir(project_path)

    report = DoctorReport(project_path=str(project_path))

    gate_map = {
        "progress-over-time": lambda: _gate_progress_over_time(project_data, sample_interval),
        "master-status": lambda: _gate_master_status(plans_dir),
        "subplan-statuses": lambda: _gate_subplan_statuses(plans_dir),
        "blacklist": lambda: _gate_blacklist(project_data),
        "lock-holders": lambda: _gate_lock_holders(project_data),
        "process-set": lambda: _gate_process_set(project_path),
        "sentinel-vs-reality": lambda: _gate_sentinel_vs_reality(project_data),
        "config-resolution": lambda: _gate_config_resolution(project_path),
    }

    for gate_name in GATE_ORDER:
        result = gate_map[gate_name]()
        report.gates.append(result)

        if result.status == "blocked":
            report.verdict = f"blocked: {result.name} — {result.evidence}"
            return report

    # No blocker found — check if all passed or some are unknown.
    unknowns = [g for g in report.gates if g.status == "unknown"]
    if unknowns:
        names = ", ".join(g.name for g in unknowns)
        report.verdict = f"unknown: {len(unknowns)} gate(s) could not be evaluated ({names})"
    else:
        report.verdict = "pass: all gates clear"

    return report


def _resolve_project_data(project_path: Path) -> Path:
    """Resolve the .ilk-data directory for a project.

    Checks $ILK_DATA_HOME first, then ~/.ilk-data.
    """
    import os
    data_home = os.environ.get("ILK_DATA_HOME") or os.environ.get("ILK_DATA_DIR")
    if data_home:
        return Path(data_home)
    return Path.home() / ".ilk-data"


def _resolve_plans_dir(project_path: Path) -> Path:
    """Resolve the plans directory for a project.

    Tries external ~/.ilk-data first, then legacy in-tree docs/plans/.
    """
    # Try to derive project key from path.
    project_data = _resolve_project_data(project_path)
    # The project key is the path with slashes replaced by hyphens.
    key = str(project_path).replace("/", "-").lstrip("-")
    external = project_data / "projects" / key / "plans"
    if external.exists():
        return external
    # Legacy in-tree.
    legacy = project_path / "docs" / "plans"
    if legacy.exists():
        return legacy
    return external  # Return the external path even if it doesn't exist.


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose why nothing is running for a project."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Path to the project root."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit findings as structured JSON."
    )
    parser.add_argument(
        "--sample-interval", type=float, default=20.0,
        help="Seconds between the two progress-over-time samples (default: 20)."
    )
    args = parser.parse_args(argv)

    project_path = Path(args.project_path).resolve()
    if not project_path.exists():
        print(f"error: project path does not exist: {project_path}", file=sys.stderr)
        return 1

    report = run_doctor(project_path, sample_interval=args.sample_interval)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for gate in report.gates:
            print(f"  [{gate.status:>7}] {gate.name}: {gate.evidence}")
            if gate.artifact:
                print(f"           consulted: {gate.artifact}")
        print()
        print(f"verdict: {report.verdict}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
