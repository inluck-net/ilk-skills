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


_BLOCK_SCALAR_INDICATORS = {">", ">-", "|", "|-"}


def count_local_checks_items(yaml_text: str) -> int:
    """Count list items declared under ``local_checks:``, parsed or not.

    Pairs with :func:`parse_local_checks_block`. That function returns ``[]``
    for a block it cannot read, which is indistinguishable from a block that
    declares nothing — and ``all([])`` is ``True``, so an unreadable gate block
    becomes a silent pass.

    Measured 2026-09-15: a resolver-generated sub-plan declared

        local_checks:
          - name: repo-verifier-1
            cmd: bunx tsc --noEmit -p convex/tsconfig.json --pretty false

    using ``cmd:`` where the parser reads ``command:``. It parsed to ``[]``, the
    step passed unconditionally, and the sub-plan shipped ``2/2`` reporting
    "0 fail" with nothing having run. 66 of 67 batch-verification sub-plans on
    that host had the same shape; 63 shipped.

    Counting the ITEMS lets the caller tell "declared nothing" from "declared
    something I could not read" — the difference between an absent gate and a
    broken one.  ``local_checks: []`` counts 0 and stays legitimate.
    """
    lines = yaml_text.splitlines()
    in_block = False
    item_indent: int | None = None
    count = 0
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        if not in_block:
            if stripped.startswith("local_checks:"):
                rhs = stripped[len("local_checks:"):].strip()
                if rhs in ("[]", "[ ]"):
                    return 0
                in_block = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        leading = len(line) - len(line.lstrip(" "))
        if stripped.startswith("- "):
            # The first item fixes the list indent; deeper "- " lines belong to
            # a nested value, not to this list.
            if item_indent is None:
                item_indent = leading
            if leading == item_indent:
                count += 1
            continue
        # A non-item line dedented to or past a sibling top-level key ends the
        # block.  Anything more indented is a continuation of the current item.
        if item_indent is not None and leading <= item_indent and ":" in stripped:
            break
        if item_indent is None and leading == 0 and ":" in stripped:
            break
    return count


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
    # Block-scalar collection state: when a command value is a YAML block
    # scalar indicator (>, >-, |, |-), continuation lines are collected here.
    _collecting_bs = False  # True while collecting block-scalar body lines
    _bs_lines: list[str] = []
    _bs_indent: int = 0     # indentation of the first body line (content indent)

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

        # --- Block-scalar continuation collection ---
        if _collecting_bs:
            if line.strip() == "":
                _bs_lines.append("")
                continue
            leading = len(line) - len(line.lstrip(" "))
            # First non-empty body line sets the content indent.
            if not _bs_lines or (len(_bs_lines) == 1 and _bs_lines[-1] == ""):
                _bs_indent = leading
            if leading < _bs_indent and line.strip():
                # End of block scalar — fold collected lines into the command.
                _collecting_bs = False
                body_lines = [l[_bs_indent:] if len(l) > _bs_indent else ""
                              for l in _bs_lines]
                # Drop trailing blank lines, then join with spaces (folded).
                while body_lines and body_lines[-1] == "":
                    body_lines.pop()
                if cur is not None:
                    cur["command"] = " ".join(body_lines)
                # Fall through to process this line normally.
            else:
                _bs_lines.append(line)
                continue

        # In the local_checks block. Detect end-of-block: a non-empty line that
        # is no longer indented under it.
        if line.strip() == "":
            continue
        # First content line gives us the block indent
        if indent is None:
            stripped_lead = line.lstrip(" ")
            if stripped_lead.startswith("#"):
                # A comment before the first item is not "followed by something
                # other than a list" — it is a comment. Breaking here dropped the
                # ENTIRE block, and since all([]) is True the step then passed
                # with no gate at all. Measured 2026-09-15: 7 sub-plans on this
                # host had their gate silently disabled by the comment that
                # explained the gate. End-of-block detection below already
                # skips comments; this branch did not.
                continue
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
                val = v.strip()
                cur[k.strip()] = _coerce(val)
                if k.strip() == "command" and val in _BLOCK_SCALAR_INDICATORS:
                    _collecting_bs = True
                    _bs_lines = []
                    _bs_indent = 0  # will be set from first body line
        elif ":" in s and cur is not None:
            k, _, v = s.partition(":")
            val = v.strip()
            cur[k.strip()] = _coerce(val)
            if k.strip() == "command" and val in _BLOCK_SCALAR_INDICATORS:
                _collecting_bs = True
                _bs_lines = []
                _bs_indent = 0
        # else: ignore (comments, malformed lines)

    # Flush any pending block scalar at end of input
    if _collecting_bs and cur is not None:
        body_lines = [l[_bs_indent:] if len(l) > _bs_indent else ""
                      for l in _bs_lines]
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        cur["command"] = " ".join(body_lines)

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

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[a-z]?-")


