"""Auto-planner: idle-window planning for the top improvement candidate.

Part of sub-plan an-idle-window-plans-the-top-candidate.

When the ilk-skills queue has had no runnable master for N consecutive
scheduler cycles, ``tick`` starts one detached ``plan`` session on the
manager home for the top-ranked candidate (escalations first).

Stdlib only.  Every external command, the clock and the spawner are
injectable parameters so tests can stub them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent  # repo root
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"

if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))
if str(_WATCHDOG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_SCRIPTS))

from ilk_audit import write_audit, write_event  # noqa: E402
from ilk_paths import external_plans_dir, ilk_data_root, project_key  # noqa: E402
from plan_status import (  # noqa: E402
    extract_subplan_files,
    master_has_runnable,
    normalize_master_status,
    parse_frontmatter,
)
from promote_next_master import write_status  # noqa: E402
from triage_backlog import read_backlog_strict  # noqa: E402

from autoplan_rails import (  # noqa: E402
    check_master,
    load_project_kernel,
    mark_candidate,
    mark_master,
    rank,
    screen_candidate,
)
import autoplan_projects  # noqa: E402  (NOT kernel: extra projects + their rows)
from scheduler_scan import resolve_repo_path  # noqa: E402

# ── constants (judgment calls — see MASTER) ────────────────────────────────

IDLE_CYCLES_N = 3  # judgment call: 3 because debounce only; the draft-authoring and queued-master checks already detect owner work; wrong if an auto-planned master collides with a session-planned one more than once a week
REFUSED_BACKOFF_MIN = 60  # judgment call: 60 min because a refused start costs minutes (04:19: 2.5 min) and the per-candidate cap of 2 attempts bounds retries; wrong if refused starts exceed MAX_STARTS_PER_DAY on most days
MAX_STARTS_PER_DAY = 8  # judgment call: 8 because the planner home is an official-quota account; 8 sessions ≤ 8 × 45 min worst case; wrong if the triage home hits its quota on a capped day
PLAN_TIMEOUT_S = 2700
INIT_TIMEOUT_S = 120
RECENT_AUTHORING_MIN = 20  # judgment call: 20 min because drafts are what a session is writing; queued/active are already counted and shipped ones are the runner's own status writes; wrong if a planner start collides with a session writing a plan
MAX_CONSECUTIVE_DRAFTS = 2
CHECK_TIMEOUT_S = 300  # lint/preflight budget; tests monkeypatch it (AC-1)

# Refusal reasons that should only write one audit row per consecutive streak.
_DEDUP_REASONS = {"bad-home"}


# ── helpers ────────────────────────────────────────────────────────────────


def _now() -> float:
    """Current time (injectable for tests)."""
    return time.time()


def _read_json(path: Path) -> Any:
    """Read and parse a JSON file.  Returns None on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: Any) -> None:
    """Atomic JSON write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _find_toolkit_project(data_root: Path) -> tuple[str | None, str | None]:
    """Find the toolkit project data dir.

    Returns (project_key, error).  project_key is set on success;
    error is set on failure (not-enabled, ambiguous-toolkit).
    """
    projects_dir = data_root / "projects"
    if not projects_dir.is_dir():
        return None, "not-enabled"

    matches = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        repo = resolve_repo_path(d, d.name)
        if repo is None:
            continue
        launch_file = Path(repo) / ".ilk-launch.json"
        launch = _read_json(launch_file)
        if launch is None:
            continue
        if not (launch.get("autoplan", {}).get("enabled") is True):
            continue
        plan_cmd = Path(repo) / "commands" / "ilk-plan.md"
        if not plan_cmd.is_file():
            continue
        matches.append(d.name)

    if not matches:
        return None, "not-enabled"
    if len(matches) > 1:
        return None, "ambiguous-toolkit"
    return matches[0], None


def _is_busy(data_root: Path) -> bool:
    """Check if the system is busy (resets idle count to 0).

    train.lock is fleet-wide (a train anywhere is busy).
    running.pid, runnable queued/active master and recent-authoring checks
    apply only to the toolkit project (other projects' loops no longer block
    self-improvement; the release train waits for a quiet fleet itself (R2b)).
    """
    projects_dir = data_root / "projects"
    if not projects_dir.is_dir():
        return False

    # Fleet-wide: train.lock anywhere is busy
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        train_lock = d / "runtime" / "release" / "train.lock"
        if train_lock.is_file():
            try:
                pid = int(train_lock.read_text(encoding="utf-8").strip())
                if _pid_alive(pid):
                    return True
            except (ValueError, OSError):
                pass

    # Toolkit-scoped checks
    toolkit_key, _ = _find_toolkit_project(data_root)
    if toolkit_key is None:
        return False

    toolkit_dir = projects_dir / toolkit_key

    # Check running.pid (toolkit only)
    running_pid = toolkit_dir / "runtime" / "launcher" / "running.pid"
    if running_pid.is_file():
        try:
            pid = int(running_pid.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                return True
        except (ValueError, OSError):
            pass

    # Check for queued/active master with runnable sub-plan (toolkit only)
    repo = resolve_repo_path(toolkit_dir, toolkit_dir.name)
    if repo is None:
        return False
    plans_dir = _find_plans_dir_for_project(toolkit_dir, repo)
    if plans_dir is None:
        return False

    for master in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            fm = parse_frontmatter(master.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        # Skip auto-planned masters — they're checked at threshold
        if fm.get("auto_planned", "").strip().lower() == "true":
            continue
        status = normalize_master_status(fm.get("status", ""))
        if status in ("active", "queued"):
            if master_has_runnable(master, plans_dir):
                return True

    # Check recently modified DRAFT master (session authoring, toolkit only)
    # Only draft masters count: queued/active are already counted above and
    # shipped ones are the runner's own status writes.
    cutoff = _now() - RECENT_AUTHORING_MIN * 60
    for master in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            if master.stat().st_mtime > cutoff:
                try:
                    fm = parse_frontmatter(master.read_text(encoding="utf-8-sig"))
                    if fm.get("auto_planned", "").strip().lower() == "true":
                        continue
                    status = normalize_master_status(fm.get("status", ""))
                    if status == "draft":
                        return True
                except OSError:
                    pass
        except OSError:
            continue

    return False


def _find_plans_dir_for_project(project_dir: Path, repo: str) -> Path | None:
    """Find the plans dir for a project, preferring external."""
    # External plans dir
    key = project_dir.name
    data_root = project_dir.parent.parent  # projects/<key>/../..
    ext = data_root / "projects" / key / "plans"
    if ext.is_dir():
        return ext
    # Fallback: data_root/plans (common in tests and simple setups)
    root_plans = data_root / "plans"
    if root_plans.is_dir():
        return root_plans
    # Legacy in-tree
    in_tree = Path(repo) / "docs" / "plans"
    if in_tree.is_dir():
        return in_tree
    return None


def _has_auto_planned_in_flight(data_root: Path) -> bool:
    """Check if there's a queued/active auto-planned master."""
    projects_dir = data_root / "projects"
    if not projects_dir.is_dir():
        return False

    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        repo = resolve_repo_path(d, d.name)
        if repo is None:
            continue
        plans_dir = _find_plans_dir_for_project(d, repo)
        if plans_dir is None:
            continue

        for master in sorted(plans_dir.glob("MASTER-*.md")):
            try:
                fm = parse_frontmatter(master.read_text(encoding="utf-8-sig"))
            except OSError:
                continue
            if fm.get("auto_planned", "").strip().lower() == "true":
                status = normalize_master_status(fm.get("status", ""))
                if status in ("queued", "active"):
                    return True
    return False


def _resolve_claude_cmd() -> list[str]:
    """Resolve the claude command path."""
    # Check PATH first
    claude = shutil.which("claude")
    if claude:
        return [claude]
    # Check ~/.local/bin
    local_bin = Path.home() / ".local" / "bin" / "claude"
    if local_bin.is_file():
        return [str(local_bin)]
    return ["claude"]


