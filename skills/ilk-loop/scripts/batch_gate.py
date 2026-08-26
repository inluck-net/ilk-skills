"""batch_gate — batch-end gate verdict persistence and append freeze.

Persists the result of running the project's declared test suite once at
batch end.  The runner calls this module; it does not own the suite
invocation or the background/poll lifecycle.

Also enforces the append-freeze rule: once the batch gate starts running,
no new sub-plan may be appended to that batch (the verdict would not
cover it).  Appends during the freeze are deferred to the next batch.

Record format (JSON):
{
  "verdict":    "pass" | "fail" | "not_configured" | "error",
  "head_sha":   "<40-char hex>",
  "invocation": "<the command that was run>",
  "timestamp":  "<ISO-8601>"
}

Running-marker format (JSON):
{
  "pid":        <int>,
  "started_at": "<ISO-8601>"
}

Contract governed by detached-component-contracts.md — this module is a
new *writer* of runtime state.  See the "Adding a new reader or writer"
checklist there.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


REQUIRED_FIELDS = ("verdict", "head_sha", "invocation", "timestamp")

# Side-channel for the CLI's report.  The record schema is fixed at four
# fields and readers depend on it, so the excused/undeclared detail is
# printed rather than persisted.
_LAST_GATE_EXCUSED: list = []
_LAST_GATE_UNDECLARED: list = []

#: Poll bound used only when the caller passed none AND the project declared
#: no ``ship.suite.timeout``.  Judgment call 2026-08-26: kept at 600 rather
#: than the 300 ilk-ship/SKILL.md documents for a *missing* ship block —
#: lowering it would newly truncate suites that pass today, the opposite of
#: the defect being fixed.  Wrong if a ship block without a timeout should
#: inherit the missing-block default; that belongs in ship_config, not here.
DEFAULT_POLL_TIMEOUT = 600


@dataclass(frozen=True)
class BatchGateRecord:
    """A validated batch-gate verdict record."""
    verdict: str
    head_sha: str
    invocation: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "head_sha": self.head_sha,
            "invocation": self.invocation,
            "timestamp": self.timestamp,
        }


def record_path(runtime_dir: Path) -> Path:
    """Return the path where the batch-gate record lives."""
    return runtime_dir / "batch-gate.json"


def resolve_runtime_dir(project_path: Path) -> Optional[Path]:
    """Resolve where this project's batch-gate record lives.

    THE single resolver for the record's location.  Every writer and reader
    must call this rather than resolving on its own — the 2026-08-25 defect
    was two callers disagreeing: the runner passed
    ``ilk_paths.external_launcher_dir`` (``<data>/runtime/launcher``) while
    this module's own default was ``external_runtime_dir``
    (``<data>/runtime``), so the gate wrote its marker and suite output in
    one place and ``ship_audit`` looked for the verdict in another.

    The record is *project* runtime state, not launcher state: it outlives
    the run that produced it and is read by the audit long afterwards.  The
    launcher dir holds per-launch ephemera (``running.pid``,
    ``last-exit.json``), so the record does not belong there.

    Returns None when the project key cannot be resolved.
    """
    try:
        from ilk_paths import (  # type: ignore[import-untyped]
            external_runtime_dir, resolve_project_key,
        )
    except ImportError:
        return None
    key = resolve_project_key(project_path)
    if key is None:
        return None
    return external_runtime_dir(key)


def write_record(record: BatchGateRecord, runtime_dir: Path) -> Path:
    """Write a batch-gate record to disk.  Returns the path written."""
    p = record_path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record.to_dict(), indent=2) + "\n",
                 encoding="utf-8")
    return p


def read_record(runtime_dir: Path) -> Optional[BatchGateRecord]:
    """Read and validate a batch-gate record.

    Returns None when the file is missing, has missing fields, or is
    unreadable.  A record missing any REQUIRED_FIELDS is invalid — the
    reader must say so rather than assume a pass.
    """
    p = record_path(runtime_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for field in REQUIRED_FIELDS:
        if field not in data:
            return None
    return BatchGateRecord(
        verdict=data["verdict"],
        head_sha=data["head_sha"],
        invocation=data["invocation"],
        timestamp=data["timestamp"],
    )


# ── record validation ───────────────────────────────────────────────────────

def validate_record(
    record_path: Path,
    expected_head_sha: str,
    expected_invocation: str,
) -> str:
    """Validate a batch-gate record against the project as it is now.

    Returns one of five outcome words:
      fresh            — head_sha and invocation both match
      stale_head       — head_sha differs from current HEAD
      stale_invocation — invocation differs from what ship.suite builds
      incomplete       — a required field is missing
      absent           — no record file exists
    """
    if not record_path.is_file():
        return "absent"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "incomplete"
    if not isinstance(data, dict):
        return "incomplete"
    for field in REQUIRED_FIELDS:
        if field not in data:
            return "incomplete"
    if data["head_sha"] != expected_head_sha:
        return "stale_head"
    if data["invocation"] != expected_invocation:
        return "stale_invocation"
    return "fresh"


def validate_record_detail(
    record_path: Path,
    expected_head_sha: str,
    expected_invocation: str,
) -> str:
    """Validate and return a human-readable detail string.

    Same logic as validate_record, but the result names both sides of
    every mismatch so a reader can see what changed.
    """
    if not record_path.is_file():
        return "absent: no batch-gate record found"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "incomplete: unreadable record"
    if not isinstance(data, dict):
        return "incomplete: record is not a JSON object"
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        return f"incomplete: missing field(s): {', '.join(missing)}"
    if data["head_sha"] != expected_head_sha:
        return (
            f"stale_head: record sha {data['head_sha'][:7]} "
            f"!= current HEAD {expected_head_sha[:7]}"
        )
    if data["invocation"] != expected_invocation:
        return (
            f"stale_invocation: record invocation "
            f"'{data['invocation']}' != expected '{expected_invocation}'"
        )
    return "fresh"


# ── re-entry guard ───────────────────────────────────────────────────────────

def _gate_lock_path(runtime_dir: Path) -> Path:
    """Marker file that prevents re-entry."""
    return runtime_dir / "batch-gate.running"


def _acquire_gate_lock(runtime_dir: Path) -> bool:
    """Try to acquire the in-flight lock.  Returns True if acquired.

    The marker carries pid and started_at so the append-freeze check can
    verify the gate process is still alive (AC-5: stale sentinel).

    Refuses only while a gate process is **actually alive**.  Mere file
    presence used to be enough, which turned a single completed run into a
    permanent tombstone: the marker was never released, so every later gate
    for that project answered "already ran" forever.  Liveness here is the
    same probe ``read_gate_running_state`` already applies for the freeze —
    the two readers of this marker must not disagree about what it means.
    """
    p = _gate_lock_path(runtime_dir)
    if read_gate_running_state(runtime_dir).running:
        return False
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = {"pid": os.getpid(), "started_at": _now_iso()}
    p.write_text(json.dumps(marker), encoding="utf-8")
    return True


def _release_gate_lock(runtime_dir: Path) -> None:
    """Release the re-entry guard.  Idempotent."""
    p = _gate_lock_path(runtime_dir)
    p.unlink(missing_ok=True)


# ── append-freeze check ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class GateRunningState:
    """Whether the batch gate is currently running."""
    running: bool
    pid: Optional[int] = None
    started_at: Optional[str] = None


def read_gate_running_state(runtime_dir: Path) -> GateRunningState:
    """Check whether the batch gate is running.

    Reads the marker file and probes the pid for liveness.  A marker
    whose pid is gone is treated as stale (not running) — the same
    stale-sentinel class that kept dead watchdogs alive for 15 days.
    """
    p = _gate_lock_path(runtime_dir)
    if not p.is_file():
        return GateRunningState(running=False)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt marker → treat as stale
        return GateRunningState(running=False)

    pid = data.get("pid") if isinstance(data, dict) else None
    started_at = data.get("started_at") if isinstance(data, dict) else None

    if pid is None:
        # Legacy format (bare timestamp) — no pid to check, treat as stale
        return GateRunningState(running=False)

    # Probe the pid for liveness (AC-5)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        # Pid is gone → stale marker
        return GateRunningState(running=False)
    except PermissionError:
        # Process exists but owned by another user → still alive
        pass

    return GateRunningState(running=True, pid=pid, started_at=started_at)


# ── append-freeze ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AppendResult:
    """Result of an attempt to append a sub-plan to a batch."""
    status: str            # "appended" | "deferred" | "rejected"
    message: str           # human-readable explanation
    deferred_to: Optional[str] = None  # next batch identifier, if deferred


def append_subplan_if_allowed(
    plans_dir: Path,
    runtime_dir: Path,
    slug: str,
    body: str,
    filename_prefix: str = "2026-08-25",
) -> AppendResult:
    """Append a sub-plan to the current batch, or defer if the gate is running.

    This is the real append path — the same entry point /ilk-plan workflow #3
    uses.  AC-1b requires the freeze to be exercised through here, not just
    in a predicate called in isolation.

    AC-1:  refused while running, with message naming batch, start time, reason.
    AC-2:  defers rather than discards — caller can distinguish "deferred".
    AC-3:  appending before the gate starts works exactly as today.
    AC-4:  freeze lifts once the gate completes (marker removed).
    AC-5:  stale running-state (pid gone) does not freeze forever.
    """
    state = read_gate_running_state(runtime_dir)

    if state.running:
        # AC-1: refuse with a message naming the batch, start time, reason
        msg = (
            f"Batch gate is running (pid={state.pid}, "
            f"started_at={state.started_at}). "
            f"Sub-plan '{slug}' cannot be appended — the verdict would not "
            f"cover it.  Deferred to the next batch."
        )
        return AppendResult(
            status="deferred",
            message=msg,
            deferred_to="next-batch",
        )

    # AC-3: no freeze → append as today
    plans_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{filename_prefix}-{slug}.md"
    target = plans_dir / filename
    target.write_text(body, encoding="utf-8")

    return AppendResult(
        status="appended",
        message=f"Sub-plan '{slug}' appended as {filename}.",
    )


# ── git helper ───────────────────────────────────────────────────────────────

def _git_head_sha(project_path: Path) -> str:
    """Capture HEAD sha.  Returns 'unknown' on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if len(out) == 40 and all(c in "0123456789abcdef" for c in out):
            return out
    except (subprocess.CalledProcessError, OSError):
        pass
    return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


