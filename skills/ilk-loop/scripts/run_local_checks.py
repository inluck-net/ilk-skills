#!/usr/bin/env python3
"""
run_local_checks.py — execute the `local_checks` declared in a sub-plan.

Resolves the sub-plan file (by slug) under <project>/docs/plans/, reads
both the frontmatter `local_checks` (sub-plan level) and, if --step is
given, the per-step `local_checks:` yaml block under the matching
`### Step N` heading. Runs each command via the shell with the given
timeout; cwd = the project root.

Output is structured JSON on stdout (one JSON object). Exit code:
  0  every check passed
  1  at least one check failed (details in JSON)
  2  configuration error (sub-plan not found, malformed yaml, etc.)

This script is a pure observer. It does NOT mutate any file, commit
anything, or signal the loop driver to stop. Callers (the loop driver
or an interactive agent) decide what to do with the results.

By default the working tree is isolated to HEAD before running checks
(stashing uncommitted changes, restoring them afterwards). Use --no-isolate
to skip this for debugging; results then say nothing about what will ship.

Usage:
  run_local_checks.py --project <path> --slug <subplan-slug> [--step N] [--no-isolate]

Environment: requires Python 3.8+. Uses stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Sibling module — resolves plans dir under the new ~/.ilk-data convention,
# and resolves meta-project state for per-sub-plan cwd switching.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import (  # noqa: E402
    find_plans_dir as _resolve_plans_dir,
    find_project_root,
    read_meta_manifest,
    MetaManifestError,
)

# ship_config lives under ilk-ship/scripts/ — add to path so we can read
# path_prelude from the project's .ilk-launch.json.
_SHIP_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-ship" / "scripts"
if str(_SHIP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SHIP_SCRIPTS_DIR))


# ── front-matter / yaml helpers (tiny stdlib parser, schema-specific) ────────

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_text(path: Path) -> str:
    # utf-8-sig tolerates BOM (Windows Notepad, PowerShell -Encoding UTF8)
    return path.read_text(encoding="utf-8-sig")


def split_frontmatter(body: str) -> tuple[str, str]:
    """Return (frontmatter_text, body_after_frontmatter). Empty fm if none."""
    m = FRONTMATTER_RE.match(body)
    if not m:
        return "", body
    return m.group(1), body[m.end():]


def parse_local_checks_block(yaml_text: str) -> list[dict]:
    """
    Parse a `local_checks:` list out of a yaml-ish text block.

    Schema:
      local_checks:
        - command: <one-line or quoted string>
          timeout: <int seconds>
        - command: ...

    Tiny parser tuned for the schema; not a general yaml parser. Returns
    [] when local_checks is missing, empty, or set to `[]`.
    """
    lines = yaml_text.splitlines()
    out: list[dict] = []
    in_block = False
    indent = None
    cur: dict | None = None

    for raw in lines:
        # Strip trailing newline; preserve leading whitespace for indent detection
        line = raw.rstrip("\n").rstrip("\r")

        if not in_block:
            stripped = line.strip()
            if stripped.startswith("local_checks:"):
                # Inline empty list?
                rhs = stripped[len("local_checks:"):].strip()
                if rhs in ("[]", "[ ]"):
                    return []
                in_block = True
                continue
            continue

        # In the local_checks block. Detect end-of-block: a non-empty line that
        # is no longer indented under it.
        if line.strip() == "":
            continue
        # First content line gives us the block indent
        if indent is None:
            stripped_lead = line.lstrip(" ")
            if not stripped_lead.startswith("-"):
                # local_checks: was followed by something other than a list — stop
                break
            indent = len(line) - len(stripped_lead)

        leading = len(line) - len(line.lstrip(" "))
        if leading < indent and line.strip() and not line.lstrip(" ").startswith("#"):
            break  # exited the block

        s = line.strip()
        if s.startswith("- "):
            if cur is not None:
                out.append(cur)
            cur = {}
            inner = s[2:]
            if ":" in inner:
                k, _, v = inner.partition(":")
                cur[k.strip()] = _coerce(v.strip())
        elif ":" in s and cur is not None:
            k, _, v = s.partition(":")
            cur[k.strip()] = _coerce(v.strip())
        # else: ignore (comments, malformed lines)
    if cur is not None:
        out.append(cur)
    # Filter to only entries that have a command
    return [c for c in out if c.get("command")]


def _coerce(s: str) -> Any:
    if s == "":
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.isdigit():
        return int(s)
    # Strip surrounding quotes if any, and unescape per YAML scalar rules.
    # A double-quoted command like  "node -e \"JSON.parse(...)\""  must yield
    #   node -e "JSON.parse(...)"
    # not the literal  node -e \"...\"  — otherwise it reaches `bash -c` with
    # stray backslashes and dies with a syntax error near `(`, producing a
    # false local_checks_failed (FM-0004). Conservative: only unescape \" and
    # \\ (two-step via a sentinel) so other backslashes — Windows paths, \n in
    # a tool arg — are left untouched.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
        else:  # single-quoted YAML: only '' -> ' is an escape
            inner = inner.replace("''", "'")
        return inner
    return s


# ── sub-plan resolution ──────────────────────────────────────────────────────

def find_subplan(project: Path, slug: str) -> Path | None:
    """Find the sub-plan whose frontmatter `plan:` field matches slug."""
    plans_dir, _ = _resolve_plans_dir(project)
    if plans_dir is None or not plans_dir.is_dir():
        return None
    for p in sorted(plans_dir.glob("*.md")):
        try:
            fm, _ = split_frontmatter(read_text(p))
        except Exception:
            continue
        for line in fm.splitlines():
            s = line.strip()
            if s.startswith("plan:"):
                v = s[len("plan:"):].strip()
                if _coerce(v) == slug:
                    return p
                break
    return None


def extract_subplan_field(fm_text: str, field: str) -> str:
    """Read a flat `field: value` from a sub-plan's frontmatter text.
    Returns "" when the field is absent or empty.
    """
    needle = f"{field}:"
    for line in fm_text.splitlines():
        s = line.strip()
        if s.startswith(needle):
            v = s[len(needle):].strip()
            return str(_coerce(v))
    return ""


def resolve_run_cwd(project: Path, subplan: Path, fm_text: str) -> tuple[Path, str | None]:
    """Decide where `local_checks` commands should execute.

    Returns (cwd, error). On error the cwd value is still safe to use
    for diagnostic purposes but the caller should refuse to run.

    Rules:
      single mode → cwd = project root, error = None
      meta mode + valid `repo:` field → cwd = member repo path
      meta mode + missing `repo:` field → error
      meta mode + unknown `repo:` value → error
    """
    root, kind = find_project_root(project)
    if kind == "single" or root is None:
        return project, None

    repo_name = extract_subplan_field(fm_text, "repo")
    if not repo_name:
        return project, (
            f"sub-plan {subplan.name} is missing `repo:` frontmatter, "
            "which is required in meta projects (see .ilk-meta.json)"
        )
    try:
        manifest = read_meta_manifest(root)
    except MetaManifestError as e:
        return project, f"meta manifest invalid: {e}"
    for member in manifest["repos"]:
        if member["name"] == repo_name:
            return member["path"], None
    known = sorted(m["name"] for m in manifest["repos"])
    return project, (
        f"sub-plan {subplan.name} declares repo={repo_name!r} which is not "
        f"in .ilk-meta.json. Known members: {known}"
    )


def extract_step_local_checks(body: str, step_n: int) -> list[dict]:
    """
    Extract local_checks declared inside a per-step yaml fence.

    Looks for `### Step <N>` (or `### Step <N> —`) heading, then the next
    fenced block (```yaml or ``` ) immediately following, and parses
    local_checks from it.
    """
    # Find the heading
    pat = re.compile(rf"^###\s+Step\s+{step_n}(\s|—|-|$)", re.MULTILINE)
    m = pat.search(body)
    if not m:
        return []
    after = body[m.end():]
    # Find the next fenced block before the next ### heading
    next_heading = re.search(r"^###\s+", after, re.MULTILINE)
    region = after[: next_heading.start()] if next_heading else after
    fence = re.search(r"^```(?:yaml|yml)?\s*\n(.*?)^```", region, re.MULTILINE | re.DOTALL)
    if not fence:
        return []
    return parse_local_checks_block(fence.group(1))


# ── runner ───────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    command: str
    scope: str  # "subplan" | "step"
    timeout: int
    exit_code: int | None
    duration_sec: float
    passed: bool
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""


_STEP_HEADING_ANY_RE = re.compile(r"^###\s+Step\s+(\d+)", re.MULTILINE)


def collect_declared_local_checks(fm_text: str, body: str) -> list[dict]:
    """Every gate a sub-plan declares: frontmatter block + per-step blocks.

    A sub-plan may declare gates in frontmatter, in per-step ```yaml blocks, or
    both.  /ilk-plan writes ``local_checks: []`` in frontmatter and puts the
    real gates under each ``### Step N``, so a frontmatter-only reader reports
    a gated sub-plan as ungated.  That made three readers disagree with
    ``_detect_local_checks.py`` (which was already per-step aware):

      * ``ship_audit.read_subplan_for_audit``   -> ``final_gate: None``
      * ``ship_integrity.read_subplan_status_and_checks``
        -> ``evaluate_ship`` took the "no gate declared" branch and enforced
           nothing
      * ``run_ilk_loop_claude.sh`` ``test_ship_integrity``
        -> ``head -20 | grep`` skipped the file outright

    Observed 2026-08-21 on MASTER-2026-08-21-loop-execution-speed: 3 of 3
    sub-plans audited as ungated while every step carried a real gate.  This
    function is the single oracle; callers must not re-parse.

    De-duplicates on ``(command, timeout)`` so a gate repeated in frontmatter
    and in a step is counted once.
    """
    checks: list[dict] = list(parse_local_checks_block(fm_text))
    seen = {(c.get("command"), c.get("timeout")) for c in checks}
    for step_n in sorted({int(n) for n in _STEP_HEADING_ANY_RE.findall(body)}):
        for chk in extract_step_local_checks(body, step_n):
            key = (chk.get("command"), chk.get("timeout"))
            if key not in seen:
                seen.add(key)
                checks.append(chk)
    return checks


def _resolve_bash() -> str | None:
    """Resolve a bash that can run posix gate commands with a Windows cwd.

    Prefer git-bash (MSYS); AVOID the WSL shim at ...\\WindowsApps\\bash.exe —
    it uses /mnt/c mounts and fails on a Windows cwd / C: path (exit 127). On
    posix the system bash is fine.
    """
    import os
    import shutil
    if os.name != "nt":
        return shutil.which("bash") or "/bin/bash"
    git = shutil.which("git")
    if git:
        gitdir = os.path.dirname(os.path.dirname(git))
        for rel in (os.path.join("bin", "bash.exe"), os.path.join("usr", "bin", "bash.exe")):
            cand = os.path.join(gitdir, rel)
            if os.path.isfile(cand):
                return cand
    for pf in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
               os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if pf:
            cand = os.path.join(pf, "Git", "bin", "bash.exe")
            if os.path.isfile(cand):
                return cand
    cand = shutil.which("bash")
    if cand and "windowsapps" not in cand.lower():
        return cand
    return None


def _read_path_prelude(project: Path) -> str:
    """Read ship.suite.path_prelude from .ilk-launch.json.

    Returns the prelude string, or "" if not configured / not readable.
    Does NOT validate — ship_config.load_ship_config owns validation.
    This is a targeted reader for the hot path (every gate invocation).
    """
    try:
        from ship_config import load_ship_config, ShipConfig  # noqa: E402
        result = load_ship_config(project)
        if isinstance(result, ShipConfig):
            return result.ship.get("suite", {}).get("path_prelude", "")
    except Exception:
        pass
    return ""


def run_one(check: dict, scope: str, project: Path, default_timeout: int = 120) -> CheckResult:
    cmd = check.get("command", "")
    timeout = int(check.get("timeout", default_timeout))
    if not cmd:
        return CheckResult(command="", scope=scope, timeout=timeout,
                           exit_code=None, duration_sec=0.0, passed=False,
                           error="empty command")
    bash = _resolve_bash()
    if not bash:
        return CheckResult(command=cmd, scope=scope, timeout=timeout,
                           exit_code=None, duration_sec=0.0, passed=False,
                           error="bash not found (need git-bash; the WSL shim is unusable)")
    # Apply path_prelude if configured (AC-1, AC-2)
    path_prelude = _read_path_prelude(project)
    effective_cmd = f"{path_prelude}; {cmd}" if path_prelude else cmd
    import time
    t0 = time.monotonic()
    try:
        # Run via bash (git-bash on Windows), NOT shell=True — shell=True uses
        # cmd.exe on Windows, where posix gates (grep, etc.) don't exist, so
        # every gate errored and the loop shipped unverified. See the memory
        # autonomous-gates-not-enforced-windows.
        cp = subprocess.run(
            [bash, "-c", effective_cmd], cwd=str(project),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        dur = time.monotonic() - t0
        return CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=cp.returncode, duration_sec=round(dur, 2),
            passed=cp.returncode == 0,
            stdout_tail=_tail(cp.stdout, 2000),
            stderr_tail=_tail(cp.stderr, 2000),
        )
    except subprocess.TimeoutExpired as e:
        dur = time.monotonic() - t0
        return CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=None, duration_sec=round(dur, 2), passed=False,
            stdout_tail=_tail((e.stdout or b"").decode("utf-8", "replace"), 2000),
            stderr_tail=_tail((e.stderr or b"").decode("utf-8", "replace"), 2000),
            error=f"timeout after {timeout}s",
        )
    except Exception as e:
        dur = time.monotonic() - t0
        return CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=None, duration_sec=round(dur, 2), passed=False,
            error=f"{type(e).__name__}: {e}",
        )


def _tail(s: str | None, n: int) -> str:
    # subprocess.run can hand back None for stdout/stderr (observed on Windows);
    # len(None) would crash and the resulting TypeError gets swallowed by the
    # caller's `except`, which discards the real return code and reports a
    # passing check as failed (false local_checks_failed). Guard None/empty.
    if not s:
        return ""
    if len(s) <= n:
        return s
    return "...[truncated]...\n" + s[-n:]


# ── Tree isolation ───────────────────────────────────────────────────────────

@dataclass
class IsolationState:
    """Result of isolating the working tree to HEAD for gate execution."""
    head_sha: str | None = None
    dirty_paths: int = 0
    isolated: bool = False
    restore_error: str | None = None


@contextlib.contextmanager
def isolate_to_head(project: Path):
    """Context manager that pins the working tree to HEAD for gate execution.

    The driver runs the gate *after* the step's commit —
    ``run_ilk_loop_claude.sh:2247``, inside the ``total_new > 0`` branch.
    So by the time this runs, everything the iteration authored is already
    committed, and everything still uncommitted is by construction not this
    step's work.

    Behaviour:
      - **Tree already clean** (``dirty_paths == 0``) — the common case.
        Do nothing at all: no stash, no subprocess beyond the two probes.
        ``isolated=True``.
      - **Tree dirty** — ``git stash push -u -m "ilk-gate-isolation <sha>"``,
        run the checks, then ``git stash pop``.  ``isolated=True`` iff both
        the push and the pop succeeded.
      - **Not a git repo / git absent** — ``isolated=False``, ``head_sha=None``,
        gate still runs.  Do not crash; a non-git project must still be
        gateable.

    Safety rules (all non-negotiable):
      - **Never ``git stash drop``.**  On a pop conflict the stash entry stays
        on the stack and its ref goes into ``restore_error``, so the work is
        recoverable by hand.
      - **Restore runs in a ``finally``.**  A check that raises, times out, or
        is killed must still un-stash.
      - **Do not ``stash --keep-index``.**  The index is part of "not committed".
      - The stash message carries a fixed ``ilk-gate-isolation`` prefix so an
        orphaned entry is identifiable in ``git stash list``.
    """
    state = IsolationState()

    # Probe: is this a git repo?
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(project), capture_output=True, text=True, timeout=5,
        )
        is_git = cp.returncode == 0 and cp.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        is_git = False

    if not is_git:
        # Not a git repo — gate still runs, just not isolated
        state.isolated = False
        yield state
        return

    # Get HEAD sha
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project), capture_output=True, text=True, timeout=5,
        )
        if cp.returncode == 0:
            state.head_sha = cp.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Count dirty paths (uncommitted + untracked)
    try:
        # Uncommitted tracked changes
        cp_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        )
        # Staged changes
        cp_cached = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        )
        # Untracked files
        cp_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        )
        dirty = set()
        for out in (cp_diff.stdout, cp_cached.stdout, cp_untracked.stdout):
            for line in out.splitlines():
                line = line.strip()
                if line:
                    dirty.add(line)
        state.dirty_paths = len(dirty)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        state.dirty_paths = 0

    if state.dirty_paths == 0:
        # Clean tree — no-op, just mark isolated
        state.isolated = True
        yield state
        return

    # Dirty tree — stash, run checks, restore
    stash_msg = f"ilk-gate-isolation {state.head_sha or 'unknown'}"
    stashed = False
    try:
        cp = subprocess.run(
            ["git", "stash", "push", "-u", "-m", stash_msg],
            cwd=str(project), capture_output=True, text=True, timeout=30,
        )
        if cp.returncode == 0:
            stashed = True
            state.isolated = True
        else:
            # Stash failed — gate runs against dirty tree, not isolated
            state.isolated = False
            state.restore_error = f"stash push failed: {cp.stderr.strip()}"

        yield state

    finally:
        if stashed:
            try:
                cp = subprocess.run(
                    ["git", "stash", "pop"],
                    cwd=str(project), capture_output=True, text=True, timeout=30,
                )
                if cp.returncode != 0:
                    # Pop conflict — stash entry stays on stack (never drop)
                    state.restore_error = f"stash pop failed: {cp.stderr.strip()}"
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                state.restore_error = f"stash pop error: {e}"


# ── B2 confirm-before-block decision ─────────────────────────────────────────

_BLOCKING_OUTCOMES = {"fail", "error"}


def confirm_b2_block(
    first_results: list[dict],
    rerun_results: list[dict],
) -> dict:
    """Decide whether a B2 blocking outcome is real or transient.

    The runner calls this after a first pass of local_checks has at least one
    blocking outcome (``fail`` or ``error``).  It re-runs *only* the blocking
    checks and passes both sets of results here.

    Rules:
      - No blocking outcome on first pass → NOT blocked (no re-run needed).
      - A blocking check that passes on re-run → transient, NOT blocked.
      - A blocking check that still fails/errored on re-run → real, blocked.
      - ``skipped`` is never blocking.

    Parameters
    ----------
    first_results : list[dict]
        Every check result from the first pass.  Each dict must have at least
        ``command`` (str) and ``outcome`` (str in {pass, fail, error, skipped}).
    rerun_results : list[dict]
        Results of re-running the blocking checks only (same schema).  Empty
        when the first pass had no blocking outcomes.

    Returns
    -------
    dict
        ``blocked`` (bool), ``blocking_checks`` (list of dicts with
        ``command``, ``first_outcome``, ``rerun_outcome``), and
        ``transient_cleared`` (list of commands that flipped from blocking to
        pass on re-run).
    """
    # Identify the blocking checks from the first pass
    blocking = [r for r in first_results if r.get("outcome") in _BLOCKING_OUTCOMES]

    if not blocking:
        return {
            "blocked": False,
            "blocking_checks": [],
            "transient_cleared": [],
        }

    # Build a lookup from command → rerun outcome
    rerun_map: dict[str, str] = {r["command"]: r["outcome"] for r in rerun_results}

    confirmed: list[dict] = []
    transient_cleared: list[str] = []

    for r in blocking:
        cmd = r["command"]
        first_out = r["outcome"]
        rerun_out = rerun_map.get(cmd)

        if rerun_out is None:
            # This blocking check was NOT re-run — treat as still blocking.
            # The caller is responsible for re-running all blocking checks.
            confirmed.append({
                "command": cmd,
                "first_outcome": first_out,
                "rerun_outcome": None,
            })
        elif rerun_out in _BLOCKING_OUTCOMES:
            confirmed.append({
                "command": cmd,
                "first_outcome": first_out,
                "rerun_outcome": rerun_out,
            })
        else:
            # Transient: blocked on first, passed on re-run
            transient_cleared.append(cmd)

    return {
        "blocked": len(confirmed) > 0,
        "blocking_checks": confirmed,
        "transient_cleared": transient_cleared,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def confirm_b2_main(argv: list[str]) -> int:
    """CLI entry point for B2 confirm-before-block decision.

    Usage: run_local_checks.py --confirm-b2 --first <json> --rerun <json>

    Each <json> is either a file path or inline JSON.  Outputs the
    confirm_b2_block result as JSON.  Exit code: 0 = not blocked,
    1 = blocked.
    """
    ap = argparse.ArgumentParser(
        prog="run_local_checks.py --confirm-b2",
        description="Decide whether a B2 blocking outcome is real or transient.",
    )
    ap.add_argument("--confirm-b2", action="store_true", required=True)
    ap.add_argument("--first", required=True,
                    help="JSON file (or inline) with first-pass results")
    ap.add_argument("--rerun", required=True,
                    help="JSON file (or inline) with rerun results")
    args = ap.parse_args(argv)

    def _load_json(s: str) -> list[dict]:
        p = Path(s)
        if p.is_file():
            return json.loads(read_text(p))
        return json.loads(s)

    first = _load_json(args.first)
    rerun = _load_json(args.rerun)
    result = confirm_b2_block(first, rerun)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["blocked"] else 0


def main(argv: list[str]) -> int:
    # Force UTF-8 on stdout/stderr. The JSON we print carries gate output in
    # `stdout_tail` (e.g. eslint/vitest emit U+2713 '✓'); on a zh-CN console
    # Python defaults stdout to GBK (cp936), and `print(json.dumps(...))` then
    # dies with UnicodeEncodeError → empty stdout → the runner records
    # outcome=error/exit_code=null/raw=null → a FALSE local_checks_failed even
    # though the gate passed. Reconfiguring to UTF-8 (the runner reads the temp
    # file as UTF-8) makes the JSON survive any non-ASCII gate output.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Dispatch to confirm-b2 subcommand if present
    if "--confirm-b2" in argv:
        return confirm_b2_main(argv)

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--step", type=int, default=None,
                    help="if given, also run that step's per-step local_checks")
    ap.add_argument("--no-isolate", action="store_true",
                    help="skip tree isolation to HEAD; for debugging only — "
                         "results say nothing about what will ship")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    slug = args.slug
    step = args.step

    subplan = find_subplan(project, slug)
    if subplan is None:
        print(json.dumps({"error": f"sub-plan not found for slug={slug!r} under {project}/docs/plans/"}))
        return 2

    fm_text, body = split_frontmatter(read_text(subplan))

    # In meta projects, resolve the member repo to cd into for each check.
    # In single mode this is a no-op (cwd stays as `project`).
    run_cwd, repo_error = resolve_run_cwd(project, subplan, fm_text)
    if repo_error:
        print(json.dumps({"error": repo_error, "slug": slug, "subplan_path": str(subplan)}))
        return 2

    subplan_checks = parse_local_checks_block(fm_text)
    step_checks: list[dict] = []
    if step is not None:
        step_checks = extract_step_local_checks(body, step)

    results: list[CheckResult] = []
    iso_ctx = contextlib.nullcontext(IsolationState()) if args.no_isolate else isolate_to_head(project)
    with iso_ctx as iso:
        for c in subplan_checks:
            results.append(run_one(c, "subplan", run_cwd))
        for c in step_checks:
            results.append(run_one(c, "step", run_cwd))

    passed = all(r.passed for r in results)

    # AC-1/AC-3: unisolated dirty tree is not a pass; non-git is fine.
    isolation_error = None
    if not iso.isolated and iso.dirty_paths > 0:
        passed = False
        isolation_error = (
            f"gate ran against an unisolated tree ({iso.dirty_paths} uncommitted "
            "paths) — result does not describe the commit"
        )

    out = {
        "slug": slug,
        "step": step,
        "subplan_path": str(subplan),
        "run_cwd": str(run_cwd),
        "subplan_check_count": len(subplan_checks),
        "step_check_count": len(step_checks),
        "all_passed": passed,
        "results": [asdict(r) for r in results],
        "head_sha": iso.head_sha,
        "dirty_paths": iso.dirty_paths,
        "isolated": iso.isolated,
        "restore_error": iso.restore_error,
    }
    if isolation_error:
        out["error"] = isolation_error
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