def _resolve_planner_home() -> str:
    """The home a ``plan-issue`` planner session runs on.

    The same precedence the tick gets from the scheduler's
    ``maybe_tick_autoplan`` (``scheduler.sh``): ``$ILK_AUTOPLAN_HOME``, else
    ``~/.claude-triage`` when that directory exists, else ``$CLAUDE_MANAGER_HOME``,
    else ``~/.claude-manager``.  The manager home is last because it stays GLM
    (verification runs there) and ``_plan_session`` refuses a GLM/MiMo init
    event.
    """
    env = os.environ.get("ILK_AUTOPLAN_HOME")
    if env:
        return env
    triage = Path.home() / ".claude-triage"
    if triage.is_dir():
        return str(triage)
    return os.environ.get("CLAUDE_MANAGER_HOME",
                          str(Path.home() / ".claude-manager"))


def _read_init_event(process: subprocess.Popen, timeout: float) -> tuple[dict | str, str]:
    """Read the init event from a stream-json process.

    Returns (init_event_or_reason, leftover_stdout).
    """
    import select

    deadline = _now() + timeout
    buffer = ""

    while _now() < deadline:
        remaining = deadline - _now()
        if remaining <= 0:
            break

        # Use select to wait for data with timeout
        try:
            ready, _, _ = select.select([process.stdout], [], [], min(remaining, 0.5))
        except (ValueError, OSError):
            break

        if not ready:
            continue

        try:
            chunk = process.stdout.read(4096)
        except (IOError, OSError):
            break

        if not chunk:
            break

        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")

        buffer += chunk

        # Try to parse complete JSON lines
        lines = buffer.split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (event.get("type") == "system"
                    and event.get("subtype") == "init"):
                # Return leftover data from buffer after the init line
                leftover = "\n".join(lines[i + 1:])
                return event, leftover

    return "no-init", ""


def _stream_output(process: subprocess.Popen, timeout: float, leftover: str = "") -> tuple[str, str]:
    """Read remaining output from process until timeout or exit."""
    import select

    deadline = _now() + timeout
    stdout_parts = [leftover] if leftover else []
    stderr_parts = []

    while _now() < deadline:
        remaining = deadline - _now()
        if remaining <= 0:
            break

        try:
            ready, _, _ = select.select(
                [process.stdout, process.stderr], [], [], min(remaining, 0.5)
            )
        except (ValueError, OSError):
            break

        for fd in ready:
            try:
                chunk = fd.read(4096)
            except (IOError, OSError):
                continue
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            if fd == process.stdout:
                stdout_parts.append(chunk)
            else:
                stderr_parts.append(chunk)

        if process.poll() is not None:
            # Read remaining data
            try:
                remaining_out = process.stdout.read()
                if remaining_out:
                    if isinstance(remaining_out, bytes):
                        remaining_out = remaining_out.decode("utf-8", errors="replace")
                    stdout_parts.append(remaining_out)
            except (IOError, OSError):
                pass
            try:
                remaining_err = process.stderr.read()
                if remaining_err:
                    if isinstance(remaining_err, bytes):
                        remaining_err = remaining_err.decode("utf-8", errors="replace")
                    stderr_parts.append(remaining_err)
            except (IOError, OSError):
                pass
            break

    return "".join(stdout_parts), "".join(stderr_parts)


# ── tick ───────────────────────────────────────────────────────────────────