# ── skill-root resolution ────────────────────────────────────────────────────

def _skill_root() -> Path:
    """Resolve the skill root directory.

    Walks up from this file to find the ``skills/`` directory that
    contains ``ilk-loop``, ``ilk-ship``, etc.  This works both in the
    project tree and in the installed tree.
    """
    cur = Path(__file__).resolve().parent  # scripts/
    for _ in range(6):
        if cur.name == "scripts" and cur.parent.name.startswith("ilk-"):
            candidate = cur.parent.parent  # skills/
            if (candidate / "ilk-ship" / "scripts" / "ship_config.py").is_file():
                return candidate
        cur = cur.parent
        if cur == cur.parent:
            break
    raise FileNotFoundError(
        "Cannot resolve skill root from batch_gate.py — "
        "expected skills/ilk-ship/scripts/ship_config.py to exist."
    )


# ── main entry point ─────────────────────────────────────────────────────────

def _parse_failing_node_ids(output_path: Path) -> list[str]:
    """Node ids from pytest's ``FAILED``/``ERROR`` summary lines."""
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ids: list[str] = []
    for line in text.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            rest = line.split(" ", 1)[1]
            ids.append(rest.split(" - ", 1)[0].strip())
    return ids


def _undeclared_failures(node_ids: list[str], baseline_red: list) -> list[str]:
    """Failures NOT covered by a baseline_red declaration.

    A declaration matches its own node id exactly, or acts as a file-level
    prefix when it names a file and the failure is a node inside it.  The
    prefix must end at a ``::`` boundary so ``tests/test_known.py`` cannot
    excuse ``tests/test_known_other.py`` — excusing a sibling by string
    prefix is how a declaration would start covering regressions.
    """
    declared = [str(e.get("node_id", "")) for e in (baseline_red or [])
                if isinstance(e, dict) and e.get("node_id")]
    out: list[str] = []
    for nid in node_ids:
        if any(nid == d or nid.startswith(d + "::") for d in declared):
            continue
        out.append(nid)
    return out


