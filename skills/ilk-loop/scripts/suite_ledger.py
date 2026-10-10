#!/usr/bin/env python3
"""Per-tree suite ledger: measure a tree once, look up the result later.

Part of sub-plan ``a-tree-is-measured-once`` (MASTER-2026-10-03i).

Stores full-suite results keyed by tree sha so the verify can look up HEAD
and base verdicts instead of re-measuring.  One writer only (the driver);
worker sessions are refused.
"""
from __future__ import annotations

from typing import Callable

import argparse
import hashlib
import json
import shutil
import os
import re
import signal
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


def record_point(project: Path, *, run_id: str, iteration: int,
                 slug: str, master: str, before: str, after: str,
                 shipped: list[str]) -> None:
    """Append one driver-written point row to ``points.jsonl``.

    A point marks a gated boundary: the tree changed from *before* to
    *after*, and the listed *shipped* sub-plans were shipped in that
    boundary.  The entry carries ``writer: "driver"`` and the tree sha
    of *after*.

    Raises ``LedgerRefused`` in a worker session.  Writes nothing when
    *before == after* and *shipped* is empty (no real boundary).
    """
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        raise LedgerRefused("refused in a worker session")

    if before == after and not shipped:
        return

    tree = _git(project, "rev-parse", f"{after}^{{tree}}")
    if not tree:
        return

    entry = {
        "run_id": run_id,
        "iteration": iteration,
        "slug": slug,
        "master": master,
        "before": before,
        "after": after,
        "tree": tree,
        "shipped": shipped,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "writer": "driver",
    }

    ld = ledger_dir(project)
    ld.mkdir(parents=True, exist_ok=True)
    points_path = ld / "points.jsonl"
    with open(points_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


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


# ── Owner resolution ──────────────────────────────────────────────────────────


def _read_points(project: Path) -> list[dict]:
    """Return all rows from ``points.jsonl``."""
    ld = ledger_dir(project)
    points_path = ld / "points.jsonl"
    if not points_path.is_file():
        return []
    rows: list[dict] = []
    for line in points_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _is_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    """Return True if *ancestor* is an ancestor of *descendant*."""
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
    )
    return r.returncode == 0


def _rev_list_count(project: Path, base: str, tip: str) -> int:
    """Return the number of commits in ``<base>..<tip>``."""
    out = _git(project, "rev-list", "--count", f"{base}..{tip}")
    if out is None:
        return 0
    try:
        return int(out)
    except ValueError:
        return 0


# ── Owner resolution ─────────────────────────────────────────────────────────

# Judgment call (MASTER-2026-10-10c, judgment call (a)): owner phase budget
# 300 s.  Basis: 21 ids x 5 bisect probes x 2-5 s per serial id run =
# 210-525 s; 300 s plus the suite (465-787 s measured), the head reruns
# (600 s cap) and at-base stays under the 3660 s step-0 gate cap.  Wrong if
# gh-resolve's 1860 s cap is the binding one — then lower it here, not the
# algorithm.
OWNER_BUDGET_S = 300

# One probe runs ONE id, so xdist buys nothing and every worker start-up is
# paid per probe.  The per-probe ceiling is unchanged from the linear walk.
_OWNER_PROBE_TIMEOUT_S = 120

# ``-n <x>`` / ``-n<x>`` / ``--dist <x>`` / ``--dist=<x>``.
_XDIST_RE = re.compile(r"\s-n\s*\S+|\s--dist(?:=|\s+)\S+")


def strip_xdist(invocation: str) -> str:
    """Drop pytest-xdist worker/distribution flags from *invocation*.

    Public: sub-plan ``both-rerun-arms-run-serially`` reuses it so both rerun
    arms strip the suite's flags the same way.
    """
    return _XDIST_RE.sub("", invocation)


class _OwnerBudgetExceeded(Exception):
    """Internal: the owner phase budget passed while a probe was still owed."""


def _owner_points(project: Path, base_sha: str, head_sha: str,
                  all_points: list[dict] | None = None) -> list[dict]:
    """Step 1: ``points.jsonl`` rows in ``(base_sha, head_sha]``, by ancestry.

    Id-independent, so one ``owners_of`` call reads the points file once.
    """
    if all_points is None:
        all_points = _read_points(project)
    in_range: list[dict] = []
    for p in all_points:
        after = p.get("after", "")
        if not after or after == base_sha:
            continue
        if not _is_ancestor(project, after, head_sha):
            continue
        if _is_ancestor(project, base_sha, after):
            in_range.append(p)
    in_range.sort(key=lambda p: _rev_list_count(project, base_sha, p["after"]))
    return in_range