def tick(
    *,
    data_root: Path,
    manager_home: str | None = None,
    popen_fn: Callable[..., subprocess.Popen] | None = None,
    dry_run: bool = False,
    notifier_fn: Callable[[str, str], None] | None = None,
    _time_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Called once per scheduler cycle; never waits on claude.

    Returns a dict with at least ``decision`` and optionally ``idle_cycles``.
    ``--dry-run`` computes the decision and writes nothing.
    """
    global _now
    if _time_fn is not None:
        _now = _time_fn

    autoplan_dir = data_root / "autoplan"
    state_file = autoplan_dir / "state.json"
    inflight_file = autoplan_dir / "inflight.json"
    paused_file = autoplan_dir / "paused.json"
    disabled_file = data_root / "autoplan.disabled"

    # Load state
    state = _read_json(state_file) or {}
    last_refusal = state.get("last_refusal")

    def _result(decision: str, **extra: Any) -> dict[str, Any]:
        r: dict[str, Any] = {"decision": decision}
        r.update(extra)
        return r

    def _write_refusal(reason: str, notify: bool = True) -> None:
        nonlocal last_refusal
        # Dedup: only write row if reason differs from last refusal
        if reason in _DEDUP_REASONS and reason == last_refusal:
            return
        if not dry_run:
            write_audit("autoplan-refused", "ilk-skills", root=data_root,
                        reason=reason)
            if notify:
                _notify(data_root, "blocked",
                        f"autoplan refused: {reason}",
                        notifier_fn=notifier_fn)
            state["last_refusal"] = reason
            _write_json(state_file, state)

    def _reset_idle() -> None:
        if not dry_run:
            state["idle_cycles"] = 0
            _write_json(state_file, state)

    # 1. Kill switch
    if disabled_file.exists():
        return _result("disabled")

    # 2. Paused (with expiry support)
    if paused_file.exists():
        paused_data = _read_json(paused_file)
        # Unparseable JSON or missing until → fail closed (stay paused)
        if paused_data is None or not paused_data.get("until"):
            return _result("paused")
        try:
            from datetime import datetime, timezone
            until_dt = datetime.fromisoformat(paused_data["until"])
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            expired = until_dt.timestamp() <= _now()
        except (ValueError, TypeError):
            return _result("paused")
        if expired:
            # Pause expired: remove and audit, then fall through
            if not dry_run:
                paused_file.unlink(missing_ok=True)
                write_audit("autoplan-resumed", "ilk-skills",
                            root=data_root, reason="pause expired")
        else:
            return _result("paused")

    # 3. Projects: the toolkit (kernel-found) first, then any extra project
    # autoplan_projects declares (track B).  An ambiguous toolkit stops
    # everything; "not-enabled" stops only when there is no extra project.
    projects, err = _planning_projects(data_root)
    if err:
        _write_refusal(err)
        return _result(err)

    # 4. In-flight
    if inflight_file.exists():
        inflight = _read_json(inflight_file) or {}
        pid = inflight.get("pid")
        if pid and _pid_alive(pid):
            return _result("in-flight")
        # Dead pid: remove and count as refusal
        if not dry_run:
            inflight_file.unlink(missing_ok=True)
            _write_refusal("stale-inflight")
            # Increment autoplan_attempts on the candidate
            cand_id = inflight.get("candidate")
            if cand_id:
                _increment_attempts(data_root, cand_id,
                                    project_key=inflight.get("project_key"))
        return _result("autoplan-refused")

    # 5. Busy
    if _is_busy(data_root):
        _reset_idle()
        return _result("busy")

    # 6. Idle count
    idle_cycles = state.get("idle_cycles", 0) + 1

    if not dry_run:
        state["idle_cycles"] = idle_cycles
        _write_json(state_file, state)

    if idle_cycles < IDLE_CYCLES_N:
        return _result("idle", idle_cycles=idle_cycles)

    # 7. At threshold — check gates
    # Auto-planned in flight
    if _has_auto_planned_in_flight(data_root):
        return _result("auto-planned-in-flight")

    # Daily cap
    today = time.strftime("%Y-%m-%d")
    starts_day = state.get("starts_day")
    starts_today = state.get("starts_today", 0)
    if starts_day != today:
        starts_today = 0

    if starts_today >= MAX_STARTS_PER_DAY:
        return _result("rate-limited", detail="daily-cap")

    # Outcome-based pacing (replaces MIN_HOURS_BETWEEN_STARTS_K)
    last_start = state.get("last_start")
    last_outcome = state.get("last_outcome")
    if last_start and last_outcome == "refused":
        minutes_since = (_now() - last_start) / 60
        if minutes_since < REFUSED_BACKOFF_MIN:
            return _result("rate-limited")
    # queued/drafted → no time limit (auto-planned-in-flight and
    # MAX_CONSECUTIVE_DRAFTS still apply)
    # missing last_outcome (older state) → treat as refused
    if last_start and last_outcome is None:
        minutes_since = (_now() - last_start) / 60
        if minutes_since < REFUSED_BACKOFF_MIN:
            return _result("rate-limited")

    # Bad home
    if manager_home:
        basename = os.path.basename(manager_home)
        if basename.startswith(".claude-worker"):
            _write_refusal("bad-home")
            return _result("bad-home")

    # Backlog readable, then select: the first project with an eligible,
    # kernel-clean candidate wins.  The toolkit keeps today's semantics (an
    # unreadable backlog refuses the tick); an extra project that cannot be
    # read, or has no readable kernel of its own, is skipped and audited.
    selected = None
    selected_project = None
    for project in projects:
        try:
            candidates = _load_project_entries(data_root, project)
        except Exception:
            if project.get("toolkit"):
                _write_refusal("backlog-unreadable")
                return _result("backlog-unreadable")
            write_audit("autoplan-refused", "ilk-skills", root=data_root,
                        target=project.get("key"), reason="backlog-unreadable")
            continue
        try:
            kernel = _project_kernel(project)
        except ValueError as exc:
            write_audit("autoplan-refused", "ilk-skills", root=data_root,
                        target=project.get("key"), reason="no-project-kernel",
                        detail=str(exc)[:200])
            continue

        # 8. Select candidate
        ranked = rank(candidates, sources=project.get("sources"))
        for cand in ranked:
            hit = screen_candidate(cand, kernel)
            if hit is not None:
                # Escalate kernel-touching candidate
                if not dry_run:
                    _mark_project_candidate(
                        data_root, project, cand["id"],
                        relations={"autoplan_blocked": True},
                    )
                    write_audit(
                        "escalated", "ilk-skills", root=data_root,
                        source="autoplan", reason="protected-kernel",
                        candidate=cand["id"], target=project.get("key"),
                    )
                    _notify(data_root, "blocked",
                            f"autoplan: {cand['id']} touches kernel ({hit})",
                            notifier_fn=notifier_fn)
                continue
            selected = cand
            selected_project = project
            break
        if selected is not None:
            break

    if selected is None:
        return _result("no-candidate")

    # 9. Start
    if dry_run:
        return _result("started", candidate=selected["id"])

    run_id = f"autoplan-{int(_now())}"

    # Resolve the selected project's repo
    project_key = selected_project["key"]
    toolkit_repo = _resolve_toolkit_repo(data_root, project_key)
    if toolkit_repo is None:
        _write_refusal("no-toolkit-repo")
        return _result("no-toolkit-repo")

    # Build the plan command
    plan_script = _HERE / "autoplan.py"
    claude_cmd = popen_fn is None  # use real claude if no stub

    if popen_fn is not None:
        # Test mode: use the injected spawner
        proc = popen_fn(
            [sys.executable, str(plan_script), "plan",
             "--candidate", selected["id"],
             "--project-key", project_key,
             "--run-id", run_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        # Real mode: spawn autoplan.py plan detached (goes through plan()'s
        # lint, preflight, rails, forced draft, draft_only handling).
        log_file = autoplan_dir / "runs" / f"{run_id}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        default_home = str(Path.home() / ".claude-manager")
        env = {**os.environ, "CLAUDE_MANAGER_HOME": manager_home or default_home}
        log_fh = log_file.open("a")
        proc = subprocess.Popen(
            [sys.executable, str(plan_script), "plan",
             "--candidate", selected["id"],
             "--project-key", project_key,
             "--run-id", run_id],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            env=env,
            cwd=toolkit_repo,
            start_new_session=True,
        )
        log_fh.close()  # parent releases its fd; child keeps its copy

    # Write inflight.json
    _write_json(inflight_file, {
        "pid": proc.pid,
        "candidate": selected["id"],
        "project_key": project_key,
        "run_id": run_id,
        "started": _now(),
    })

    # Write audit row
    write_audit("autoplan-started", "ilk-skills", root=data_root,
                candidate=selected["id"], run_id=run_id, pid=proc.pid)
    write_event("autoplan-started", "ilk-skills", root=data_root,
                candidate=selected["id"], run_id=run_id)

    # Update state
    state["last_start"] = _now()
    state["idle_cycles"] = 0
    state["last_refusal"] = None
    # Increment daily start counter
    today = time.strftime("%Y-%m-%d")
    if state.get("starts_day") != today:
        state["starts_day"] = today
        state["starts_today"] = 1
    else:
        state["starts_today"] = state.get("starts_today", 0) + 1
    _write_json(state_file, state)

    return _result("started", candidate=selected["id"], run_id=run_id)


# ── plan session ───────────────────────────────────────────────────────────


def _plan_session(
    *,
    repo: str,
    plans_dir: Path,
    prompt: str,
    manager_home: str,
    kernel: dict | None,
    run_id: str,
    data_root: Path,
    claude_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    preflight_cmd: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The planning session: spawn the planner, then judge its batch.

    Holds the session part of ``plan()`` — snapshot, spawn with ``cwd=repo``,
    init-event model refusal, stream with timeout, clone check against
    *repo*, new-master detection, force draft, ``check_master`` and the
    lint/preflight pair (``_run_check`` with ``--git-cwd repo`` and
    ``--project-root repo``).  It does NOT flip the master to queued, write
    audit, notify, or touch lane state (inflight.json, state.json,
    paused.json, the backlog, the attempts overlay) — the caller owns those.

    Returns the contract keys ``decision`` (``"queued-ready"``,
    ``"drafted"`` or ``"refused"``), ``problems``, ``master_path``,
    ``reason`` and ``planner_log`` (the path the planner's stdout was
    written to, ``<data_root>/autoplan/runs/<run_id>.planner.jsonl``), plus
    the hand-off the caller needs to own lane state and audit:
    ``increment_attempts``, ``blocked_reason``, ``lint_kind``, ``lint_tail``,
    ``preflight_kind``, ``preflight_tail``.
    """
    run_logs = data_root / "autoplan" / "runs"
    planner_log = run_logs / f"{run_id}.planner.jsonl"
    stdout = ""
    problems: list[str] = []
    lint_kind, lint_tail = "ok", ""
    preflight_kind, preflight_tail = "ok", ""

    def _out(decision: str, *, reason: str | None = None,
             master_path: Path | None = None,
             increment_attempts: bool = False,
             blocked_reason: str | None = None) -> dict[str, Any]:
        return {
            "decision": decision,
            "problems": list(problems),
            "master_path": master_path,
            "reason": reason,
            "planner_log": planner_log,
            "increment_attempts": increment_attempts,
            "blocked_reason": blocked_reason,
            "lint_kind": lint_kind,
            "lint_tail": lint_tail,
            "preflight_kind": preflight_kind,
            "preflight_tail": preflight_tail,
        }

    try:
        # 1. Snapshot plans dir and repo state
        before_names = {p.name for p in plans_dir.glob("*.md")}
        before_mtimes = {p.name: p.stat().st_mtime for p in plans_dir.glob("*.md")}

        git_status_before = _run_cmd(
            ["git", "status", "--porcelain"], cwd=repo, capture_stdout=True
        )
        git_head_before = _run_cmd(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_stdout=True
        )

        # 2. Run claude
        if claude_cmd is None:
            claude_cmd = _resolve_claude_cmd()

        env = {**os.environ}
        if env_overrides:
            env.update(env_overrides)
        if manager_home:
            env["CLAUDE_CONFIG_DIR"] = manager_home

        full_cmd = claude_cmd + [
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools",
            "Read Grep Glob Write Edit",
            "Bash(python3:*)", "Bash(git log:*)", "Bash(git show:*)",
            "Bash(git rev-parse:*)", "Bash(git status:*)",
            "Bash(date:*)", "Bash(ls:*)",
        ]

        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=repo,
            start_new_session=True,
        )

        # Read init event
        init, leftover = _read_init_event(proc, INIT_TIMEOUT_S)
        if isinstance(init, str):
            # Failed to get init event
            _kill_group(proc)
            return _out("refused", reason=init)

        model = init.get("model", "")
        if re.search(r"mimo|glm", model, re.IGNORECASE):
            _kill_group(proc)
            return _out("refused", reason=f"model {model}")

        # Stream remaining output with timeout
        stdout, stderr = _stream_output(proc, PLAN_TIMEOUT_S - INIT_TIMEOUT_S, leftover=leftover)

        # Check if process is still running (timed out)
        if proc.poll() is None:
            _kill_group(proc)
            problems.append("timed-out")

        # 3. Clone check
        git_status_after = _run_cmd(
            ["git", "status", "--porcelain"], cwd=repo, capture_stdout=True
        )
        git_head_after = _run_cmd(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_stdout=True
        )

        if git_status_after != git_status_before:
            # The caller turns this into the critical escalation; it owns audit.
            problems.append("clone-modified")
        elif git_head_after != git_head_before:
            problems.append("head-moved")

        # 4. New MASTER files (or any masters the stub may have written)
        after_names = {p.name for p in plans_dir.glob("*.md")}
        new_names = after_names - before_names
        new_masters = [n for n in new_names if n.startswith("MASTER-")]
        # Never fall back to pre-existing masters: on 2026-10-07 that fallback
        # forced all 122 existing masters to draft after a stale plan.

        # The planner's own verdict, read from its final answer only.
        stale_reason = planner_stale_reason(stdout)

        if not new_masters:
            if stale_reason is not None:
                reason = f"stale {stale_reason}"
                return _out("refused", reason=reason, increment_attempts=True,
                            blocked_reason=reason)
            return _out("refused", reason="no-master", increment_attempts=True)

        # 5. Force all new masters to draft
        for master_name in new_masters:
            write_status(plans_dir / master_name, "draft", allow_from_held=True)

        # If multiple masters, note the problem
        if len(new_masters) > 1:
            problems.append("multiple-masters")

        # Use the first master for checks
        master_path = plans_dir / new_masters[0]

        # Run check_master
        master_problems = check_master(
            master_path, plans_dir, kernel=kernel, repo=Path(repo),
        )
        problems.extend(master_problems)

        # Run plan_lint
        if lint_cmd is None:
            lint_script = _REPO / "skills" / "ilk-loop" / "scripts" / "plan_lint.py"
            lint_cmd = [sys.executable, str(lint_script)]

        subplan_files = extract_subplan_files(
            master_path.read_text(encoding="utf-8-sig")
        )
        lint_args = lint_cmd + [
            "--master", str(master_path),
        ]
        for sf in subplan_files:
            # Positional: plan_lint.py defines no --subplan (argparse exit 2).
            lint_args.append(str(plans_dir / sf))
        # --git-cwd: plan_lint otherwise resolves git from Path.cwd(), which is
        # autoplan's own cwd, not the repo being planned (backlog d8555939).
        lint_args.extend([
            "--git-cwd", repo,
            "--project-root", repo,
        ])

        lint_kind, lint_rc, lint_tail = _run_check(
            lint_args,
            cwd=repo,
            timeout=CHECK_TIMEOUT_S,
            log_path=run_logs / f"{run_id}.lint.txt",
        )
        if lint_kind == "exit":
            problems.append(f"lint-exit-{lint_rc}")
        elif lint_kind == "timeout":
            problems.append("lint-timeout")
        elif lint_kind == "unrunnable":
            problems.append("lint-unrunnable")

        # Run plan_preflight
        if preflight_cmd is None:
            preflight_script = _REPO / "skills" / "ilk-loop" / "scripts" / "plan_preflight.py"
            preflight_cmd = [sys.executable, str(preflight_script)]

        preflight_args = preflight_cmd + [
            str(master_path),
            "--plans-dir", str(plans_dir),
            "--project-root", repo,
        ]

        preflight_kind, preflight_rc, preflight_tail = _run_check(
            preflight_args,
            cwd=repo,
            timeout=CHECK_TIMEOUT_S,
            log_path=run_logs / f"{run_id}.preflight.txt",
        )
        if preflight_kind == "exit":
            problems.append(f"preflight-exit-{preflight_rc}")
        elif preflight_kind == "timeout":
            problems.append("preflight-timeout")
        elif preflight_kind == "unrunnable":
            problems.append("preflight-unrunnable")

        if problems:
            return _out("drafted", master_path=master_path,
                        increment_attempts=True)
        return _out("queued-ready", master_path=master_path)

    finally:
        # The planner's stdout is evidence for a red gate; keep it even when
        # the session refuses before streaming (the file is then empty).
        try:
            planner_log.parent.mkdir(parents=True, exist_ok=True)
            planner_log.write_text(stdout, encoding="utf-8", errors="replace")
        except OSError:
            pass


