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

Usage:
  run_local_checks.py --project <path> --slug <subplan-slug> [--step N]

Environment: requires Python 3.8+. Uses stdlib only.
"""
from __future__ import annotations

import argparse
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
    # Strip surrounding quotes if any
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
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
    import time
    t0 = time.monotonic()
    try:
        # Run via bash (git-bash on Windows), NOT shell=True — shell=True uses
        # cmd.exe on Windows, where posix gates (grep, etc.) don't exist, so
        # every gate errored and the loop shipped unverified. See the memory
        # autonomous-gates-not-enforced-windows.
        cp = subprocess.run(
            [bash, "-c", cmd], cwd=str(project),
            capture_output=True, text=True, timeout=timeout,
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


def _tail(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return "...[truncated]...\n" + s[-n:]


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--step", type=int, default=None,
                    help="if given, also run that step's per-step local_checks")
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
    for c in subplan_checks:
        results.append(run_one(c, "subplan", run_cwd))
    for c in step_checks:
        results.append(run_one(c, "step", run_cwd))

    passed = all(r.passed for r in results)
    out = {
        "slug": slug,
        "step": step,
        "subplan_path": str(subplan),
        "run_cwd": str(run_cwd),
        "subplan_check_count": len(subplan_checks),
        "step_check_count": len(step_checks),
        "all_passed": passed,
        "results": [asdict(r) for r in results],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