def _first_parent_commits(project: Path, start: str, end: str) -> list[str]:
    """First-parent commits of ``<start>..<end>``, oldest first.

    ``--reverse`` matters: ``git rev-list`` lists newest-first, and the
    bisect's invariant "base green, head red" needs index 0 to be the commit
    right after base.
    """
    out = _git(project, "rev-list", "--first-parent", "--reverse",
               f"{start}..{end}")
    if out is None:
        return []
    return [c for c in out.splitlines() if c.strip()]


def _owner_point_for(project: Path, in_range: list[dict],
                     commit: str) -> dict | None:
    """The point row whose ``before..after`` range contains *commit*."""
    for p in in_range:
        before = p.get("before", "")
        after = p.get("after", "")
        if not before or not after:
            continue
        if (_is_ancestor(project, before, commit) and
                _is_ancestor(project, commit, after)):
            return p
    return None


def _owner_from_ledger(project: Path, node_id: str, *, base_sha: str,
                       head_sha: str, in_range: list[dict],
                       invocation: str | None) -> tuple[dict | None, list[str]]:
    """Steps 2-3: name the owner from the ledger, else hand back the probe.

    Returns ``(verdict, commits)``.  A non-``None`` *verdict* is terminal.
    ``None`` means "the ledger cannot name an owner": the caller must probe
    the oldest-first first-parent *commits* instead.
    """
    if not in_range:
        return {"slug": None, "sha": None, "how": "unknown"}, []

    # Step 2: determine the starting state (base tree).
    base_tree = _git(project, "rev-parse", f"{base_sha}^{{tree}}")
    base_entry = lookup(project, base_tree, invocation) if base_tree else None
    if base_entry is not None:
        # Green at base if the id is NOT in the base's failing set.
        if node_id in set(base_entry.get("failing_nodes", [])):
            # Already red at base — no owner within the range.
            return {"slug": None, "sha": None, "how": "unknown"}, []

    # Walk points to find last_green and first_red.
    first_red: dict | None = None
    last_green: dict | None = None
    for p in in_range:
        tree = _git(project, "rev-parse", f"{p['after']}^{{tree}}")
        if not tree:
            continue
        entry = lookup(project, tree, invocation)
        if entry is None:
            # No ledger entry for this point — skip it.
            continue
        if node_id in set(entry.get("failing_nodes", [])):
            first_red = p
            break
        last_green = p

    if first_red is None:
        # Points exist but the ledger names no owner: no entry at all, or
        # green at every ledgered point.  Probe base..head instead.  The
        # caller only asks for ids it measured green at base, so "base is
        # green" is measured, not assumed here.
        return None, _first_parent_commits(project, base_sha, head_sha)

    # Step 3: check if every commit in the interval belongs to one slug.
    interval_start = last_green["after"] if last_green else base_sha
    interval_end = first_red["after"]
    commit_list = _first_parent_commits(project, interval_start, interval_end)

    covering_slugs: set[str] = set()
    all_covered = bool(commit_list)
    for commit in commit_list:
        p = _owner_point_for(project, in_range, commit)
        if p is None:
            all_covered = False
            break
        covering_slugs.add(p.get("slug", ""))

    if all_covered and len(covering_slugs) == 1:
        return ({"slug": covering_slugs.pop(), "sha": first_red["after"],
                 "how": "point"}, [])

    # The interval spans more than one slug — probe it (step 4).
    if not commit_list:
        return {"slug": None, "sha": None, "how": "unknown"}, []
    return None, commit_list


def _owner_snapshot(project: Path) -> Path | None:
    """One ``git clone`` shared by every probe of one ``owners_of`` call."""
    snap_dir: str | None = None
    try:
        snap_dir = tempfile.mkdtemp(prefix="ilk-owner-bisect-")
        root = ledger_root(project)
        subprocess.run(
            ["git", "clone", "--shared", "--no-checkout", str(root), snap_dir],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=60,
        )
        return Path(snap_dir)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        if snap_dir:
            shutil.rmtree(snap_dir, ignore_errors=True)
        return None