def run_batch_gate(
    project_path: Path,
    runtime_dir: Path,
    *,
    _wait_helper: Optional[Path] = None,
    _poll_timeout: Optional[int] = None,
) -> Optional[BatchGateRecord]:
    """Run the batch-end gate: resolve suite, execute, persist verdict.

    Returns the record written, or None if re-entry was detected (the
    gate already ran).  The runner calls this at the ALL-SHIPPED point.

    AC-1: runs exactly once per batch — guarded by the persisted record,
          which is keyed on the HEAD the suite ran against.
    AC-5: missing/unrunnable/failing suite → fail, never pass.
    """
    head_sha = _git_head_sha(project_path)

    # ── re-entry guard (AC-1) ────────────────────────────────────────────
    # The *record* is the guard: this batch's HEAD already has a verdict, so
    # re-running would only re-derive it.  The running marker is a separate
    # thing — an in-flight lock, released below.  Conflating the two is what
    # made one run disable the gate permanently.
    existing = read_record(runtime_dir)
    if existing is not None and existing.head_sha == head_sha:
        return None  # already ran for this HEAD

    if not _acquire_gate_lock(runtime_dir):
        return None  # a gate process is running right now

    try:
        rec = _run_gate_inner(
            project_path, runtime_dir,
            _wait_helper=_wait_helper,
            _poll_timeout=_poll_timeout,
        )
    except Exception:
        # AC-6: gate code error → record error, never hang
        rec = BatchGateRecord(
            verdict="error",
            head_sha=head_sha,
            invocation="<gate-code-error>",
            timestamp=_now_iso(),
        )
    finally:
        _release_gate_lock(runtime_dir)

    # AC-3: persist on EVERY path.  A verdict that is computed and returned
    # but never written is a verdict nobody can read — the gate reported
    # `fail` to stdout on 2026-08-25 while disk kept a 3-hour-old `pass`.
    write_record(rec, runtime_dir)
    return rec