# ── plan ───────────────────────────────────────────────────────────────────


def plan(
    *,
    candidate_id: str,
    project_key: str,
    run_id: str,
    data_root: Path,
    toolkit_repo: str,
    manager_home: str,
    claude_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    preflight_cmd: list[str] | None = None,
    draft_only: bool | None = None,
    notifier_fn: Callable[[str, str], None] | None = None,
    env_overrides: dict[str, str] | None = None,
    _time_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """The detached planning part.  ``finally``: remove inflight.json.

    Returns a dict with ``decision`` and optional ``problems``.

    When *draft_only* is ``True``, the full planner pipeline runs (lint,
    preflight, rails, audit, candidate-linking) but the master stays
    ``status: draft``.  A ``dry-period-drafted`` audit event is written
    instead of ``autoplan-queued``.  The consecutive-drafts counter does
    not increment and ``paused.json`` is never written for an intentional
    draft-only outcome.
    """
    global _now
    if _time_fn is not None:
        _now = _time_fn

    autoplan_dir = data_root / "autoplan"
    inflight_file = autoplan_dir / "inflight.json"
    paused_file = autoplan_dir / "paused.json"
    disabled_file = data_root / "autoplan.disabled"

    problems: list[str] = []
    result: dict[str, Any] = {"decision": "unknown"}

    try:
        # 0. Resolve and validate draft_only config (fail-closed).
        #    The keyword argument overrides the toolkit config; the toolkit
        #    config is the source of truth for validation.
        launch_file = Path(toolkit_repo) / ".ilk-launch.json"
        launch_cfg = _read_json(launch_file) or {}
        autoplan_cfg = launch_cfg.get("autoplan", {})
        draft_only_val = autoplan_cfg.get("draft_only")
        if draft_only is not None:
            # Keyword argument overrides config
            draft_only_val = draft_only
        # Validate: must be bool or absent
        if draft_only_val is not None and not isinstance(draft_only_val, bool):
            _write_plan_refused(
                data_root,
                f"draft_only config error: expected bool, got {type(draft_only_val).__name__}",
                candidate_id,
            )
            result = {
                "decision": "refused",
                "reason": f"draft_only config error: expected bool, got {type(draft_only_val).__name__}",
            }
            return result
        draft_only = draft_only_val

        # 1. Kill switch
        if disabled_file.exists():
            _write_plan_refused(data_root, "disabled", candidate_id)
            result = {"decision": "refused", "reason": "disabled"}
            return result

        # 2. Resolve the plans dir the session will write into
        plans_dir = _find_plans_dir_for_project(
            data_root / "projects" / project_key, toolkit_repo
        )
        if plans_dir is None:
            plans_dir = data_root / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)

        # 3. Build prompt
        # An extra project must be discovered (its kernel comes from its own
        # repo).  Any other key keeps the pre-track-B behaviour exactly: the
        # toolkit backlog and the toolkit kernel.
        project = _project_by_key(data_root, project_key) or {
            "key": project_key, "toolkit": True,
        }
        try:
            candidates = _load_project_entries(data_root, project)
            project_kernel = _project_kernel(project)
        except Exception:
            _write_plan_refused(data_root, "backlog-unreadable", candidate_id)
            result = {"decision": "refused", "reason": "backlog-unreadable"}
            return result

        candidate = None
        for c in candidates:
            if c.get("id") == candidate_id:
                candidate = c
                break

        if candidate is None:
            _write_plan_refused(data_root, "candidate-not-found", candidate_id)
            result = {"decision": "refused", "reason": "candidate-not-found"}
            return result

        prompt = _build_plan_prompt(candidate, toolkit_repo, project_key)

        # 4. The planning session: spawn, judge, lint and preflight.
        session = _plan_session(
            repo=toolkit_repo,
            plans_dir=plans_dir,
            prompt=prompt,
            manager_home=manager_home,
            kernel=project_kernel,
            run_id=run_id,
            data_root=data_root,
            claude_cmd=claude_cmd,
            lint_cmd=lint_cmd,
            preflight_cmd=preflight_cmd,
            env_overrides=env_overrides,
        )
        problems = list(session.get("problems") or [])

        # 5. A clone the planner touched is a critical escalation.
        #    The session only records it; audit and notify stay here.
        if "clone-modified" in problems:
            write_audit(
                "escalated", "ilk-skills", root=data_root,
                source="autoplan", reason="clone-modified",
                severity="critical", candidate=candidate_id,
            )
            _notify(data_root, "blocked",
                    f"autoplan: clone modified during plan ({candidate_id})",
                    notifier_fn=notifier_fn)

        # 6. Refusals: the audit row and any attempts bump stay here.
        if session["decision"] == "refused":
            reason = session.get("reason") or "unknown"
            _write_plan_refused(data_root, reason, candidate_id)
            if session.get("increment_attempts"):
                _increment_attempts(data_root, candidate_id, project_key=project_key,
                                    blocked_reason=session.get("blocked_reason"))
            result = {"decision": "refused", "reason": reason}
            return result

        master_path = session["master_path"]

        # Lane bookkeeping: link the master to this candidate and run.
        mark_master(master_path, candidate_id=candidate_id, run_id=run_id)

        # 7. Decide draft or queued
        if not problems:
            if draft_only is True:
                # Draft-only dry period: full pipeline ran clean but
                # the master stays draft for human review.  The
                # consecutive-drafts counter does NOT increment and
                # paused.json is never written for an intentional
                # draft-only outcome.
                _mark_project_candidate(
                    data_root, project, candidate_id,
                    status="planned",
                    relations={"autoplan_master": master_path.name},
                )
                write_audit("dry-period-drafted", "ilk-skills", root=data_root,
                            candidate=candidate_id, master=master_path.name,
                            run_id=run_id)
                write_event("dry-period-drafted", "ilk-skills", root=data_root,
                            candidate=candidate_id, master=master_path.name)
                # Reset consecutive_drafts to 0 (intentional draft-only
                # outcome is not a failed draft).
                state = _read_json(autoplan_dir / "state.json") or {}
                state["consecutive_drafts"] = 0
                state["last_outcome"] = "drafted"
                _write_json(autoplan_dir / "state.json", state)
                result = {"decision": "drafted", "master": master_path.name,
                          "draft_only": True}
            else:
                write_status(master_path, "queued", allow_from_held=True)
                _mark_project_candidate(
                    data_root, project, candidate_id,
                    status="planned",
                    relations={"autoplan_master": master_path.name},
                )
                write_audit("autoplan-queued", "ilk-skills", root=data_root,
                            candidate=candidate_id, master=master_path.name,
                            run_id=run_id)
                write_event("autoplan-queued", "ilk-skills", root=data_root,
                            candidate=candidate_id, master=master_path.name)
                state = _read_json(autoplan_dir / "state.json") or {}
                state["consecutive_drafts"] = 0
                state["last_outcome"] = "queued"
                _write_json(autoplan_dir / "state.json", state)
                result = {"decision": "queued", "master": master_path.name}
        else:
            # Stay draft
            truncated = [p[:300] for p in problems[:20]]
            # Keep the check output on the row that says why it drafted.  A
            # "tail" is only worth having from its end, so clip to the last
            # 2000 chars (same convention as run_local_checks._tail).
            check_tails: dict[str, Any] = {}
            if session.get("lint_kind") != "ok":
                check_tails["lint_tail"] = (session.get("lint_tail") or "")[-2000:]
            if session.get("preflight_kind") != "ok":
                check_tails["preflight_tail"] = (session.get("preflight_tail") or "")[-2000:]
            write_audit("autoplan-drafted", "ilk-skills", root=data_root,
                        candidate=candidate_id, master=master_path.name,
                        run_id=run_id, problems=truncated, **check_tails)
            _notify(data_root, "blocked",
                    f"autoplan drafted: {master_path.name} "
                    f"({len(problems)} problems)",
                    notifier_fn=notifier_fn)
            _increment_attempts(data_root, candidate_id, project_key=project_key)

            # Track consecutive drafts
            state = _read_json(autoplan_dir / "state.json") or {}
            consecutive = state.get("consecutive_drafts", 0) + 1
            state["consecutive_drafts"] = consecutive
            state["last_outcome"] = "drafted"
            _write_json(autoplan_dir / "state.json", state)

            if consecutive >= MAX_CONSECUTIVE_DRAFTS:
                _write_json(paused_file, {
                    "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "reason": f"consecutive drafts ({consecutive})",
                    "run_ids": [run_id],
                })
                write_audit(
                    "autoplan-refused", "ilk-skills", root=data_root,
                    reason=f"consecutive-drafts-{consecutive}",
                )

            result = {
                "decision": "drafted",
                "master": master_path.name,
                "problems": truncated,
            }

        return result

    finally:
        inflight_file.unlink(missing_ok=True)
        # Record last_outcome for outcome-based pacing in tick()
        if result.get("decision") == "refused":
            try:
                st = _read_json(autoplan_dir / "state.json") or {}
                st["last_outcome"] = "refused"
                _write_json(autoplan_dir / "state.json", st)
            except (OSError, TypeError):
                pass