def _bisect_first_red(snap: Path, commits: list[str], *,
                      invocation: str, node_id: str, runner,
                      deadline: float) -> str | None:
    """First commit in *commits* (oldest-first) where *node_id* is red.

    The caller measured base green and head red, so the predicate is monotone
    and a binary search costs ``ceil(log2(len(commits)))`` probes instead of
    one probe per commit.  Raises ``_OwnerBudgetExceeded`` when *deadline*
    passes; a probe that times out counts as green, as the linear walk did.
    """
    def probe(idx: int) -> bool:
        """True when the id is red at ``commits[idx]``."""
        if time.monotonic() >= deadline:
            raise _OwnerBudgetExceeded(node_id)
        subprocess.run(
            ["git", "-C", str(snap), "checkout", "--detach", commits[idx]],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True, timeout=60,
        )
        remaining = deadline - time.monotonic()
        timeout = max(1, min(_OWNER_PROBE_TIMEOUT_S, int(remaining)))
        rc, _out, _err, timed_out = runner(
            f"{strip_xdist(invocation)} {node_id}",
            shell=True, cwd=str(snap), timeout=timeout,
        )
        return rc != 0 and not timed_out

    lo, hi = 0, len(commits) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if probe(mid):
            hi = mid
        else:
            lo = mid + 1
    # ``lo == hi``.  Index ``len-1`` is never probed inside the loop (``mid``
    # is always < ``hi``), so confirm the head itself when it is the answer:
    # a green head means nothing in the range is red.
    if lo == len(commits) - 1 and not probe(lo):
        return None
    return commits[lo]


def owners_of(project: Path, node_ids, *,
              base_sha: str, head_sha: str,
              budget_s: float = OWNER_BUDGET_S,
              runner=None) -> dict:
    """Resolve the owning sub-plan for every id in *node_ids*.

    Steps 1-3 of :func:`owner_of` run unchanged; step 4 is a binary search
    over the range's first-parent commits under one wall-clock *budget_s*,
    in ONE shared clone, with xdist stripped from every probe.  A caller
    passing a single ``node_id`` string gets that id's verdict dict back; a
    list gets ``{node_id: verdict}`` covering every id it passed.

    Ids the budget does not finish carry ``how: "budget"``; nothing raises
    past the caller.
    """
    single = isinstance(node_ids, str)
    ids = [node_ids] if single else list(node_ids)
    run = runner if runner is not None else _bounded_run

    verdicts: dict[str, dict] = {
        nid: {"slug": None, "sha": None, "how": "unknown"} for nid in ids
    }
    if not ids:
        return verdicts

    try:
        invocation = _resolve_invocation(project)
    except LedgerNotConfigured:
        invocation = None

    # Steps 1-3 share the id-independent work: one points read, one filter.
    in_range = _owner_points(project, base_sha, head_sha)

    deadline = time.monotonic() + budget_s
    snap: Path | None = None
    snap_attempted = False
    try:
        for nid in ids:
            if time.monotonic() >= deadline:
                verdicts[nid] = {"slug": None, "sha": None, "how": "budget"}
                continue
            verdict, commits = _owner_from_ledger(
                project, nid, base_sha=base_sha, head_sha=head_sha,
                in_range=in_range, invocation=invocation)
            if verdict is not None:
                verdicts[nid] = verdict
                continue
            if not commits or invocation is None:
                verdicts[nid] = {"slug": None, "sha": None, "how": "unknown"}
                continue
            if not snap_attempted:
                snap_attempted = True
                snap = _owner_snapshot(project)
            if snap is None:
                verdicts[nid] = {"slug": None, "sha": None, "how": "unknown"}
                continue
            try:
                first_red = _bisect_first_red(
                    snap, commits, invocation=invocation, node_id=nid,
                    runner=run, deadline=deadline)
            except _OwnerBudgetExceeded:
                verdicts[nid] = {"slug": None, "sha": None, "how": "budget"}
                continue
            except (OSError, subprocess.SubprocessError, TimeoutError):
                verdicts[nid] = {"slug": None, "sha": None, "how": "unknown"}
                continue
            if first_red is None:
                verdicts[nid] = {"slug": None, "sha": None, "how": "unknown"}
                continue
            point = _owner_point_for(project, in_range, first_red)
            if point is None:
                verdicts[nid] = {"slug": None, "sha": first_red,
                                 "how": "unknown"}
            else:
                verdicts[nid] = {"slug": point.get("slug"), "sha": first_red,
                                 "how": "bisect"}
    finally:
        if snap is not None:
            shutil.rmtree(snap, ignore_errors=True)

    if single:
        return verdicts[ids[0]]
    return verdicts


def owner_of(project: Path, node_id: str, *,
             base_sha: str, head_sha: str) -> dict:
    """Determine which sub-plan owns a failing *node_id*.

    Returns ``{"slug": str|None, "sha": str|None, "how": "point"|"bisect"|"unknown"}``.

    Thin wrapper over :func:`owners_of` — one id, the default
    :data:`OWNER_BUDGET_S` budget.  The algorithm is documented there.
    """
    return owners_of(project, node_id, base_sha=base_sha, head_sha=head_sha)


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