def _slug_from_filename(fname: str) -> str:
    """The slug ``loop_status.py:377-384`` derives for this file.

    Strips the ``.md`` suffix and the leading ``YYYY-MM-DD`` date prefix
    (with the optional same-day letter, e.g. ``2026-09-08b-``).  Kept as a
    local regex rather than importing ``plan_slug.strip_date_prefix`` because
    this module is invoked as a script from arbitrary working directories and
    has no sibling-import shim; a failed import here would turn every gate
    into an ``error``, which is a blocking outcome.
    """
    stem = fname[:-3] if fname.endswith(".md") else fname
    return _DATE_PREFIX_RE.sub("", stem, count=1)


def find_subplan(project: Path, slug: str) -> Path | None:
    """Find the sub-plan for *slug*, by frontmatter `plan:` OR by filename.

    FIVE components carry a sub-plan identity.  Three derive it from the
    FILENAME, and two from FRONTMATTER:

      * ``loop_status.py`` strips the date prefix and ``.md`` -- and its form
        is what the loop prints and what ``ship-proof.jsonl`` records, so the
        filename form is the canonical one;
      * ``quarantine_subplan.py`` globs ``*-<slug>.md``;
      * ``ship_audit.read_subplan_for_audit`` reads frontmatter ``plan:``
        (``ship_audit.py:557`` at time of writing), and
        ``check_step_commits`` then unions ledger rows on EXACT slug equality
        (``ship_audit.py:204-208``), so a frontmatter form that differs from
        the filename form silently matches no row;
      * this function used to be the lone outlier, matching only frontmatter.

    ``ship_audit`` was missing from this list until 2026-09-09.  That omission
    had a cost: a peer's fix proposal for the mismatch below targeted only
    ``find_subplan``, and would have looked applied while leaving the stall
    live, because ``ship_audit`` reads the same frontmatter and was not in the
    table anyone was working from.  Anything added here that resolves a
    sub-plan identity belongs in this list, and the list is load-bearing rather
    than decorative.

    That mattered because a generator may write a run id into the filename and
    omit it from frontmatter, so ``2026-09-08-issue-4796-work-52d08c9c.md``
    carries ``plan: issue-4796-work``.  Asked for the slug every other
    component reports, this returned None -- and a not-found is an ``error``,
    which is in ``_BLOCKING_OUTCOMES``.  Because a name mismatch is
    deterministic, the confirm-before-block re-run reproduced it, so the loop
    read a harness failure as a real gate failure and stopped.  Measured
    2026-09-08 on a consumer host: two seconds elapsed between worker-done and
    loop-stop against a gate declaring ``bun run test, timeout: 300``.  No test
    ever ran.

    Frontmatter is still tried FIRST, so nothing that resolved before resolves
    differently now; the filename glob is a fallback, not a replacement.
    """
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
    # Fallback: the filename-derived form the rest of the toolkit uses.
    #
    # This is an EXACT derivation, not a `*-<slug>.md` glob.  The glob was the
    # first shape tried and it is ambiguous: asked for `work-52d08c9c` with
    # both `2026-09-08-work-52d08c9c.md` and
    # `2026-09-08-issue-4796-work-52d08c9c.md` on disk, it matches both and
    # `sorted()` silently returns the second.  Resolving to the WRONG sub-plan
    # is worse than not resolving -- it gates work nobody asked about while
    # reporting success.  quarantine_subplan.py:33-39 still uses the glob and
    # carries the same latent ambiguity; that is pre-existing and not changed
    # here.
    #
    # Deriving the slug the way loop_status.py:377-384 does makes the match
    # one-to-one, which is exactly the invariant the caller is asking about:
    # "the slug the loop reports for this sub-plan".
    for p in sorted(plans_dir.glob("*.md")):
        if p.name.startswith("MASTER"):
            continue
        if _slug_from_filename(p.name) == slug:
            return p
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