# ── plan-issue ─────────────────────────────────────────────────────────────


def _bad_input(fields: list[str]) -> list[str]:
    return [f"bad-input: {f}" for f in fields]


def _as_str_list(raw: Any, field: str, problems: list[str]) -> list[str]:
    """A list-of-strings input field; absent means ``[]`` (the contract default)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        problems.append(f"bad-input: {field}")
        return []
    return list(raw)


def plan_issue(
    *,
    input_path: Path,
    outcome_path: Path,
    data_root: Path,
    manager_home: str,
    claude_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    preflight_cmd: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> int:
    """Turn one admitted GitHub issue into a queued master in ITS worktree.

    The gh-resolve entry point (MASTER's "Contract already sent to gh-resolve").
    Unlike ``plan()`` this never touches the RSI lane: no ``inflight.json``,
    no ``state.json``, no ``paused.json``, no backlog mark, no attempts
    overlay, no audit and no notification (MASTER judgment call d — gh-resolve
    runs several issues in parallel and owns its own retries).

    Returns 0 for ``queued``, 3 for ``drafted``, 1 for ``failed``.  The
    outcome file is written atomically on every path, including exceptions.
    """
    data_root = Path(data_root)
    problems: list[str] = []
    plans_dir: Path | None = None
    master_path: Path | None = None
    planner_log: Path | None = None

    def _finish(outcome: str, code: int,
                final_problems: list[str] | None = None) -> int:
        payload = {
            "outcome": outcome,
            "problems": list(final_problems if final_problems is not None
                             else problems),
            "master_path": str(master_path) if master_path else None,
            "plans_dir": str(plans_dir) if plans_dir else None,
            "planner_log": str(planner_log) if planner_log else None,
        }
        _write_json(Path(outcome_path), payload)
        return code

    try:
        raw = _read_json(Path(input_path))
        if not isinstance(raw, dict):
            return _finish("failed", 1, ["bad-input: input"])

        # 1. Validate before anything is spawned (AC-4).
        issue = raw.get("issue")
        if not isinstance(issue, dict):
            problems.append("bad-input: issue")
        else:
            for field in ("repo", "number", "title", "body"):
                if field not in issue:
                    problems.append(f"bad-input: issue.{field}")

        project_root_raw = raw.get("project_root")
        project_root: Path | None = None
        if not isinstance(project_root_raw, str) or not project_root_raw.strip():
            problems.append("bad-input: project_root")
        else:
            candidate_root = Path(project_root_raw)
            if not candidate_root.is_dir() or not (candidate_root / ".git").exists():
                problems.append("bad-input: project_root")
            else:
                project_root = candidate_root

        base_branch = raw.get("base_branch")
        if not isinstance(base_branch, str) or not base_branch.strip():
            problems.append("bad-input: base_branch")

        run_id = raw.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            problems.append("bad-input: run_id")

        scope_paths = _as_str_list(raw.get("scope_paths"), "scope_paths", problems)
        write_targets = _as_str_list(raw.get("write_targets"), "write_targets", problems)
        local_checks = _as_str_list(raw.get("local_checks"), "local_checks", problems)

        if problems or project_root is None:
            return _finish("failed", 1)

        # 2. The issue's own plans dir — never the toolkit's.
        plans_dir = external_plans_dir(project_key(project_root))
        plans_dir.mkdir(parents=True, exist_ok=True)

        # 3. Kernel rail: the project's own autoplan.kernel_file when it
        #    declares one, else None (MASTER judgment call f).
        kernel = None
        launch = _read_json(project_root / ".ilk-launch.json") or {}
        if isinstance(launch, dict):
            rel = (launch.get("autoplan") or {}).get("kernel_file")
            if rel:
                kernel = _project_kernel({
                    "toolkit": False,
                    "key": project_key(project_root),
                    "repo": str(project_root),
                })

        prompt = _build_plan_issue_prompt(
            issue if isinstance(issue, dict) else {},
            base_branch=base_branch,
            scope_paths=scope_paths,
            write_targets=write_targets,
            local_checks=local_checks,
            run_id=run_id,
        )

        session = _plan_session(
            repo=str(project_root),
            plans_dir=plans_dir,
            prompt=prompt,
            manager_home=manager_home,
            kernel=kernel,
            run_id=run_id,
            data_root=data_root,
            claude_cmd=claude_cmd,
            lint_cmd=lint_cmd,
            preflight_cmd=preflight_cmd,
            env_overrides=env_overrides,
        )
        problems = list(session.get("problems") or [])
        master_path = session.get("master_path")
        log_path = session.get("planner_log")
        if log_path is not None:
            planner_log = Path(log_path)

        # The planner's own verdict, read from the final answer.  Both markers
        # are plan-issue verdicts even when a master was produced: the session
        # would otherwise queue a batch for an issue it just declared unplanned.
        stdout = ""
        if planner_log is not None:
            try:
                stdout = planner_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        unplannable = planner_unplannable_reason(stdout)
        stale = planner_stale_reason(stdout)

        if session["decision"] == "refused":
            reason = session.get("reason") or "unknown"
            if unplannable:
                reason = f"unplannable {unplannable}"
            elif stale:
                reason = f"stale {stale}"
            return _finish("failed", 1, [reason])

        if master_path is None:
            return _finish("failed", 1,
                           [session.get("reason") or "no-master"])
        master_path = Path(master_path)

        if unplannable:
            return _finish("failed", 1, [f"unplannable {unplannable}"])
        if stale:
            return _finish("failed", 1, [f"stale {stale}"])

        if session["decision"] == "queued-ready":
            write_status(master_path, "queued", allow_from_held=True)
            return _finish("queued", 0)

        return _finish("drafted", 3, problems)

    except Exception as exc:  # noqa: BLE001 — the outcome file is the verdict
        return _finish("failed", 1, [f"error: {type(exc).__name__}: {exc}"])


# ── probe ──────────────────────────────────────────────────────────────────


def probe(
    *,
    manager_home: str,
    claude_cmd: list[str] | None = None,
) -> dict[str, Any]:
    """Start ``claude -p "Reply OK"`` on the manager home, read init event.

    Returns ``{"home", "model", "refused"}``.
    Writes nothing.
    """
    if claude_cmd is None:
        claude_cmd = _resolve_claude_cmd()

    env = {**os.environ, "CLAUDE_CONFIG_DIR": manager_home}

    proc = subprocess.Popen(
        claude_cmd + [
            "-p", "Reply OK",
            "--output-format", "stream-json",
            "--verbose",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )

    init, _ = _read_init_event(proc, INIT_TIMEOUT_S)
    _kill_group(proc)

    if isinstance(init, str):
        return {"home": manager_home, "model": None, "refused": init}

    return {
        "home": manager_home,
        "model": init.get("model"),
        "refused": None,
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ilk auto-planner")
    sub = parser.add_subparsers(dest="command")

    # tick
    tick_p = sub.add_parser("tick", help="Called once per scheduler cycle")
    tick_p.add_argument("--dry-run", action="store_true")
    tick_p.add_argument("--json", action="store_true",
                        help="Output as JSON")

    # plan
    plan_p = sub.add_parser("plan", help="Detached planning session")
    plan_p.add_argument("--candidate", required=True)
    plan_p.add_argument("--project-key", required=True)
    plan_p.add_argument("--run-id", required=True)

    # plan-issue
    plan_issue_p = sub.add_parser(
        "plan-issue",
        help="Plan one admitted GitHub issue into a queued master",
    )
    plan_issue_p.add_argument(
        "--input", dest="input_path", required=True, metavar="PATH",
        help="issue.json (the gh-resolve request; see SKILL.md)",
    )
    plan_issue_p.add_argument(
        "--outcome", dest="outcome_path", required=True, metavar="PATH",
        help="outcome.json to write (queued / drafted / failed)",
    )

    # probe
    probe_p = sub.add_parser("probe", help="Test manager home")
    probe_p.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "tick":
        result = tick(
            data_root=ilk_data_root(),
            manager_home=os.environ.get("CLAUDE_MANAGER_HOME",
                                        str(Path.home() / ".claude-manager")),
            dry_run=args.dry_run,
        )
        if args.json:
            print(json.dumps(result))
        else:
            print(f"{result['decision']}"
                  + (f" {result.get('idle_cycles', '')}" if "idle_cycles" in result else ""))
        return 0

    elif args.command == "plan":
        data_root = ilk_data_root()
        result = plan(
            candidate_id=args.candidate,
            project_key=args.project_key,
            run_id=args.run_id,
            data_root=data_root,
            toolkit_repo=os.getcwd(),
            manager_home=os.environ.get("CLAUDE_MANAGER_HOME",
                                        str(Path.home() / ".claude-manager")),
        )
        print(json.dumps(result))
        return 0

    elif args.command == "plan-issue":
        return plan_issue(
            input_path=Path(args.input_path),
            outcome_path=Path(args.outcome_path),
            data_root=ilk_data_root(),
            manager_home=_resolve_planner_home(),
        )

    elif args.command == "probe":
        manager_home = os.environ.get("CLAUDE_MANAGER_HOME",
                                      str(Path.home() / ".claude-manager"))
        result = probe(manager_home=manager_home)
        if args.json:
            print(json.dumps(result))
        else:
            print(f"home={result['home']} model={result['model']} "
                  f"refused={result['refused']}")
        return 0

    else:
        parser.print_help()
        return 2


# ── internal helpers ───────────────────────────────────────────────────────


def _planning_projects(data_root: Path) -> tuple[list[dict], str | None]:
    """The toolkit (found here, in the kernel) first, then extra projects.

    Returns (projects, error).  ``ambiguous-toolkit`` is always an error;
    ``not-enabled`` is one only when there is no extra project either.
    """
    projects: list[dict] = []
    toolkit_key, err = _find_toolkit_project(data_root)
    if err == "ambiguous-toolkit":
        return [], err
    if toolkit_key is not None:
        projects.append({
            "key": toolkit_key,
            "repo": _resolve_toolkit_repo(data_root, toolkit_key),
            "toolkit": True,
            "backlog_mode": "toolkit",
            "sources": None,
        })
    try:
        extra = autoplan_projects.discover_extra(data_root) or []
    except Exception:
        extra = []
    for p in extra:
        if not isinstance(p, dict) or not p.get("key") or p.get("key") == toolkit_key:
            continue
        # The repo is resolved HERE from the project's own data dir, never
        # taken from autoplan_projects: it decides where the planner runs and
        # which .ilk-launch.json names the kernel screened against.
        repo = _resolve_toolkit_repo(data_root, p["key"])
        if repo is None:
            continue
        projects.append({**p, "repo": repo, "toolkit": False})
    if not projects:
        return [], err or "not-enabled"
    return projects, None


def _project_by_key(data_root: Path, key: str) -> dict | None:
    projects, _err = _planning_projects(data_root)
    for p in projects:
        if p["key"] == key:
            return p
    return None


def _project_kernel(project: dict) -> dict | None:
    """The kernel to screen *project*'s candidates and masters against.

    None = the toolkit's own kernel (unchanged).  A non-toolkit project's
    kernel file is read from that project's OWN .ilk-launch.json
    (``autoplan.kernel_file``, relative to its repo), never from
    autoplan_projects, and is loaded FAIL CLOSED: raises ValueError when
    absent or unreadable, and the caller skips the project.
    """
    if project.get("toolkit"):
        return None
    repo = project.get("repo")
    if not repo:
        raise ValueError(f"project {project.get('key')}: no repo")
    launch = _read_json(Path(repo) / ".ilk-launch.json") or {}
    rel = (launch.get("autoplan") or {}).get("kernel_file") if isinstance(launch, dict) else None
    if not rel or not isinstance(rel, str):
        raise ValueError(f"project {project.get('key')}: no autoplan.kernel_file")
    kfile = (Path(repo) / rel).resolve()
    if Path(repo).resolve() not in kfile.parents:
        raise ValueError(f"project {project.get('key')}: kernel_file outside the repo")
    return load_project_kernel(kfile)


def _overlay_path(data_root: Path, key: str) -> Path:
    return data_root / "autoplan" / "projects" / key / "overlay.json"


def _load_project_entries(data_root: Path, project: dict) -> list[dict]:
    """Candidate rows for *project*.  The toolkit reads its backlog dir as
    before; any other project reads through autoplan_projects, with autoplan's
    own overlay (attempts, blocked, planned) merged on top, because its
    backlog is the project's to write, not ours."""
    if project.get("toolkit"):
        return read_backlog_strict(_backlog_dir(data_root))
    rows = autoplan_projects.load_entries(project)
    overlay = _read_json(_overlay_path(data_root, project["key"])) or {}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        o = overlay.get(str(r.get("id"))) if isinstance(overlay, dict) else None
        if isinstance(o, dict):
            r = dict(r)
            if o.get("status"):
                r["status"] = o["status"]
            rel = dict(r.get("relations") or {})
            rel.update(o.get("relations") or {})
            r["relations"] = rel
        out.append(r)
    return out


def _mark_project_candidate(data_root: Path, project: dict | None, candidate_id: str, *,
                            status: str | None = None,
                            relations: dict | None = None,
                            increment: dict | None = None) -> None:
    """mark_candidate for the toolkit; the overlay for any other project."""
    if project is None or project.get("toolkit"):
        mark_candidate(candidate_id, status=status, relations=relations,
                       increment=increment, backlog_dir=_backlog_dir(data_root))
        return
    path = _overlay_path(data_root, project["key"])
    overlay = _read_json(path) or {}
    if not isinstance(overlay, dict):
        overlay = {}
    o = overlay.setdefault(str(candidate_id), {})
    if status:
        o["status"] = status
    rel = o.setdefault("relations", {})
    rel.update(relations or {})
    for k, n in (increment or {}).items():
        try:
            rel[k] = int(rel.get(k, 0)) + int(n)
        except (TypeError, ValueError):
            rel[k] = int(n)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, overlay)


