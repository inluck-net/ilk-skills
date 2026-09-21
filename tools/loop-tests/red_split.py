#!/usr/bin/env python3
"""red_split — clone-vs-worktree failing-id split.

Runs a selection of pytest node ids under TWO cwd contexts — the current
directory and a scratch `git worktree add --detach` at HEAD — and prints
one table row per id: `node id | clone | worktree | verdict`. The split is
the measurement behind sub-plan suite-greens-last-reds (2026-09-21): the
g2-verify Findings showed a family of ids that pass from the clone and fail
inside ANY git worktree, and this tool turns that observation into a table
with a denominator instead of a remembered list.

Exit codes: 0 for a COMPLETED measurement — pass or fail, it is a report,
like ilk-worker-model show. 2 for usage / parse errors (bad baseline json,
missing ids file, pytest usage error, unresolvable repo): a run that never
measured both contexts must not read as a clean report.

Inputs:
  ids_file (positional)   one node id per line; blank lines and `#`
                          comments ignored
  --from-baseline PATH    release baseline json (`.ilk-baselines/<tag>__<sha>
                          .json`, written by ilk-ship) — reads its node_ids
  --extra ID              repeatable; appended to the selection (the step-0
                          gate adds test_baseline_unchanged this way)

Baseline resolution: PATH is tried as given (cwd-relative), then — because
`.ilk-baselines/` is gitignored and exists only in the main checkout —
under the main checkout root resolved via `git rev-parse --git-common-dir`.
Without the fallback, every invocation from a worktree (the selfmod batch
runs all its gates from one) would die "file not found" on a file the
worktree can never contain.

The suite invocation is the project's DECLARED one (`.ilk-launch.json` →
ship.suite command + flags), not a hand-typed pytest line — the split must
measure what the project's suite would measure, in two contexts. `-v
--tb=no -p no:cacheprovider` are appended so outcomes parse per-id and the
run leaves no cache in the tree it measures.

The "clone" column is the CURRENT DIRECTORY, whatever it is. The preamble
names both context paths, so a run whose "clone" context is itself a
worktree (e.g. from the selfmod worktree) says so instead of implying it
measured the clone.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2

# `path/to/test.py::Class::test_x PASSED [ 12%]` — the token after the node
# id is the outcome; anything after it (percent, skip reason) is ignored.
_OUTCOME_RE = re.compile(r"^(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")

# pytest exit codes that mean the run COMPLETED (0 = all passed,
# 1 = tests failed). Anything else (2 interrupt, 3 internal, 4 usage,
# 5 nothing collected) is not a completed measurement for that context.
_COMPLETED_EXIT = {0, 1}

_FAILISH = {"FAILED", "ERROR"}
_SKIPISH = {"SKIPPED", "XFAIL"}


def die(msg: str) -> "None":
    print(f"red_split: error: {msg}", file=sys.stderr)
    sys.exit(EXIT_USAGE)


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )


def git_checked(*args: str, cwd: Path | None = None) -> str:
    r = git(*args, cwd=cwd)
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed (rc={r.returncode}): "
            f"{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def repo_root(cwd: Path) -> Path:
    return Path(git_checked("rev-parse", "--show-toplevel", cwd=cwd))


def main_checkout_root(cwd: Path) -> Path | None:
    """Root of the main checkout when cwd is (or hangs off) a worktree.

    `git-common-dir` points at the shared `.git` of the main checkout for
    every worktree, and at the repo's own `.git` when cwd IS the main
    checkout — so its parent is always the main checkout root.
    """
    r = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    if r.returncode != 0:
        return None
    common = Path(r.stdout.strip())
    return common.parent


def load_ids(ids_file: Path | None, baseline: str | None,
             extras: list[str]) -> list[str]:
    ids: list[str] = []
    if ids_file is not None:
        if not ids_file.is_file():
            die(f"ids file not found: {ids_file}")
        for line in ids_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    elif baseline is not None:
        path = resolve_baseline(Path(baseline))
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            die(f"cannot read baseline {path}: {exc}")
        node_ids = data.get("node_ids") if isinstance(data, dict) else None
        if not isinstance(node_ids, list) or not node_ids:
            die(f"baseline {path} has no node_ids list")
        ids.extend(str(n) for n in node_ids)
        print(f"# baseline: {path} ({data.get('tag', '?')}, "
              f"{len(node_ids)} ids)")
    else:
        die("no input: pass an ids file or --from-baseline (see --help)")
    ids.extend(extras)
    seen: set[str] = set()
    unique = [i for i in ids if not (i in seen or seen.add(i))]
    return unique


def resolve_baseline(as_given: Path) -> Path:
    """Baseline path as given, else the same relative path in the main
    checkout. `.ilk-baselines/` is gitignored: worktrees never contain it."""
    if as_given.is_file():
        return as_given
    main_root = main_checkout_root(Path.cwd())
    if main_root is not None:
        candidate = main_root / as_given
        if candidate.is_file():
            print(f"# baseline: {as_given} not in cwd — using main checkout "
                  f"copy {candidate}")
            return candidate
    die(f"baseline not found: {as_given}"
        + (f" (also tried {main_root / as_given})" if main_root else ""))


def declared_suite(cwd: Path) -> tuple[list[str], int]:
    """The project's declared suite command + per-context timeout bound."""
    cmd = ["python3", "-m", "pytest"]
    bound = 960  # declared suite timeout (900) + margin, if launch file absent
    launch = cwd / ".ilk-launch.json"
    try:
        cfg = json.loads(launch.read_text(encoding="utf-8-sig"))
        suite = cfg.get("ship", {}).get("suite", {})
        if suite.get("command"):
            cmd = suite["command"].split()
        flags = suite.get("flags") or []
        cmd.extend(str(f) for f in flags)
        if isinstance(suite.get("timeout"), int):
            bound = suite["timeout"] + 60
    except (OSError, json.JSONDecodeError, ValueError):
        pass  # unconfigured context: python3 -m pytest + pytest.ini addopts
    return cmd, bound