@dataclass
class StepGate:
    """Result of :func:`step_gate_fence`: what the locator found for one step."""
    heading_count: int
    fence_text: str | None = None
    declares_local_checks: bool = False


def step_gate_fence(body: str, step_n: int) -> StepGate:
    r"""Unified locator for a step's gate fence.

    Returns a :class:`StepGate` describing what was found for ``step_n``.

    * heading: ``^###\s+Step\s+N(?!\d)`` — any punctuation after N, and
      ``Step 1`` does not match ``Step 10``.
    * ``heading_count`` counts every match; the region runs from the LAST
      match to the next ``^###\s`` heading.  **Judgment call: last wins.**
      Basis: C3's spurious heading sits in quoted prose before the real one.
    * fences: a line scanner that tracks open/close state.  `````<lang>```
      opens, ```` ``` ```` closes, so a closer is never an opener.  The
      chosen fence is the first yaml or untagged fence whose content has a
      ``^\s*local_checks:`` line.
    * ``declares_local_checks`` = any line in the region matching
      ``^\s*local_checks:`` with list content, inside or outside a fence.
    """
    heading_re = re.compile(rf"^###\s+Step\s+{step_n}(?!\d)", re.MULTILINE)
    matches = list(heading_re.finditer(body))
    heading_count = len(matches)
    if heading_count == 0:
        return StepGate(heading_count=0)

    region_start = matches[-1].end()
    next_heading = re.search(r"^###\s+", body[region_start:], re.MULTILINE)
    region = body[region_start:region_start + next_heading.start()] if next_heading else body[region_start:]

    # Check if the region declares local_checks at all (inside or outside a fence).
    # Detect any local_checks declaration: YAML list (indented items) or
    # JSON array (opening bracket).  Excludes `local_checks: []` which is
    # a deliberate "no gate" marker.
    _lc_re = re.compile(r"^\s*['\"]?local_checks['\"]?\s*:\s*(?:\n\s+\S|\[(?!\s*\]))", re.MULTILINE)
    declares_local_checks = bool(_lc_re.search(region))

    # Scan for fences with open/close tracking.
    fence_re = re.compile(r"^(```\w*)\s*$", re.MULTILINE)
    pos = 0
    in_fence = False
    fence_lang = ""
    fence_start = -1
    while pos < len(region):
        fm = fence_re.search(region, pos)
        if not fm:
            break
        line = fm.group(1).strip()
        if in_fence:
            if line == "```":
                # Close the current fence
                fence_content = region[fence_start:fm.start()]
                if fence_lang in ("", "yaml", "yml"):
                    if re.search(r"^\s*local_checks:", fence_content, re.MULTILINE):
                        parsed = parse_local_checks_block(fence_content)
                        if any(c.get("command") for c in parsed):
                            return StepGate(
                                heading_count=heading_count,
                                fence_text=fence_content,
                                declares_local_checks=declares_local_checks,
                            )
                in_fence = False
                pos = fm.end()
            else:
                # A ```<lang> while already in a fence is NOT a closer — skip
                pos = fm.end()
        else:
            # Open a new fence
            in_fence = True
            fence_lang = line[3:] if len(line) > 3 else ""
            fence_start = fm.end()
            pos = fm.end()

    return StepGate(heading_count=heading_count, declares_local_checks=declares_local_checks)


def extract_step_local_checks(body: str, step_n: int) -> list[dict]:
    """Extract local_checks declared inside a per-step yaml fence.

    Delegates to :func:`step_gate_fence` for the unified locator.
    """
    gate = step_gate_fence(body, step_n)
    if gate.fence_text is None:
        return []
    return parse_local_checks_block(gate.fence_text)


def step_declared_timeout(body: str, step_n: int) -> int:
    """Sum of ``timeout:`` over step N's declared checks (0 when none declare one).

    Same parser as the gate runner, so the driver's outer cap and the gate
    it bounds read the same declaration.
    """
    return sum(int(c.get("timeout") or 0) for c in extract_step_local_checks(body, step_n))