def _backlog_dir(data_root: Path) -> Path:
    """Resolve the backlog directory."""
    return data_root / "ilk-skills-improvements"


def _resolve_toolkit_repo(data_root: Path, project_key: str) -> str | None:
    """Resolve the toolkit repo path for a project key."""
    project_dir = data_root / "projects" / project_key
    return resolve_repo_path(project_dir, project_key)


def _build_plan_prompt(candidate: dict, toolkit_repo: str,
                       project_key: str) -> str:
    """Build the prompt for the claude -p session."""
    # Build task description inline (candidate is a dict, not an object).
    lines = [
        "# Task: ilk-skills toolkit improvements",
        "",
        "Source: improvement backlog (emitted by `/ilk-feedback` postmortems).",
        "",
        f"## 1. {candidate.get('title', '')}",
        "",
        f"- **Kind**: {candidate.get('kind', '')}",
        f"- **Gap**: {candidate.get('gap', '')}",
    ]
    evidence = candidate.get("evidence", {})
    if isinstance(evidence, dict):
        ev_parts = [f"{k}={v}" for k, v in evidence.items() if v]
        if ev_parts:
            lines.append(f"- **Evidence**: {', '.join(ev_parts)}")
    if candidate.get("proposed_fix"):
        lines.append(f"- **Proposed fix**: {candidate['proposed_fix']}")
    lines.append(
        f"- **Leverage**: {candidate.get('leverage', '')} | "
        f"**Severity**: {candidate.get('severity', '')}"
    )
    lines.append(
        f"- **Seen**: {candidate.get('seen_count', 0)}x "
        f"(first: {candidate.get('first_seen', '')})"
    )
    lines.append("")
    task = "\n".join(lines)
    return (
        f"Follow <this release>/commands/ilk-plan.md exactly, as "
        f"/ilk-plan --yes {task}\n\n"
        f"Candidate id: {candidate.get('id')}\n"
        f"Project key: {project_key}\n\n"
        f"Overrides:\n"
        f"- Leave the MASTER status: draft (skip step 8b; the auto-planner decides)\n"
        f"- 1-2 sub-plans plus the batch-verification sub-plan last\n"
        f"- Never touch any PROTECTED_KERNEL path\n"
        f"- Write only inside the plans dir\n"
        f"- Never edit, commit, branch or stash in the repo\n"
        f"- If the candidate's evidence no longer reproduces at HEAD, "
        f"write no plan and end with a line AUTOPLAN: stale <reason>"
    )


