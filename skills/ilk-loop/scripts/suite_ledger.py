#!/usr/bin/env python3
"""Per-tree suite ledger: measure a tree once, look up the result later.

Part of sub-plan ``a-tree-is-measured-once`` (MASTER-2026-10-03i).

Stores full-suite results keyed by tree sha so the verify can look up HEAD
and base verdicts instead of re-measuring.  One writer only (the driver);
worker sessions are refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Sibling modules.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Preserve the original HOME before tests can monkeypatch it.
# Python's user site-packages depends on HOME; changing it breaks
# pytest discovery in subprocesses.
_ORIGINAL_HOME = os.environ.get("HOME", "")

from bounded_run import run as _bounded_run  # noqa: E402


# ── Exceptions ────────────────────────────────────────────────────────────────


class LedgerRefused(PermissionError):
    """Raised when a worker session attempts to write the ledger."""


class LedgerNotConfigured(RuntimeError):
    """Raised when ship.suite is not configured for the project."""


class LedgerUnmeasured(RuntimeError):
    """Raised when a suite run cannot be parsed (timeout, no summary)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ── Path helpers ──────────────────────────────────────────────────────────────


def _git(project: Path, *args: str) -> str | None:
    """Run a read-only git command, returning stripped stdout or None."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=project, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def ledger_root(project: Path) -> Path:
    """Return the repo that owns the git common dir for *project*.

    A selfmod worktree and its clone share one git common dir, so they
    share one ledger.
    """
    common_dir = _git(project, "rev-parse", "--path-format=absolute",
                      "--git-common-dir")
    if not common_dir:
        raise FileNotFoundError(
            f"cannot resolve git-common-dir for {project}")
    return Path(common_dir).parent


def ledger_dir(project: Path) -> Path:
    """Return the ledger directory for *project*.

    The ledger lives under the external logs dir for the git-common-dir
    repo, at ``verification/ledger/``.  A selfmod worktree and its clone
    share one ledger.
    """
    from ilk_paths import external_logs_dir, resolve_project_key  # type: ignore[import-untyped]

    root = ledger_root(project)
    key = resolve_project_key(root)
    if not key:
        raise FileNotFoundError(
            f"no ilk project key resolves from {root}")
    return external_logs_dir(key) / "verification" / "ledger"


# ── Invocation resolution ─────────────────────────────────────────────────────


def _resolve_invocation(project: Path) -> str:
    """Build the expected invocation from ship.suite.

    Reuses ``ship_audit._resolve_expected_invocation`` so the ledger and
    the gate cannot drift.
    """
    try:
        from ship_audit import _resolve_expected_invocation  # type: ignore[import-untyped]
    except ImportError:
        pass
    else:
        inv = _resolve_expected_invocation(project)
        if inv:
            return inv

    # Fallback: read .ilk-launch.json directly.
    config_path = _find_launch_config(project)
    if not config_path:
        raise LedgerNotConfigured(
            f"no .ilk-launch.json found for {project}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerNotConfigured(
            f"cannot read {config_path}: {exc}")
    suite = config.get("ship", {}).get("suite", {})
    command = suite.get("command", "")
    flags = suite.get("flags", [])
    if not command:
        raise LedgerNotConfigured(
            f"ship.suite.command is empty in {config_path}")
    return command if not flags else f"{command} {' '.join(flags)}"


def _find_launch_config(project: Path) -> Path | None:
    """Walk up from *project* looking for .ilk-launch.json."""
    d = Path(project).resolve()
    for _ in range(10):
        candidate = d / ".ilk-launch.json"
        if candidate.is_file():
            return candidate
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def _is_ledger_disabled(project: Path) -> bool:
    """Return True when ship.ledger is explicitly false."""
    config_path = _find_launch_config(project)
    if not config_path:
        return False
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return config.get("ship", {}).get("ledger") is False


# ── Entry helpers ─────────────────────────────────────────────────────────────


def _compute_digest(entry: dict) -> str:
    """Compute the sha256 digest of an entry (excluding the digest field)."""
    copy = {k: v for k, v in entry.items() if k != "digest"}
    return hashlib.sha256(
        json.dumps(copy, sort_keys=True).encode()
    ).hexdigest()


def _validate_entry(entry: dict, invocation: str) -> bool:
    """Return True if *entry* is valid (digest matches, invocation matches)."""
    stored_digest = entry.get("digest")
    if not stored_digest:
        return False
    if _compute_digest(entry) != stored_digest:
        return False
    if entry.get("invocation") != invocation:
        return False
    return True


# ── Core API ──────────────────────────────────────────────────────────────────


def measure(project: Path, sha, *, run_id: str | None = None,
            write: bool = True) -> dict:
    """Measure the suite at *sha* in a shared-clone snapshot and write a ledger entry.

    Args:
        project: Path to the git repo.
        sha: The commit sha to measure (str or Path).
        run_id: Optional run identifier for the entry.
        write: If True, write the entry to disk.  If False, just return it.

    Returns:
        The ledger entry dict.

    Raises:
        LedgerRefused: If called from a worker session.
        LedgerNotConfigured: If ship.suite is not configured.
        LedgerUnmeasured: If the suite run cannot be parsed.
    """
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        raise LedgerRefused("refused in a worker session")

    sha_str = str(sha)
    root = ledger_root(project)
    invocation = _resolve_invocation(project)

    # Import parsers from verification_record.
    from verification_record import (  # type: ignore[import-untyped]
        _parse_for, _base_env, compute_suite_budget,
    )

    parser = _parse_for(invocation)
    budget, _ = compute_suite_budget(project, None)

    # Create a shared-clone snapshot.
    snap_dir = tempfile.mkdtemp(prefix="ilk-ledger-")
    try:
        subprocess.run(
            ["git", "clone", "--shared", "--no-checkout", str(root), snap_dir],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=60,
        )
        subprocess.run(
            ["git", "-C", snap_dir, "checkout", "--detach", sha_str],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=60,
        )

        snap_path = Path(snap_dir)
        env = _base_env(snap_path)
        # Restore original HOME so Python's user site-packages works.
        if _ORIGINAL_HOME:
            env["HOME"] = _ORIGINAL_HOME

        # Run the suite.
        t0 = time.monotonic()
        rc, stdout, stderr, timed_out = _bounded_run(
            invocation, shell=True, cwd=snap_dir, timeout=budget, env=env,
        )
        elapsed = round(time.monotonic() - t0)

        raw_output = (stdout or "") + (stderr or "")

        if timed_out:
            raise LedgerUnmeasured(
                f"suite exceeded {budget}s timeout")

        try:
            parsed = parser(raw_output)
        except ValueError as exc:
            raise LedgerUnmeasured(str(exc))

        # Get tree sha.
        tree = _git(project, "rev-parse", f"{sha_str}^{{tree}}")
        if not tree:
            raise LedgerUnmeasured(
                f"cannot resolve tree for {sha_str}")

        started = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t0))
        finished = time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                 time.localtime(t0 + elapsed))

        import socket
        entry: dict = {
            "schema": 1,
            "tree": tree,
            "commit": sha_str,
            "invocation": invocation,
            "env": "snapshot-clone-v1",
            "counts": parsed["counts"],
            "failing_nodes": sorted(parsed["failing_nodes"]),
            "suite_duration_sec": elapsed,
            "started": started,
            "finished": finished,
            "run_id": run_id,
            "writer": "driver",
            "host": socket.gethostname(),
        }
        entry["digest"] = _compute_digest(entry)

        if write:
            ld = ledger_dir(project)
            ld.mkdir(parents=True, exist_ok=True)

            # Write the raw suite output.
            output_path = ld / f"{tree}.output.txt"
            output_path.write_text(raw_output, encoding="utf-8")
            entry["output_sha256"] = hashlib.sha256(
                raw_output.encode()
            ).hexdigest()

            # Recompute digest with output_sha256 included.
            entry["digest"] = _compute_digest(entry)

            # Write the entry atomically.
            entry_path = ld / f"{tree}.json"
            tmp_path = entry_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(entry, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp_path, entry_path)

        return entry

    finally:
        # Clean up the snapshot.
        import shutil
        shutil.rmtree(snap_dir, ignore_errors=True)


def lookup(project: Path, tree: str,
           invocation: str | None = None) -> dict | None:
    """Look up a ledger entry for *tree*.

    Returns the entry dict if found and valid, None otherwise.  Prints
    the reason on stderr when returning None.
    """
    ld = ledger_dir(project)
    entry_path = ld / f"{tree}.json"

    if not entry_path.is_file():
        print(f"lookup: no entry at {entry_path}", file=sys.stderr)
        return None

    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lookup: cannot read {entry_path}: {exc}", file=sys.stderr)
        return None

    # Resolve invocation if not provided.
    if invocation is None:
        try:
            invocation = _resolve_invocation(project)
        except LedgerNotConfigured as exc:
            print(f"lookup: {exc}", file=sys.stderr)
            return None

    # Validate digest.
    stored_digest = entry.get("digest")
    if not stored_digest:
        print("lookup: no digest in entry", file=sys.stderr)
        return None
    computed = _compute_digest(entry)
    if computed != stored_digest:
        print(
            f"lookup: digest mismatch for {tree}: "
            f"stored={stored_digest[:12]}… computed={computed[:12]}…",
            file=sys.stderr,
        )
        return None

    # Validate invocation.
    if entry.get("invocation") != invocation:
        print(
            f"lookup: invocation mismatch for {tree}: "
            f"entry={entry.get('invocation')!r} requested={invocation!r}",
            file=sys.stderr,
        )
        return None

    return entry


# ── Spawn / wait ──────────────────────────────────────────────────────────────


def spawn(project: Path, sha, *, run_id: str | None = None) -> dict:
    """Spawn a background ledger measurement, returning immediately.

    Returns a dict with ``action`` one of:
    - ``exists``: the entry is already in the ledger.
    - ``running``: a measurement for this tree is already in progress.
    - ``queued``: another tree is running; this one is queued.
    - ``spawned``: a new background job was started.
    - ``disabled``: ship.ledger is false.
    - ``not-configured``: ship.suite is not configured.
    """
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        raise LedgerRefused("refused in a worker session")

    sha_str = str(sha)

    # Check disabled.
    if _is_ledger_disabled(project):
        return {"action": "disabled"}

    # Resolve tree.
    tree = _git(project, "rev-parse", f"{sha_str}^{{tree}}")
    if not tree:
        return {"action": "not-configured", "reason": "cannot resolve tree"}

    # Check existing entry.
    existing = lookup(project, tree)
    if existing:
        return {"action": "exists", "tree": tree}

    ld = ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    # Check running.
    running_path = ld / "running.json"
    if running_path.is_file():
        try:
            running = json.loads(running_path.read_text(encoding="utf-8"))
            pid = running.get("pid")
            if pid and _pid_is_alive(pid):
                if running.get("tree") == tree:
                    return {"action": "running", "tree": tree, "pid": pid}
                # Another tree is running — queue this one.
                queued_path = ld / "queued.json"
                queued_path.write_text(
                    json.dumps({"sha": sha_str, "tree": tree,
                                "run_id": run_id}),
                    encoding="utf-8",
                )
                return {"action": "queued", "tree": tree}
        except (OSError, json.JSONDecodeError):
            pass

    # Double-fork to detach.
    pid = _spawn_detached(project, sha_str, run_id)
    return {"action": "spawned", "tree": tree, "pid": pid}


def _pid_is_alive(pid: int) -> bool:
    """Return True if *pid* is a live process."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _spawn_detached(project: Path, sha_str: str,
                    run_id: str | None) -> int:
    """Double-fork a detached ledger measurement.  Returns the intermediate pid."""
    # Pipe for synchronization: grandchild writes after running.json is created.
    read_fd, write_fd = os.pipe()

    # First fork.
    pid = os.fork()
    if pid > 0:
        # Parent: close write end, wait for signal from grandchild, then wait
        # for intermediate child.
        os.close(write_fd)
        # Block until grandchild writes one byte.
        os.read(read_fd, 1)
        os.close(read_fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        return pid

    # Intermediate child: create new session.
    os.close(read_fd)
    os.setsid()

    # Second fork.
    pid2 = os.fork()
    if pid2 > 0:
        # Intermediate: exit immediately so parent can return.
        os._exit(0)

    # Grandchild: exec the measurement.
    ld = ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)

    # Write running.json.
    running_path = ld / "running.json"
    running_path.write_text(
        json.dumps({"pid": os.getpid(), "tree": _git(project, "rev-parse", f"{sha_str}^{{tree}}"),
                    "sha": sha_str, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}),
        encoding="utf-8",
    )

    # Signal parent that running.json is written.
    os.write(write_fd, b"\x00")
    os.close(write_fd)

    # Build the command line.  Must never contain "run_ilk_loop".
    script = str(_SCRIPTS_DIR / "suite_ledger.py")
    cmd = [sys.executable, script, "measure",
           "--project", str(project), "--sha", sha_str]
    if run_id:
        cmd.extend(["--run-id", run_id])

    # Restore original HOME before exec so Python's user site-packages works.
    if _ORIGINAL_HOME:
        os.environ["HOME"] = _ORIGINAL_HOME

    # exec replaces the process.
    os.execvp(cmd[0], cmd)