#: Default per-check timeout when a gate item declares none.  Used by both
#: :func:`run_one` (its ``default_timeout`` signature default) and
#: :func:`gate_declared_timeout` (to count undeclared checks), so the driver's
#: cap and the runner's own budget agree on what an undeclared check costs.
DEFAULT_CHECK_TIMEOUT_S = 120


def gate_declared_timeout(body: str, step_n: int) -> int:
    """Total declared budget the helper *actually* executes for step ``step_n``.

    The helper (:func:`run_one` loop in ``main``) runs BOTH the frontmatter
    ``local_checks`` block and the per-step block, sequentially.  This
    function sums their declared timeouts — exactly the pair the helper
    executes — so the driver's outer cap matches the helper's real budget.

    Each check contributes its declared ``timeout`` if present, else
    :data:`DEFAULT_CHECK_TIMEOUT_S` (the same value :func:`run_one` uses).
    """
    fm_text, body_text = split_frontmatter(body)
    fm_checks = parse_local_checks_block(fm_text)
    step_checks = extract_step_local_checks(body_text, step_n)
    total = 0
    for c in fm_checks:
        total += int(c.get("timeout") or DEFAULT_CHECK_TIMEOUT_S)
    for c in step_checks:
        total += int(c.get("timeout") or DEFAULT_CHECK_TIMEOUT_S)
    return total


def count_step_local_checks_items(body: str, step_n: int) -> int:
    """Item count for a per-step fence — the step-scoped twin of
    :func:`count_local_checks_items`.

    Delegates to :func:`step_gate_fence` for the unified locator.
    """
    gate = step_gate_fence(body, step_n)
    if gate.fence_text is None:
        return 0
    return count_local_checks_items(gate.fence_text)


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
    outcome: str = ""       # "pass" | "fail" | "error" — per-check classification
    reason: str | None = None  # human-readable why this outcome (null for pass/fail)
    path_prelude_applied: bool = False  # true when _read_path_prelude returned non-empty


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


_ILK_CHECK_RE = re.compile(r"^ILK-CHECK:\s*unmeasured\s+(.+)$", re.MULTILINE)


def _classify_check(
    exit_code: int | None,
    error: str,
    stderr_full: str,
) -> tuple[str, str | None]:
    """Classify a check result into (outcome, reason).

    The tri-state classification:
      - exit 0 ⇒ pass
      - exit_code is None (timeout, spawn exception) ⇒ error, reason from error
      - exit 126 / 127 ⇒ error, reason "command not executable" /
        "command not found: <leading word>"
      - stderr contains ILK-CHECK: unmeasured <text> ⇒ error, reason = captured text
      - any other nonzero ⇒ fail
    """
    if exit_code is None:
        # timeout or spawn exception
        return "error", error

    if exit_code == 0:
        return "pass", None

    # exit 126 / 127: command not found / not executable
    if exit_code == 126:
        return "error", "command not executable"
    if exit_code == 127:
        # Extract the leading word from the command for a useful reason.
        # The error field from Exception would say "FileNotFoundError" but
        # exit 127 comes from bash itself, not from our except block.
        return "error", "command not found"

    # ILK-CHECK: unmeasured marker on stderr
    m = _ILK_CHECK_RE.search(stderr_full)
    if m:
        return "error", m.group(1).strip()

    # Any other nonzero: measured failure
    return "fail", None