def _background_measure_command(project: Path, sha_str: str,
                                run_id: str | None = None, *,
                                write: bool = True) -> list[str]:
    """The command a BACKGROUND ledger measurement runs, at the lowest priority.

    Judgment call (owner, 2026-10-04): lower the background run's priority
    rather than its worker count.  With probes overlapping a running suite,
    the probe slowed 1.33x at normal priority (limit 1.20), 1.07x under
    ``taskpolicy -b nice -n 19``, 1.05x at ``-n 4``.  Priority keeps the
    invocation (and so the ledger lookup key and the stored baselines) at the
    configured ``-n 8``.  Wrong if step 2's contention gate still exceeds
    1.20 with this command.  A foreground ``measure()`` (a verify waiting on
    the result) is not wrapped.
    """
    prefix: list[str] = []
    if sys.platform == "darwin" and shutil.which("taskpolicy"):
        prefix = ["taskpolicy", "-b"]
    prefix += ["nice", "-n", "19"]
    script = str(_SCRIPTS_DIR / "suite_ledger.py")
    cmd = [sys.executable, script, "measure",
           "--project", str(project), "--sha", sha_str]
    if run_id:
        cmd.extend(["--run-id", run_id])
    if not write:
        cmd.append("--no-write")
    return prefix + cmd


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
    cmd = _background_measure_command(project, sha_str, run_id)

    # Restore original HOME before exec so Python's user site-packages works.
    if _ORIGINAL_HOME:
        os.environ["HOME"] = _ORIGINAL_HOME

    # Detach stdio. Without this the measurement inherits the caller's
    # stdout, and a caller reading it (the runner's `result=$(... spawn)`)
    # blocks until the whole suite ends -- measured 2026-10-06, run
    # 20261006-174019: the loop sat in phase `between` for 7+ minutes.
    _devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(_devnull, 0)
    _log = os.open(str(ld / f"measure-{sha_str[:12]}.log"),
                   os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(_log, 1)
    os.dup2(_log, 2)
    # Close every other inherited fd.  The runner runs under ilk_run_lock.py's
    # flock, so the measurement otherwise keeps run.lock and every re-dispatch
    # refuses "another runner holds this lock" until the suite ends -- measured
    # 2026-10-06 23:13, gh-resolve: lsof showed the measure on run.lock fd 3u.
    os.closerange(3, os.sysconf("SC_OPEN_MAX"))

    # exec replaces the process.
    os.execvp(cmd[0], cmd)


def _promote_default(pids: list[int]) -> int:
    """Promote background pids to foreground QoS (darwin only).

    Runs ``taskpolicy -B -p <pid>`` per pid and returns how many returned 0.
    On non-darwin this is a no-op returning 0.
    """
    if sys.platform != "darwin" or not shutil.which("taskpolicy"):
        return 0
    ok = 0
    for pid in pids:
        try:
            r = subprocess.run(
                ["taskpolicy", "-B", "-p", str(pid)],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                ok += 1
        except (OSError, subprocess.SubprocessError):
            pass
    return ok


def _all_descendants(pid: int) -> list[int]:
    """Return *pid* plus all its descendants (breadth-first)."""
    result = [pid]
    queue = [pid]
    try:
        out = subprocess.check_output(
            ["ps", "-A", "-o", "pid=,ppid="],
            text=True, encoding="utf-8", errors="replace", timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return result
    children_of: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                c, p = int(parts[0]), int(parts[1])
                children_of.setdefault(p, []).append(c)
            except ValueError:
                pass
    while queue:
        current = queue.pop(0)
        for child in children_of.get(current, []):
            result.append(child)
            queue.append(child)
    return result


def _kill_tree(pid: int) -> None:
    """SIGTERM *pid* and descendants; wait 5 s; SIGKILL survivors."""
    pids = _all_descendants(pid)
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    deadline = time.monotonic() + 5
    for p in pids:
        remaining = max(0, deadline - time.monotonic())
        try:
            os.waitpid(p, os.WNOHANG)
        except (OSError, ChildProcessError):
            pass
        if remaining > 0 and _pid_is_alive(p):
            try:
                os.waitpid(p, 0)
            except (OSError, ChildProcessError):
                pass
    for p in pids:
        if _pid_is_alive(p):
            try:
                os.kill(p, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def wait_for(project: Path, tree: str, invocation: str,
             timeout_s: int = 120, *,
             sha: str | None = None,
             _promote: Callable[[list[int]], int] = _promote_default,
             _lookup: Callable | None = None,
             ) -> dict | None:
    """Poll until *tree* appears in the ledger or the timeout expires.

    When ``running.json`` names our tree with a live pid, the drainer is
    promoted once (``_promote``).  Past the deadline the drainer is
    superseded (killed) so the caller can measure.  Never two suites at
    once.

    Args:
        sha: The commit sha we are waiting for (needed for the ancestor
             check on a superseded drainer).  Read from ``queued.json``
             when absent.
        _promote: Injectable; called once with the drainer's pid tree.
        _lookup: Injectable; defaults to ``lookup``.
    """
    _lookup = _lookup or lookup
    deadline = time.monotonic() + timeout_s
    promoted = False
    while time.monotonic() < deadline:
        entry = _lookup(project, tree, invocation)
        if entry:
            return entry

        # Check if the running job is still alive.
        ld = ledger_dir(project)
        running_path = ld / "running.json"
        live_drainer = False
        if running_path.is_file():
            try:
                running = json.loads(running_path.read_text(encoding="utf-8"))
                pid = running.get("pid")
                if pid and _pid_is_alive(pid):
                    if running.get("tree") == tree:
                        # Promote once: raise the background measure's QoS
                        # so it is not starved while we wait.
                        if not promoted:
                            pids = _all_descendants(pid)
                            n = _promote(pids)
                            print(
                                f"[ledger] promoted background measure pid "
                                f"{pid} ({n} processes): a foreground reader "
                                f"is waiting",
                                file=sys.stderr,
                            )
                            promoted = True
                        time.sleep(2)
                        continue
                    live_drainer = True
                    # Superseded drainer: the running measure is of a
                    # DIFFERENT tree whose sha is a strict ancestor of
                    # our queued sha.  Stop it so we can measure now.
                    queued_path = ld / "queued.json"
                    if queued_path.is_file():
                        try:
                            queued = json.loads(
                                queued_path.read_text(encoding="utf-8"))
                            queued_sha = queued.get("sha", "")
                            running_sha = running.get("sha", "")
                            our_queued = queued.get("tree") == tree
                            if (our_queued and running_sha and queued_sha
                                    and running_sha != queued_sha
                                    and _is_ancestor(
                                        project, running_sha, queued_sha)):
                                _kill_tree(pid)
                                stamp = time.strftime(
                                    "%Y%m%d-%H%M%S", time.gmtime())
                                os.replace(
                                    running_path,
                                    running_path.with_name(
                                        f"running.json.superseded-{stamp}"),
                                )
                                print(
                                    f"[ledger] background measure of "
                                    f"superseded {running_sha[:12]} stopped; "
                                    f"{queued_sha[:12]} is waiting",
                                    file=sys.stderr,
                                )
                                return None
                        except (OSError, json.JSONDecodeError):
                            pass
                elif pid:
                    # Dead pid — set aside so it does not block future spawns.
                    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                    dead_path = running_path.with_name(
                        f"running.json.dead-{stamp}")
                    try:
                        running_path.rename(dead_path)
                    except OSError:
                        pass
                    print(
                        f"[ledger] running.json named dead pid {pid}; "
                        f"set aside",
                        file=sys.stderr,
                    )
            except (OSError, json.JSONDecodeError):
                pass

        # Check queued.  A queued job for our tree can only be drained while
        # a live drainer exists.  With no live drainer the queue is stuck and
        # waiting would sleep until the full gate timeout.
        queued_path = ld / "queued.json"
        if queued_path.is_file():
            try:
                queued = json.loads(queued_path.read_text(encoding="utf-8"))
                if queued.get("tree") == tree:
                    if live_drainer:
                        time.sleep(2)
                        continue
                    print(
                        "[ledger] queued.json names this tree but nothing "
                        "can drain it; measuring here",
                        file=sys.stderr,
                    )
            except (OSError, json.JSONDecodeError):
                pass

        # Not running and not queued — no point waiting.
        break

    # Past the deadline: if we promoted a drainer of our tree and it is
    # still alive, supersede it so the caller can measure instead of
    # running a second suite beside it.
    if promoted:
        running_path = ledger_dir(project) / "running.json"
        if running_path.is_file():
            try:
                running = json.loads(running_path.read_text(encoding="utf-8"))
                pid = running.get("pid")
                if pid and _pid_is_alive(pid) and running.get("tree") == tree:
                    _kill_tree(pid)
                    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                    os.replace(
                        running_path,
                        running_path.with_name(
                            f"running.json.superseded-{stamp}"),
                    )
                    print(
                        f"[ledger] background measure pid {pid} outlived "
                        f"the wait bound ({timeout_s} s); superseded, "
                        f"measuring here",
                        file=sys.stderr,
                    )
            except (OSError, json.JSONDecodeError):
                pass

    return None


# ── Return reds to owner ──────────────────────────────────────────────────────


def _parse_owners_table(text: str) -> dict[str, dict]:
    """Parse the ``## Owners`` section's table.

    Returns ``{node_id: {"slug": str, "sha": str, "how": str}}``.
    An empty dict means no Owners section (or empty table).
    """
    idx = text.find("## Owners")
    if idx == -1:
        return {}
    rest = text[idx + len("## Owners"):]
    nxt = rest.find("\n## ")
    if nxt != -1:
        rest = rest[:nxt]

    owners: dict[str, dict] = {}
    for line in rest.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if set(line) <= set("|- :"):  # separator
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] == "node id":  # header
            continue
        owners[cells[0]] = {
            "slug": cells[1],
            "sha": cells[2],
            "how": cells[3],
        }
    return owners


def _parse_at_base_table(text: str) -> list[list[str]]:
    """Parse the ``## At-base rerun`` section's table rows.

    Returns data rows as lists of stripped cells (drops header and separator).
    """
    idx = text.find("## At-base rerun")
    if idx == -1:
        # Fall back to "## At-base" for older records.
        idx = text.find("## At-base")
    if idx == -1:
        return []
    rest = text[idx:]
    nxt = rest.find("\n## ", 1)
    if nxt != -1:
        rest = rest[:nxt]

    rows: list[list[str]] = []
    for line in rest.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if set(line) <= set("|- :"):  # separator
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    # Drop header row.
    return rows[1:] if len(rows) > 1 else []


def return_reds(project: Path, *, batch: str, plans_dir: Path) -> int:
    """Return attributed red ids to their in-batch owning sub-plans.

    Exit codes:
    - 0: returned successfully.
    - 1: refused (worker session).
    - 3: no owner for some attributed id, or owner sub-plan file missing.
    - 4: id already returned to the same slug.
    - 5: nothing attributed, so nothing was returned.  Distinct from 0 on
      purpose: the runner reads 0 as "returned" and skips the verify
      worker.  A record with only flaky-owed and failed-at-base rows was
      read that way and redispatched gh-resolve 10a every poll with no
      worker (runs 20261010-083327, -083830, recorded as timeout).

    On exit 0, per owner slug: the sub-plan is reopened to
    ``status: in-progress`` at its last step, a local_checks item is
    appended, and a ``#### Returned red`` block is added under
    ``## Findings``.
    """
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        print("return-reds: refused in a worker session", file=sys.stderr)
        return 1

    # Read the verification record.
    from verify_attribution import resolve_batch_record, derive_attributed

    try:
        record_path = resolve_batch_record(project, batch)
    except Exception as exc:
        print(f"return-reds: cannot resolve record: {exc}", file=sys.stderr)
        return 3

    text = record_path.read_text(encoding="utf-8")

    # Parse at-base table to get attributed rows.
    at_base_rows = _parse_at_base_table(text)
    if not at_base_rows:
        print("return-reds: no at-base rows in record", file=sys.stderr)
        return 3

    try:
        bad_rows, _flaky = derive_attributed(at_base_rows)
    except Exception as exc:
        print(f"return-reds: derive_attributed failed: {exc}", file=sys.stderr)
        return 3

    if not bad_rows:
        # Nothing attributed — nothing to return.  Not 0: see exit code 5.
        print("return-reds: nothing attributed, nothing returned", file=sys.stderr)
        return 5

    # Parse Owners table.
    owners = _parse_owners_table(text)
    if not owners:
        print("return-reds: no ## Owners section in record", file=sys.stderr)
        return 3

    # Check every attributed id has an owner.
    for row in bad_rows:
        nid = row[0]
        info = owners.get(nid)
        if info is None:
            print(f"return-reds: {nid} has no owner row", file=sys.stderr)
            return 3
        slug = info["slug"]
        if slug == "—" or not slug:
            print(f"return-reds: {nid} has owner '—'", file=sys.stderr)
            return 3
        # Check owner sub-plan file exists.
        found = False
        for p in plans_dir.iterdir():
            if p.suffix == ".md" and slug in p.name:
                found = True
                break
        if not found:
            print(f"return-reds: owner {slug} has no sub-plan file",
                  file=sys.stderr)
            return 3

    # Check for already-returned ids.
    returned_dir = ledger_dir(project) / "returned"
    for row in bad_rows:
        nid = row[0]
        info = owners.get(nid, {})
        slug = info.get("slug", "")
        returned_path = returned_dir / f"{slug}.json"
        if returned_path.is_file():
            try:
                already = json.loads(returned_path.read_text(encoding="utf-8"))
                if nid in already:
                    print(f"return-reds: {nid} already returned to {slug}",
                          file=sys.stderr)
                    return 4
            except (OSError, json.JSONDecodeError):
                pass

    # Group attributed ids by owner slug.
    by_slug: dict[str, list[str]] = {}
    for row in bad_rows:
        nid = row[0]
        info = owners.get(nid, {})
        slug = info.get("slug", "")
        by_slug.setdefault(slug, []).append(nid)

    # Resolve the configured invocation (for the local_checks item).
    invocation = ""
    try:
        invocation = _resolve_invocation(project)
    except LedgerNotConfigured:
        pass

    # Strip -n N, --dist X, -q, -p, and no:cacheprovider from invocation.
    import re as _re
    stripped = _re.sub(r"\s+-n\s+\d+", "", invocation)
    stripped = _re.sub(r"\s+--dist\s+\S+", "", stripped)
    stripped = _re.sub(r"\s+-q\b", "", stripped)
    stripped = _re.sub(r"\s+-p\s+\S+", "", stripped)

    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # Per owner slug: reopen the sub-plan.
    for slug, ids in by_slug.items():
        # Find the sub-plan file.
        plan_path = None
        for p in plans_dir.iterdir():
            if p.suffix == ".md" and slug in p.name:
                plan_path = p
                break
        if plan_path is None:
            continue  # Already checked above.

        plan_text = plan_path.read_text(encoding="utf-8")

        # Read estimated_steps from frontmatter.
        fm_match = _re.match(r"^---\n(.*?)\n---", plan_text, _re.DOTALL)
        estimated_steps = 2
        if fm_match:
            for line in fm_match.group(1).splitlines():
                m = _re.match(r"estimated_steps:\s*(\d+)", line)
                if m:
                    estimated_steps = int(m.group(1))

        last_step = estimated_steps - 1

        # Update frontmatter: status → in-progress, current_step → last_step.
        plan_text = _re.sub(
            r"^status:\s*\S+", "status: in-progress",
            plan_text, count=1, flags=_re.MULTILINE,
        )
        plan_text = _re.sub(
            r"^current_step:\s*\d+", f"current_step: {last_step}",
            plan_text, count=1, flags=_re.MULTILINE,
        )

        # Build the local_checks command for the returned ids.
        ids_part = " ".join(ids)
        check_command = f"{stripped} {ids_part} -p no:cacheprovider"

        # Append to the last step's local_checks yaml fence.
        step_heading_re = _re.compile(
            rf"^###\s+Step\s+{last_step}(?!\d)", _re.MULTILINE)
        m = step_heading_re.search(plan_text)
        if m:
            after = plan_text[m.end():]
            next_heading = _re.search(r"^###\s+", after, _re.MULTILINE)
            region_end = m.end() + (next_heading.start()
                                    if next_heading else len(after))
            region = plan_text[m.end():region_end]

            fence_re = _re.compile(
                r"^(```\w*)\s*\n(.*?)^```", _re.MULTILINE | _re.DOTALL)
            fm = fence_re.search(region)
            if fm:
                indent = "  "
                # Use a hyphen in the timestamp to avoid YAML colon issues.
                ts_safe = ts.replace(":", "-")
                new_item = (
                    f"{indent}# returned-red {ts_safe}\n"
                    f"{indent}- command: \"{check_command}\"\n"
                    f"{indent}  timeout: 600\n"
                )
                # Insert before the closing ``` fence closer.
                # fm.start(0) is the start of the opening ```,
                # fm.group(2) is the fence content.
                fence_content_end = fm.start(0) + len(fm.group(1)) + 1 + len(fm.group(2))
                insert_pos = m.end() + fence_content_end
                plan_text = (
                    plan_text[:insert_pos] +
                    new_item +
                    plan_text[insert_pos:]
                )

        # Append #### Returned red block after ## Findings heading.
        ids_list = "\n".join(f"  - {nid}" for nid in ids)
        record_str = str(record_path)
        finding_block = (
            f"\n#### Returned red {ts}\n"
            f"- Ids:\n{ids_list}\n"
            f"- Record: {record_str}\n"
            f"- Action: fix these in this sub-plan's scope, gate, ship, "
            f"end your turn\n"
        )

        if "## Findings" in plan_text:
            plan_text = plan_text.replace(
                "## Findings\n", "## Findings\n" + finding_block, 1)
        else:
            plan_text += f"\n## Findings\n{finding_block}\n"

        plan_path.write_text(plan_text, encoding="utf-8")

        # Record returned ids.
        returned_dir.mkdir(parents=True, exist_ok=True)
        returned_path = returned_dir / f"{slug}.json"
        existing: list[str] = []
        if returned_path.is_file():
            try:
                existing = json.loads(
                    returned_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        existing.extend(ids)
        returned_path.write_text(
            json.dumps(existing, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return 0


# ── Contention probe ──────────────────────────────────────────────────────────


# Seconds to let the background suite reach pytest before probing.
_CONTENTION_WARMUP_S = 25


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

    # Concurrent runs: start the full suite exactly as a background ledger
    # run starts it (same command, same priority), wait until pytest is
    # running, then run the probe alongside.  The old in-process thread ran at
    # normal priority and its probes could finish before the snapshot clone
    # had even started pytest (2026-10-04: 0.98 vs 1.33 on the same host).
    import subprocess as _sp
    concurrent_times = []
    concurrent_failing = []
    bg = _sp.Popen(
        _background_measure_command(
            project, _git(project, "rev-parse", "HEAD"), write=False),
        stdout=_sp.PIPE, stderr=_sp.DEVNULL, text=True, encoding="utf-8",
        errors="replace", cwd=str(project),
    )
    time.sleep(_CONTENTION_WARMUP_S)
    for _ in range(repeats):
        if bg.poll() is not None:
            break  # the suite finished before the probe: not a measurement
        concurrent_times.append(_run_probe())
    if not concurrent_times:
        bg.wait()
        raise LedgerUnmeasured(
            "contention: the background suite ended before any probe overlapped it")
    try:
        out, _ = bg.communicate(timeout=900)
        concurrent_failing = json.loads(out or "{}").get("failing_nodes", [])
    except (ValueError, _sp.TimeoutExpired):
        bg.kill()

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
    p_measure.add_argument("--no-write", action="store_true",
                           help="measure only; do not write a ledger entry")

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
    p_wait.add_argument("--sha", default=None,
                        help="commit sha for the ancestor check")
    p_wait.add_argument("--timeout", type=int, default=120)

    # point
    p_point = sub.add_parser("point",
                              help="Record a driver-written point row")
    p_point.add_argument("--project", type=Path, required=True)
    p_point.add_argument("--run-id", required=True)
    p_point.add_argument("--iteration", type=int, required=True)
    p_point.add_argument("--slug", default="")
    p_point.add_argument("--master", default="")
    p_point.add_argument("--before", required=True)
    p_point.add_argument("--after", required=True)
    p_point.add_argument("--shipped", default="")

    # contention
    p_contention = sub.add_parser("contention",
                                   help="Run contention probe")
    p_contention.add_argument("--project", type=Path, required=True)
    p_contention.add_argument("--probe", nargs="+", required=True)
    p_contention.add_argument("--max-slowdown", type=float, default=1.20)

    # return-reds
    p_return = sub.add_parser("return-reds",
                               help="Return attributed reds to their owner")
    p_return.add_argument("--project", type=Path, required=True)
    p_return.add_argument("--batch", required=True)
    p_return.add_argument("--plans-dir", type=Path, required=True)

    args = ap.parse_args()

    if args.command == "measure":
        try:
            entry = measure(args.project, args.sha, run_id=args.run_id,
                            write=not args.no_write)
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
                         timeout_s=args.timeout, sha=args.sha)
        if entry:
            print(json.dumps(entry, indent=2, sort_keys=True))
            return 0
        print(f"ILK-CHECK: unmeasured timeout after {args.timeout}s",
              file=sys.stderr)
        return 1

    elif args.command == "point":
        shipped_list = [s for s in args.shipped.split(",") if s] if args.shipped else []
        try:
            record_point(
                args.project,
                run_id=args.run_id,
                iteration=args.iteration,
                slug=args.slug,
                master=args.master,
                before=args.before,
                after=args.after,
                shipped=shipped_list,
            )
            return 0
        except LedgerRefused as exc:
            print(f"ILK-CHECK: unmeasured {exc}", file=sys.stderr)
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

    elif args.command == "return-reds":
        return return_reds(args.project, batch=args.batch,
                           plans_dir=args.plans_dir)

    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(_cli())