def wait_for(project: Path, tree: str, invocation: str,
             timeout_s: int = 120) -> dict | None:
    """Poll until *tree* appears in the ledger or the timeout expires.

    Returns the entry dict if found, None on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        entry = lookup(project, tree, invocation)
        if entry:
            return entry

        # Check if the running job is still alive.
        ld = ledger_dir(project)
        running_path = ld / "running.json"
        if running_path.is_file():
            try:
                running = json.loads(running_path.read_text(encoding="utf-8"))
                pid = running.get("pid")
                if pid and _pid_is_alive(pid) and running.get("tree") == tree:
                    time.sleep(2)
                    continue
            except (OSError, json.JSONDecodeError):
                pass

        # Check queued.
        queued_path = ld / "queued.json"
        if queued_path.is_file():
            try:
                queued = json.loads(queued_path.read_text(encoding="utf-8"))
                if queued.get("tree") == tree:
                    time.sleep(2)
                    continue
            except (OSError, json.JSONDecodeError):
                pass

        # Not running and not queued — no point waiting.
        break

    return None


# ── Contention probe ──────────────────────────────────────────────────────────


def contention(project: Path, probe_paths: list[str],
               repeats: int = 3) -> dict:
    """Measure contention between the ledger run and a probe suite.

    Runs the probe alone *repeats* times, then runs the probe while a
    full suite measurement is in progress *repeats* more times.  Returns
    timing and concurrency-failure data.
    """
    import statistics

    def _run_probe() -> float:
        """Run the probe suite and return elapsed seconds."""
        cmd = f"python3 -m pytest {' '.join(probe_paths)} -q -p no:cacheprovider"
        t0 = time.monotonic()
        _bounded_run(cmd, shell=True, cwd=str(project), timeout=300)
        return time.monotonic() - t0

    # Solo runs.
    solo_times = [_run_probe() for _ in range(repeats)]

    # Concurrent runs: start a full suite in a thread, run probe alongside.
    import threading

    concurrent_times = []
    concurrent_failing = []

    def _full_suite_thread() -> None:
        nonlocal concurrent_failing
        try:
            result = measure(project, _git(project, "rev-parse", "HEAD"),
                             write=False)
            concurrent_failing = result.get("failing_nodes", [])
        except (LedgerUnmeasured, LedgerNotConfigured):
            pass

    suite_thread = threading.Thread(target=_full_suite_thread, daemon=True)
    suite_thread.start()

    for _ in range(repeats):
        concurrent_times.append(_run_probe())

    suite_thread.join(timeout=10)

    # Determine which failing ids pass when run alone (without -n/--dist).
    solo_pass_ids = []
    if concurrent_failing:
        from verification_record import read_baseline_red  # type: ignore[import-untyped]
        baseline_red = read_baseline_red(project)
        # ids that are failing in the concurrent run but NOT in baseline_red
        # and pass when rerun alone.
        for fid in concurrent_failing:
            if fid not in baseline_red:
                # Rerun alone.
                cmd = f"python3 -m pytest {fid} -q -p no:cacheprovider"
                rc, _, _, _ = _bounded_run(
                    cmd, shell=True, cwd=str(project), timeout=60,
                )
                if rc == 0:
                    solo_pass_ids.append(fid)

    solo_median = statistics.median(solo_times)
    concurrent_median = statistics.median(concurrent_times)
    ratio = concurrent_median / solo_median if solo_median > 0 else 1.0

    return {
        "solo_s": solo_median,
        "concurrent_s": concurrent_median,
        "ratio": ratio,
        "concurrent_failing": concurrent_failing,
        "solo_pass_ids": solo_pass_ids,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli() -> int:
    """Command-line interface."""
    ap = argparse.ArgumentParser(
        description="Per-tree suite ledger")
    sub = ap.add_subparsers(dest="command")

    # measure
    p_measure = sub.add_parser("measure",
                                help="Measure a tree and write a ledger entry")
    p_measure.add_argument("--project", type=Path, required=True)
    p_measure.add_argument("--sha", required=True)
    p_measure.add_argument("--run-id")

    # spawn
    p_spawn = sub.add_parser("spawn",
                              help="Spawn a background measurement")
    p_spawn.add_argument("--project", type=Path, required=True)
    p_spawn.add_argument("--sha", required=True)
    p_spawn.add_argument("--run-id")

    # lookup
    p_lookup = sub.add_parser("lookup",
                               help="Look up a tree in the ledger")
    p_lookup.add_argument("--project", type=Path, required=True)
    p_lookup.add_argument("--tree", required=True)

    # wait
    p_wait = sub.add_parser("wait",
                             help="Wait for a tree to appear in the ledger")
    p_wait.add_argument("--project", type=Path, required=True)
    p_wait.add_argument("--tree", required=True)
    p_wait.add_argument("--timeout", type=int, default=120)

    # contention
    p_contention = sub.add_parser("contention",
                                   help="Run contention probe")
    p_contention.add_argument("--project", type=Path, required=True)
    p_contention.add_argument("--probe", nargs="+", required=True)
    p_contention.add_argument("--max-slowdown", type=float, default=1.20)

    args = ap.parse_args()

    if args.command == "measure":
        try:
            entry = measure(args.project, args.sha, run_id=args.run_id)
            print(json.dumps(entry, indent=2, sort_keys=True))
            return 0
        except (LedgerRefused, LedgerNotConfigured, LedgerUnmeasured) as exc:
            print(f"ILK-CHECK: unmeasured {exc}", file=sys.stderr)
            return 1

    elif args.command == "spawn":
        try:
            result = spawn(args.project, args.sha, run_id=args.run_id)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except LedgerRefused as exc:
            print(f"ILK-CHECK: unmeasured {exc}", file=sys.stderr)
            return 1

    elif args.command == "lookup":
        entry = lookup(args.project, args.tree)
        if entry:
            print(json.dumps(entry, indent=2, sort_keys=True))
            return 0
        return 1

    elif args.command == "wait":
        try:
            invocation = _resolve_invocation(args.project)
        except LedgerNotConfigured as exc:
            print(f"ILK-CHECK: unmeasured {exc}", file=sys.stderr)
            return 1
        entry = wait_for(args.project, args.tree, invocation,
                         timeout_s=args.timeout)
        if entry:
            print(json.dumps(entry, indent=2, sort_keys=True))
            return 0
        print(f"ILK-CHECK: unmeasured timeout after {args.timeout}s",
              file=sys.stderr)
        return 1

    elif args.command == "contention":
        result = contention(args.project, args.probe)
        print(json.dumps(result, indent=2, sort_keys=True))
        # Write to ledger dir.
        ld = ledger_dir(args.project)
        ld.mkdir(parents=True, exist_ok=True)
        tree = _git(args.project, "rev-parse", "HEAD^{tree}")
        if tree:
            out_path = ld / f"contention-{tree}.json"
            out_path.write_text(
                json.dumps(result, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        # Check thresholds.
        if result["ratio"] > args.max_slowdown:
            print(f"ILK-CHECK: contention ratio {result['ratio']:.2f} "
                  f"exceeds {args.max_slowdown}", file=sys.stderr)
            return 1
        if result["solo_pass_ids"]:
            print(f"ILK-CHECK: concurrency-only failures: "
                  f"{result['solo_pass_ids']}", file=sys.stderr)
            return 1
        return 0

    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_cli())