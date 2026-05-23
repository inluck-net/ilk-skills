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

# Sibling module — resolves plans dir under the new ~/.ilk-data convention
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import find_plans_dir as _resolve_plans_dir  # noqa: E402


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


def run_one(check: dict, scope: str, project: Path, default_timeout: int = 120) -> CheckResult:
    cmd = check.get("command", "")
    timeout = int(check.get("timeout", default_timeout))
    if not cmd:
        return CheckResult(command="", scope=scope, timeout=timeout,
                           exit_code=None, duration_sec=0.0, passed=False,
                           error="empty command")
    import time
    t0 = time.monotonic()
    try:
        cp = subprocess.run(
            cmd, shell=True, cwd=str(project),
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
    subplan_checks = parse_local_checks_block(fm_text)
    step_checks: list[dict] = []
    if step is not None:
        step_checks = extract_step_local_checks(body, step)

    results: list[CheckResult] = []
    for c in subplan_checks:
        results.append(run_one(c, "subplan", project))
    for c in step_checks:
        results.append(run_one(c, "step", project))

    passed = all(r.passed for r in results)
    out = {
        "slug": slug,
        "step": step,
        "subplan_path": str(subplan),
        "subplan_check_count": len(subplan_checks),
        "step_check_count": len(step_checks),
        "all_passed": passed,
        "results": [asdict(r) for r in results],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