def _build_plan_issue_prompt(issue: dict, *, base_branch: str,
                             scope_paths: list[str],
                             write_targets: list[str],
                             local_checks: list[str],
                             run_id: str) -> str:
    """The prompt for one admitted GitHub issue (the ``plan-issue`` session).

    The issue body goes in verbatim inside a fenced block; the constraints are
    listed verbatim so the batch carries them literally.  The Overrides are the
    ``plan-issue`` half of the contract: never ask a question (an unplannable
    issue ends with ``AUTOPLAN: unplannable <reason>``), leave the MASTER draft
    (``plan_issue`` flips it), write only inside the plans dir, never touch the
    repo, and produce exactly one MASTER.
    """
    body = issue.get("body") or ""
    lines = [
        "Follow <this release>/commands/ilk-plan.md exactly, as /ilk-plan --yes",
        "",
        "# Task: one admitted GitHub issue",
        "",
        f"- **Repo**: {issue.get('repo', '')}",
        f"- **Issue**: #{issue.get('number', '')}",
        f"- **Title**: {issue.get('title', '')}",
        f"- **URL**: {issue.get('url', '')}",
        "",
        "Issue body (verbatim):",
        "",
        "```",
        body,
        "```",
        "",
        "## Constraints",
        "",
        f"- base_branch: {base_branch} (write it as the master's `base_branch:`)",
        "- scope_paths:",
        *[f"  - {p}" for p in scope_paths],
        "- write_targets:",
        *[f"  - {p}" for p in write_targets],
        "- local_checks (use these as the work sub-plan gates):",
        *[f"  - {c}" for c in local_checks],
        f"- run_id: {run_id}",
        "",
        "Overrides:",
        "- Never ask a question — if the issue cannot be planned end with a line",
        "  `AUTOPLAN: unplannable <reason>`",
        "- Leave the MASTER status: draft (the auto-planner decides)",
        "- Write only inside the plans dir",
        "- Never edit, commit, branch or stash in the repo",
        "- Exactly one MASTER",
    ]
    return "\n".join(lines)


