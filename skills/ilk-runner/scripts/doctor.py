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

import ilk_paths  # noqa: E402

from plan_status import (  # noqa: E402
    extract_subplan_files,
    is_master_runnable_status,
    master_has_runnable,
    normalize_master_status,
    parse_frontmatter,
)
from loop_status import pick_active_master  # noqa: E402

sys.path.insert(0, str(_SKILL_ROOT / "ilk-watchdog" / "scripts"))
from blacklist_status import is_blacklisted  # noqa: E402


def _pick_master(masters: list[Path]) -> Path:
    """Choose the master whose state the verdict should reflect.

    ``pick_active_master`` is the canonical manual-path selector: exactly one
    ``active`` wins; otherwise the highest-priority ``queued``; otherwise the
    newest by mtime.  Using ``sorted(...)[0]`` instead — as this tool
    originally did — reports whichever master sorts first by filename, which
    on a real project meant answering "all work complete" from a master two
    months stale while an ``active`` one was mid-run.
    """
    chosen, _ = pick_active_master(masters, json_mode=True)
    return chosen

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

    # The ACTIVE master decides the verdict — not the first by filename.
    master_path = _pick_master(masters)
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

    master_path = _pick_master(masters)
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
    """Gate 3: blacklist / backoff via blacklist_status.is_blacklisted.

    Reuses the scheduler's own decision function so the doctor can never
    disagree with the component that actually parks the project.
    """
    artifact = str(project_data / "runtime" / "launcher" / "postmortems")
    try:
        state = is_blacklisted(project_data)
    except Exception as exc:  # noqa: BLE001 — a broken probe must not read as pass
        return GateResult(
            name="blacklist",
            status="unknown",
            evidence=f"cannot evaluate blacklist: {type(exc).__name__}: {exc}",
            artifact=artifact,
        )

    classification = state.get("classification") or "none"
    reason = state.get("reason") or "unspecified"

    if state.get("blacklisted"):
        expiry = state.get("expiry") or "no expiry recorded"
        return GateResult(
            name="blacklist",
            status="blocked",
            evidence=(
                f"project is parked: {reason} "
                f"(classification={classification}, expiry={expiry}) — "
                f"clear it with an ack via /ilk-resume"
            ),
            artifact=artifact,
        )

    ack = state.get("ack_cleared_at")
    detail = f"classification={classification}, reason={reason}"
    if ack:
        detail += f", resume-ack at {ack}"
    return GateResult(
        name="blacklist",
        status="pass",
        evidence=f"not blacklisted ({detail})",
        artifact=artifact,
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
        # "No sentinel" has two very different meanings, and calling both
        # "no run has started" is a false inference: on 2026-08-12 this gate
        # said exactly that about a project whose run had just finished 10
        # iterations (gate 0 had read iter-10.log from it moments earlier).
        # A completed run that left no sentinel is the record-erasure case
        # v0.9.57 fixed — it is a finding, not a pass.
        runs_dir = project_data / "logs" / "runs"
        run_ids: list[str] = []
        if runs_dir.is_dir():
            run_ids = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
        if not run_ids:
            return GateResult(
                name="sentinel-vs-reality",
                status="pass",
                evidence=f"no last-exit.json and no run directories — no run has started",
                artifact=artifact,
            )
        return GateResult(
            name="sentinel-vs-reality",
            status="unknown",
            evidence=(
                f"no last-exit.json, but {len(run_ids)} run dir(s) exist "
                f"(newest: {run_ids[-1]}) — a run executed and left no exit "
                f"sentinel, so its outcome cannot be read here"
            ),
            artifact=f"{artifact} (runs: {runs_dir})",
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


def _gate_scheduler_visibility(project_data: Path, plans_dir: Path) -> GateResult:
    """Gate 7: can the cross-project scheduler actually SEE this project?

    The gap this closes: on 2026-08-20 ilk-doctor returned
    ``verdict: pass: all gates clear`` for a project the scheduler had been
    unable to see for over an hour. Gate ``master-status`` reported
    ``active (runnable)`` and gate ``process-set`` reported ``no runner
    processes found`` — both true, and together they describe a **stranded**
    master: runnable, nothing running, nothing coming. No gate joined them, and
    none asked the scheduler's own scanner what it saw. The cause was a
    ``TypeError`` inside ``_scan_one_project`` that ``scan_projects`` swallows
    per-project by design, so the project was simply absent from every scan.

    Three outcomes, per the doctor's contract that a gate which cannot be
    evaluated reports ``unknown`` and never ``pass``:

    * the scan **raises** for this project -> ``blocked`` (the scheduler will
      never dispatch it, and nothing else reports the reason)
    * the scan returns **no entry** while the master is runnable -> ``blocked``
      (the stranded-active shape: runnable but not dispatchable)
    * the scan returns an entry, or returns none because the master is
      legitimately not runnable -> ``pass``

    Calls ``scheduler_scan`` in-process rather than shelling its CLI: the whole
    point of the gate is to distinguish "absent" from "raised", and only an
    in-process call yields the exception itself.
    """
    name = "scheduler-visibility"
    artifact = str(project_data)

    try:
        sys.path.insert(0, str(_SKILL_ROOT / "ilk-watchdog" / "scripts"))
        import scheduler_scan  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - import environment
        return GateResult(
            name=name,
            status="unknown",
            evidence=f"cannot import scheduler_scan: {type(exc).__name__}: {exc}",
            artifact=artifact,
        )

    # The scheduler only ever iterates ``<data root>/projects/*``. If this
    # project has no data dir there, "absent from the scan" carries no
    # information about strandedness — the gate cannot be evaluated, and the
    # doctor's contract is that such a gate reports `unknown`, never `pass`
    # (and never a false `blocked` either).
    if not project_data.is_dir():
        return GateResult(
            name=name,
            status="unknown",
            evidence=(
                "no project data directory — cannot tell 'stranded' from "
                "'never launched here'; the scheduler scans "
                "<data root>/projects/* only"
            ),
            artifact=artifact,
        )

    # Is the master runnable at all? Without this the gate cannot tell a
    # legitimately-finished project from a stranded one.
    master_runnable = False
    masters = sorted(plans_dir.glob("MASTER-*.md")) if plans_dir.is_dir() else []
    if masters:
        try:
            master_path = _pick_master(masters)
            text = master_path.read_text(encoding="utf-8-sig")
            status = normalize_master_status(parse_frontmatter(text).get("status", ""))
            master_runnable = bool(
                is_master_runnable_status(status)
                and master_has_runnable(master_path, plans_dir)
            )
        except OSError:
            master_runnable = False

    try:
        entry = scheduler_scan._scan_one_project(project_data)
    except Exception as exc:
        where = getattr(scheduler_scan, "_exc_origin", lambda _e: "?")(exc)
        return GateResult(
            name=name,
            status="blocked",
            evidence=(
                f"scheduler scan RAISED for this project — it is invisible to "
                f"the scheduler and will never be dispatched: "
                f"{type(exc).__name__}: {exc} @ {where}"
            ),
            artifact=artifact,
        )

    if entry is not None:
        return GateResult(
            name=name,
            status="pass",
            evidence=(
                f"scheduler scan sees this project "
                f"(oldest_queued_ts={entry.get('oldest_queued_ts')}, "
                f"has_active_master={entry.get('has_active_master')})"
            ),
            artifact=artifact,
        )

    if master_runnable:
        return GateResult(
            name=name,
            status="blocked",
            evidence=(
                "STRANDED: the master is runnable but the scheduler scan "
                "returns no entry for this project, so nothing will dispatch "
                "it. Run `scheduler_scan.py --scan-errors` for per-project "
                "scan failures"
            ),
            artifact=artifact,
        )

    return GateResult(
        name=name,
        status="pass",
        evidence=(
            "not dispatchable, and correctly so — no runnable master for the "
            "queue model to pick up"
        ),
        artifact=artifact,
    )


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
    "scheduler-visibility",
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
        "scheduler-visibility": lambda: _gate_scheduler_visibility(project_data, plans_dir),
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
    """Resolve the project's OWN data dir: ``<data root>/projects/<key>``.

    Delegates to ``ilk_paths`` — do NOT re-derive the key here.  The original
    implementation returned the data *root* and built the key with
    ``str(path).replace("/", "-")``, which produced two defects:

    * every gate consulted ``~/.ilk-data/logs/runs`` and
      ``~/.ilk-data/runtime/launcher/run.lock`` instead of the paths under
      ``projects/<key>/`` — and reported the resulting absence as ``pass``;
    * the key was not lowercased and had no length cap, so it diverged from
      the canonical key.  That only *appeared* to work because macOS APFS is
      case-insensitive; on a case-sensitive volume it resolved to nothing.

    ``ilk_paths.project_key`` also applies the 80-char cap with a sha1 suffix,
    which a hand-rolled key silently gets wrong for deep paths.
    """
    return ilk_paths.project_data_dir(ilk_paths.project_key(project_path))


def _resolve_plans_dir(project_path: Path) -> Path:
    """Resolve the plans directory for a project.

    Tries external ``~/.ilk-data/projects/<key>/plans`` first, then legacy
    in-tree ``docs/plans/``.
    """
    external = _resolve_project_data(project_path) / "plans"
    if external.exists():
        return external
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