def run_one(check: dict, scope: str, project: Path,
            default_timeout: int = DEFAULT_CHECK_TIMEOUT_S) -> CheckResult:
    cmd = check.get("command", "")
    timeout = int(check.get("timeout", default_timeout))
    if not cmd:
        r = CheckResult(command="", scope=scope, timeout=timeout,
                        exit_code=None, duration_sec=0.0, passed=False,
                        error="empty command")
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, "")
        return r
    bash = _resolve_bash()
    if not bash:
        r = CheckResult(command=cmd, scope=scope, timeout=timeout,
                        exit_code=None, duration_sec=0.0, passed=False,
                        error="bash not found (need git-bash; the WSL shim is unusable)")
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, "")
        return r
    # Fail closed: a bare YAML block-scalar indicator is not a real command.
    # ``>-`` as a bash command redirects to a file named ``-`` and exits 0,
    # so a gate using it passes vacuously.  Refuse rather than run blind.
    _BARE_BLOCK_SCALAR_INDICATORS = {">", ">-", "|", "|-"}
    if cmd.strip() in _BARE_BLOCK_SCALAR_INDICATORS:
        r = CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=None, duration_sec=0.0, passed=False,
            error=f"refusing to execute bare block-scalar indicator {cmd!r} — "
                  f"the gate command could not be parsed",
        )
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, "")
        return r
    # Apply path_prelude if configured (AC-1, AC-2)
    path_prelude = _read_path_prelude(project)
    applied = bool(path_prelude)
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
        stderr_full = cp.stderr or ""
        r = CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=cp.returncode, duration_sec=round(dur, 2),
            passed=cp.returncode == 0,
            stdout_tail=_tail(cp.stdout, 2000),
            stderr_tail=_tail(stderr_full, 2000),
            path_prelude_applied=applied,
        )
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, stderr_full)
        return r
    except subprocess.TimeoutExpired as e:
        dur = time.monotonic() - t0
        stderr_full = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        r = CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=None, duration_sec=round(dur, 2), passed=False,
            stdout_tail=_tail((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), 2000),
            stderr_tail=_tail(stderr_full, 2000),
            error=f"timeout after {timeout}s",
            path_prelude_applied=applied,
        )
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, stderr_full)
        return r
    except Exception as e:
        dur = time.monotonic() - t0
        r = CheckResult(
            command=cmd, scope=scope, timeout=timeout,
            exit_code=None, duration_sec=round(dur, 2), passed=False,
            error=f"{type(e).__name__}: {e}",
            path_prelude_applied=applied,
        )
        r.outcome, r.reason = _classify_check(r.exit_code, r.error, "")
        return r


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
def isolate_to_head(project: Path, snapshot: Path | None = None):
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
      - **Tree dirty, snapshot provided** — stash only tracked changes and
        untracked paths that appeared *after* the snapshot was taken
        (``iteration_snapshot.py changed-since``).  Pre-existing untracked
        files stay in the tree.  Restore by the stash's own SHA
        (``git stash apply --index <sha>``, then drop).  ``isolated=True``
        iff both the push and the restore succeeded.
      - **Tree dirty, no snapshot** (manual ``run_local_checks.py`` call) —
        ``git stash push -u``, then restore by SHA.  ``isolated=True`` iff
        both succeeded.
      - **Not a git repo / git absent** — ``isolated=False``, ``head_sha=None``,
        gate still runs.  Do not crash; a non-git project must still be
        gateable.

    Safety rules (all non-negotiable):
      - **Never ``git stash pop``.**  It takes whatever is on top.  Restore
        by the stash's own SHA, captured right after the push.
      - **On a failed apply, leave the stash in place.**  Drop only on a
        successful apply.  The sha goes into ``restore_error`` with a
        recovery command.
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
        encoding="utf-8", errors="replace",
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
        encoding="utf-8", errors="replace",
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
        encoding="utf-8", errors="replace",
        )
        # Staged changes
        cp_cached = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
        )
        # Untracked files
        cp_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
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

    # Dirty tree — stash, run checks, restore by SHA.
    stash_msg = f"ilk-gate-isolation {state.head_sha or 'unknown'}"
    stashed = False
    stash_sha: str | None = None
    try:
        # When a snapshot is available, stash only tracked changes and
        # untracked paths that appeared *after* the snapshot.  Pre-existing
        # untracked files stay in the tree.
        use_snapshot = False
        new_paths: list[str] = []
        if snapshot is not None and snapshot.is_file():
            snap_script = Path(__file__).resolve().parent / "iteration_snapshot.py"
            cp_snap = subprocess.run(
                [sys.executable, str(snap_script),
                 "changed-since", str(project), "--snapshot", str(snapshot)],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if cp_snap.returncode == 0:
                new_paths = [p for p in cp_snap.stdout.split("\0") if p]
                use_snapshot = True
            # returncode 2 = missing/unreadable snapshot → fall through to -u

        stash_args = ["git", "stash", "push", "-m", stash_msg]
        cp = None
        if use_snapshot and new_paths:
            # New untracked paths since snapshot — stash tracked changes
            # AND only those new untracked paths via --pathspec-from-file.
            stash_args.extend(["-u", "--pathspec-file-nul"])
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False
            ) as tmp:
                tmp.write("\0".join(new_paths).encode("utf-8"))
                tmp_path = tmp.name
            try:
                cp = subprocess.run(
                    [*stash_args, f"--pathspec-from-file={tmp_path}"],
                    cwd=str(project), capture_output=True, text=True,
                    timeout=30, encoding="utf-8", errors="replace",
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            if cp is not None and cp.returncode == 0:
                n_pre = state.dirty_paths - len(new_paths)
                if n_pre > 0:
                    print(
                        f"[gate-isolation] left in place (present before this "
                        f"iteration): {n_pre} untracked path(s)",
                        file=sys.stderr,
                    )
        elif use_snapshot and not new_paths:
            # Snapshot available but nothing changed since — only tracked
            # changes need stashing (no -u).  If there are no tracked
            # changes either, skip stashing entirely.
            # Check for tracked dirty paths (not untracked).
            cp_tracked = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=str(project), capture_output=True, text=True,
                timeout=10, encoding="utf-8", errors="replace",
            )
            cp_cached = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=str(project), capture_output=True, text=True,
                timeout=10, encoding="utf-8", errors="replace",
            )
            has_tracked = bool(
                cp_tracked.stdout.strip() or cp_cached.stdout.strip()
            )
            if has_tracked:
                cp = subprocess.run(
                    stash_args,
                    cwd=str(project), capture_output=True, text=True,
                    timeout=30, encoding="utf-8", errors="replace",
                )
            else:
                # Nothing to stash — tree has only pre-existing untracked.
                state.isolated = True
                yield state
                return
        else:
            # No snapshot — stash everything (legacy behaviour).
            stash_args.insert(3, "-u")
            cp = subprocess.run(
                stash_args,
                cwd=str(project), capture_output=True, text=True,
                timeout=30, encoding="utf-8", errors="replace",
            )

        if cp.returncode == 0:
            stashed = True
            state.isolated = True
            # Capture the stash SHA immediately after push.
            cp_sha = subprocess.run(
                ["git", "rev-parse", "refs/stash"],
                cwd=str(project), capture_output=True, text=True,
                timeout=5, encoding="utf-8", errors="replace",
            )
            if cp_sha.returncode == 0:
                stash_sha = cp_sha.stdout.strip()
        else:
            # Stash failed — gate runs against dirty tree, not isolated
            state.isolated = False
            state.restore_error = f"stash push failed: {cp.stderr.strip()}"

        yield state

    finally:
        if stashed and stash_sha:
            # Restore by SHA: apply --index, then drop.
            try:
                cp = subprocess.run(
                    ["git", "stash", "apply", "--index", stash_sha],
                    cwd=str(project), capture_output=True, text=True,
                    timeout=30, encoding="utf-8", errors="replace",
                )
                if cp.returncode != 0:
                    state.restore_error = (
                        f"stash apply failed ({stash_sha[:12]}): "
                        f"{cp.stderr.strip()}; recover with: "
                        f"git stash apply {stash_sha}"
                    )
                else:
                    # Apply succeeded — drop the entry.
                    cp_list = subprocess.run(
                        ["git", "stash", "list", "--format=%H"],
                        cwd=str(project), capture_output=True, text=True,
                        timeout=10, encoding="utf-8", errors="replace",
                    )
                    if cp_list.returncode == 0:
                        for idx, line in enumerate(cp_list.stdout.splitlines()):
                            if line.strip() == stash_sha:
                                subprocess.run(
                                    ["git", "stash", "drop", f"stash@{{{idx}}}"],
                                    cwd=str(project), capture_output=True,
                                    text=True, timeout=10,
                                    encoding="utf-8", errors="replace",
                                )
                                break
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                state.restore_error = (
                    f"stash restore error ({stash_sha[:12]}): {e}; "
                    f"recover with: git stash apply {stash_sha}"
                )
        elif stashed and not stash_sha:
            # Pushed but couldn't capture SHA — unusual; warn.
            state.restore_error = (
                "stash pushed but SHA not captured; "
                "check git stash list for ilk-gate-isolation entries"
            )


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
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="tree the gate isolates and runs in, when it differs "
                         "from the plans-resolution root (--project). The "
                         "selfmod-isolation case: --project is the recorded "
                         "original root (whose project key owns the plans "
                         "dir), --repo-root is the isolated worktree holding "
                         "the commits being gated. Single-repo projects only.")
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

    # Selfmod isolation decouples the two roots (measured 2026-09-20, runs
    # 20260920-131919/-132454/-133041): plans resolve from the recorded
    # original root (--project, whose project key owns the plans dir), while
    # the gate must isolate and verify the WORKTREE tree (--repo-root) — the
    # clone's HEAD is pre-merge at gate time, so gating the clone would
    # verify a tree without the iteration's commits. Honoured in single mode
    # only: a meta project's run cwd comes from `repo:` frontmatter and must
    # not be overridden.
    if args.repo_root is not None:
        _root, _kind = find_project_root(project)
        if _kind != "single":
            print(json.dumps({
                "error": "--repo-root is only valid for single-repo projects; "
                         "meta projects derive the run cwd from `repo:` frontmatter",
                "slug": slug,
                "subplan_path": str(subplan),
            }))
            return 2
        run_cwd = args.repo_root.resolve()

    subplan_checks = parse_local_checks_block(fm_text)
    step_checks: list[dict] = []
    if step is not None:
        step_checks = extract_step_local_checks(body, step)

    # A declared gate that yields nothing runnable is MALFORMED, not absent.
    # `all([])` is True, so without this an unparseable block passes the step
    # with no gate having run. Measured 2026-09-15: a resolver-generated
    # sub-plan used `cmd:` where this parser reads `command:`; it parsed to [],
    # the step passed unconditionally, and it shipped 2/2 reporting "0 fail".
    # 66 of 67 batch-verification sub-plans on that host had the same shape.
    malformed: list[str] = []
    fm_items = count_local_checks_items(fm_text)
    if fm_items > len(subplan_checks):
        malformed.append(
            f"frontmatter local_checks declares {fm_items} item(s) but "
            f"{len(subplan_checks)} parsed"
        )
    if step is not None:
        step_gate = step_gate_fence(body, step)
        step_items = count_step_local_checks_items(body, step)
        if step_items > len(step_checks):
            malformed.append(
                f"step {step} local_checks declares {step_items} item(s) but "
                f"{len(step_checks)} parsed"
            )
        # A step whose region declares local_checks in a shape the locator
        # cannot resolve (e.g. the list sits in a ```json fence) ⇒ refuse.
        if step_gate.declares_local_checks and len(step_checks) == 0:
            malformed.append(
                f"step {step} declares local_checks but 0 were extracted — "
                f"heading or fence shape"
            )
    if malformed:
        print(json.dumps({
            "error": "malformed local_checks: " + "; ".join(malformed)
                     + ". Each item needs a `command:` key (a `cmd:`/`name:` pair "
                       "is not read). Refusing rather than reporting a pass: an "
                       "unreadable gate is a broken gate, not an absent one.",
            "slug": slug,
            "step": step,
            "subplan_path": str(subplan),
            "all_passed": False,
        }))
        return 2

    results: list[CheckResult] = []
    iso_tree = args.repo_root if args.repo_root is not None else project
    iso_ctx = contextlib.nullcontext(IsolationState()) if args.no_isolate else isolate_to_head(iso_tree)
    with iso_ctx as iso:
        for c in subplan_checks:
            results.append(run_one(c, "subplan", run_cwd))
        for c in step_checks:
            results.append(run_one(c, "step", run_cwd))

    passed = all(r.passed for r in results)

    # Rollup outcome: fail > error > pass.  Used by the runner's
    # local_check_outcome when present; absent ⇒ fall back to all_passed.
    rollup = "pass"
    for r in results:
        if r.outcome == "fail":
            rollup = "fail"
            break
        if r.outcome == "error":
            rollup = "error"
            # don't break — a later "fail" would upgrade the rollup

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
        "outcome": rollup,
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