_STALE_MARK = "AUTOPLAN: stale"


def planner_final_text(stdout: str) -> str:
    """The planner's final answer from its ``--output-format stream-json``.

    The ``result`` event's text when there is one, else the text blocks of
    the last assistant message, else the non-JSON lines (plain-text output,
    e.g. test stubs).  Tool results are never included: on 2026-10-09 01:06
    a planner grepped autoplan.py, the tool output echoed this module's
    prompt template line ``AUTOPLAN: stale <reason>``, and a whole-stdout
    substring match blocked a live defect row (579b6efa) as stale.
    """
    result_text: str | None = None
    last_assistant: list[str] = []
    plain: list[str] = []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            plain.append(line)
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            result_text = ev["result"]
        elif ev.get("type") == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                last_assistant = texts
    if result_text is not None:
        return result_text
    if last_assistant:
        return "\n".join(last_assistant)
    return "\n".join(plain)


def planner_stale_reason(stdout: str) -> str | None:
    """The reason from a final-answer line ``AUTOPLAN: stale <reason>``.

    Only a line that STARTS with the marker counts, and the template
    placeholder ``<reason>`` or an empty reason is not a verdict.  Capped at
    300 characters (402d6231: a reason once carried the raw stream tail).
    """
    for line in planner_final_text(stdout).splitlines():
        text = line.strip().strip("`*").strip()
        if not text.startswith(_STALE_MARK):
            continue
        reason = text[len(_STALE_MARK):].strip(" :—-")
        if not reason or reason.startswith("<reason>"):
            continue
        return reason[:300]
    return None


_UNPLANNABLE_MARK = "AUTOPLAN: unplannable"


def planner_unplannable_reason(stdout: str) -> str | None:
    """The reason from a final-answer line ``AUTOPLAN: unplannable <reason>``.

    Same reading rules as ``planner_stale_reason``: only a line that STARTS
    with the marker counts, the template placeholder ``<reason>`` or an empty
    reason is not a verdict, and the reason is capped at 300 characters.  This
    is the ``plan-issue`` verdict for an issue the planner cannot turn into a
    batch at all (the plan's Overrides tell it to end with this line rather
    than ask a question).
    """
    for line in planner_final_text(stdout).splitlines():
        text = line.strip().strip("`*").strip()
        if not text.startswith(_UNPLANNABLE_MARK):
            continue
        reason = text[len(_UNPLANNABLE_MARK):].strip(" :—-")
        if not reason or reason.startswith("<reason>"):
            continue
        return reason[:300]
    return None


def _write_plan_refused(data_root: Path, reason: str,
                         candidate_id: str) -> None:
    """Write an autoplan-refused audit row."""
    write_audit("autoplan-refused", "ilk-skills", root=data_root,
                reason=reason, candidate=candidate_id)


def _increment_attempts(data_root: Path, candidate_id: str,
                        blocked_reason: str | None = None,
                        project_key: str | None = None) -> None:
    """Add 1 to autoplan_attempts on a candidate.

    A *blocked_reason* (a planner "stale" refusal) also sets
    ``autoplan_blocked``: the evidence no longer reproduces, so the row must
    not be re-planned on the next start.  The toolkit's rows are marked under
    the backlog lock as before; another project's go to autoplan's overlay.
    """
    project = _project_by_key(data_root, project_key) if project_key else None
    try:
        _mark_project_candidate(
            data_root, project, candidate_id,
            relations=(
                {"autoplan_blocked": blocked_reason}
                if blocked_reason else None
            ),
            increment={"autoplan_attempts": 1},
        )
    except (KeyError, ValueError):
        pass


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill a process and its group."""
    import signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def _run_cmd(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    capture_stdout: bool = False,
) -> int | str | None:
    """Run a command and return its exit code (or stdout if capture_stdout)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if capture_stdout:
            return result.stdout
        return result.returncode
    except (subprocess.TimeoutExpired, OSError):
        return None


def _run_check(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int | None = None,
    log_path: Path | None = None,
) -> tuple[str, int | None, str]:
    """Run a lint/preflight check, keep its output, and fail closed.

    Returns ``(kind, returncode, tail)`` where *kind* is ``"ok"``, ``"exit"``,
    ``"timeout"`` or ``"unrunnable"``, *returncode* is the process exit code
    (``None`` when it never got one), and *tail* is the last 20 lines of the
    combined stdout+stderr joined by ``\n``.

    ``_run_cmd`` reads a timeout or an ``OSError`` as ``None``, and ``plan()``
    only appended a problem when the code was a non-zero int — so a check that
    never ran looked exactly like a pass (backlog 874b649b).  Here both are
    results: ``"timeout"`` and ``"unrunnable"`` are kinds the caller turns into
    problems.  The full output goes to *log_path* so a red check can still be
    read afterwards (backlog 874b649b).
    """
    if timeout is None:
        timeout = CHECK_TIMEOUT_S
    out = ""
    kind = "ok"
    rc: int | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        # Missing binary / not executable / cwd gone: the check never ran.
        kind = "unrunnable"
        out = f"{type(exc).__name__}: {exc}"
    else:
        try:
            out, _ = proc.communicate(timeout=timeout)
            rc = proc.returncode
            kind = "ok" if rc == 0 else "exit"
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            kind = "timeout"
        out = out or ""

    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(out, encoding="utf-8", errors="replace")
        except OSError:
            pass

    tail = "\n".join(out.splitlines()[-20:])
    return kind, rc, tail


def _default_notifier(event: str, detail: str) -> None:
    """Send a notification via the native ilk_notify.py subprocess."""
    notify_script = _REPO / "skills" / "ilk-watchdog" / "scripts" / "ilk_notify.py"
    if not notify_script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(notify_script),
             "--event", event,
             "--project", "ilk-skills",
             "--detail", detail],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _notify(data_root: Path, event: str, detail: str,
            notifier_fn: Callable[[str, str], None] | None = None) -> None:
    """Send a notification.  *notifier_fn* overrides the native backend."""
    target = notifier_fn if notifier_fn is not None else _default_notifier
    try:
        target(event, detail)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())