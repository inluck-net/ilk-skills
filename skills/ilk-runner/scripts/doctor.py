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
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Resolve the sibling ilk-loop/scripts directory for shared helpers.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent.parent  # skills/ilk-runner → skills → repo root
sys.path.insert(0, str(_SKILL_ROOT / "ilk-loop" / "scripts"))

from plan_status import (  # noqa: E402
    extract_subplan_files,
    is_master_runnable_status,
    master_has_runnable,
    normalize_master_status,
    parse_frontmatter,
)

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
    """Gate 0: sample newest iter-NN.log twice, report byte/line deltas.

    Growing ⇒ ``progressing`` (and the walk stops).  Static ⇒ ``quiet``
    (never ``stalled`` — a 15-minute foreground gate is byte-identical to
    a stall in any single sample).
    """
    import time

    runs_dir = project_data / "logs" / "runs"
    artifact = str(runs_dir)

    if not runs_dir.exists():
        return GateResult(
            name="progress-over-time",
            status="pass",
            evidence="no runs directory (no run has started)",
            artifact=artifact,
        )

    # Find the newest run directory by mtime.
    run_dirs = sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return GateResult(
            name="progress-over-time",
            status="pass",
            evidence="no run directories found",
            artifact=artifact,
        )

    latest_run = run_dirs[0]
    artifact = str(latest_run)

    # Find the newest iter log file (any extension: .log, .jsonl, .txt).
    iter_files = sorted(
        latest_run.glob("iter-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not iter_files:
        return GateResult(
            name="progress-over-time",
            status="pass",
            evidence=f"no iter logs in {latest_run.name}",
            artifact=artifact,
        )

    iter_file = iter_files[0]
    artifact = str(iter_file)

    # First sample.
    try:
        size1 = iter_file.stat().st_size
        lines1 = iter_file.read_text(encoding="utf-8-sig", errors="replace").count("\n")
    except OSError as exc:
        return GateResult(
            name="progress-over-time",
            status="unknown",
            evidence=f"cannot read iter log: {exc}",
            artifact=artifact,
        )

    # Wait for the sample interval.
    time.sleep(sample_interval)

    # Second sample.
    try:
        size2 = iter_file.stat().st_size
        lines2 = iter_file.read_text(encoding="utf-8-sig", errors="replace").count("\n")
    except OSError as exc:
        return GateResult(
            name="progress-over-time",
            status="unknown",
            evidence=f"cannot read iter log on second sample: {exc}",
            artifact=artifact,
        )

    delta_bytes = size2 - size1
    delta_lines = lines2 - lines1

    if delta_bytes > 0 or delta_lines > 0:
        return GateResult(
            name="progress-over-time",
            status="pass",
            evidence=f"progressing — {delta_bytes} bytes, {delta_lines} lines in {sample_interval}s "
                     f"(file: {iter_file.name})",
            artifact=artifact,
        )

    return GateResult(
        name="progress-over-time",
        status="pass",
        evidence=f"quiet — 0 bytes, 0 lines in {sample_interval}s "
                 f"(file: {iter_file.name}). This is not a stall — "
                 f"a long foreground gate is byte-identical to a stall in a single sample.",
        artifact=artifact,
    )


def _gate_master_status(plans_dir: Path) -> GateResult:
    """Gate 1: master status — draft / paused / shipped / none found."""
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    artifact = str(plans_dir)

    if not masters:
        return GateResult(
            name="master-status",
            status="blocked",
            evidence="no MASTER-*.md found in plans directory",
            artifact=artifact,
        )

    # Use the first master found (sorted by filename).
    master_path = masters[0]
    artifact = str(master_path)

    try:
        text = master_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return GateResult(
            name="master-status",
            status="unknown",
            evidence=f"cannot read master: {exc}",
            artifact=artifact,
        )

    fm = parse_frontmatter(text)
    raw_status = fm.get("status", "")
    status = normalize_master_status(raw_status)

    if not status:
        return GateResult(
            name="master-status",
            status="blocked",
            evidence="master has no status field in frontmatter",
            artifact=artifact,
        )

    if status == "draft":
        return GateResult(
            name="master-status",
            status="blocked",
            evidence=f"master status is 'draft' (not released to queue)",
            artifact=artifact,
        )

    if status == "paused":
        return GateResult(
            name="master-status",
            status="blocked",
            evidence=f"master status is 'paused'",
            artifact=artifact,
        )

    if status == "shipped":
        return GateResult(
            name="master-status",
            status="pass",
            evidence=f"master status is 'shipped' — all work complete",
            artifact=artifact,
        )

    # queued or active — master is runnable.
    return GateResult(
        name="master-status",
        status="pass",
        evidence=f"master status is '{status}' (runnable)",
        artifact=artifact,
    )


_RUNNABLE_SUBPLAN_STATUSES = {"pending", "in-progress"}


def _gate_subplan_statuses(plans_dir: Path) -> GateResult:
    """Gate 2: sub-plan statuses — all shipped / all blocked / nothing runnable."""
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    artifact = str(plans_dir)

    if not masters:
        return GateResult(
            name="subplan-statuses",
            status="unknown",
            evidence="no master found — cannot evaluate sub-plans",
            artifact=artifact,
        )

    master_path = masters[0]
    artifact = str(master_path)

    try:
        master_text = master_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return GateResult(
            name="subplan-statuses",
            status="unknown",
            evidence=f"cannot read master: {exc}",
            artifact=artifact,
        )

    registered = extract_subplan_files(master_text)
    if not registered:
        return GateResult(
            name="subplan-statuses",
            status="pass",
            evidence="master has no registered sub-plans",
            artifact=artifact,
        )

    # Collect statuses for every registered sub-plan.
    statuses: dict[str, str] = {}
    for fname in registered:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            statuses[fname] = "pending"  # missing → treat as pending
            continue
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            statuses[fname] = "pending"
            continue
        fm = parse_frontmatter(sub_text)
        statuses[fname] = fm.get("status", "pending").strip()

    # Classify.
    all_shipped = all(s == "shipped" for s in statuses.values())
    if all_shipped:
        return GateResult(
            name="subplan-statuses",
            status="pass",
            evidence=f"all {len(statuses)} sub-plan(s) shipped",
            artifact=artifact,
        )

    blocked = [f for f, s in statuses.items() if s not in _RUNNABLE_SUBPLAN_STATUSES and s != "shipped"]
    runnable = [f for f, s in statuses.items() if s in _RUNNABLE_SUBPLAN_STATUSES]

    if not runnable:
        blocked_names = ", ".join(blocked)
        return GateResult(
            name="subplan-statuses",
            status="blocked",
            evidence=f"no runnable sub-plans; blocked: {blocked_names}",
            artifact=artifact,
        )

    # There are runnable sub-plans — this gate passes.
    return GateResult(
        name="subplan-statuses",
        status="pass",
        evidence=f"{len(runnable)} runnable, {len(blocked)} blocked, "
                 f"{sum(1 for s in statuses.values() if s == 'shipped')} shipped",
        artifact=artifact,
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
    """Gate 4: lock holders — lsof on runtime/launcher/run.lock.

    Reports the LIVE holders, not the pid recorded in the file — the
    recorded pid is routinely dead while inherited-FD_CLOEXEC descendants
    (gtimeout, claude -p, tee) hold it.
    """
    lock_path = project_data / "runtime" / "launcher" / "run.lock"
    artifact = str(lock_path)

    if not lock_path.exists():
        return GateResult(
            name="lock-holders",
            status="pass",
            evidence="run.lock does not exist (no lock held)",
            artifact=artifact,
        )

    # Check if lsof is available.
    try:
        subprocess.run(["lsof", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return GateResult(
            name="lock-holders",
            status="unknown",
            evidence="lsof not available — cannot check lock holders",
            artifact=artifact,
        )

    try:
        result = subprocess.run(
            ["lsof", str(lock_path)],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="lock-holders",
            status="unknown",
            evidence="lsof timed out",
            artifact=artifact,
        )

    lines = [l for l in result.stdout.strip().splitlines() if l]
    # First line is the header; actual holders are lines 2+.
    holders = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2:
            pid = parts[1]
            cmd = " ".join(parts[8:]) if len(parts) > 8 else parts[0]
            holders.append((pid, cmd))

    if not holders:
        return GateResult(
            name="lock-holders",
            status="pass",
            evidence="run.lock exists but no live process holds it (stale lock)",
            artifact=artifact,
        )

    holder_strs = [f"pid {pid} ({cmd})" for pid, cmd in holders]
    return GateResult(
        name="lock-holders",
        status="blocked",
        evidence=f"run.lock held by: {', '.join(holder_strs)}",
        artifact=artifact,
    )


def _gate_process_set(project_path: Path) -> GateResult:
    """Gate 5: process set — runners matching the project path.

    Uses the shared ilk_project_runners helper from _ilk_pid.sh.
    """
    artifact = f"ps -eo pid,command | ilk_project_runners {project_path}"

    # Source the shared helper and call ilk_project_runners.
    pid_script = str(_SKILL_ROOT / "ilk-loop" / "scripts" / "_ilk_pid.sh")
    norm = str(project_path).rstrip("/")

    try:
        result = subprocess.run(
            ["bash", "-c",
             f'source "{pid_script}" && ilk_project_runners "{norm}"'],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="process-set",
            status="unknown",
            evidence="process check timed out",
            artifact=artifact,
        )
    except (FileNotFoundError, OSError) as exc:
        return GateResult(
            name="process-set",
            status="unknown",
            evidence=f"cannot run process check: {exc}",
            artifact=artifact,
        )

    pids = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]

    if not pids:
        return GateResult(
            name="process-set",
            status="pass",
            evidence="no runner processes found for this project",
            artifact=artifact,
        )

    return GateResult(
        name="process-set",
        status="pass",
        evidence=f"{len(pids)} runner process(es): {', '.join(pids)}",
        artifact=artifact,
    )


LIVE_SENTINEL_STATES = {"running"}


def _gate_sentinel_vs_reality(project_data: Path,
                               live_pids: list[str] | None = None) -> GateResult:
    """Gate 6: sentinel vs reality — last-exit.json state compared against live pids.

    A ``running`` sentinel with no live runner is the stale-sentinel case.
    """
    sentinel_path = project_data / "runtime" / "launcher" / "last-exit.json"
    artifact = str(sentinel_path)

    if not sentinel_path.exists():
        return GateResult(
            name="sentinel-vs-reality",
            status="pass",
            evidence="no last-exit.json (no run has started)",
            artifact=artifact,
        )

    try:
        text = sentinel_path.read_text(encoding="utf-8-sig")
        sentinel = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        return GateResult(
            name="sentinel-vs-reality",
            status="unknown",
            evidence=f"cannot read sentinel: {exc}",
            artifact=artifact,
        )

    state = sentinel.get("state", "")
    pid = sentinel.get("pid")
    run_id = sentinel.get("run_id", "")

    if state not in LIVE_SENTINEL_STATES:
        return GateResult(
            name="sentinel-vs-reality",
            status="pass",
            evidence=f"sentinel state is '{state}' (terminal) for run {run_id}",
            artifact=artifact,
        )

    # Sentinel says running — check against live process set.
    pids = live_pids or []
    pid_str = str(pid) if pid else ""

    if pid_str and pid_str in pids:
        return GateResult(
            name="sentinel-vs-reality",
            status="pass",
            evidence=f"sentinel says running (pid {pid}), process is alive",
            artifact=artifact,
        )

    if pids:
        return GateResult(
            name="sentinel-vs-reality",
            status="blocked",
            evidence=f"sentinel says running (pid {pid}), but live runners are: {', '.join(pids)} "
                     f"(sentinel pid not in set — stale sentinel or pid mismatch)",
            artifact=artifact,
        )

    return GateResult(
        name="sentinel-vs-reality",
        status="blocked",
        evidence=f"sentinel says running (pid {pid}, run {run_id}), but no live runner processes found — "
                 f"stale sentinel (runner crashed without finalizing)",
        artifact=artifact,
    )


DEFAULT_MAX_ITER = 100
DEFAULT_TIMEOUT = 30


def _gate_config_resolution(project_path: Path) -> GateResult:
    """Gate 7: resolved config vs .ilk-launch.json.

    Reads the launch config from the same locations the launcher uses
    (external plans dir, docs/plans/, project root) so the doctor cannot
    disagree with the launcher.
    """
    # Resolve the config file location (same precedence as launch.sh).
    config_path = _resolve_launch_config(project_path)
    artifact = str(config_path) if config_path else str(project_path / ".ilk-launch.json")

    if config_path is None or not config_path.exists():
        return GateResult(
            name="config-resolution",
            status="pass",
            evidence=f"no .ilk-launch.json found — defaults apply "
                     f"(max_iterations={DEFAULT_MAX_ITER}, iteration_timeout_min={DEFAULT_TIMEOUT})",
            artifact=artifact,
        )

    try:
        text = config_path.read_text(encoding="utf-8-sig")
        cfg = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        return GateResult(
            name="config-resolution",
            status="unknown",
            evidence=f"cannot read config: {exc}",
            artifact=artifact,
        )

    cfg_max = cfg.get("max_iterations", "")
    cfg_timeout = cfg.get("iteration_timeout_min", "")

    # Show what the launcher would resolve to.
    resolved_max = cfg_max if cfg_max else DEFAULT_MAX_ITER
    resolved_timeout = cfg_timeout if cfg_timeout else DEFAULT_TIMEOUT

    parts = []
    if cfg_max:
        parts.append(f"max_iterations={cfg_max}")
    else:
        parts.append(f"max_iterations=default({DEFAULT_MAX_ITER})")
    if cfg_timeout:
        parts.append(f"iteration_timeout_min={cfg_timeout}")
    else:
        parts.append(f"iteration_timeout_min=default({DEFAULT_TIMEOUT})")

    return GateResult(
        name="config-resolution",
        status="pass",
        evidence=f"config resolved: {', '.join(parts)}",
        artifact=artifact,
    )


def _resolve_launch_config(project_path: Path) -> Path | None:
    """Resolve .ilk-launch.json using the same precedence as launch.sh.

    1. External plans dir (~/.ilk-data/projects/<key>/plans/.ilk-launch.json)
    2. docs/plans/.ilk-launch.json
    3. <project>/.ilk-launch.json
    """
    plans_dir = _resolve_plans_dir(project_path)
    for candidate in [
        plans_dir / ".ilk-launch.json",
        project_path / "docs" / "plans" / ".ilk-launch.json",
        project_path / ".ilk-launch.json",
    ]:
        if candidate.exists():
            return candidate
    return None


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

    # live_pids is populated by gate 5 and consumed by gate 6.
    live_pids: list[str] = []

    gate_map = {
        "progress-over-time": lambda: _gate_progress_over_time(project_data, sample_interval),
        "master-status": lambda: _gate_master_status(plans_dir),
        "subplan-statuses": lambda: _gate_subplan_statuses(plans_dir),
        "blacklist": lambda: _gate_blacklist(project_data),
        "lock-holders": lambda: _gate_lock_holders(project_data),
        "process-set": lambda: _gate_process_set(project_path),
        "sentinel-vs-reality": lambda: _gate_sentinel_vs_reality(project_data, live_pids),
        "config-resolution": lambda: _gate_config_resolution(project_path),
    }

    for gate_name in GATE_ORDER:
        result = gate_map[gate_name]()
        report.gates.append(result)

        # Capture live pids from gate 5 for gate 6.
        if gate_name == "process-set" and result.status == "pass":
            # Parse pids from evidence like "2 runner process(es): 12345, 67890"
            for part in result.evidence.split(":"):
                pid_part = part.strip()
                if pid_part and all(c.isdigit() or c in ", " for c in pid_part):
                    live_pids = [p.strip() for p in pid_part.split(",") if p.strip().isdigit()]

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