def _run_gate_inner(
    project_path: Path,
    runtime_dir: Path,
    *,
    _wait_helper: Optional[Path] = None,
    _poll_timeout: Optional[int] = None,
) -> BatchGateRecord:
    """Core gate logic — separated for clean error wrapping."""
    # ── resolve suite invocation ─────────────────────────────────────────
    # Import here to avoid hard dependency at module level
    sys_path_backup = list(__import__("sys").path)
    try:
        __import__("sys").path.insert(
            0, str(_skill_root() / "ilk-ship" / "scripts"))
        from ship_config import NotConfigured, load_ship_config  # type: ignore[import-untyped]
    finally:
        __import__("sys").path[:] = sys_path_backup

    config = load_ship_config(project_path)
    head_sha = _git_head_sha(project_path)

    if isinstance(config, NotConfigured):
        # AC-5: surface the config path so the runner reports what did not run.
        if config.resolved_path:
            inv = f"not_configured: no ship block in {config.resolved_path}"
        else:
            inv = "not_configured: no .ilk-launch.json found"
        return BatchGateRecord(
            verdict="not_configured",
            head_sha=head_sha,
            invocation=inv,
            timestamp=_now_iso(),
        )

    invocation = config.ship["suite"]["command"]
    flags = config.ship["suite"].get("flags", [])
    full_cmd = invocation if not flags else f"{invocation} {' '.join(flags)}"

    # ── resolve the poll bound: explicit > declared > fallback ───────────
    # gh-resolve declares ship.suite.timeout: 1800 and nothing read it: the
    # runner passes no --poll-timeout and the default was 600, while its
    # suite measures 925.79s.  The bound expired 325s before the suite could
    # finish, so that project could never get a real verdict.
    if _poll_timeout is not None:
        poll_bound = _poll_timeout
    else:
        declared = config.ship["suite"].get("timeout")
        poll_bound = (
            int(declared)
            if isinstance(declared, int) and not isinstance(declared, bool)
            and declared > 0
            else DEFAULT_POLL_TIMEOUT
        )

    # ── run the suite backgrounded + polled ──────────────────────────────
    output_file = runtime_dir / "batch-gate-suite.output"
    wait = _wait_helper or (_skill_root() / "ilk-loop" / "scripts" /
                            "wait_for_background_output.sh")

    # The helper polls for an "[exited with code N]" marker, which the
    # harness appends when *it* auto-backgrounds a Bash call.  Nothing
    # appends it to a Popen'd process's stdout, so without this wrapper the
    # poll can never succeed: it burns its full bound and returns 125, and
    # the verdict is `fail` no matter what the suite did.  Measured on run
    # 20260825-180144 — 0 occurrences of the marker in 3063 lines of output,
    # 263s spent waiting after the suite had already finished.
    wrapped_cmd = f"{full_cmd}\nprintf '[exited with code %s]\\n' \"$?\"\n"

    proc = subprocess.Popen(
        wrapped_cmd,
        shell=True,
        stdout=open(output_file, "w"),  # noqa: SIM115
        stderr=subprocess.STDOUT,
        cwd=project_path,
    )

    # Poll with the shipped helper (AC-2: never foreground)
    try:
        result = subprocess.run(
            ["bash", str(wait), str(output_file),
             "--timeout", str(poll_bound)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=poll_bound + 30,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        exit_code = 125  # wait_for_background_output's inconclusive code
    finally:
        proc.kill()   # no-op once the marker was written; bounds the timeout path
        proc.wait()   # reap — the 2026-08-25 run left a <defunct> child

    if exit_code == 0:
        verdict = "pass"
        excused, undeclared = [], []
    else:
        # A non-zero suite is not automatically a failed batch: `baseline_red`
        # declares known-red node ids with a reason and a date.  Until
        # 2026-08-26 this module had ZERO references to it, so a project with
        # any inherited failure could never record `pass` — and therefore
        # /ilk-ship Phase 0 could never release it, whatever the batch did.
        #
        # The distinction is the whole value: an UNDECLARED failure still
        # fails.  A declaration is a written claim, not an amnesty for
        # whatever fails beside it.
        failing = _parse_failing_node_ids(output_file)
        undeclared = _undeclared_failures(failing, config.ship.get("baseline_red", []))
        excused = [f for f in failing if f not in undeclared]
        # No parsed failures at all + non-zero exit means the suite died for
        # some other reason (collection error, crash, timeout) — fail, never
        # excuse what we could not read.
        verdict = "pass" if (failing and not undeclared) else "fail"
    _LAST_GATE_EXCUSED[:] = excused
    _LAST_GATE_UNDECLARED[:] = undeclared

    return BatchGateRecord(
        verdict=verdict,
        head_sha=head_sha,
        invocation=full_cmd,
        timestamp=_now_iso(),
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Run the batch-end gate and persist the verdict.",
    )
    parser.add_argument("--project", type=Path, required=True,
                        help="Project root path")
    parser.add_argument("--runtime-dir", type=Path, default=None,
                        help="Runtime dir (default: resolve from ilk_paths)")
    parser.add_argument("--run", action="store_true", required=True,
                        help="Actually run the gate")
    parser.add_argument("--poll-timeout", type=int, default=None,
                        help="Override the poll bound (seconds).  Unset by "
                             "default so the project's declared "
                             "ship.suite.timeout wins; falls back to "
                             f"{DEFAULT_POLL_TIMEOUT}s when neither is given.")
    args = parser.parse_args()

    if not args.run:
        parser.print_help()
        return

    project = args.project.resolve()
    if args.runtime_dir:
        runtime = args.runtime_dir.resolve()
    else:
        resolved = resolve_runtime_dir(project)
        if resolved is None:
            print("[batch-gate] ERROR: cannot resolve project key",
                  file=__import__("sys").stderr)
            raise SystemExit(1)
        runtime = resolved

    rec = run_batch_gate(project, runtime, _poll_timeout=args.poll_timeout)
    if rec is None:
        print("[batch-gate] Re-entry detected — gate already ran. Skipping.")
        return

    print(f"[batch-gate] verdict={rec.verdict} head_sha={rec.head_sha[:12]}")
    # A pass that hides declared failures must say so, or it reads as green.
    if _LAST_GATE_EXCUSED:
        print(f"[batch-gate] {len(_LAST_GATE_EXCUSED)} failure(s) excused by "
              f"ship.baseline_red")
    if _LAST_GATE_UNDECLARED:
        print(f"[batch-gate] {len(_LAST_GATE_UNDECLARED)} UNDECLARED failure(s) "
              f"— not covered by ship.baseline_red:")
        for nid in _LAST_GATE_UNDECLARED:
            print(f"[batch-gate]   {nid}")

    # The runner captures $? (run_ilk_loop_claude.sh:1333).  Exiting 0 on a
    # failing verdict is why a 32-failure suite printed under the runner's
    # "Gate completed." banner on 2026-08-25.
    #
    # `not_configured` stays 0 deliberately: SP6 created that verdict so
    # "no suite" would not read as "suite failed", and one exit code for
    # both undoes it.  It is still printed, and plan_lint flags the batch at
    # plan time.  Wrong if batch-end should hard-stop on zero coverage —
    # that wants a third exit code, not this one folded into 1.
    if rec.verdict in ("fail", "error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