def run_context(cmd: list[str], ids: list[str], cwd: Path, bound: int,
                label: str) -> dict[str, str]:
    """Run the selection once in `cwd`; return {node id: outcome}."""
    argv = [*cmd, *ids, "-v", "--tb=no", "-p", "no:cacheprovider"]
    print(f"# running {label} context: {' '.join(cmd)} + {len(ids)} ids",
          file=sys.stderr)
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=bound)
    except subprocess.TimeoutExpired:
        die(f"{label} context exceeded {bound}s — not a completed measurement")
    if r.returncode not in _COMPLETED_EXIT:
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
        die(f"{label} context: pytest exited {r.returncode} "
            "(usage/internal error) — not a completed measurement")
    outcomes: dict[str, str] = {}
    for line in r.stdout.splitlines():
        m = _OUTCOME_RE.match(line.strip())
        if m:
            outcomes[m.group(1)] = m.group(2)
    return outcomes


def classify(clone: str, worktree: str) -> str:
    def failish(cell: str) -> bool:
        return cell in _FAILISH or cell == "absent"

    if failish(clone) and failish(worktree):
        return "red-both"
    if clone in _SKIPISH or worktree in _SKIPISH:
        return "skipped"
    if failish(worktree):
        return "worktree-only red"
    if failish(clone):
        return "clone-only red"
    return "green"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="clone-vs-worktree failing-id split (a report: "
                    "exit 0 whenever the measurement completed)")
    ap.add_argument("ids_file", nargs="?", type=Path, default=None,
                    help="file of node ids, one per line (# comments ok)")
    ap.add_argument("--from-baseline", metavar="PATH", default=None,
                    help="release baseline json to read node_ids from")
    ap.add_argument("--extra", action="append", default=[], metavar="ID",
                    help="node id to append to the selection (repeatable)")
    args = ap.parse_args(argv)

    cwd = Path.cwd()
    root = repo_root(cwd)
    head = git_checked("rev-parse", "HEAD", cwd=cwd)

    ids = load_ids(args.ids_file, args.from_baseline, args.extra)
    cmd, bound = declared_suite(cwd)

    scratch = Path(tempfile.mkdtemp(prefix="ilk-redsplit-"))
    try:
        wt = git("worktree", "add", "--detach", str(scratch), "HEAD",
                 cwd=cwd)
        if wt.returncode != 0:
            die(f"git worktree add failed (rc={wt.returncode}): "
                f"{wt.stderr.strip()}")
        clone_out = run_context(cmd, ids, cwd, bound, "clone")
        worktree_out = run_context(cmd, ids, scratch, bound, "worktree")
    finally:
        cleanup = git("worktree", "remove", "--force", str(scratch), cwd=cwd)
        if cleanup.returncode != 0:
            shutil.rmtree(scratch, ignore_errors=True)
            git("worktree", "prune", cwd=cwd)

    print(f"# red_split @ HEAD {head[:12]} · {len(ids)} ids")
    print(f"# clone context:    {cwd}")
    print(f"# worktree context: {scratch} (detached, removed)")
    print("node id | clone | worktree | verdict")
    tally: dict[str, int] = {}
    absent = 0
    for node in ids:
        c = clone_out.get(node, "absent")
        w = worktree_out.get(node, "absent")
        absent += (c == "absent") + (w == "absent")
        verdict = classify(c, w)
        tally[verdict] = tally.get(verdict, 0) + 1
        print(f"{node} | {c.lower()} | {w.lower()} | {verdict}")
    if absent:
        print(f"# WARNING: {absent} cell(s) absent — id(s) pytest did not "
              f"report (collection error?); counted as red")
    print("# summary: " + " · ".join(
        f"{v}×{n}" for v, n in sorted(tally.items(), key=lambda kv: -kv[1])))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
