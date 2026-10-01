"""Bisect helper to find which commit first broke a set of test nodes.

Called by the runner when the widest gate goes red and the failing node
ids passed at the master's ``base_sha``.  Binary-searches
``git rev-list --first-parent <base>..<head>`` to find the first commit
where the nodes fail.

Also provides ``attribute_red`` for at-base attribution: measures whether
a red gate was caused by the running iteration (owned), an earlier commit
in the batch (inherited), was already red before the batch (pre-existing),
or could not be measured (unmeasured — fail closed, treated as owned).

Stdlib only.  Reads/writes with ``encoding='utf-8-sig'`` where needed.

Usage::

    python3 red_owner.py --repo <r> --base <sha> --head <sha> \\
        --cmd <gate cmd> --node <id> [--node <id> ...] [--budget-s 300]

    python3 red_owner.py --attribute --repo <r> \\
        --iteration-base <sha> --batch-base <sha> --head <sha> \\
        --cmd <gate cmd> [--stdout-file <path>] [--budget-s 300]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Sibling module — bounded subprocess execution with process-group cleanup.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bounded_run import run as _bounded_run  # noqa: E402


def _run_test(repo: Path, cmd: str, nodes: list[str]) -> bool:
    """Run the test command in ``repo``.  Returns True if green."""
    full_cmd = cmd
    if nodes:
        full_cmd = cmd + " " + " ".join(nodes)
    rc, _stdout, _stderr, _timed_out = _bounded_run(
        ["bash", "-c", full_cmd],
        cwd=str(repo), timeout=300,
    )
    return rc == 0


def _commit_subject(repo: Path, sha: str) -> str:
    r = subprocess.run(
        ["git", "log", "--format=%s", "-1", sha],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip()


def _rev_list_first_parent(repo: Path, base: str, head: str) -> list[str]:
    """Return commits in base..head (exclusive of base), first-parent."""
    r = subprocess.run(
        ["git", "rev-list", "--first-parent", f"{base}..{head}"],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"rev-list failed: {r.stderr}"
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def bisect_red_owner(
    repo: Path,
    base: str,
    head: str,
    cmd: str,
    nodes: list[str],
    budget_s: int = 300,
) -> dict:
    """Find the first commit in base..head that breaks the nodes.

    Returns a dict with:
      - ``first_red``: SHA or None
      - ``first_red_subject``: commit subject or None
      - ``base_green``: bool — whether the base is green
      - ``reason``: present only when ``first_red`` is None due to budget
    """
    import time
    start = time.monotonic()

    # Step 1: binary search.
    commits = list(reversed(_rev_list_first_parent(repo, base, head)))
    if not commits:
        return {"first_red": None, "first_red_subject": None, "base_green": True}

    # Create a detached worktree for testing (including the base check).
    workdir = Path(tempfile.mkdtemp(prefix="red-owner-"))
    wt_path = workdir / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_path), base],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    try:
        # Verify base is green (in the worktree, at the base commit).
        base_green = _run_test(wt_path, cmd, nodes)
        if not base_green:
            return {"first_red": None, "first_red_subject": None, "base_green": False}
        lo, hi = 0, len(commits) - 1
        first_red_idx = len(commits)  # sentinel: all green

        max_runs = math.ceil(math.log2(len(commits))) + 1
        runs = 0

        while lo <= hi and runs < max_runs:
            if time.monotonic() - start > budget_s:
                return {"first_red": None, "first_red_subject": None,
                        "base_green": True, "reason": "budget"}

            mid = (lo + hi) // 2
            sha = commits[mid]

            # Checkout the candidate in the worktree.
            subprocess.run(
                ["git", "checkout", "--detach", sha],
                cwd=wt_path, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )

            red_here = not _run_test(wt_path, cmd, nodes)
            runs += 1

            if red_here:
                first_red_idx = mid
                hi = mid - 1
            else:
                lo = mid + 1

        if first_red_idx < len(commits):
            sha = commits[first_red_idx]
            return {
                "first_red": sha,
                "first_red_subject": _commit_subject(repo, sha),
                "base_green": True,
            }
        return {"first_red": None, "first_red_subject": None, "base_green": True}

    finally:
        # Clean up worktree — never touch the live tree.
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        try:
            workdir.rmdir()
        except OSError:
            pass


def _parse_pytest_nodes(stdout: str) -> list[str]:
    """Extract failing node ids from pytest output."""
    import re
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    clean = ansi_re.sub("", stdout)
    node_re = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
    return list(dict.fromkeys(node_re.findall(clean)))


def _run_at_base_worktree(
    repo: Path, base_sha: str, cmd: str, nodes: list[str], budget_s: int,
) -> dict:
    """Run the command (or specific nodes) at base_sha in a detached worktree.

    Returns {node_id: "passed"|"failed"} or {"_command": "passed"|"failed"}
    when nodes is empty.
    """
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="red-owner-attrib-"))
    wt = tmp / "wt"
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_sha],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        if add.returncode != 0:
            raise RuntimeError(
                f"could not create worktree at {base_sha[:12]}: "
                f"{(add.stderr or '').strip()[:200]}"
            )

        if nodes:
            # Run each node individually.
            # Extract pytest options from the command (skip test files).
            # e.g., "python3 -m pytest test_a.py test_b.py -q" -> "python3 -m pytest -q"
            import shlex
            cmd_parts = shlex.split(cmd)
            # Find where pytest options start (after "pytest" and test files).
            pytest_idx = None
            for i, part in enumerate(cmd_parts):
                if part == "pytest":
                    pytest_idx = i
                    break
            if pytest_idx is not None:
                # Collect options (start with -) after pytest.
                options = []
                for part in cmd_parts[pytest_idx + 1:]:
                    if part.startswith("-"):
                        options.append(part)
                base_cmd = " ".join(cmd_parts[:pytest_idx + 1] + options)
            else:
                base_cmd = cmd

            results = {}
            for nid in nodes:
                r = subprocess.run(
                    f"{base_cmd} {nid}", shell=True, cwd=wt,
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=budget_s,
                )
                results[nid] = "passed" if r.returncode == 0 else "failed"
            return results
        else:
            # Run the whole command.
            r = subprocess.run(
                cmd, shell=True, cwd=wt,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=budget_s,
            )
            return {"_command": "passed" if r.returncode == 0 else "failed"}
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def _extract_plan_slug(commit_msg: str) -> str | None:
    """Extract the plan slug from a [plan:<slug>#…] trailer."""
    m = re.search(r"\[plan:([^#\]]+)#", commit_msg)
    return m.group(1) if m else None


def _commit_subject(repo: Path, sha: str) -> str:
    r = subprocess.run(
        ["git", "log", "--format=%s", "-1", sha],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip()


def attribute_red(
    repo: Path,
    iteration_base: str,
    batch_base: str | None,
    head: str,
    cmd: str,
    stdout_tail: str = "",
    budget_s: int = 300,
) -> dict:
    """Attribute a red gate by measurement at the iteration and batch base.

    Returns a dict with:
      - verdict: "owned"|"inherited"|"pre-existing"|"unmeasured"
      - iteration_base, batch_base: the bases used
      - node_ids: list of failing node ids parsed from stdout_tail
      - owner_sha, owner_subject, owner_slug: for inherited verdict
      - reason: human-readable explanation
      - elapsed_s: wall-clock seconds
      - nodes: per-node verdicts when node_ids were parsed

    Verdict semantics:
      - owned: green at iteration base — this iteration broke it.
      - inherited: red at iteration base, green at batch base — an earlier
        commit in this batch broke it.
      - pre-existing: red at both bases (or batch base unresolved).
      - unmeasured: budget ran out, worktree failed, or base unknown.
        Behaves like owned (fail closed).
    """
    start = time.monotonic()

    # Parse node ids from stdout_tail.
    node_ids = _parse_pytest_nodes(stdout_tail) if stdout_tail else []

    # Fail closed: budget of 0 means we can't measure anything.
    if budget_s <= 0:
        return {
            "verdict": "unmeasured",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "budget of 0 — cannot measure",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": [],
        }

    # Validate bases.
    def _sha_exists(sha: str) -> bool:
        r = subprocess.run(
            ["git", "cat-file", "-e", sha],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0

    if not _sha_exists(iteration_base):
        return {
            "verdict": "unmeasured",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": f"iteration base {iteration_base[:12]} not found in repo",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": [],
        }

    batch_base_valid = batch_base and _sha_exists(batch_base)

    # Measure at iteration base.
    try:
        if node_ids:
            iter_results = _run_at_base_worktree(
                repo, iteration_base, cmd, node_ids, budget_s
            )
            iter_green = all(v == "passed" for v in iter_results.values())
        else:
            iter_results = _run_at_base_worktree(
                repo, iteration_base, cmd, [], budget_s
            )
            iter_green = iter_results.get("_command") == "passed"
    except (RuntimeError, subprocess.TimeoutExpired):
        return {
            "verdict": "unmeasured",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "worktree creation or test run failed at iteration base",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": [],
        }

    # If green at iteration base, this iteration broke it (owned).
    if iter_green:
        nodes = []
        if node_ids:
            nodes = [
                {"node_id": nid, "verdict": "owned",
                 "owner_sha": None, "owner_slug": None}
                for nid in node_ids
            ]
        return {
            "verdict": "owned",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "green at iteration base — this iteration broke it",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": nodes,
        }

    # Red at iteration base. Check batch base.
    if not batch_base_valid:
        # Can't measure at batch base — pre-existing (fail closed on base).
        nodes = []
        if node_ids:
            nodes = [
                {"node_id": nid, "verdict": "pre-existing",
                 "owner_sha": None, "owner_slug": None}
                for nid in node_ids
            ]
        return {
            "verdict": "pre-existing",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "red at iteration base and batch base is unresolved",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": nodes,
        }

    # Measure at batch base.
    elapsed_so_far = time.monotonic() - start
    remaining_budget = max(1, budget_s - int(elapsed_so_far))
    try:
        if node_ids:
            batch_results = _run_at_base_worktree(
                repo, batch_base, cmd, node_ids, remaining_budget
            )
            batch_green = all(v == "passed" for v in batch_results.values())
        else:
            batch_results = _run_at_base_worktree(
                repo, batch_base, cmd, [], remaining_budget
            )
            batch_green = batch_results.get("_command") == "passed"
    except (RuntimeError, subprocess.TimeoutExpired):
        # Can't measure at batch base — pre-existing.
        nodes = []
        if node_ids:
            nodes = [
                {"node_id": nid, "verdict": "pre-existing",
                 "owner_sha": None, "owner_slug": None}
                for nid in node_ids
            ]
        return {
            "verdict": "pre-existing",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "red at iteration base and batch base measurement failed",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": nodes,
        }

    if batch_green:
        # Red at iteration base, green at batch base — inherited.
        # Find the owner using bisect_red_owner between batch_base and iteration_base.
        owner_sha = None
        owner_subject = None
        owner_slug = None

        # Budget for bisect: whatever remains.
        elapsed_now = time.monotonic() - start
        bisect_budget = max(1, budget_s - int(elapsed_now))

        if node_ids:
            # Per-node attribution for inherited nodes.
            nodes = []
            for nid in node_ids:
                nid_verdict = "inherited"
                nid_owner_sha = None
                nid_owner_slug = None

                # Check if this specific node was green at iteration base.
                nid_iter = iter_results.get(nid, "failed")
                if nid_iter == "passed":
                    # This node was green at iteration base — owned by this iteration.
                    nid_verdict = "owned"
                else:
                    # This node was red at iteration base — find owner.
                    bisect_result = bisect_red_owner(
                        repo, batch_base, iteration_base, cmd, [nid],
                        budget_s=bisect_budget,
                    )
                    if bisect_result.get("first_red"):
                        nid_owner_sha = bisect_result["first_red"]
                        # Extract slug from commit message.
                        msg = _commit_subject(repo, nid_owner_sha)
                        owner_subject = msg
                        nid_owner_slug = _extract_plan_slug(msg)
                    if nid_verdict == "inherited" and nid_owner_sha:
                        owner_sha = nid_owner_sha
                        owner_slug = nid_owner_slug
                nodes.append({
                    "node_id": nid,
                    "verdict": nid_verdict,
                    "owner_sha": nid_owner_sha,
                    "owner_slug": nid_owner_slug,
                })

            # Record verdict is the strongest: owned > inherited > pre-existing.
            has_owned = any(n["verdict"] == "owned" for n in nodes)
            has_inherited = any(n["verdict"] == "inherited" for n in nodes)
            if has_owned:
                record_verdict = "owned"
            elif has_inherited:
                record_verdict = "inherited"
            else:
                record_verdict = "pre-existing"
        else:
            # Whole command — no per-node breakdown.
            bisect_result = bisect_red_owner(
                repo, batch_base, iteration_base, cmd, [],
                budget_s=bisect_budget,
            )
            if bisect_result.get("first_red"):
                owner_sha = bisect_result["first_red"]
                owner_subject = bisect_result.get("first_red_subject")
                owner_slug = _extract_plan_slug(owner_subject or "")
            record_verdict = "inherited"
            nodes = []

        return {
            "verdict": record_verdict,
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": owner_sha,
            "owner_subject": owner_subject,
            "owner_slug": owner_slug,
            "reason": "red at iteration base, green at batch base — inherited",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": nodes,
        }
    else:
        # Red at both bases — pre-existing.
        nodes = []
        if node_ids:
            nodes = [
                {"node_id": nid, "verdict": "pre-existing",
                 "owner_sha": None, "owner_slug": None}
                for nid in node_ids
            ]
        return {
            "verdict": "pre-existing",
            "iteration_base": iteration_base,
            "batch_base": batch_base,
            "node_ids": node_ids,
            "owner_sha": None,
            "owner_subject": None,
            "owner_slug": None,
            "reason": "red at both iteration base and batch base",
            "elapsed_s": round(time.monotonic() - start, 2),
            "nodes": nodes,
        }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--base", help="base SHA (green) for bisect mode")
    ap.add_argument("--head", help="head SHA")
    ap.add_argument("--cmd", required=True, help="test command to run")
    ap.add_argument("--node", action="append", dest="nodes", default=[],
                    help="test node id to run (repeatable)")
    ap.add_argument("--budget-s", type=int, default=300,
                    help="wall-clock budget in seconds (default: 300)")
    ap.add_argument("--attribute", action="store_true",
                    help="attribution mode: measure at iteration and batch base")
    ap.add_argument("--iteration-base",
                    help="iteration base SHA (for --attribute)")
    ap.add_argument("--batch-base",
                    help="batch base SHA (for --attribute, may be 'none')")
    ap.add_argument("--stdout-file",
                    help="file containing gate stdout (for --attribute); '-' for stdin")
    args = ap.parse_args(argv)

    if args.attribute:
        # Attribution mode.
        if not args.iteration_base:
            print("red_owner: --iteration-base required with --attribute",
                  file=sys.stderr)
            return 2
        if not args.head:
            print("red_owner: --head required with --attribute",
                  file=sys.stderr)
            return 2

        batch_base = args.batch_base
        if batch_base in (None, "none", "None", ""):
            batch_base = None

        # Read stdout_tail from file or stdin.
        stdout_tail = ""
        if args.stdout_file:
            if args.stdout_file == "-":
                stdout_tail = sys.stdin.read()
            else:
                p = Path(args.stdout_file)
                if p.is_file():
                    stdout_tail = p.read_text(encoding="utf-8", errors="replace")

        result = attribute_red(
            args.repo,
            iteration_base=args.iteration_base,
            batch_base=batch_base,
            head=args.head,
            cmd=args.cmd,
            stdout_tail=stdout_tail,
            budget_s=args.budget_s,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    # Bisect mode.
    if not args.base:
        print("red_owner: --base required in bisect mode (or use --attribute)",
              file=sys.stderr)
        return 2
    if not args.head:
        print("red_owner: --head required", file=sys.stderr)
        return 2

    if not args.cmd.strip():
        # An empty command passes at every commit, so a bisect over it can
        # only ever report "no owner" -- a verdict nobody measured.  The
        # runner passed --cmd "" for weeks (design D5); refuse instead.
        print("red_owner: --cmd is empty; nothing to bisect", file=sys.stderr)
        return 2

    result = bisect_red_owner(
        args.repo, args.base, args.head, args.cmd, args.nodes, args.budget_s,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))