#!/usr/bin/env python3
"""Plan-quality authoring checks for ilk2 plans — the standalone checker.

Copied from V1's ``plan_lint.py`` (the V1 loop skill's ``scripts/plan_lint.py``,
at commit f40d3065) and trimmed to the checks that judge a *plan*, not V1's
runner.  docs/07-retiring-v1.md section 7.4.2 ("plan_lint.py, a hybrid") splits
the original three ways: the structural, submission-critical checks move into
ilk2 itself; the authoring heuristics stay in a standalone checker in the
planner's folder; the V1-runner checks are dropped.  This file is that
standalone checker.  It imports nothing from the V1 loop's script folder, and
it reads nothing under the V1 data home.

Removed, and why — each served V1's runner rather than plan quality:

* **Verification sub-plan machinery.**  must-exist, attribution-measured,
  the compile-only verify gate, the hand-typed suite command,
  verify-must-run-the-scripts, its scope mismatch, the old-form step 1, the
  whole-suite gate outside the verify sub-plan, and batch-has-no-suite.
  ilk2 owns verification: the batch-verification plan is judged by the
  driver against the batch base, so there is no authoring lint for it here.
* **Gate budgets and timing.**  budget-vs-gate-timeout, the gate budget that
  reads V1's timing corpus, foreground or unbounded whole-suite gates, and
  the whole-suite baseline note.  Those bound how long V1's runner may sit on
  a gate; ilk2's ``submit --check`` measures step size instead.
* **V1 conventions.**  the no-diff step demanding ``--allow-empty`` (ilk2
  accepts a no-diff step at its parent), ``gate_first`` without a gate,
  ``supervised_only``, ``ilk_profile`` / ``result_file``, and the
  contract-change review that names V1 runner files.
* **Toolkit source hygiene** (JSON-mode stderr writes, ``& python`` in
  ``.ps1``): those lint toolkit scripts, not plans.
* **``lint_step_window_fit``** — unit-mismatched (it compared counts with
  minutes); ilk2's ``submit --check`` measures step size instead.

Kept: gate correctness, gate scope, red-first, degrade discipline, test
quality, escaped-bug, structure, and spec traceability — plus the
``--master`` / ``--spec`` CLI.

CLI:
    python plan_check.py [--master MASTER.md] <subplan.md> [<subplan.md> ...]
        prints ``WARN: <slug>: <msg>`` lines (ASCII); exit 1 if any finding.

    python plan_check.py --spec <spec.md> [<spec.md> ...]
        runs the spec pillar -> outcome-AC traceability check instead.

    ``--verified`` (or auto-detected: ``--master`` plus a linted sub-plan
    declaring ``batch_verification: true``) makes ``lint_shared_module_gate``
    advisory — its findings print as ``INFO:`` lines and do not count toward
    the exit code.  Every other check is unaffected.

Reads files with ``utf-8-sig`` (zh-CN Windows configs may carry a BOM).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# ── Helpers copied from the V1 loop's scripts ────────────────────────────────
#
# plan_lint borrowed these from its siblings in the loop's script folder.
# plan_check imports nothing from that folder (docs/07 section 7.4.2: the copy
# is standalone), so the kept checks get the same parsers here.  Semantics are
# unchanged from the originals.

_BLOCK_SCALAR_INDICATORS = {">", ">-", "|", "|-"}


def _coerce(s: str):
    """YAML-ish scalar coercion for local_checks entries (quoted, bool, int)."""
    if s == "":
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.isdigit():
        return int(s)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
        else:  # single-quoted YAML: only '' -> ' is an escape
            inner = inner.replace("''", "'")
        return inner
    return s


def parse_local_checks_block(yaml_text: str) -> list[dict]:
    """Parse a `local_checks:` list out of a yaml-ish text block.

    Copied from the V1 loop's ``run_local_checks.parse_local_checks_block`` so
    this file imports nothing from that folder.  Schema and semantics are
    unchanged: returns [] when local_checks is missing, empty, or `[]`.
    """
    lines = yaml_text.splitlines()
    out: list[dict] = []
    in_block = False
    indent = None
    cur: dict | None = None
    _collecting_bs = False
    _bs_lines: list[str] = []
    _bs_indent: int = 0

    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")

        if not in_block:
            stripped = line.strip()
            if stripped.startswith("local_checks:"):
                rhs = stripped[len("local_checks:"):].strip()
                if rhs in ("[]", "[ ]"):
                    return []
                in_block = True
                continue
            continue

        if _collecting_bs:
            if line.strip() == "":
                _bs_lines.append("")
                continue
            leading = len(line) - len(line.lstrip(" "))
            if not _bs_lines or (len(_bs_lines) == 1 and _bs_lines[-1] == ""):
                _bs_indent = leading
            if leading < _bs_indent and line.strip():
                _collecting_bs = False
                body_lines = [l[_bs_indent:] if len(l) > _bs_indent else ""
                              for l in _bs_lines]
                while body_lines and body_lines[-1] == "":
                    body_lines.pop()
                if cur is not None:
                    cur["command"] = " ".join(body_lines)
            else:
                _bs_lines.append(line)
                continue

        if line.strip() == "":
            continue
        if indent is None:
            stripped_lead = line.lstrip(" ")
            if stripped_lead.startswith("#"):
                continue
            if not stripped_lead.startswith("-"):
                break
            indent = len(line) - len(stripped_lead)

        leading = len(line) - len(line.lstrip(" "))
        if leading < indent and line.strip() and not line.lstrip(" ").startswith("#"):
            break

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
                    _bs_indent = 0
        elif ":" in s and cur is not None:
            k, _, v = s.partition(":")
            val = v.strip()
            cur[k.strip()] = _coerce(val)
            if k.strip() == "command" and val in _BLOCK_SCALAR_INDICATORS:
                _collecting_bs = True
                _bs_lines = []
                _bs_indent = 0

    if _collecting_bs and cur is not None:
        body_lines = [l[_bs_indent:] if len(l) > _bs_indent else ""
                      for l in _bs_lines]
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        cur["command"] = " ".join(body_lines)

    if cur is not None:
        out.append(cur)
    return [c for c in out if c.get("command")]


_STEPS_SECTION_RE = re.compile(
    r"^## Steps\s*\n(.*?)(?=\n## |\Z)", re.MULTILINE | re.DOTALL
)


def steps_section_body(body: str) -> str:
    """Return only the content inside ``## Steps`` sections.

    Legacy fallback: when no ``## Steps`` heading exists, return *body*.
    """
    sections = [m.group(1) for m in _STEPS_SECTION_RE.finditer(body)]
    if not sections:
        return body
    return "\n".join(sections)


class StepGate:
    """Result of :func:`step_gate_fence`: what the locator found for one step."""

    def __init__(
        self,
        heading_count: int,
        fence_text: str | None = None,
        declares_local_checks: bool = False,
    ) -> None:
        self.heading_count = heading_count
        self.fence_text = fence_text
        self.declares_local_checks = declares_local_checks


def step_gate_fence(body: str, step_n: int) -> StepGate:
    """Unified locator for a step's gate fence (copied from run_local_checks).

    * heading: ``^###\s+Step\s+N(?!\d)``.
    * ``heading_count`` counts every match; the region runs from the LAST
      match to the next ``^###\s`` heading.
    * The chosen fence is the first yaml or untagged fence whose content has
      a ``^\s*local_checks:`` line with at least one extractable command.
    """
    scoped = steps_section_body(body)
    heading_re = re.compile(rf"^###\s+Step\s+{step_n}(?!\d)", re.MULTILINE)
    matches = list(heading_re.finditer(scoped))
    heading_count = len(matches)
    if heading_count == 0:
        return StepGate(heading_count=0)

    region_start = matches[-1].end()
    next_heading = re.search(r"^###\s+", scoped[region_start:], re.MULTILINE)
    region = (scoped[region_start:region_start + next_heading.start()]
              if next_heading else scoped[region_start:])

    _lc_re = re.compile(
        r"^\s*['\"]?local_checks['\"]?\s*:\s*(?:\n\s+\S|\[(?!\s*\]))",
        re.MULTILINE,
    )
    declares_local_checks = bool(_lc_re.search(region))

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
                pos = fm.end()
        else:
            in_fence = True
            fence_lang = line[3:] if len(line) > 3 else ""
            fence_start = fm.end()
            pos = fm.end()

    return StepGate(
        heading_count=heading_count,
        declares_local_checks=declares_local_checks,
    )


# Options whose NEXT token is a value, never a path (pytest CLI).
_CLI_VALUE_OPTS = frozenset({
    "-k", "-m", "-p", "-c", "-o", "-n",
    "--deselect", "--ignore", "--ignore-glob", "--pyargs",
    "--rootdir", "--confcutdir", "--basetemp", "--tb",
    "--timeout", "--durations", "--durations-min",
    "--junitxml", "--junit-prefix", "--maxfail", "--import-mode",
    "--dist", "--override-ini", "--code-highlight", "--stepwise-skip",
    "--log-level", "--log-format", "--log-date-format", "--log-cli-level",
    "--log-file", "--log-file-level", "--log-file-mode",
    "--log-file-format", "--log-file-date-format",
})

_SHORT_FLAGS_RE = re.compile(r"^-[A-Za-z]+$")
_LONG_OPT_RE = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9-]*$")
_GLOB_RE = re.compile(r"[*?\[\]{}]")


def _norm_path(p: str) -> str:
    """Repo-relative path form used for comparison (posix, no ``./``)."""
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _coverage_token(tok: str):
    """Classify one positional token: ``(path, has_node_selector, ok)``."""
    if not tok:
        return None, False, True
    if _GLOB_RE.search(tok):
        return None, False, False
    path_part, sep, _node = tok.partition("::")
    if sep and not path_part:
        return None, False, False
    candidate = path_part if sep else tok
    if candidate.startswith("-"):
        return None, False, True
    return _norm_path(candidate), bool(sep), True


def _declared_command_coverage(command: str):
    """Parse one declared command's test file/directory arguments.

    Returns ``(whole_covered, node_only, parseable)``.  Copied from the V1
    loop's ``run_local_checks._declared_command_coverage`` so this file
    imports nothing from that folder.
    """
    raw = command.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    try:
        tokens = shlex.split(raw, comments=True, posix=True)
    except ValueError:
        return set(), set(), False
    if not tokens:
        return set(), set(), False
    if len(tokens) == 1 and " " in tokens[0] and "pytest" in tokens[0]:
        try:
            tokens = shlex.split(tokens[0], comments=True, posix=True)
        except ValueError:
            return set(), set(), False

    runner_at = None
    for i, tok in enumerate(tokens):
        base = os.path.basename(tok)
        if base in ("pytest", "vitest") or base.startswith("pytest"):
            runner_at = i
            break
        if tok == "-m" and i + 1 < len(tokens) and tokens[i + 1] in ("pytest", "unittest"):
            runner_at = i + 1
            break
    if runner_at is None:
        return set(), set(), True

    whole: set[str] = set()
    node_only: set[str] = set()
    i = runner_at + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            for rest in tokens[i + 1:]:
                path, node, ok = _coverage_token(rest)
                if not ok:
                    return set(), set(), False
                if path is None:
                    continue
                (node_only if node else whole).add(path)
            break
        if tok.startswith("-"):
            if "=" in tok:
                i += 1
                continue
            if tok in _CLI_VALUE_OPTS:
                i += 2
                continue
            if _SHORT_FLAGS_RE.match(tok) or tok in ("-h", "--help"):
                i += 1
                continue
            if _LONG_OPT_RE.match(tok):
                return set(), set(), False
            return set(), set(), False
        path, node, ok = _coverage_token(tok)
        if not ok:
            return set(), set(), False
        if path is not None:
            if node:
                node_only.add(path)
            else:
                whole.add(path)
        i += 1

    whole -= node_only
    return whole, node_only, True


def _read_path_prelude(project: Path) -> str:
    """Read ``ship.suite.path_prelude`` from the project's .ilk-launch.json.

    Returns "" when not configured or not readable.  Targeted reader: the V1
    loop's ``ship_config`` loader is not carried over.
    """
    try:
        blob = json.loads(
            (project / ".ilk-launch.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError):
        return ""
    if not isinstance(blob, dict):
        return ""
    ship = blob.get("ship")
    if not isinstance(ship, dict):
        return ""
    suite = ship.get("suite")
    if not isinstance(suite, dict):
        return ""
    prelude = suite.get("path_prelude", "")
    return prelude if isinstance(prelude, str) else ""


_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[a-z]?-")


def _strip_date_prefix(slug: str) -> str:
    """Strip a leading ``YYYY-MM-DD[<letter>]-`` prefix; idempotent.

    Same semantics as the V1 loop's ``plan_slug.strip_date_prefix``; that
    module is not imported here.
    """
    return _DATE_PREFIX_RE.sub("", slug, count=1)


# Working directory for git operations.  Overridable via --git-cwd for tests.
_GIT_CWD: Path | None = None

# Project root for importer-lookup grep.  Overridable for tests.
_PROJECT_ROOT: Path | None = None

# Fallback / degrade markers: language that says "there is a safe alternative
# path if the capability is missing".
_FALLBACK_MARKERS = re.compile(
    r"(AC-GUARD|self-guard|degrade|safe default|"
    r"implement to[^\n]{0,60}pattern|build to[^\n]{0,40}pattern|"
    r"is OPTIONAL|optional[^\n]{0,40}(if|when))",
    re.IGNORECASE,
)

# A hard MCP gate in env_prereqs: `claude mcp list | grep -q <token>`.
_HARD_MCP_GATE = re.compile(r"claude\s+mcp\s+list[^\n]*grep\s+-q\s+([A-Za-z0-9_.-]+)")

# A step instruction to set blocked (not the frontmatter `status:` field).
_SET_BLOCKED = re.compile(r"(set|sets|->|→)[^\n]{0,30}status:\s*blocked", re.IGNORECASE)




def _token_is_optional(body: str, token: str) -> bool:
    """True if `token` is referenced near optional/absent/fallback language."""
    t = re.escape(token)
    patterns = [
        rf"{t}\b[^\n]{{0,60}}(optional|absent|not connected|unavailable|fallback|if absent)",
        rf"(if|when|absent|optional|no)\b[^\n]{{0,40}}{t}",
        rf"(AC-GUARD|self-guard|degrade|implement to[^\n]{{0,40}}pattern)[^\n]{{0,200}}{t}",
        rf"{t}[^\n]{{0,200}}(AC-GUARD|implement to[^\n]{{0,40}}pattern|degrade)",
    ]
    return any(re.search(p, body, re.IGNORECASE) for p in patterns)


def lint_envprereq_fallback_contradiction(text: str, slug: str) -> list[str]:
    """Flag a hard MCP env_prereq for a capability that also has a fallback path."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    has_fallback = bool(_FALLBACK_MARKERS.search(text))  # markers may live in frontmatter comments too
    if not has_fallback:
        return findings
    for token in sorted(set(_HARD_MCP_GATE.findall(text))):
        if _token_is_optional(text, token):
            findings.append(
                f"{slug}: hard env_prereq 'claude mcp list | grep -q {token}' "
                f"contradicts a documented fallback for '{token}': the gate fast-fails "
                f"to blocked before the fallback runs. Make '{token}' optional (encode "
                f"the degrade path in step logic), not a hard env_prereq."
            )
    return findings


def lint_block_when_default_exists(text: str, slug: str) -> list[str]:
    """Flag a step that sets status:blocked when a safe default/fallback exists."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    if _SET_BLOCKED.search(body) and _FALLBACK_MARKERS.search(body):
        findings.append(
            f"{slug}: a step sets 'status: blocked' although the sub-plan documents a "
            f"safe default/fallback: on a headless loop that stalls for a human instead "
            f"of degrading. Prefer degrade-to-default; reserve 'blocked' for un-closeable gaps."
        )
    return findings


_SCOPE_PATHS_RE = re.compile(r"^scope_paths:\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s+-\s+\"?([^\"\n]+)\"?\s*$", re.MULTILINE)


def _extract_scope_paths(text: str) -> list[str]:
    """Extract scope_paths list from YAML-like frontmatter."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return []
    fm = text[m.start():m.end()]
    # Find scope_paths: then collect indented list items
    sm = _SCOPE_PATHS_RE.search(fm)
    if not sm:
        return []
    after = fm[sm.end():]
    # Stop at the next top-level key (or the closing ---).  _LIST_ITEM_RE is
    # permissive enough to swallow the following block otherwise: on a sub-plan
    # whose `local_checks:` follows `scope_paths:`, it returned the whole
    # local_checks body as a second "scope path" (measured 2026-08-29).  Every
    # consumer of scope_paths then reasons over that garbage -- including the
    # off-base branch check, which resolves each entry against git.
    stop = re.search(r"^(?:[A-Za-z_][A-Za-z0-9_]*:|---\s*$)", after, re.M)
    if stop:
        after = after[:stop.start()]
    return _LIST_ITEM_RE.findall(after)


# ── Brittle exact-list-assertion guard (FM-0002) ────────────────────────────
#
# A sub-plan's local_checks command that asserts exact equality on a list/set
# (e.g. `== ["a","b"]`, `deepStrictEqual(x, ['a'])`) against a growing
# accessor is brittle — adding a member breaks the gate.  Warn and recommend
# superset/contains instead.  See failure-modes.md FM-0002.

# Patterns that indicate an exact-list-equality assertion.
_BRITTLE_EXACT_LIST_RE = re.compile(
    r"""
    ==\s*\[              #  == [ ... ]
    |deepStrictEqual\s*\(  #  deepStrictEqual( ... )
    |deepEqual\s*\(        #  deepEqual( ... )
    |assertEqual\s*\(      #  assertEqual( ... )
    |assertEquals\s*\(     #  assertEquals( ... )
    """,
    re.VERBOSE,
)

# Patterns that indicate a containment / superset assertion (correct form).
_CONTAINMENT_RE = re.compile(
    r"""
    contains\s*\(          #  jq contains([ ... ])  or  set >= { ... }
    |>=\s*\{               #  superset set literal
    |\bsubset\s*of\b       #  natural-language "subset of"
    |\bcontains\b          #  generic contains keyword
    """,
    re.VERBOSE | re.IGNORECASE,
)


_BLOCK_SCALAR_INDICATORS = {">", ">-", "|", "|-"}


def _collect_commands_from_yaml_block(block_text: str) -> list[str]:
    """Extract command values from a YAML local_checks block, handling block scalars.

    When a command value is a YAML block-scalar indicator (>, >-, |, |-),
    the following indented lines are the body.  This function folds them into
    a single command string so that ``command: >-\\n    echo hello`` yields
    ``echo hello``, not ``>-``.
    """
    commands: list[str] = []
    lines = block_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*(?:-\s+)?command:\s*(.+)", line)
        if m:
            val = m.group(1).strip()
            if val in _BLOCK_SCALAR_INDICATORS:
                # Collect continuation lines (more indented than this line).
                cmd_indent = len(line) - len(line.lstrip(" "))
                body_lines: list[str] = []
                j = i + 1
                content_indent: int | None = None
                while j < len(lines):
                    cline = lines[j]
                    if cline.strip() == "":
                        body_lines.append("")
                        j += 1
                        continue
                    cleading = len(cline) - len(cline.lstrip(" "))
                    if content_indent is None:
                        content_indent = cleading
                    if cleading < content_indent:
                        break
                    body_lines.append(cline[content_indent:])
                    j += 1
                # Drop trailing blanks and fold.
                while body_lines and body_lines[-1] == "":
                    body_lines.pop()
                commands.append(" ".join(body_lines))
                i = j
                continue
            else:
                commands.append(val)
        i += 1
    return commands


def _extract_local_checks_commands(text: str) -> list[str]:
    """Extract command values from the local_checks list in frontmatter."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return []
    fm = text[m.start():m.end()]
    lc = re.search(r"^local_checks:\s*$", fm, re.MULTILINE)
    if not lc:
        return []
    after = fm[lc.end():]
    return _collect_commands_from_yaml_block(after)


def lint_brittle_exact_list_assertion(text: str, slug: str) -> list[str]:
    """Flag a local_checks command with an exact-list-equality assertion."""
    findings: list[str] = []
    commands = _extract_all_local_checks_commands(text)
    for cmd in commands:
        has_exact = bool(_BRITTLE_EXACT_LIST_RE.search(cmd))
        if not has_exact:
            continue
        # If the same command also uses a containment pattern, it's likely
        # checking containment, not exact equality — skip.
        if _CONTAINMENT_RE.search(cmd):
            continue
        findings.append(
            f"{slug}: local_checks command '{cmd.strip()[:80]}' appears to assert "
            f"exact list/set equality against a growing accessor (FM-0002). "
            f"Use a superset/contains assertion instead (e.g. jq 'contains([...])', "
            f"assert set(...) >= {{...}}) to avoid brittleness when members are added."
        )
    return findings


# ── Escaped-bug regression gate ────────────────────────────────────────────────
#
# A sub-plan that fixes a human-found escaped bug (declared via
# ``regression_for:`` frontmatter field) must carry at least one reproducing
# ``local_check`` — either in frontmatter ``local_checks:`` or in a per-step
# ``local_checks:`` yaml block.  The linter cannot verify a check truly
# reproduces the bug, so the enforceable contract is structural presence.
# See decomposition-principles.md §escaped-bug-regression-gate.

# Frontmatter field: regression_for: <escaped-bug-tracker-id>
_REGRESSION_FOR_RE = re.compile(r"^regression_for: *([^\r\n]*)$", re.MULTILINE)

# Per-step local_checks block: ```yaml ... local_checks: ... command: ... ```
_STEP_LOCAL_CHECKS_BLOCK_RE = re.compile(
    r"```yaml\n.*?local_checks:\s*\n(.*?)(?:```|\Z)", re.S
)


def _extract_regression_for(text: str) -> str | None:
    """Return the regression_for value from frontmatter, or None if absent/empty."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return None
    fm = text[m.start():m.end()]
    match = _REGRESSION_FOR_RE.search(fm)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value else None


def _has_any_local_check(text: str) -> bool:
    """True if the sub-plan declares at least one local_check anywhere."""
    # Uses the widened extractor: if a per-step block has a command, that counts.
    cmds = _extract_all_local_checks_commands(text)
    return bool(cmds)


def lint_escaped_bug_regression_gate(text: str, slug: str) -> list[str]:
    """Flag an escaped-bug fix sub-plan that has no reproducing local_check."""
    findings: list[str] = []
    if _extract_regression_for(text) is None:
        return findings
    if _has_any_local_check(text):
        return findings
    findings.append(
        f"{slug}: regression_for is set but the sub-plan declares no local_check "
        f"(neither frontmatter local_checks nor per-step local_checks block). "
        f"An escaped-bug fix must carry a reproducing local_check to prevent "
        f"the same class of bug from escaping a gate twice."
    )
    return findings


# ── Frontmatter local_check path-created-later guard ────────────────────────
#
# A sub-plan's frontmatter ``local_checks`` run at EVERY step.  If a command
# references a path that the plan's own later steps create, the check fails
# on earlier steps (e.g. pytest exit 4 "file or directory not found").  This
# lint flags such references so the planner can move the check to that step's
# per-step block.
#
# See decomposition-principles.md §8 local_checks anti-patterns.

# Common CLI tokens that look like paths but aren't.
_NON_PATH_TOKENS = frozenset({
    "python", "python3", "node", "npm", "npx", "bash", "sh", "powershell",
    "pytest", "jest", "mocha", "cargo", "go", "make", "cmake",
    "-m", "-c", "-q", "-v", "-x", "-s", "-k", "-n",
    "--timeout", "--timeout-method", "--keepdb", "--verbosity", "--noinput",
    "--tb", "--co", "--collect-only", "-rx", "-rxs",
    "test", "tests", "src", "lib", "bin", "dist", "build",
    "run",  # vitest/jest subcommand
})


def _looks_like_path(token: str) -> bool:
    """True if *token* is plausibly a filesystem path (not a flag or program name)."""
    if not token or token.startswith("-"):
        return False
    if token in _NON_PATH_TOKENS:
        return False
    # Skip version-like strings (e.g. "3.12", "2.7")
    if re.match(r"^\d+\.\d+", token):
        return False
    # Contains a path separator → likely a path
    if "/" in token or "\\" in token:
        return True
    # Has a file extension → likely a path
    if re.search(r"\.[a-zA-Z0-9]{1,10}$", token):
        return True
    return False


def lint_frontmatter_path_created_later(text: str, slug: str) -> list[str]:
    """Flag a frontmatter local_check that references a path the plan creates later."""
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    if not scope_paths:
        return findings
    commands = _extract_local_checks_commands(text)
    if not commands:
        return findings
    # Normalize scope_paths: strip trailing slashes for comparison
    normalized_scope = {p.rstrip("/\\") for p in scope_paths}
    for cmd in commands:
        tokens = cmd.split()
        for token in tokens:
            if not _looks_like_path(token):
                continue
            norm = token.rstrip("/\\")
            # Match if the token IS a scope path, OR is an ancestor directory of
            # one (the plan creates files UNDER this dir). This is the real
            # tray-actions shape: command refs `tools/xbar/tests/` while
            # scope_paths lists `tools/xbar/tests/test_*.py` (esc d400d9e7).
            nt = norm.replace("\\", "/")
            # Both directions are real:
            #   command names the DIR, scope_paths lists files under it
            #     (tray-actions shape, esc d400d9e7); and
            #   scope_paths names the DIR, command names a file under it.
            # Matching only the first let the second ship unflagged.
            covered = any(
                (spn := sp.replace("\\", "/")) == nt
                or spn.startswith(nt + "/")
                or nt.startswith(spn.rstrip("/") + "/")
                for sp in normalized_scope
            )
            if not covered:
                continue
            # The token is a path this plan creates — check if it exists now.
            try:
                exists = Path(token).exists()
            except (OSError, ValueError):
                continue  # Skip on weird input
            if not exists:
                findings.append(
                    f"{slug}: frontmatter local_check references '{token}' which "
                    f"this sub-plan's steps create -- subplan-scope checks run at "
                    f"EVERY step and will fail before the step that creates it; "
                    f"move it to that step's per-step local_checks or drop it "
                    f"from frontmatter."
                )
    return findings


# ── E2e/device-poll local_check without env_prereq ───────────────────────────
#
# A sub-plan that declares an e2e, browser-automation, or service-poll
# local_check (e.g. ``node e2e/*.mjs``, ``playwright test``, a localhost URL,
# ``devtools``/``chrome-devtools``, or poll phrasing) but has no ``env_prereqs``
# reachability probe and no ``docs/loop/preflight.sh`` reference is a
# reachability gap: the gate will burn its timeout into ``local-checks-stuck``
# when the dependency is unreachable.  Warn so the planner adds an
# ``env_prereqs`` entry.  See decomposition-principles.md section 10.

# Reachability probes — commands that ARE env_prereq-style checks, not test gates.
_REACHABILITY_CMD_RE = re.compile(r"^\s*(curl|wget)\s", re.IGNORECASE)

# E2e / browser-automation / service-poll markers in a local_check command.
_E2E_DEVICE_POLL_RE = re.compile(
    r"e2e/"
    r"|playwright"
    r"|cypress"
    r"|\.mjs"
    r"|\.spec\."
    r"|devtools"
    r"|chrome-devtools"
    r"|--browserUrl"
    r"|poll"
    r"|wait\s+for"
    r"|App\s+not\s+ready",
    re.IGNORECASE,
)

# Env_prereqs frontmatter field with at least one entry (non-empty list).
_ENV_PREREQS_PRESENT_RE = re.compile(
    r"^env_prereqs:\s*\n\s+-\s+\S", re.MULTILINE
)

_PREFLIGHT_REF_RE = re.compile(r"docs/loop/preflight\.sh")


def _extract_env_prereqs(text: str) -> bool:
    """True if the frontmatter declares a non-empty env_prereqs list."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return False
    fm = text[m.start():m.end()]
    return bool(_ENV_PREREQS_PRESENT_RE.search(fm))


def _has_preflight_ref(text: str) -> bool:
    """True if the body references docs/loop/preflight.sh."""
    body = _strip_frontmatter(text)
    return bool(_PREFLIGHT_REF_RE.search(body))


def lint_e2e_check_without_env_prereq(text: str, slug: str) -> list[str]:
    """Flag an e2e/device-poll local_check with no env_prereq reachability probe."""
    findings: list[str] = []
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    if not commands:
        return findings
    # Fast-exit: env_prereqs present or preflight referenced -> no finding.
    if _extract_env_prereqs(text):
        return findings
    if _has_preflight_ref(text):
        return findings
    for cmd in commands:
        # Skip reachability probes (curl/wget) — those are env_prereq-style checks, not test gates.
        if _REACHABILITY_CMD_RE.search(cmd):
            continue
        if _E2E_DEVICE_POLL_RE.search(cmd):
            findings.append(
                f"{slug}: local_check '{cmd.strip()[:80]}' looks like an "
                f"e2e/device-poll command but the sub-plan declares no "
                f"env_prereqs reachability probe. Add an env_prereqs entry "
                f"with a fast-fail verify_cmd (see decomposition-principles "
                f"section 10) to avoid local-checks-stuck timeouts."
            )
    return findings


# ── Whole-suite command classification ──────────────────────────────────────
#
# The kept gate-scope checks need to know whether a ``local_checks`` command
# runs a pre-existing whole suite (``pytest`` with no path scope, ``npm
# test``) or a genuinely scoped selection.  The baseline-note check that
# used to sit here is a V1-runner concern and was dropped with the other
# gate-budget lints.
#
# Heuristics for "whole suite":
#   - ``pytest`` / ``py.test`` / ``vitest`` / ``jest`` with no positional
#     arg that looks like a specific file or directory
#   - ``bash tests/*.sh`` or ``sh tests/*.sh`` (shell glob on test dir)
#   - ``npm test`` / ``yarn test`` / ``bun test`` with no specific file arg

_WHOLE_SUITE_CMD_RE = re.compile(
    r"""
    \b(?:pytest|py\.test|vitest|jest)\b
    |\bbash\s+tests/\*\.sh\b
    |\bsh\s+tests/\*\.sh\b
    |\bnpm\s+(?:run\s+)?test\b
    |\byarn\s+test\b
    |\bbun\s+test\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


# Flags that consume the NEXT token as their value.  Without this, e.g.
# ``--timeout-method thread`` leaves "thread" looking like a bare path.
_VALUE_TAKING_FLAGS = frozenset({
    "-k", "-m", "-n", "-p", "-c", "-o", "-W",
    "--timeout", "--timeout-method", "--tb", "--maxfail", "--ignore",
    "--deselect", "--durations", "--rootdir", "-r",
})

# A selector genuinely narrows which tests run; keep treating it as scoped.
_SELECTOR_FLAGS = frozenset({"-k", "--deselect"})

_TEST_FILE_EXT_RE = re.compile(r"\.(?:py|ts|tsx|js|jsx|mjs)$", re.IGNORECASE)


def _is_directory_arg(token: str) -> bool:
    """True if *token* names a DIRECTORY tree rather than one test file.

    A directory argument does not scope a suite in any way this lint cares
    about: pytest still collects the whole tree, so a collection error or a
    baseline-red test anywhere under it fails the gate.  Treating it as "scoped"
    hid 47 of 131 effectively-whole-suite gates across the real corpus (35%),
    including the two most common gate forms in it (``pytest tests/ -q``, 33
    occurrences; ``pytest tests/ -x -q``, 17).

    A single file or a ``::node_id`` is genuinely scoped and returns False.
    """
    if "::" in token:
        return False                          # node id → one test
    if _TEST_FILE_EXT_RE.search(token):
        return False                          # a single test file
    # Require a path shape so a stray bare word is never read as a directory.
    return token.endswith("/") or "/" in token or token in {"tests", "test", "src"}


def _strip_runner_prefix(cmd: str) -> str:
    """Strip a package-runner prefix (``bunx``, ``npx``, ``pnpm dlx``,
    ``yarn dlx``, or the ``<pm> run <script>`` forms) from *cmd*.

    Returns the command after the prefix.  For script forms
    (``bun run test:e2e``) the remaining token is a script name, not a
    test-runner invocation — callers must re-check against
    ``_WHOLE_SUITE_CMD_RE``.
    """
    # Strip surrounding quotes that YAML extraction may leave on the command.
    if (cmd.startswith('"') and cmd.endswith('"')) or (
        cmd.startswith("'") and cmd.endswith("'")
    ):
        cmd = cmd[1:-1]
    tokens = cmd.split()
    _RUNNER_PREFIXES = {"bunx", "npx"}
    _RUNNER_DLX = {"pnpm", "yarn"}  # followed by "dlx"
    _SCRIPT_RUNNERS = {"bun", "npm", "pnpm", "yarn"}  # followed by "run"

    if not tokens:
        return cmd

    head = tokens[0].lower()

    # ``bunx vitest run …`` / ``npx vitest run …``
    if head in _RUNNER_PREFIXES and len(tokens) > 1:
        return " ".join(tokens[1:])

    # ``pnpm dlx vitest run …`` / ``yarn dlx vitest run …``
    if head in _RUNNER_DLX and len(tokens) > 2 and tokens[1].lower() == "dlx":
        return " ".join(tokens[2:])

    # ``bun run test:e2e`` / ``npm run test:unit`` / etc.
    if head in _SCRIPT_RUNNERS and len(tokens) > 2 and tokens[1].lower() == "run":
        return " ".join(tokens[2:])

    return cmd


def _is_whole_suite_command(cmd: str) -> bool:
    """True if *cmd* runs a pre-existing whole test suite (no file scope)."""
    cmd_stripped = cmd.strip()
    # Strip surrounding quotes BEFORE capturing ``original`` so that
    # ``prefix_stripped`` reflects only a runner prefix, not quote removal.
    # YAML extraction may leave quotes on the command; those are a
    # serialisation detail and must never change classification.
    if (cmd_stripped.startswith('"') and cmd_stripped.endswith('"')) or (
        cmd_stripped.startswith("'") and cmd_stripped.endswith("'")
    ):
        cmd_stripped = cmd_stripped[1:-1]
    # Strip runner prefixes (bunx, npx, pnpm dlx, yarn dlx, pm run <script>)
    # BEFORE the regex check — ``bun run test:e2e`` doesn't match the bare
    # ``bun test`` pattern, but after stripping the prefix it's a script form
    # that should be classified BROAD.
    original = cmd_stripped
    cmd_stripped = _strip_runner_prefix(cmd_stripped)
    prefix_stripped = (cmd_stripped != original)
    if not _WHOLE_SUITE_CMD_RE.search(cmd_stripped):
        # A script-form command (``bun run test:e2e``) left only a script name
        # — not a test-runner invocation.  The script could do anything, so
        # treat it as a whole-suite gate (BROAD).
        if prefix_stripped:
            return True  # prefix was stripped → script form → BROAD
        return False
    # For pytest/vitest/jest, decide from the positional args: a single file or
    # node id scopes the run; a DIRECTORY does not (it runs the whole tree).
    if re.search(r"\b(?:pytest|py\.test|vitest|jest)\b", cmd_stripped, re.IGNORECASE):
        tokens = cmd_stripped.split()
        positional: list[str] = []
        skip_next = False
        for tok in tokens[1:]:  # skip the leading program name
            if skip_next:
                skip_next = False
                continue
            if tok.startswith("-"):
                # ``--flag=value`` carries its value inline; ``--flag value``
                # consumes the next token.
                if "=" not in tok and tok in _VALUE_TAKING_FLAGS:
                    skip_next = True
                if tok.split("=", 1)[0] in _SELECTOR_FLAGS:
                    return False  # a -k/--deselect selector scopes the run
                continue
            if tok in _NON_PATH_TOKENS:
                # ``tests``, ``test``, ``src`` etc. are non-path tokens but
                # still directory args when passed to pytest — they scope the
                # collection tree, not a single file.
                if _is_directory_arg(tok):
                    positional.append(tok)
                continue
            positional.append(tok)
        if any(_is_directory_arg(t) for t in positional):
            return True   # a directory tree → whole suite
        if positional:
            return False  # file paths / node ids only → genuinely scoped
    return True


# ── POSIX-only test assertion guard ──────────────────────────────────────────
#
# A ``.sh`` test (or a ``local_check`` shell command) that asserts a POSIX file
# mode (``rw-------``, ``stat -c %A``, ``chmod 600`` check) without a
# ``uname``/``OSTYPE`` guard cannot pass on Windows Git Bash.  Real case:
# ``test_worker_bootstrap.sh`` rw------- check (2026-06-28 drawing-worker run,
# backlog 602e2039).

_POSIX_MODE_ASSERTION_RE = re.compile(
    r"""
    rw-------                          # permission string literal
    |stat\s+-c\s+%(?:A|a)             # stat -c %A or %a (Linux-only format)
    |chmod\s+[0-7]{3,4}\b             # chmod 600 / chmod 755 / etc.
    |ls\s+-l[^\n]*rw-------           # ls -l ... rw-------
    """,
    re.VERBOSE,
)

_PLATFORM_GUARD_RE = re.compile(
    r"""
    uname                              # uname check
    |\$OSTYPE                          # $OSTYPE variable
    |\bOSTYPE\b.*(?:==|!=|~=)          # OSTYPE comparison
    |if\s*\[\[.*OSTYPE                 # if [[ "$OSTYPE" == ...
    |platform|operating.system         # generic platform check
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_posix_only_test_assertion(text: str, slug: str) -> list[str]:
    """Flag a local_check with POSIX-only perm assertions and no platform guard."""
    findings: list[str] = []
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    body = _strip_frontmatter(text)
    full_text = text  # guards may appear anywhere
    # Check commands first — inline POSIX assertions.
    for cmd in commands:
        if not _POSIX_MODE_ASSERTION_RE.search(cmd):
            continue
        if _PLATFORM_GUARD_RE.search(full_text):
            continue
        findings.append(
            f"{slug}: local_check '{cmd.strip()[:80]}' asserts a POSIX file "
            f"mode (rw-------, stat -c %A, chmod) without a uname/OSTYPE "
            f"platform guard. This check cannot pass on Windows Git Bash. "
            f"Add a uname guard or skip on non-POSIX platforms."
        )
    # Also check the body — a referenced .sh test may contain the assertions.
    if not findings and _POSIX_MODE_ASSERTION_RE.search(body):
        if not _PLATFORM_GUARD_RE.search(full_text):
            findings.append(
                f"{slug}: sub-plan body references POSIX file mode assertions "
                f"(stat -c %A, chmod, rw-------) but no uname/OSTYPE platform "
                f"guard is present. The referenced test cannot pass on Windows "
                f"Git Bash. Add a uname guard or skip on non-POSIX platforms."
            )
    return findings


# ── Network-tool mock-only gate guard ──────────────────────────────────────
#
# A sub-plan that ships a new HTTP/network tool (body mentions
# urllib/requests/``api.``/endpoint/``_post``) whose ONLY gate is a unit test
# that mocks the network boundary (``patch(... _post)``, injected fake) with
# no integration/import-resolve/live smoke → the live path can ship broken.
# Real case: draw.py ``_load_minimax_token`` ModuleNotFoundError (2026-06-28).

_NETWORK_TOOL_SIGNAL_RE = re.compile(
    r"""
    urllib
    |requests\.|requests\.get|requests\.post
    |api\.\w+                          # api.minimax, api.openai, etc.
    |_post\b
    |_get\b
    |endpoint
    |http\.client
    |aiohttp
    |httpx
    """,
    re.VERBOSE | re.IGNORECASE,
)

_MOCK_PATTERN_RE = re.compile(
    r"""
    patch\s*\(                         # mock.patch(...)
    |@patch                            # @patch decorator
    |inject.*fake                      # injected fake
    |mock.*network                     # mock the network
    |fake.*response                    # fake response
    |_post.*mock|mock.*_post           # mock the _post function
    """,
    re.VERBOSE | re.IGNORECASE,
)

_INTEGRATION_SMOKE_RE = re.compile(
    r"""
    import.*resolve                    # import-resolve check
    |import\s+\w+.*\bfrom\b           # import check
    |python\s+-c\s+.*import           # python -c "import ..."
    |live\s+smoke                      # live smoke test
    |integration                       # integration test
    |env_prereqs                       # has env prereqs (live dependency)
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_network_tool_mock_only_gate(text: str, slug: str) -> list[str]:
    """Flag a network-tool sub-plan whose only gates mock the network."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    full_text = text
    # Only flag if the body signals a network tool.
    if not _NETWORK_TOOL_SIGNAL_RE.search(body):
        return findings
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    if not commands:
        return findings
    # Check if ALL commands are mock-only (no integration smoke).
    all_cmds_text = " ".join(commands)
    has_mock = bool(_MOCK_PATTERN_RE.search(all_cmds_text) or _MOCK_PATTERN_RE.search(body))
    has_integration = bool(_INTEGRATION_SMOKE_RE.search(all_cmds_text) or _extract_env_prereqs(full_text))
    if has_mock and not has_integration:
        findings.append(
            f"{slug}: sub-plan signals a network tool (urllib/requests/_post) "
            f"but every local_check mocks the network boundary with no "
            f"integration/import-resolve/live smoke and no env_prereqs. "
            f"The live path can ship broken (cf. draw.py ModuleNotFoundError). "
            f"Add an import-resolve or live smoke check."
        )
    return findings


# ── Vertical-slice AC guard (model-only, no consumer AC) ─────────────────────
#
# A sub-plan that adds a model/logic capability (new exported function, class,
# or export symbol) in a non-UI module whose EVERY local_check is a pure-unit
# test (pytest/vitest) with no consumer entry-point keyword (UI hit-test, CLI
# verb, HTTP route, e2e sim) is the "orphaned model" shape:
# the model compiles and unit-tests pass, but nothing proves a player/user can
# actually reach it.  Warn so the planner adds a consumer-level AC.
#
# See: decomposition-principles.md §8, §12 ("orphaned model" / model-only gate).

# New model symbol: a def/class/export that signals new capability.
# Matches both literal code (def upgrade, class Enemy, export function)
# and prose descriptions ("Adds upgrade()", "new function computePath").
_NEW_SYMBOL_RE = re.compile(
    r"""
    (?:^|\s)def\s+\w+                             #  Python def
    |(?:^|\s)class\s+\w+                          #  Python class
    |export\s+(?:function|const|class)\s+\w+      #  JS/TS export
    |(?:adds?|new|implement|create|introduce)\s+  #  prose: introduces
      (?:\w+\s+)?(?:\w+\.)?\w+\(                  #  "upgrade(" or "foo.bar("
    |(?:adds?|new|implement|create|introduce)\s+  #  prose: introduces
      (?:exported?\s+)?(?:function|class|method|api)\b  #  "new function"
    """,
    re.VERBOSE | re.MULTILINE | re.IGNORECASE,
)

# UI / presentation-layer path markers — a scope_path containing one of these
# is a UI file, not a model/logic module.
_UI_PATH_RE = re.compile(
    r"ui|frontend|components?|pages?|views?|screens?|hud|renderer",
    re.IGNORECASE,
)

# Consumer entry-point keywords — presence in a local_check or AC means the
# check exercises the symbol through a real entry point, not only unit tests.
_CONSUMER_ENTRY_RE = re.compile(
    r"""
    click|take_snapshot|press_key|fill\(|hover\(|        # chrome-devtools
    fetch\(|curl\b|requests\.\w+|httpx|aiohttp|          # HTTP
    playwright|cypress|selenium|                          # browser e2e
    e2e/|/e2e\b|                                          # e2e directory
    subprocess|run_command|cli\b|invoke\b|                # CLI verbs
    integration|live\s*smoke|                             # integration
    navigate|goto|open_page|new_page|                     # page navigation
    socket|websocket                                      # real-time
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_vertical_slice_ac(text: str, slug: str) -> list[str]:
    """Flag a sub-plan that adds a model symbol with only pure-unit gates."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    scope_paths = _extract_scope_paths(text)
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    # Need at least one scope_path in a non-UI module and one command.
    if not scope_paths or not commands:
        return findings
    # At least one scope_path must be a non-UI module (model/logic layer).
    has_non_ui = any(not _UI_PATH_RE.search(p) for p in scope_paths)
    if not has_non_ui:
        return findings
    # The body must introduce a new model symbol.
    if not _NEW_SYMBOL_RE.search(body):
        return findings
    # Check if ALL local_checks are pure-unit (no consumer entry-point keyword).
    all_cmds_text = " ".join(commands)
    combined = all_cmds_text + " " + body
    if _CONSUMER_ENTRY_RE.search(combined):
        return findings
    findings.append(
        f"{slug}: sub-plan adds a model/logic symbol (def/class/export) but "
        f"every local_check is a pure-unit test with no consumer entry-point "
        f"keyword (UI hit-test, CLI verb, HTTP route, e2e sim). This is the "
        f"'orphaned model' shape — the model compiles and "
        f"unit-tests pass but nothing proves a player/user can reach it. "
        f"Add an AC that exercises the symbol through its real entry point."
    )
    return findings


# ── Anti-hardcode integration gate (config/data wiring) ──────────────────────
#
# A sub-plan that introduces per-instance data (per-stage path, per-tenant
# config, per-level theme) that an existing module SHOULD consume, but whose
# local_checks don't assert the consumer actually reads the new data (vs a
# hardcoded constant), is the 'data-present but runtime-broken' shape: the data exists but the
# consumer is still hardcoded to a different source.  Warn so the planner
# adds a check that binds the consumer to the data.
#
# Conservative: only fires when BOTH the data-introduction AND
# consumer-should-read signals are present AND no read-assertion exists.
#
# See: decomposition-principles.md §8 (anti-hardcode integration).

# Per-instance data: body describes data that varies by stage/tenant/level/etc.
_PER_INSTANCE_DATA_RE = re.compile(
    r"""
    per[-_ ]?(?:stage|tenant|level|feature|config|instance|user|team|env)
    |(?:path|paths)\s+(?:array|list|data)
    |(?:config|registry|theme)\s+(?:array|list|data|file)
    |distinct\s+(?:path|config|registry|theme)
    |different\s+(?:path|config|registry|theme|rails?)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Consumer-should-read: body says an existing module should use the new data.
_CONSUMER_SHOULD_READ_RE = re.compile(
    r"""
    should\s+(?:consume|read|use|import|load|follow|reference)
    |(?:consumes?|reads?|imports?|loads?|follows?)\s+(?:the|this|its|active|new)
    |existing\s+\w+\s+(?:should|must|needs?\s+to)
    |hardcoded?\s+(?:to|constant|value)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Read-assertion: a local_check command that actually verifies the consumer
# reads the new data (not just prose describing the gap).
# Requires an assertion/test/verify keyword (not just "should consume").
_READ_ASSERTION_RE = re.compile(
    r"""
    (?:assert|check|test|verify)\b.*\b(?:reads?|consumes?|uses?|imports?|loads?|follows?)\b
    |\b(?:reads?|consumes?|uses?|imports?|loads?|follows?)\b.*\b(?:assert|check|test|verify)\b
    |integration\s+test.*\b(?:read|consume|use|follow)\b
    |\b(?:read|consume|use|follow)\b.*integration\s+test
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_anti_hardcode_integration(text: str, slug: str) -> list[str]:
    """Flag per-instance data introduction without consumer read-assertion."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    # Need both signals in the body to fire.
    if not _PER_INSTANCE_DATA_RE.search(body):
        return findings
    if not _CONSUMER_SHOULD_READ_RE.search(body):
        return findings
    # Check if any local_check asserts the consumer reads the new data.
    all_cmds_text = " ".join(commands)
    combined = all_cmds_text + " " + body
    if _READ_ASSERTION_RE.search(combined):
        return findings
    findings.append(
        f"{slug}: sub-plan introduces per-instance data and says an existing "
        f"module should consume it, but no local_check asserts the consumer "
        f"actually reads the new data (vs a hardcoded constant). This is the "
        f"'data-present but runtime-broken' shape. Add a "
        f"local_check that verifies the consumer reads from the new data source."
    )
    return findings


# ── UI-promise-wiring guard (affordance without binding) ─────────────────
#
# A sub-plan that introduces a UI affordance/prompt that advertises a
# capability (key hint, button label, tooltip, shortcut, indicator) but
# whose local_checks and body contain no wiring/trigger assertion (event
# handler, keybind registration, click/press_key/e2e that exercises the
# affordance) is the "promise-without-wiring" shape: the UI prompts the
# user to act, but nothing is bound.  This is worse than a missing feature
# because the prompt reads as a *promise*, and the unmet promise breaks
# trust.  Warn so the planner adds a check that the affordance is wired.
#
# See: decomposition-principles.md §8.
# See: commands/ilk-plan.md step 7g.

# Affordance-advertisement signal: a UI prompt that advertises a capability.
# Matches patterns like "press C", "按 E", "button labeled X", "tooltip",
# "key hint", "shortcut", "indicator" (e.g. ×N speed indicator).
_UI_ADVERTISEMENT_RE = re.compile(
    r"""
    press\s+[A-Za-z0-9]                    # "press C", "press E"
    |按\s*[A-Za-z0-9]                       # "按E", "按 E"
    |menu\s+item                           # "menu item"
    |button\s+(?:labeled|labelled|that\s+says)  # "button labeled X"
    |tooltip                               # tooltip hint
    |key\s*hint                            # key hint
    |hint\s+(?:text|label|bar|line|shown|displayed|appears)  # hint + UI noun/verb
    |(?:shows?|displays?|on-?screen|status\s+bar)\b[^.\n]{0,40}\bhint  # display-verb + hint
    |提示.*(?:key|键)|(?:key|键).*提示            # Chinese: "提示 key" / "key 提示"
    |shortcut                              # keyboard shortcut
    |indicator                             # e.g. "×N speed indicator"
    |displays?\b.*\b(?:press|tap|click|open)  # "displays 'press X'"
    |shows?\b.*\b(?:press|tap|click|open)   # "shows 'press X'"
    |提示.*(?:按|press|tap|click)            # Chinese: "提示按X"
    |(?:按|press).*提示                      # Chinese: "按X提示"
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Wiring/trigger assertion: keywords proving the affordance is actually bound
# or exercised.  Found in local_checks commands OR the body.
_WIRING_ASSERTION_RE = re.compile(
    r"""
    addeventlistener|onclick|on[a-z]+=     # DOM event binding
    |keydown|keyup|keypress                # keyboard events
    |bind|wire|register|handler            # generic wiring
    |press_key|click\b|take_snapshot       # chrome-devtools actions
    |playwright|cypress|e2e                # browser e2e frameworks
    |input\.on|addEventListener            # input event binding
    |trigger|dispatch                      # event dispatch
    |模拟.*(?:按|click|press)|测试.*(?:按|click|press)  # Chinese: "模拟按X"
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_ui_promise_wiring(text: str, slug: str) -> list[str]:
    """Flag a UI affordance advertisement with no wiring/trigger assertion."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    # Structural early-exit: no commands means nothing to gate against.
    if not commands:
        return findings
    # The advertisement signal must be present in the body.
    if not _UI_ADVERTISEMENT_RE.search(body):
        return findings
    # Check if any wiring/trigger assertion exists in commands or body.
    all_cmds_text = " ".join(commands)
    combined = all_cmds_text + " " + body
    if _WIRING_ASSERTION_RE.search(combined):
        return findings
    findings.append(
        f"{slug}: sub-plan introduces a UI affordance/prompt that advertises "
        f"a capability (key hint, button label, shortcut, indicator) but "
        f"neither local_checks nor the body contains a wiring/trigger "
        f"assertion (event handler, keybind, click, press_key, e2e). This is "
        f"the 'promise-without-wiring' shape — the user is prompted to act "
        f"but nothing is bound. Add a local_check that verifies the "
        f"affordance is actually wired (press_key, click, take_snapshot, or "
        f"a binding assertion). See decomposition-principles.md §8."
    )
    return findings


# ── Balance-drift regression flag ─────────────────────────────────────────────
#
# A sub-plan that changes a core mechanic or tunable formula (a coefficient,
# multiplier, threshold, rate, weight, pricing/scoring formula, or core
# path/algorithm) but whose local_checks/ACs contain no baseline before/after
# regression comparison (snapshot/golden compare, recorded baseline, or an
# explicit before-vs-after assertion) is the "balance-drift" shape.  The change
# silently shifts behaviour because nobody noticed the before/after delta.
# Warn so the planner adds a baseline regression gate, or splits tuning into
# its own batch.
#
# See: decomposition-principles.md §8.
# See: commands/ilk-plan.md step 7g.

# Change verb: signals that something is being altered.
_CHANGE_VERB_RE = re.compile(
    r"""
    \bchange\b
    |\bmodif(?:y|ies|ied)\b
    |\badjust(?:s|ed)?\b
    |\btune[ds]?\b
    |\brebalanc(?:e|es|ed|ing)\b
    |\btweak(?:s|ed)?\b
    |\brework(?:s|ed)?\b
    |\brevis(?:e|es|ed|ing)\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Mechanic/formula noun: signals a core mechanic or tunable formula.
#
# NOTE (2026-09-08): ``\bpath\b`` was removed. It shipped in the original
# commit alongside twelve game/economy tunables, but "path" is overwhelmingly
# a filesystem/control-flow noun ("scope_paths", "the write path", "rollback
# path"). Measured over 613 sub-plans across every project: 64 balance-drift
# fires, 54 of them (84%) triggered by path/paths alone — all false positives.
# Do not re-add it without a qualifier ("path array", "waypoint", "per-stage
# path"); a lint wrong five times in six is one planners learn to skim.
_MECHANIC_NOUN_RE = re.compile(
    r"""
    \bformula\b
    |\bcoefficients?\b
    |\bmultipliers?\b
    |\bthresholds?\b
    |\brates?\b
    |\bweights?\b
    |\bdamage\b
    |\bpricing\b
    |\bscoring\b
    |\bcore\s+mechanic\b
    |\btuning\b
    |\bbalance\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Baseline-regression assertion: evidence that a before/after comparison exists.
_BASELINE_ASSERTION_RE = re.compile(
    r"""
    \bbaseline\b
    |\bbefore[\s/-]*after\b
    |\bbefore[- ]and[- ]after\b
    |\bregression\b
    |\bgolden\b
    |\bsnapshot\s+compare\b
    |\bcompare[sd]?\b[^\n]{0,60}\bbaseline\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def lint_balance_regression_flag(text: str, slug: str) -> list[str]:
    """Flag a core-mechanic change with no baseline regression assertion."""
    findings: list[str] = []
    body = _strip_frontmatter(text)
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    # Structural early-exit: no commands means nothing to gate against.
    if not commands:
        return findings
    # Both a change verb and a mechanic noun must be present (conservative).
    if not _CHANGE_VERB_RE.search(body):
        return findings
    if not _MECHANIC_NOUN_RE.search(body):
        return findings
    # Check if any baseline/regression assertion exists in commands or body.
    all_cmds_text = " ".join(commands)
    combined = all_cmds_text + " " + body
    if _BASELINE_ASSERTION_RE.search(combined):
        return findings
    findings.append(
        f"{slug}: sub-plan changes a core mechanic or tunable formula "
        f"(coefficient, multiplier, threshold, rate, weight, pricing/scoring) "
        f"but contains no baseline before/after regression assertion "
        f"(baseline, golden, snapshot compare, before-and-after). "
        f"This is the 'balance-drift' shape — the change silently shifts "
        f"behaviour without a before/after delta check. Add a local_check "
        f"that compares against a recorded baseline, or split tuning into "
        f"its own batch. See decomposition-principles.md §8."
    )
    return findings


# ── Vacuous test-selector guard ──────────────────────────────────────────────
#
# A sub-plan's ``local_checks`` may gate on a test command carrying a selector
# (``-k``, ``-m``, or a ``::`` node id).  At plan time the tests may not exist
# yet, so the selector is a *prediction* — and a wrong prediction is silent:
# pytest reports ``no tests collected`` and exits 0, so the gate passes.
#
# This lint flags any test-runner command carrying such a selector so the
# planner gates on the whole test file instead (the safe default).
#
# See decomposition-principles.md §8 local_checks anti-patterns.

# Test-runner binary pattern (same as _WHOLE_SUITE_CMD_RE but anchored for
# prefix-stripping).
_TEST_RUNNER_RE = re.compile(
    r"\b(?:pytest|py\.test|vitest|jest)\b", re.IGNORECASE
)

# Selector patterns — only meaningful inside a test-runner command.
_SELECTOR_RE = re.compile(
    r"""
    (?:^|\s)-k\s+\S+         # pytest -k <pattern>
    |(?:^|\s)-m\s+\S+        # pytest -m <marker>
    |::\w+                   # pytest file.py::test_name
    """,
    re.VERBOSE,
)


def _extract_body(text: str) -> str:
    """Return the sub-plan body (everything after the frontmatter closing ---)."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return text
    return text[m.end():]


def _extract_all_local_checks_commands(text: str) -> list[str]:
    """Every local_checks command: frontmatter AND per-step yaml blocks.

    ``_extract_local_checks_commands`` is deliberately frontmatter-only —
    ``lint_frontmatter_path_created_later`` depends on that narrowness, since a
    per-step check referencing a path its own step creates is correct, not a
    finding. Lints that must see *every* gate use this instead.

    Measured 2026-08-10: real sub-plans in this repo declare 1 frontmatter gate
    and 3-6 per-step gates, so a frontmatter-only lint sees roughly a fifth of
    the gates it claims to cover. Selectors in particular live almost entirely
    in per-step blocks.
    """
    commands = list(_extract_local_checks_commands(text))
    body = _strip_frontmatter(text)
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        commands.extend(_collect_commands_from_yaml_block(block_match.group(1)))
    return commands


def lint_unverifiable_test_selector(text: str, slug: str) -> list[str]:
    """Flag a local_check test command carrying a selector the planner cannot verify."""
    findings: list[str] = []
    commands = _extract_all_local_checks_commands(text)
    # The escape below asks "does the sub-plan's PROSE name this test file?".
    # A per-step ``local_checks`` yaml block is itself part of the body, so the
    # command's own file token is always present in it — leaving the blocks in
    # makes the escape fire on every per-step gate and silences the lint
    # entirely (observed 2026-08-10: the lint warned only on frontmatter gates,
    # which is where selectors almost never appear). Strip the gate blocks so
    # only genuine prose counts as justification.
    body = _STEP_LOCAL_CHECKS_BLOCK_RE.sub(" ", _extract_body(text))
    for cmd in commands:
        # Strip a leading ``python -m`` / ``python3 -m`` prefix so that
        # ``-m pytest`` is not mistaken for pytest's ``-m`` marker selector.
        normalized = re.sub(
            r"^.*?\bpython\d*\s+-m\s+", "", cmd.strip(), count=1
        )
        if not _TEST_RUNNER_RE.search(normalized):
            continue
        # Check for selector patterns in the NORMALIZED command (after
        # stripping the python -m prefix).
        m = _SELECTOR_RE.search(normalized)
        if not m:
            continue
        # Escape: if the sub-plan body names the test file the command
        # targets, the selector is considered justified (the planner knows
        # which test it matches).  Extract file-path tokens from the
        # command and check if any appear in the body.
        tokens = normalized.split()
        file_tokens = [
            t for t in tokens
            if re.search(r"\.(?:py|ts|js|tsx|jsx|mjs)$", t)
            and not t.startswith("-")
        ]
        if file_tokens and any(ft in body for ft in file_tokens):
            continue  # body names the test file — selector justified
        selector = m.group(0).strip()
        findings.append(
            f"{slug}: local_check test command carries selector '{selector}' "
            f"which the planner cannot verify at plan time -- if the selector "
            f"matches zero tests the gate passes silently. Gate on the whole "
            f"test file instead, or justify the selector by naming the test "
            f"file in the sub-plan body (see decomposition-principles.md "
            f"section 8)."
        )
    return findings


# ── Duplicate import-basename gate lint ──────────────────────────────────────
#
# pytest's default ``prepend`` import mode keys modules by basename.  Two
# selected test files sharing a basename in ONE invocation are a collection
# error (``import file mismatch``), so the gate is red for a structural reason
# and not because of the code under test.  Measured 2026-10-11 on gh-resolve:
# a synthesized mention gate joined ``tests/pins/test_43a_…py`` with
# ``tests/test_43a_…py`` and died at collection — 112 collected / 1 error —
# stopping the batch after the declared gate had already passed.
#
# The lint accepts a same-basename pair only when the effective import
# strategy is proven compatible: the command itself sets
# ``--import-mode=importlib``, or the project's pytest config does (this
# toolkit is exactly that case — see pytest.ini), or the command is a vitest
# invocation, which has no such import key.  An explicit
# ``--import-mode=prepend`` on the command overrides a compatible project
# config and is NOT a waiver.


def _pytest_import_mode_of_command(cmd: str) -> str | None:
    """Explicit ``--import-mode=`` value in *cmd*, or None when not set."""
    m = re.search(r"--import-mode(?:=|\s+)(\S+)", cmd)
    return m.group(1) if m else None


def _project_allows_duplicate_basenames(project_root: Path | None) -> bool:
    """True when the project's pytest config sets ``--import-mode=importlib``."""
    root = project_root if project_root is not None else _resolve_project_root()
    try:
        ini = root / "pytest.ini"
        if ini.exists() and "--import-mode=importlib" in ini.read_text(
            encoding="utf-8-sig"
        ):
            return True
        cfg = root / "setup.cfg"
        if cfg.exists() and "--import-mode=importlib" in cfg.read_text(
            encoding="utf-8-sig"
        ):
            return True
        pyproject = root / "pyproject.toml"
        if pyproject.exists() and "--import-mode=importlib" in pyproject.read_text(
            encoding="utf-8-sig"
        ):
            return True
    except Exception:
        return False
    return False


def lint_duplicate_import_basename(
    text: str, slug: str, project_root: Path | None = None
) -> list[str]:
    """Flag a declared pytest invocation passing two same-basename test files.

    Every selected file must still be gated — the fix is to split the pair into
    separate bounded invocations, not to drop one of them.  A command whose file arguments cannot be resolved
    reports nothing: an unaskable oracle must not manufacture findings.
    """
    findings: list[str] = []
    allow_dupes = _project_allows_duplicate_basenames(project_root)

    for cmd in _extract_all_local_checks_commands(text):
        if not cmd:
            continue
        # vitest has no basename-keyed import mode; skip it entirely.
        if re.search(r"(^|[\s/])vitest(\s|$)", cmd):
            continue
        mode = _pytest_import_mode_of_command(cmd)
        if mode is not None:
            if mode == "importlib":
                continue
            compatible = False
        else:
            compatible = allow_dupes
        if compatible:
            continue
        if "pytest" not in cmd:
            continue
        whole, node_only, parseable = _declared_command_coverage(cmd)
        if not parseable:
            continue
        paths = sorted(whole | node_only)
        by_base: dict[str, list[str]] = {}
        for p in paths:
            base = p.replace("\\", "/").rsplit("/", 1)[-1]
            by_base.setdefault(base, []).append(p)
        for base, group in sorted(by_base.items()):
            if len(group) > 1:
                findings.append(
                    f"{slug}: declared pytest gate passes {len(group)} files "
                    f"named {base!r} in one invocation ({', '.join(group)}) -- "
                    f"pytest's default prepend import mode cannot collect two "
                    f"modules sharing a basename (import file mismatch). Split "
                    f"them into separate bounded invocations, or prove a "
                    f"compatible import strategy (--import-mode=importlib). "
                    f"Do not drop a file to silence this."
                )
    return findings


def _normalize_command(cmd: str) -> str:
    """Normalise a command for comparison: strip leading cd && and collapse whitespace."""
    # Strip leading ``cd <path> &&`` prefix.
    cmd = re.sub(r"^cd\s+\S+\s+&&\s*", "", cmd.strip())
    # Collapse whitespace.
    return " ".join(cmd.split())


def _is_narrower(body_cmd: str, gate_cmd: str) -> bool:
    """True if body_cmd is a strict prefix-with-more-args of gate_cmd.

    e.g. body ``pytest tests/test_x.py`` is narrower than gate ``pytest -q``.
    """
    body_parts = body_cmd.split()
    gate_parts = gate_cmd.split()
    if len(body_parts) <= len(gate_parts):
        return False
    # Body has more args — check if gate is a prefix of body.
    return body_parts[:len(gate_parts)] == gate_parts


# A body line that actually INSTRUCTS a run, as opposed to mentioning a command.
# Anchored at the start of the bullet's content (after list markers / emphasis)
# so that "Run `X`" fires while "Full-suite `X` green" and "The suite `X` takes
# ~15 minutes" do not.  Deliberately strict: this lint's design constraint is
# "prefer missing a case to firing on a good plan".
_RUN_VERB_RE = re.compile(
    r"""^\s*
    (?:[-*+]\s*|\d+[.)]\s*|>\s*)*      # list markers / blockquote
    (?:\*{1,2}|_{1,2})?                # optional emphasis
    (?:re-?)?(?:run|execute|invoke)\b  # the imperative
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Inline code spans — how commands are written in a step body.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# Shell-ish fenced blocks: their contents ARE instructions to run.
_SHELL_FENCE_RE = re.compile(
    r"```(?:bash|sh|shell|console|zsh)\s*\n(.*?)```", re.S | re.IGNORECASE
)


def _body_instructed_commands(body_clean: str) -> list[tuple[int, str]]:
    """Commands the body tells the worker to RUN, as (offset, command) pairs.

    Two sources, both instruction-shaped:

    * an inline code span on a line whose content starts with a run verb;
    * every non-comment line inside a shell-ish fenced block.

    Prose that merely names a command ("the suite ``X`` takes 15 minutes") is
    excluded by construction — that is AC-4.  The WHOLE command is captured, so
    a narrower variant keeps its extra arguments and can be recognised as
    narrower — that is AC-3.  Matching only the gate's own substring, as the
    first implementation did, made both checks unreachable.
    """
    found: list[tuple[int, str]] = []
    for m in _SHELL_FENCE_RE.finditer(body_clean):
        base = m.start(1)
        for line in m.group(1).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                found.append((base, stripped))
    for line_match in re.finditer(r"^.*$", body_clean, re.M):
        line = line_match.group(0)
        if not _RUN_VERB_RE.match(line):
            continue
        for code in _INLINE_CODE_RE.finditer(line):
            found.append((line_match.start(), code.group(1)))
    return found


def lint_redundant_gate(text: str, slug: str) -> list[str]:
    """Flag a step body that instructs a command already declared in its local_checks.

    The driver runs local_checks AFTER the commit, so a body that runs the same
    command doubles a long job inside the iteration budget.
    """
    findings: list[str] = []
    commands = _extract_all_local_checks_commands(text)
    body = _strip_frontmatter(text)
    # Strip the local_checks yaml blocks from the body: a gate block is itself
    # body text, so leaving it in makes every gate match itself.
    body_clean = _STEP_LOCAL_CHECKS_BLOCK_RE.sub(" ", body)
    instructed = _body_instructed_commands(body_clean)

    seen: set[str] = set()
    for gate_cmd in commands:
        gate_norm = _normalize_command(gate_cmd)
        if not gate_norm or gate_norm in seen:
            continue
        for offset, body_cmd in instructed:
            body_norm = _normalize_command(body_cmd)
            if not body_norm:
                continue
            # A strictly narrower run is a legitimate fast inner-loop check.
            if _is_narrower(body_norm, gate_norm):
                continue
            if body_norm.lower() != gate_norm.lower():
                continue
            heads = re.findall(r"^###\s+Step\s+(\S+)", body_clean[:offset], re.M)
            label = f"step {heads[-1]}" if heads else "a step"
            seen.add(gate_norm)
            findings.append(
                f"{slug}: {label} instructs '{gate_cmd.strip()}' which is already "
                f"declared in that step's local_checks -- the driver runs "
                f"local_checks after the commit, so remove the manual run from "
                f"the step body."
            )
            break
    return findings


# ── Shared-module gate lint (caller-aware importer oracle) ────────────
#
# A sub-plan whose scope_paths modifies a module that other files import,
# and whose gates only run a single test file, hides integration bugs.
# The oracle uses grep to find production importers (non-test .py files)
# of the changed module.  If the oracle cannot run, it reports nothing
# rather than firing — an unaskable oracle must not manufacture findings.
#
# See decomposition-principles.md §8.
# See commands/ilk-plan.md step 7a (the manual pass that was skipped).


def _resolve_project_root() -> Path:
    """Return the project root for importer-lookup grep.

    Uses ``_PROJECT_ROOT`` if set, otherwise ``Path.cwd()``.
    """
    return _PROJECT_ROOT if _PROJECT_ROOT is not None else Path.cwd()


def _resolve_module_name(scope_path: str) -> str | None:
    """Derive the importable Python module name from a scope_path.

    Returns ``None`` for non-Python files or paths where the module name
    cannot be determined.
    """
    p = scope_path.replace("\\", "/")
    # Walk up to find the .py file — skip directory-only entries.
    parts = p.split("/")
    for i, part in enumerate(parts):
        if part.endswith(".py"):
            module = part[:-3]  # strip .py
            if module == "__init__":
                # Use the parent directory name as the module name.
                if i > 0:
                    return parts[i - 1]
                return None
            return module
    return None


def _is_test_path(rel_path: str) -> bool:
    """True if *rel_path* looks like a test file or lives in a test directory.

    Matches standard pytest discovery conventions:
    - Directories: ``test/``, ``tests/``, ``spec/``, ``specs/``
    - Files: ``test_*.py``, ``*_test.py`` (at any nesting level)

    Does NOT match substrings — ``testing_utils.py`` is NOT excluded, nor is
    ``conftest.py`` or ``my_test_data.py``.
    """
    parts = rel_path.replace("\\", "/").split("/")
    _TEST_DIR_NAMES = {"test", "tests", "spec", "specs"}
    for part in parts[:-1]:  # directories only (exclude the filename)
        if part in _TEST_DIR_NAMES:
            return True
    filename = parts[-1]
    if filename.startswith("test_") and filename.endswith(".py"):
        return True
    if filename.endswith("_test.py"):
        return True
    return False


def _find_testpaths(project_root: Path) -> list[Path]:
    """Return configured test directories, falling back to ``tests/``.

    Reads ``testpaths`` from pytest.ini / setup.cfg / pyproject.toml.
    """
    for ini_name, section_start, key_re in [
        ("pytest.ini", "[pytest]", r"^testpaths\s*=\s*(.+)"),
        ("setup.cfg", "[tool:pytest]", r"^testpaths\s*=\s*(.+)"),
    ]:
        ini = project_root / ini_name
        if not ini.exists():
            continue
        in_section = False
        for line in ini.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("["):
                in_section = line.strip().startswith(section_start)
            elif in_section:
                m = re.match(key_re, line.strip())
                if m:
                    return [project_root / d.strip()
                            for d in m.group(1).split()]
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8-sig")
        in_section = False
        for line in text.splitlines():
            if line.strip() == "[tool.pytest.ini_options]":
                in_section = True
            elif line.strip().startswith("["):
                in_section = False
            elif in_section:
                m = re.match(r"^testpaths\s*=\s*\[(.+)\]", line.strip())
                if m:
                    return [project_root / d.strip().strip('"').strip("'")
                            for d in m.group(1).split(",")]
    default = project_root / "tests"
    return [default] if default.is_dir() else []


def _discover_tests_by_import(
    module_name: str, project_root: Path,
) -> set[str]:
    """Find test files that import *module_name* via ``ast`` parsing.

    Thin wrapper around ``test_importers.importer_tests`` (this folder's
    copy of V1's helper).  Returns a set of resolved absolute paths.  Only
    files under the project's configured ``testpaths`` (or ``tests/``) are
    scanned.

    Falls back to direct filesystem scan when the project is not a git
    repository (e.g. in test fixtures with ``tmp_path``), matching any
    import segment just as the helper does.
    """
    # Check if this is a git repo — importer_tests requires git ls-files.
    git_dir = project_root / ".git"
    if git_dir.exists():
        # ``test_importers.py`` sits beside this file.  Isolated mode
        # (``python -I``) drops the script's directory from ``sys.path``;
        # put it back so the sibling helper is importable.
        script_dir = str(Path(__file__).resolve().parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        try:
            from test_importers import importer_tests  # noqa: E402
            rel_paths = importer_tests(project_root, [f"{module_name}.py"])
            return {str(project_root / p) for p in rel_paths}
        except ImportError:
            pass  # Fall through to filesystem scan.

    # Fallback: filesystem scan of every test file — no git here, so
    # ``git ls-files`` cannot separate tracked from untracked.  Match the
    # helper's semantics: the module name may be ANY segment of a dotted
    # import (``from pkg.cli import f`` names ``cli``), not just the first.
    test_dirs = _find_testpaths(project_root)
    results: set[str] = set()
    for td in test_dirs:
        if not td.is_dir():
            continue
        for py in td.rglob("test_*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"),
                                 filename=str(py))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if module_name in alias.name.split("."):
                            results.add(str(py))
                            break
                elif isinstance(node, ast.ImportFrom):
                    if node.module and module_name in node.module.split("."):
                        results.add(str(py))
                        break
    return results


def _find_importers(module_name: str, project_root: Path) -> list[str]:
    """Find production Python files that import *module_name*.

    Uses ``grep -rn`` with ``--include="*.py"`` to locate ``from <mod> import``
    or ``import <mod>`` lines.  Returns a list of relative file paths (the
    files that import the module).  Excludes test files (files in standard
    test directories or matching ``test_*.py`` / ``*_test.py``).

    Returns an empty list when grep cannot run, finds nothing, or the
    project root is not searchable — the oracle fails silently rather than
    manufacturing findings.
    """
    pattern = f"from {module_name} import|import {module_name}"
    try:
        result = subprocess.run(
            ["grep", "-Ern", "--include=*.py", "-l", pattern, str(project_root)],
            capture_output=True,
            text=True,
                encoding="utf-8", errors="replace",
            timeout=15,
        )
        if result.returncode != 0:
            # grep exit 1 = no matches; exit 2 = error.  Either way, no importers found.
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # grep missing or timed out — oracle unaskable, report nothing.
        return []

    importers: list[str] = []
    root_str = str(project_root)
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        # grep returns absolute paths; make them relative.
        abs_path = line.strip()
        if abs_path.startswith(root_str):
            rel = abs_path[len(root_str):].lstrip("/")
        else:
            rel = abs_path
        # Exclude test files — integration risk is about production callers.
        if _is_test_path(rel):
            continue
        importers.append(rel)
    return importers


def _extract_test_file_tokens(cmd: str) -> list[str]:
    """Extract test-file path tokens from a command string."""
    tokens = cmd.strip().split()
    return [
        t for t in tokens
        if re.search(r"(?:^|/)test[_a-zA-Z].*\.(?:py|ts|js|tsx|jsx|mjs)$", t)
        and not t.startswith("-")
    ]


def _strip_dot_slash_prefix(path: str) -> str:
    """Strip leading ``./`` prefixes only (not ``../`` or ``.hidden``).

    ``str.lstrip("./")`` strips a *set* of characters (``.`` and ``/``),
    so ``../tests`` becomes ``tests`` and ``.github/tests`` becomes
    ``github/tests``.  This helper strips only the literal prefix ``./``,
    repeated until the string no longer starts with it.
    """
    while path.startswith("./"):
        path = path[2:]
    return path


def _extract_directory_args(cmd: str) -> list[str]:
    """Extract directory-tree arguments from a command string.

    A directory argument (e.g. ``tests``, ``tests/pins/``) covers every test
    file under it.  Normalises ``./`` prefixes and trailing ``/``.
    Skips flags and non-path tokens (program names, runners).

    Directory args starting with ``..`` (outside the project) are
    returned as-is so callers can distinguish them from project-local
    paths.
    """
    tokens = cmd.strip().split()
    dirs: list[str] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t.startswith("-"):
            if "=" not in t and t in _VALUE_TAKING_FLAGS:
                skip_next = True
            continue
        if _is_directory_arg(t):
            norm = _strip_dot_slash_prefix(t)
            if norm.endswith("/"):
                norm = norm[:-1]
            dirs.append(norm)
            continue
        if t in _NON_PATH_TOKENS:
            continue
    return dirs


def _test_file_under_any_dir(
    test_path: str, dirs: list[str], project_root: Path | None = None,
) -> bool:
    """True if *test_path* falls under any of the *dirs*.

    *dirs* are relative to *project_root*.  When *test_path* is absolute
    and *project_root* is given, the comparison strips the root prefix.
    """
    norm_test = test_path
    if project_root is not None:
        root_str = str(project_root)
        if norm_test.startswith(root_str + "/"):
            norm_test = norm_test[len(root_str) + 1:]
    norm_test = _strip_dot_slash_prefix(norm_test)
    for d in dirs:
        if norm_test.startswith(d + "/") or norm_test == d:
            return True
    return False


def _resolve_test_paths(
    test_files: list[str], project_root: Path,
    scope_path: str | None = None,
) -> set[str]:
    """Resolve test-file tokens to real paths on disk.

    Searches in order:
    1. The token as-is under *project_root* (top-level ``tests/`` layout).
    2. A sibling ``tests/`` directory when the scope path lives under
       ``skills/<skill>/scripts/`` (this repo's layout).
    3. A project-wide glob for the filename as a last resort.

    Returns the set of *existing* resolved paths.  When nothing is found
    the set is empty — callers treat that as "tests unresolvable" and skip
    the finding rather than firing on a phantom path.
    """
    resolved: set[str] = set()
    for tf in test_files:
        p = project_root / tf
        if p.exists():
            resolved.add(str(p))
            continue

        # Extract the bare test filename for searching.
        test_filename = tf.rsplit("/", 1)[-1] if "/" in tf else tf

        # Sibling-dir search: skills/<skill>/scripts/m.py →
        # skills/<skill>/tests/test_m.py
        if scope_path:
            norm_sp = scope_path.replace("\\", "/")
            if "/scripts/" in norm_sp:
                skill_dir = project_root / norm_sp.split("/scripts/")[0]
                candidate = skill_dir / "tests" / test_filename
                if candidate.exists():
                    resolved.add(str(candidate))
                    continue

        # Glob search as a last resort.
        found = list(project_root.rglob(test_filename))
        if found:
            resolved.add(str(found[0]))
            continue

    return resolved


def _gate_covers_module_and_callers(
    cmd: str,
    changed_module_test_files: set[str],
    callers_test_files: set[str],
    project_root: Path,
) -> bool:
    """True if *cmd* runs tests for both the changed module and its callers."""
    test_tokens = _extract_test_file_tokens(cmd)
    gate_paths = _resolve_test_paths(test_tokens, project_root) if test_tokens else set()
    # Directory arguments cover every test file under them.
    dir_args = _extract_directory_args(cmd)
    all_required = changed_module_test_files | callers_test_files
    if dir_args and all_required:
        dir_covers = all(
            _test_file_under_any_dir(t, dir_args, project_root)
            for t in all_required
        )
        if dir_covers:
            return True
    covers_module = bool(gate_paths & changed_module_test_files)
    covers_callers = bool(gate_paths & callers_test_files)
    return covers_module and covers_callers


def lint_shared_module_gate(text: str, slug: str) -> list[str]:
    """Flag a gate that misses the changed module's resolved callers' tests.

    A sub-plan that modifies a module other files import, and whose gates do
    not run the callers' tests, hides integration bugs.  A gate that covers
    both the changed module's tests AND the resolved importers' tests satisfies
    the rule — a whole-suite gate is not required.
    """
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    if not scope_paths:
        return findings

    project_root = _resolve_project_root()

    # Collect non-test .py source files from scope_paths.
    source_files: list[tuple[str, str]] = []  # (scope_path, module_name)
    for sp in scope_paths:
        norm = sp.replace("\\", "/")
        # Skip test files, docs, non-Python.
        basename = norm.rsplit("/", 1)[-1] if "/" in norm else norm
        if not basename.endswith(".py"):
            continue
        if _is_test_path(norm):
            continue
        module = _resolve_module_name(sp)
        if module:
            source_files.append((sp, module))

    if not source_files:
        return findings  # No source files changed — docs-only or test-only.

    # Extract ALL local_checks commands (frontmatter + per-step blocks).
    commands = _extract_all_local_checks_commands(text)
    if not commands:
        return findings  # No gates — nothing to check.

    # Check whether ANY gate runs a directory or whole suite — compliant.
    for cmd in commands:
        if _is_whole_suite_command(cmd):
            return findings

    # Check if ANY later step in the sub-plan runs a broader gate.
    body = _strip_frontmatter(text)
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        block_cmds = re.findall(r"command:\s*(.+)", block_match.group(1))
        for cmd in block_cmds:
            if _is_whole_suite_command(cmd):
                return findings  # Later step widens — compliant.

    # D9 item 1: the LAST step's gate must cover the changed module's
    # importers' tests.  A last-step gate that misses a required test is
    # a HARD finding — earlier steps' gates are not judged.
    steps = _extract_step_sections(body)
    last_step_gate_paths: set[str] | None = None
    last_heading: str | None = None
    if steps:
        last_no = max(s[0] for s in steps)
        last_heading = next(s[1] for s in steps if s[0] == last_no)
        last_fence = _step_first_yaml_fence(body, last_no)
        if last_fence is not None:
            last_cmds = re.findall(r"command:\s*(.+)", last_fence)
            if last_cmds:
                # A whole-suite gate on the last step exempts the sub-plan.
                for cmd in last_cmds:
                    if _is_whole_suite_command(cmd):
                        return findings
                last_step_gate_paths = set()
                for cmd in last_cmds:
                    last_step_gate_paths |= _resolve_test_paths(
                        _extract_test_file_tokens(cmd), project_root,
                    )

    # All gates are file-scoped.  Check whether any gate covers both the
    # changed module AND its resolved importers' tests.
    for sp, module in source_files:
        importers = _find_importers(module, project_root)
        if not importers:
            continue  # No production importers — leaf module, no finding.

        # Resolve test-file paths for the changed module and its importers.
        module_test = _resolve_test_paths(
            [f"tests/test_{module}.py"], project_root,
            scope_path=sp,
        )
        callers_test: set[str] = set()
        for imp in importers:
            imp_module = _resolve_module_name(imp)
            if imp_module:
                # Filename-based resolution (legacy).
                callers_test |= _resolve_test_paths(
                    [f"tests/test_{imp_module}.py"], project_root,
                    scope_path=imp,
                )
                # Import-graph discovery: find test files that import the
                # importer module (one level of production importers + test
                # files that import them).
                callers_test |= _discover_tests_by_import(
                    imp_module, project_root,
                )

        # NOTE (2026-08-26): this used to `continue` whenever module_test OR
        # callers_test was empty.  That silenced the lint's two core cases —
        # a shared module whose own tests do not exist, and a caller with no
        # test file — which are exactly the situations the finding is about.
        # It broke the parent batch's AC-1 and AC-2 and left 2 tests red for
        # a day.
        #
        # The false positive SP5 was fixing (this repo keeps tests under
        # skills/<skill>/tests/, so `tests/test_<m>.py` never resolved and the
        # lint fired even when the gate DID cover them) is already fixed by
        # the sibling-directory resolution in _resolve_test_paths.  The extra
        # guard was belt-and-braces that cost more than it saved.
        #
        # A caller with no tests is not a reason for silence: the finding's
        # own remedy — "add a gate that runs the callers' tests (or the full
        # suite)" — still applies, and the whole-suite option covers it.
        #
        # AND, not OR.  That one word reconciles two batches whose contracts
        # looked contradictory but were each right about their own case:
        #
        #   neither side resolves  -> nothing to reason from   -> silent (SP5 AC-5)
        #   module tests resolve   -> enough to judge coverage -> fire (parent AC-1)
        #   caller tests resolve   -> enough to judge coverage -> fire (parent AC-2)
        #
        # SP5's OR silenced the two cases the lint exists for; requiring BOTH
        # sides to be unresolvable keeps its false-positive guard and returns
        # the finding.
        if not module_test and not callers_test:
            continue

        # D9 item 1: the last step's gate must cover the required tests.
        # When a last step with at least one gate command exists, judge it
        # on its own.  A HARD finding fires when it misses a required test.
        if last_step_gate_paths is not None:
            required = set()
            if callers_test:
                required |= callers_test
            if module_test:
                required |= module_test
            missing_last = sorted(required - last_step_gate_paths)
            if missing_last:
                root_str = str(project_root)
                rel_missing = [
                    t[len(root_str):].lstrip("/") if t.startswith(root_str) else t
                    for t in missing_last
                ]
                findings.append(
                    f"HARD {slug}: {last_heading} changes nothing that "
                    f"its gate does not test — scope_path '{sp}' changes "
                    f"module '{module}', imported by: "
                    f"{', '.join(sorted(set(importers)))}.  "
                    f"The last step's gate misses: "
                    f"{', '.join(rel_missing)}.  Add them to that step's "
                    f"local_checks (D9: the last gate covers the changed "
                    f"module's importers' tests)."
                )
            continue  # Last step's gate is the primary check.

        # Fallback: no per-step last gate.  Check the UNION of all gates,
        # not each gate individually.
        # Compliant when every resolved caller test is in the union, and,
        # if test_<module>.py exists, it is in the union too.
        all_gate_paths: set[str] = set()
        for cmd in commands:
            all_gate_paths |= _resolve_test_paths(
                _extract_test_file_tokens(cmd), project_root,
            )

        # Collect what's actually missing from the union.
        missing_callers = sorted(callers_test - all_gate_paths) if callers_test else []
        missing_module = sorted(module_test - all_gate_paths) if module_test else []
        all_missing = missing_callers + missing_module

        if not all_missing:
            continue  # Union covers everything — compliant.

        # Emit only when something is actually missing, and name it.
        importer_names = ", ".join(sorted(set(importers)))
        root_str = str(project_root)
        rel_missing = [
            t[len(root_str):].lstrip("/") if t.startswith(root_str) else t
            for t in all_missing
        ]
        missing_str = f"  The gate misses: {', '.join(rel_missing)}."
        findings.append(
            f"{slug}: scope_path '{sp}' changes module '{module}' which is "
            f"imported by: {importer_names}.  The union of all gates does "
            f"not cover every required test.{missing_str}  Add a gate that "
            f"runs the missing tests (or the full suite)."
        )

    return findings


# ── Git-aware scope-path lint ─────────────────────────────────────────

def _resolve_git_cwd() -> Path:
    """Return the working directory for git operations.

    Uses ``_GIT_CWD`` if set (via ``--git-cwd``), otherwise ``Path.cwd()``.
    """
    return _GIT_CWD if _GIT_CWD is not None else Path.cwd()


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command in *cwd*. Returns (rc, stdout, stderr).

    Never swallows stderr — the caller decides what to surface.
    """
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
                encoding="utf-8", errors="replace",
            timeout=15,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 127, "", "git: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git: timed out"


_GLOB_CHARS = re.compile(r"[*?[]")


def _is_validatable_scope_path(p: str) -> bool:
    """True if *p* is a literal relative file path the lint can check.

    Skips: glob patterns (``*``, ``?``, ``[...]``), absolute paths,
    API-style paths (``/api/...``), and empty strings.
    """
    if not p or not p.strip():
        return False
    if p.startswith("/"):
        return False  # absolute or API path
    if _GLOB_CHARS.search(p):
        return False  # glob pattern — file class, not a specific file
    return True


def _resolve_base_ref(base: str, cwd: Path) -> tuple[str | None, bool]:
    """Resolve *base* to a git ref, preferring ``origin/<base>``.

    Returns ``(resolved_ref, is_remote)`` where *resolved_ref* is the
    ref to validate against (or ``None`` if unresolvable) and *is_remote*
    is True when the resolved ref is ``origin/<base>``.
    """
    # Try origin/<base> first.
    rc, _, _ = _run_git(["rev-parse", "--verify", f"origin/{base}"], cwd)
    if rc == 0:
        return f"origin/{base}", True
    # Fall back to local <base>.
    rc, _, _ = _run_git(["rev-parse", "--verify", base], cwd)
    if rc == 0:
        return base, False
    return None, False


def lint_gate_misses_a_test_that_pins_its_file(text: str, slug: str) -> list[str]:
    """Warn when a scoped data file is pinned by a test no gate selects.

    A sub-plan that modifies a data file (non-Python, non-docs, concrete path)
    and whose gates do not run the tests that reference that file hides
    integration bugs.  The shared-module lint covers Python modules; this lint
    covers non-Python data files.

    The finding names the data file, up to 5 pinning test paths, and
    suggests adding them to the last step's gate.
    """
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    if not scope_paths:
        return findings

    project_root = _resolve_project_root()
    if not project_root or not project_root.is_dir():
        return findings  # No project root — nothing to check.

    # Collect data-file candidates from scope_paths.
    # Only data-file extensions are checked — code (.py), shell (.sh),
    # and markdown (.md) are referenced by tests far too widely to gate on.
    # Judgment call: wrong if a real red escapes through a pinned .sh; the
    # batch verification's full scope is the backstop there.
    _DATA_FILE_EXTENSIONS = frozenset({
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".csv", ".tmpl", ".template", ".txt", ".env.example",
    })
    data_files: list[str] = []
    for sp in scope_paths:
        norm = sp.replace("\\", "/")
        # Skip globs.
        if "*" in norm or "?" in norm:
            continue
        # Skip test files.
        if _is_test_path(norm):
            continue
        # Must be a concrete file (not a directory).
        if norm.endswith("/"):
            continue
        basename = norm.rsplit("/", 1)[-1] if "/" in norm else norm
        # Allow: explicit data-file extension, or basename containing .template.
        ext = ""
        if "." in basename:
            ext = "." + basename.rsplit(".", 1)[-1]
        if ext not in _DATA_FILE_EXTENSIONS and ".template." not in basename:
            continue
        data_files.append(sp)

    if not data_files:
        return findings  # No data files to check.

    # A batch_verification: true sub-plan runs the full suite — compliant.
    if _has_batch_verification_marker(text):
        return findings

    # Extract ALL local_checks commands.
    commands = _extract_all_local_checks_commands(text)
    if not commands:
        return findings  # No gates — nothing to check.

    # Check whether ANY gate runs a whole suite — compliant.
    for cmd in commands:
        if _is_whole_suite_command(cmd):
            return findings

    # Check if ANY later step in the sub-plan runs a broader gate.
    body = _strip_frontmatter(text)
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        block_cmds = re.findall(r"command:\s*(.+)", block_match.group(1))
        for cmd in block_cmds:
            if _is_whole_suite_command(cmd):
                return findings  # Later step widens — compliant.

    # For each data file, find test files that pin it.
    for data_file in data_files:
        basename = data_file.rsplit("/", 1)[-1] if "/" in data_file else data_file

        # Find test files that contain the basename.
        pinning_tests: list[str] = []
        test_count = 0
        for test_path in project_root.rglob("test_*.py"):
            test_count += 1
            if test_count > 2000:
                break  # Cap the scan.
            try:
                content = test_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if basename in content:
                rel = str(test_path.relative_to(project_root))
                pinning_tests.append(rel)

        # Also check for .test.ts, .spec.ts, *_test.py patterns.
        for pattern in ["*_test.py", "*.test.ts", "*.spec.ts"]:
            for test_path in project_root.rglob(pattern):
                test_count += 1
                if test_count > 2000:
                    break
                try:
                    content = test_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if basename in content:
                    rel = str(test_path.relative_to(project_root))
                    if rel not in pinning_tests:
                        pinning_tests.append(rel)

        if not pinning_tests:
            continue  # No tests pin this file — no finding.

        # Check if any gate selects any of these tests.
        # A gate selects a test when its command names the test's path or a
        # parent directory of it, or when it is a whole-suite command.
        selected_tests: set[str] = set()
        for cmd in commands:
            for test_path in pinning_tests:
                # Direct match.
                if test_path in cmd:
                    selected_tests.add(test_path)
                    continue
                # Parent directory match — the directory must appear as an
                # explicit directory target (``tests/``), not merely as a
                # substring of a longer file path (``tests/test_other.py``).
                test_dir = str(Path(test_path).parent)
                if re.search(r"(?<!\S)" + re.escape(test_dir) + r"/(?:\s|$)", cmd):
                    selected_tests.add(test_path)

        unselected = [t for t in pinning_tests if t not in selected_tests]
        if unselected:
            # Cap at 5 test paths in the finding.
            shown = unselected[:5]
            remaining = len(unselected) - len(shown)
            test_list = ", ".join(shown)
            if remaining > 0:
                test_list += f" (and {remaining} more)"
            findings.append(
                f"{slug}: scope_paths contains data file {data_file} "
                f"pinned by tests not selected by any gate: {test_list}. "
                f"Add them to the last step's gate, or the next sub-plan "
                f"whose gate selects one will be blamed for this one's red"
            )

    return findings


def lint_scope_path_off_base_branch(
    text: str, slug: str, base_ref: str = "main",
    *,
    _is_default_base: bool = False,
) -> list[str]:
    """Flag a scope_path that exists only off the base branch.

    Three-way discriminator per path:
      - absent from all history  -> OK (new file the sub-plan will create)
      - present on base          -> OK
      - absent on base, present on another ref  -> HARD finding

    Skips glob patterns (``*``, ``?``, ``[...]``) — those represent file
    classes, not specific files the three-way discriminator can evaluate.

    When git cannot be consulted (not a repo / unresolvable base / git
    absent), reports ``unknown`` — never a pass.

    *base_ref* is explicit (AC-8) with documented default ``"main"``.
    *_is_default_base* is True when no ``base_branch:`` was declared in the
    master — the finding text names this so the reader knows the check
    didn't silently pick a wrong ref.
    """
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    if not scope_paths:
        return findings

    cwd = _resolve_git_cwd()
    if not (cwd / ".git").exists():
        # Not a git repo — no branches to confuse, nothing to validate.
        return findings

    # Resolve base_ref: prefer origin/<base>, fall back to local <base>.
    resolved_ref, is_remote = _resolve_base_ref(base_ref, cwd)
    # Detect if this is the default "main" (no explicit base_branch declared).
    is_default = _is_default_base or base_ref == "main"
    if resolved_ref is None:
        # Unresolvable — report unknown with the ref name.
        ref_label = base_ref if is_default else f"origin/{base_ref}"
        for p in scope_paths:
            if not _is_validatable_scope_path(p):
                continue
            findings.append(
                f"{slug}: scope_path '{p}': unknown — "
                f"base ref '{ref_label}' could not be resolved"
            )
        return findings

    # Build a human-readable label for the resolved ref.
    if is_default:
        ref_label = f"{resolved_ref} (default)"
    elif is_remote:
        ref_label = resolved_ref
    else:
        ref_label = f"{resolved_ref} (no remote-tracking ref)"

    for p in scope_paths:
        if not _is_validatable_scope_path(p):
            continue
        rc_list, out_list, err_list = _run_git(
            ["rev-list", "--all", "--", p], cwd,
        )
        if rc_list != 0:
            findings.append(
                f"{slug}: scope_path '{p}': unknown — "
                f"git rev-list failed (rc={rc_list}): {err_list}"
            )
            continue

        if not out_list:
            # Path nowhere in history — new file, OK.
            continue

        rc_base, _, err_base = _run_git(
            ["cat-file", "-e", f"{resolved_ref}:{p}"], cwd,
        )
        if rc_base == 0:
            # Present on base — OK.
            continue

        # A path absent from the base ref but present on the CHECKED-OUT branch
        # is not off-base: the loop commits to HEAD, and HEAD has it.  This is
        # the normal state during any batch whose commits are not yet pushed.
        # Resolving against a single ref is wrong in both directions -- a stale
        # local base false-fired before (2026-08-29, 2511 commits behind), and
        # preferring origin/<base> false-fired after (2026-08-29, 30 unpushed
        # commits).  Off-base means absent from the base AND from HEAD.
        rc_head, _, _ = _run_git(["cat-file", "-e", f"HEAD:{p}"], cwd)
        if rc_head == 0:
            continue

        # rc=128 "path 'X' does not exist in 'Y'" = genuine absence → HARD.
        # rc=128 "exists on disk, but not in 'Y'" (macOS git) = same.
        # rc=128 "invalid object name" / "Not a valid object" = bad ref → unknown.
        is_absent = rc_base == 1 or (
            rc_base == 128
            and ("does not exist in" in err_base or "but not in" in err_base)
        )
        if is_absent:
            # Absent on base and HEAD — check whether any ref's tip tree
            # actually contains the path.  History is the wrong question:
            # `git branch --contains <commit>` names refs that contain the
            # commit, including deletions, so a file deleted on main would
            # list main as "carrying" it.  Instead, test tree presence with
            # `git cat-file -e <ref>:<path>`.
            rc_each, out_each, err_each = _run_git(
                ["for-each-ref", "--format=%(refname:short)",
                 "refs/heads", "refs/remotes"],
                cwd,
            )
            ref_names: list[str] = []
            if rc_each == 0 and out_each:
                for ref in out_each.splitlines():
                    ref = ref.strip()
                    if not ref:
                        continue
                    # Skip symbolic remote HEADs (e.g. origin/HEAD -> origin/main).
                    if ref.endswith("/HEAD"):
                        continue
                    # Already checked base and HEAD above.
                    if ref in (resolved_ref, "HEAD"):
                        continue
                    rc_check, _, _ = _run_git(
                        ["cat-file", "-e", f"{ref}:{p}"], cwd,
                    )
                    if rc_check == 0:
                        ref_names.append(ref)
            if not ref_names:
                # Path absent from all tip trees — same as a new file, OK.
                continue
            findings.append(
                f"HARD {slug}: scope_path '{p}' is absent on "
                f"base ref '{ref_label}' AND on HEAD, but exists on: "
                f"{', '.join(ref_names)}. "
                f"This would route commits to the wrong branch."
            )
            continue

        # Unexpected failure — git could not evaluate.
        findings.append(
            f"{slug}: scope_path '{p}': unknown — "
            f"git cat-file failed (rc={rc_base}): {err_base}"
        )

    return findings


# ── Gate-extractability check ─────────────────────────────────────────────────
#
# plan_check validates gate TEXT but never asks whether the runtime parser can
# actually EXTRACT the declared commands.  This lint reuses the inlined
# ``parse_local_checks_block`` as the oracle: if a sub-plan
# declares a ``local_checks`` block (frontmatter or per-step yaml fence) and
# the runtime parser extracts 0 commands from it, that is a finding — the gate
# will not run.
#
# Zero false-positive surface by construction: the oracle is the consumer itself.
# See decomposition-principles.md §gate-extractability.


def lint_gate_extractable(text: str, slug: str) -> list[str]:
    """Flag a local_checks block the runtime parser cannot extract commands from.

    A sub-plan that declares ``local_checks:`` in frontmatter or a per-step
    yaml fence, but from which the runtime parser extracts 0 commands, has a
    gate that will not run.  Also flags a count mismatch: body declares N
    ``command:`` lines in gate blocks but the runtime extracts M < N.

    AC-2: per-step yaml block with 0 extractable commands → finding.
    AC-3: frontmatter local_checks with 0 extractable commands → finding.
    AC-5: count mismatch (declared > extracted) → finding.
    AC-6: does not import yaml.
    """
    findings: list[str] = []

    # ── Frontmatter block ─────────────────────────────────────────────────
    # ``local_checks: []`` is a deliberate "no frontmatter gate" marker —
    # only flag when there IS content that the runtime cannot extract.
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if m:
        fm = text[m.start():m.end()]
        fm_lc = re.search(r"^local_checks:", fm, re.MULTILINE)
        if fm_lc:
            fm_after = fm[fm_lc.end():]
            # Skip empty inline list: local_checks: [] or local_checks: [ ]
            if not re.match(r"\s*\[\s*\]\s*$", fm_after.split("\n", 1)[0]):
                fm_parsed = parse_local_checks_block(fm[fm_lc.start():])
                if not fm_parsed:
                    findings.append(
                        f"HARD {slug}: frontmatter local_checks declares a gate "
                        f"but the runtime parser extracts 0 commands from it."
                    )

    # ── Per-step yaml blocks ──────────────────────────────────────────────
    # Only multi-line ``local_checks:`` blocks (with indented list items).
    # Inline ``local_checks: []`` is a deliberate empty marker — skip it.
    body = _strip_frontmatter(text)
    _PERSTEP_MULTI = re.compile(
        r"```(?:yaml|yml)?\s*\n(.*?local_checks:\s*\n.*?)```",
        re.S,
    )

    for block_match in _PERSTEP_MULTI.finditer(body):
        block_text = block_match.group(1)
        parsed = parse_local_checks_block(block_text)
        if not parsed:
            heading = _nearest_step_heading(body, block_match.start())
            findings.append(
                f"HARD {slug}: {heading} declares a local_checks gate "
                f"but the runtime parser extracts 0 commands from it."
            )

    # ── Unified step-gate check (step_gate_fence) ────────────────────────
    # The per-step yaml regex above only catches ```yaml fences.  A step
    # that declares local_checks inside a ```json fence (or any other
    # non-yaml fence) still "declares" a gate the runtime cannot extract.
    # Use the loop's own locator (step_gate_fence) to find these.
    # Also flag duplicate headings (heading_count > 1): the locator uses
    # the LAST match, which may not be the real step.
    step_heading_re = re.compile(r"^###\s+Step\s+(\d+)", re.MULTILINE)
    seen_steps: set[int] = set()
    for shm in step_heading_re.finditer(body):
        sn = int(shm.group(1))
        if sn in seen_steps:
            continue
        seen_steps.add(sn)
        gate = step_gate_fence(body, sn)
        if gate.heading_count > 1:
            findings.append(
                f"HARD {slug}: ### Step {sn} appears {gate.heading_count} times; "
                f"the locator uses the last match, which may not be the real step."
            )
        if gate.declares_local_checks and gate.fence_text is None:
            findings.append(
                f"HARD {slug}: ### Step {sn} declares local_checks in a shape "
                f"the runtime locator cannot extract (e.g. non-yaml fence)."
            )

    # ── Count mismatch (AC-5) ─────────────────────────────────────────────
    # Count command: lines the body DECLARES in gate blocks via regex,
    # then compare against what the runtime parser actually extracts.
    declared_cmds: list[str] = []
    extracted_cmds: list[str] = []
    if m:
        fm = text[m.start():m.end()]
        fm_lc = re.search(r"^local_checks:", fm, re.MULTILINE)
        if fm_lc:
            fm_block = fm[fm_lc.start():]
            declared_cmds.extend(re.findall(r"command:\s*(.+)", fm_block))
            extracted_cmds.extend(
                c.get("command", "") for c in parse_local_checks_block(fm_block)
            )
    for block_match in re.finditer(
        r"```(?:yaml|yml)?\s*\n(.*?)```", body, re.S,
    ):
        block_text = block_match.group(1)
        if "local_checks:" in block_text:
            declared_cmds.extend(re.findall(r"command:\s*(.+)", block_text))
            extracted_cmds.extend(
                c.get("command", "")
                for c in parse_local_checks_block(block_text)
            )
    if len(extracted_cmds) < len(declared_cmds):
        findings.append(
            f"{slug}: gate count mismatch — body declares "
            f"{len(declared_cmds)} command(s) in local_checks blocks but "
            f"the runtime parser extracts {len(extracted_cmds)}."
        )

    return findings


def _nearest_step_heading(body: str, pos: int) -> str:
    """Find the ``### Step N`` heading nearest before *pos* in *body*."""
    region = body[:pos]
    m = re.findall(r"###\s+Step\s+\d+[^\n]*", region)
    return m[-1].strip() if m else "<unknown step>"


# ── Exit-status-loss lints (§8 enforcement) ──────────────────────────────────
#
# These enforce decomposition-principles.md §8 mechanically. The rules are:
#   (a) A command piped to | tail, | head, or | awk 'NR==1' discards the
#       upstream exit status — the check always "passes".
#   (b) A while loop whose condition pipes grep -q into another command tests
#       the wrong command's exit status (grep -q closes the pipe early).
#
# Both are plan-time lints (not runtime hooks).

# Trailing pipe patterns that discard upstream exit status.
_EXIT_STATUS_DISCARD_RE = re.compile(
    r"""
    \|\s*(?:tail|head)\s*(?:-\d+)?\s*$        # | tail, | head, | tail -10
    |\|\s*awk\s+['"]NR\s*==\s*1['"]\s*$        # | awk 'NR==1'
    """,
    re.VERBOSE | re.MULTILINE,
)

# Commands whose exit status is the assertion itself — not a discard.
_ASSERTION_CMD_RE = re.compile(
    r"""
    grep\s+-[a-zA-Z]*q    # grep -q (or -qX combos)
    |bash\s+-o\s+pipefail  # bash -o pipefail wrapper
    """,
    re.VERBOSE,
)

# Broken process-wait: while ... | grep -q ... | grep -v grep
_BROKEN_WAIT_RE = re.compile(
    r"""
    while\s+               # while keyword
    .*?                    # some command
    \|\s*grep\s+-q\s+      # piped to grep -q (closes pipe early)
    .*?                    # pattern arg
    \|\s*grep\s+-v\s+grep  # piped to grep -v grep (tests wrong status)
    """,
    re.VERBOSE | re.DOTALL,
)


# ── Red-first step 0 under a frontmatter gate (§8 enforcement) ──────────────
#
# Frontmatter (subplan-scope) ``local_checks`` run at **EVERY** step, step 0
# included. §8 separately recommends the red-first step-0 pattern: a step whose
# whole purpose is to *record* failing tests. Pair the two and the frontmatter
# gate is red on iteration 1 by construction — before a single line of the fix
# exists.
#
# Why this is worth a HARD finding rather than a note: on 2026-08-20 six
# sub-plans were authored with exactly this pairing, all six passed plan_lint
# clean, and the resulting red gate drove the bash runner's ship-integrity pass
# to rewrite `status: shipped` -> `in-progress` on **69 of 150** sub-plan files
# from every prior batch (the runner lacked the cross-run scoping guard its
# PowerShell twin has had all along).
#
# The pairing ALONE is not the defect, and flagging it alone would false-
# positive on legitimate plans: a frontmatter gate deliberately scoped to
# already-green files coexists fine with a red-first step 0 (real sub-plans in
# this repo do precisely that, with a comment saying so). The finding needs the
# gate to actually COVER what step 0 makes red. Two discriminators, both tight:
#
#   (a) the frontmatter command is a whole-suite / directory-scoped run, so it
#       necessarily collects step 0's new failing tests; or
#   (b) the frontmatter command names a test path that step 0's own section
#       also names — the same file, gated at every step.

# Markers that a step's purpose is to LAND FAILING TESTS.
_RED_FIRST_RE = re.compile(
    r"red-first"
    r"|red\s+first"
    r"|\bred\s+state\b"
    r"|\d+\s+failed\b"
    r"|records?\s+(?:the\s+)?failing"
    r"|(?:must|should|expected\s+to)\s+fail"
    r"|failing\s+tests?\s+(?:that|which)?\s*(?:land|committed|recorded)"
    r"|assert(?:s|ing)?\s+the\s+red",
    re.IGNORECASE,
)


def _extract_step_section(body: str, step_no: int) -> str:
    """Return the text of ``### Step <step_no>`` up to the next step heading."""
    pat = re.compile(
        rf"^###\s+Step\s+{step_no}\b.*?(?=^###\s+Step\s+\d+\b|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pat.search(body)
    return m.group(0) if m else ""


def _test_paths_in(cmd: str) -> set[str]:
    """Test-ish paths mentioned in *cmd*, normalised to forward slashes."""
    out: set[str] = set()
    for tok in cmd.split():
        if tok.startswith("-"):
            continue
        norm = tok.strip("'\"`,;()").replace("\\", "/")
        if not norm or "/" not in norm and not norm.startswith("test"):
            continue
        if _is_test_path(norm) or re.search(r"test[^/]*\.(?:py|ts|tsx|js|mjs|sh)$", norm):
            out.add(norm.rstrip("/"))
    return out


def lint_redfirst_step0_under_frontmatter_gate(text: str, slug: str) -> list[str]:
    """Flag a red-first step 0 whose red is inside the frontmatter gate's scope."""
    findings: list[str] = []

    fm_commands = _extract_local_checks_commands(text)
    if not fm_commands:
        return findings

    body = _strip_frontmatter(text)
    step0 = _extract_step_section(body, 0)
    if not step0 or not _RED_FIRST_RE.search(step0):
        return findings

    step0_paths = set()
    for m in re.finditer(r"[\w./\\-]+", step0):
        tok = m.group(0)
        if _is_test_path(tok.replace("\\", "/")):
            step0_paths.add(tok.replace("\\", "/").rstrip("/"))

    for cmd in fm_commands:
        # (a) whole-suite / directory run — collects step 0's new red tests.
        if _is_whole_suite_command(cmd):
            findings.append(
                f"{slug}: step 0 is red-first (it lands FAILING tests) while the "
                f"frontmatter local_check `{cmd.strip()}` runs the whole suite or a "
                f"directory tree. Frontmatter checks run at EVERY step, so this "
                f"gate is red on iteration 1 by construction, before any fix "
                f"exists. HARD FINDING: move this gate into the per-step "
                f"local_checks of the step that makes it green, or scope it to "
                f"files step 0 does not touch."
            )
            continue
        # (b) the gate names a test path step 0 also names.
        overlap = _test_paths_in(cmd) & step0_paths
        if overlap:
            named = ", ".join(sorted(overlap))
            findings.append(
                f"{slug}: step 0 is red-first and the frontmatter local_check "
                f"`{cmd.strip()}` runs the very test path(s) it makes red "
                f"({named}). Frontmatter checks run at EVERY step, so this gate "
                f"is red on iteration 1 by construction. HARD FINDING: move it "
                f"to the per-step local_checks of the step that turns it green."
            )

    return findings


def lint_exit_status_discarded(text: str, slug: str) -> list[str]:
    """Flag commands whose exit status is discarded by a trailing pipe.

    Enforces decomposition-principles.md §8: | tail, | head, | awk 'NR==1'
    after a check command discards the upstream exit status.

    Does NOT fire when:
    - The command uses bash -o pipefail (preserves upstream status).
    - The pipeline ends in grep -q (the final command IS the assertion).
    - There is no pipe at all.
    """
    findings: list[str] = []

    # Extract all local_checks command strings from the text.
    for cmd in _extract_gate_commands(text):
        if not _EXIT_STATUS_DISCARD_RE.search(cmd):
            continue
        # Check for exemptions.
        if _ASSERTION_CMD_RE.search(cmd):
            continue
        findings.append(
            f"HARD {slug}: gate command discards exit status: {cmd!r}. "
            f"Piping to | tail, | head, or | awk 'NR==1' means the check "
            f"always 'passes' regardless of the upstream result. "
            f"Either run the command directly, use bash -o pipefail to "
            f"preserve the upstream status, or end the pipeline with an "
            f"assertion command (e.g. | grep -q 'passed'). "
            f"See decomposition-principles.md §8."
        )
    return findings


def lint_broken_process_wait(text: str, slug: str) -> list[str]:
    """Flag broken process-wait idioms where the loop tests the wrong status.

    Enforces decomposition-principles.md §8: a while loop whose condition
    pipes grep -q into another command (e.g. grep -v grep) tests the
    SECOND command's exit status, not the first's. The loop exits
    immediately.
    """
    findings: list[str] = []

    for cmd in _extract_gate_commands(text):
        if _BROKEN_WAIT_RE.search(cmd):
            findings.append(
                f"HARD {slug}: broken process-wait idiom: {cmd!r}. "
                f"'grep -q' closes the pipe on first match, so the loop "
                f"condition tests the SECOND grep's status, not the first's. "
                f"This loop exits immediately. Use 'pgrep' or "
                f"'kill -0 $pid' to test process liveness. "
                f"See decomposition-principles.md §8."
            )
    return findings


def _extract_gate_commands(text: str) -> list[str]:
    """Extract all command strings from local_checks blocks in *text*."""
    commands: list[str] = []

    # Frontmatter local_checks.
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if m:
        fm = text[m.start():m.end()]
        fm_lc = re.search(r"^local_checks:", fm, re.MULTILINE)
        if fm_lc:
            fm_block = fm[fm_lc.start():]
            for entry in parse_local_checks_block(fm_block):
                cmd = entry.get("command", "")
                if cmd:
                    commands.append(cmd)

    # Per-step local_checks in yaml blocks.
    for block_match in re.finditer(
        r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.S,
    ):
        block_text = block_match.group(1)
        if "local_checks:" in block_text:
            for entry in parse_local_checks_block(block_text):
                cmd = entry.get("command", "")
                if cmd:
                    commands.append(cmd)

    return commands


# ── Gate PATH portability check ───────────────────────────────────────────

# Shell builtins and keywords that are never "executables" to resolve.
_SHELL_BUILTINS_AND_KEYWORDS = frozenset({
    "cd", "echo", "export", "set", "unset", "shift", "source", ".",
    "eval", "exec", "exit", "return", "trap", "wait", "read", "declare",
    "typeset", "local", "readonly", "true", "false", "test", "[", "[[",
    "for", "while", "until", "if", "then", "else", "elif", "fi", "do",
    "done", "case", "esac", "in", "function", "select", "time", "coproc",
})

# Pattern for VAR=value prefix tokens.
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _parse_path_prelude_dirs(prelude: str) -> list[str]:
    """Extract directory list from a ``export PATH="dir1:dir2:$PATH"`` prelude.

    Returns a list of directories added by the prelude, in order.
    Handles both single and double quotes, and ``$PATH`` at start or end.
    """
    if not prelude:
        return []
    # Match: export PATH="..." or export PATH='...'
    m = re.search(r'export\s+PATH=["\']([^"\']+)["\']', prelude)
    if not m:
        return []
    raw = m.group(1)
    dirs = []
    for part in raw.split(":"):
        part = part.strip()
        if part and part != "$PATH":
            # Expand ~ to the user's home.
            if part.startswith("~"):
                part = str(Path.home()) + part[1:]
            dirs.append(part)
    return dirs


def _get_effective_path_dirs(project: Path) -> tuple[list[str] | None, str]:
    """Compute the effective PATH directories a gate will resolve against.

    Returns (dirs, error_message).  On success, ``dirs`` is a list of
    directories and ``error_message`` is "".  On failure, ``dirs`` is None
    and ``error_message`` names what failed (AC-7).
    """
    # 1. getconf PATH — the POSIX-guaranteed floor.
    try:
        result = subprocess.run(
            ["getconf", "PATH"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode != 0:
            return None, f"getconf PATH exited {result.returncode}"
        posix_path = result.stdout.strip()
        if not posix_path:
            return None, "getconf PATH returned empty"
        dirs = [d for d in posix_path.split(":") if d]
    except FileNotFoundError:
        return None, "getconf not found"
    except subprocess.TimeoutExpired:
        return None, "getconf PATH timed out"

    # 2. Append directories from path_prelude.
    prelude = _read_path_prelude(project)
    prelude_dirs = _parse_path_prelude_dirs(prelude)
    dirs.extend(prelude_dirs)

    return dirs, ""


def _leading_executable(cmd: str) -> str | None:
    """Find the leading executable token in a shell command.

    Skips VAR=value prefixes and shell builtins/keywords (AC-6).
    Returns None if no executable is found (pure builtin/keyword command).
    """
    # Strip surrounding quotes that the YAML parser may leave.
    cmd = cmd.strip()
    # Remove outer quotes if present (from YAML extraction).
    if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in ('"', "'"):
        cmd = cmd[1:-1]
    tokens = cmd.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        # Strip any leading quotes or path components for comparison.
        bare = token.split("/")[-1].strip("'\"")
        # Skip shell separators.
        if token in ("&&", "||", ";", "|", ";;", ";&", ";;&"):
            i += 1
            continue
        if _VAR_ASSIGN_RE.match(token):
            i += 1
            continue
        if bare in _SHELL_BUILTINS_AND_KEYWORDS:
            # Skip the builtin/keyword and its arguments until a separator.
            i += 1
            # Skip arguments to builtins (tokens that don't look like separators).
            while i < len(tokens):
                arg = tokens[i]
                # Shell separators start a new command.
                if arg in ("&&", "||", ";", "|", ";;", ";&", ";;&"):
                    break
                i += 1
            continue
        # This is the leading executable.  Return the FULL token, not `bare`:
        # a command may name an interpreter by absolute or relative path
        # (`/opt/homebrew/bin/npx vitest run`), which needs no PATH lookup at
        # all.  `bare` exists only for the builtin/keyword comparison above.
        return token.strip("'\"")
    return None


def _cmd_for_display(cmd: str) -> str:
    """Strip VAR=value prefixes from command for display in findings."""
    cmd = cmd.strip()
    # Remove outer quotes if present (from YAML extraction).
    if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in ('"', "'"):
        cmd = cmd[1:-1]
    tokens = cmd.split()
    # Skip VAR=value prefixes.
    i = 0
    while i < len(tokens) and _VAR_ASSIGN_RE.match(tokens[i]):
        i += 1
    return " ".join(tokens[i:]) if i > 0 else cmd


def lint_gate_executable_on_driver_path(text: str, slug: str) -> list[str]:
    """Flag a gate command whose leading executable cannot resolve on the driver's PATH.

    AC-4: Finding names the executable AND the search space.
    AC-5: bunx produces a finding with no prelude; silenced with prelude.
    AC-6: Shell builtins and VAR=value prefixes are not reported.
    AC-7: Undeterminable PATH ⇒ ``unknown``, never silent pass.
    AC-8: Registered in ALL_CHECKS.
    """
    findings: list[str] = []
    commands = _extract_all_local_checks_commands(text)

    if not commands:
        return findings

    # Resolve project root for reading path_prelude.
    project = _resolve_project_root()

    # Get the effective PATH once.
    path_dirs, path_error = _get_effective_path_dirs(project)

    for cmd in commands:
        exe = _leading_executable(cmd)
        if exe is None:
            continue  # pure builtin/keyword command (AC-6)

        # If PATH cannot be determined, report unknown (AC-7).
        if path_dirs is None:
            display_cmd = _cmd_for_display(cmd)[:80]
            findings.append(
                f"{slug}: gate command '{display_cmd}' starts with "
                f"'{exe}' but effective PATH is unknown — {path_error}. "
                f"Cannot verify portability."
            )
            continue

        # An executable named by path (absolute or relative) is not a PATH
        # lookup -- it either exists and is executable, or it does not.  Reading
        # it as a PATH search made the lint flag `/opt/homebrew/bin/npx`, i.e.
        # the very remedy it recommends for an unresolvable bare command.
        if "/" in exe:
            cand = Path(exe) if exe.startswith("/") else (project / exe if project else Path(exe))
            if cand.exists() and os.access(cand, os.X_OK):
                continue
            display_cmd = _cmd_for_display(cmd)[:80]
            findings.append(
                f"{slug}: gate command '{display_cmd}' names "
                f"'{exe}' by path, which is not an executable file on this host."
            )
            continue

        # Check if the executable resolves on the effective PATH.
        found = False
        for d in path_dirs:
            candidate = Path(d) / exe
            if candidate.exists() and (candidate.is_file() or candidate.is_symlink()):
                # Check execute permission (POSIX).
                if os.access(candidate, os.X_OK):
                    found = True
                    break

        if not found:
            # AC-4: name both the executable and the search space.
            search_space = ":".join(path_dirs)
            display_cmd = _cmd_for_display(cmd)[:80]
            findings.append(
                f"{slug}: gate command '{display_cmd}' starts with "
                f"'{exe}' which was not found on the driver's PATH. "
                f"Searched: {search_space}"
            )

    return findings


# A template token the planner never filled, e.g. `<record path>`.
#
# Two narrowings, both derived from measurement rather than guessed:
#
# * letters/digits/spaces/hyphens/underscores/apostrophes only, so shell
#   redirection (`cmd < in > out`) and `2>&1` cannot match;
# * the token must contain a SPACE or a HYPHEN.  Every placeholder appearing
#   inside a gate command across the toolkit's templates is multi-word —
#   `<skill-root>`, `<batch-slug>`, `<record path>`,
#   `<command that proves this step's outcome>` (4 of 4).  Every false positive
#   in a sweep of 678 existing sub-plans across 11 projects was a bare single
#   word that genuinely belonged to its command: `<key>PATH</key>` and
#   `<integer>5</integer>` in plist greps, `update <id>` in a commander.js
#   signature (4 of 4).  One character of structure separates the two sets.
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9 _'-]*>")


def lint_gate_placeholder_unresolved(text: str, slug: str) -> list[str]:
    """Flag a gate command still carrying an unsubstituted template token.

    A rendered sub-plan is not a template.  A ``local_checks`` command that
    still reads ``<record path>`` or ``<skill-root>`` cannot run, and the way it
    fails is the expensive part: the shell reports a missing file or an unknown
    redirection, which reads as *the thing the gate checks is absent* rather
    than *the command was never finished*.

    Measured on gh-resolve 2026-09-15/16.  The batch-verification template said
    ``verify_attribution.py <record path>`` and asked the planner to resolve it.
    The planner resolved ``<skill-root>`` and ``<batch-slug>`` in the same file
    and left this one literal, on **both** batches of that day.  Each gate then
    failed as "record not found", ``ship_integrity`` read a red gate, and both
    sub-plans were reverted from ``shipped`` to ``in-progress`` **after their
    suites had run green** — 5262 passed / 0 failed for one, ``suite_failed: 0``
    over 5319 tests for the other.  The diagnosis the message invited was
    "re-run the suite", which could never fix it.

    This is the class, not the instance: v0.9.105 fixed that one token, and any
    future template token is the same defect under a new name.  A planner that
    forgets one is caught here, at plan time, before a run is spent.
    """
    findings: list[str] = []
    for cmd in _extract_all_local_checks_commands(text):
        for token in dict.fromkeys(_UNRESOLVED_PLACEHOLDER_RE.findall(cmd)):
            inner = token[1:-1]
            if " " not in inner and "-" not in inner:
                continue  # a bare word is a literal, not a template token
            if f"</{inner}>" in cmd:
                continue  # an XML element, closed in the same command
            findings.append(
                f"HARD {slug}: gate command carries an unsubstituted template "
                f"placeholder {token!r} and cannot run. Resolve it when the "
                f"plan is rendered \u2014 a literal placeholder fails as "
                f"\"not found\", which reads as a missing artifact rather than "
                f"an unfinished command."
            )
    return findings


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block."""
    return re.sub(r"^---.*?---", "", text, count=1, flags=re.S)


def lint_tier_forbids_its_evidence(text: str, slug: str) -> list[str]:
    """Flag a sub-plan whose tier claims loop-verified while forbidding test evidence.

    ``loop-verified`` is defined as *runtime gate proves correctness (pytest
    boots the app, real HTTP/CLI/browser smoke runs)*.  A sub-plan that
    declares ``must_add_tests: false`` with an empty ``unit_test_targets``
    has forbidden the very evidence the tier claims.  The tier is a claim
    about evidence; the other two fields forbid the evidence.

    Only fires when ALL three are present and contradictory:
    - ``verification_tier: loop-verified``
    - ``must_add_tests: false``
    - ``unit_test_targets: []`` (empty or absent)

    ``compile-only`` and ``device-manual`` with ``must_add_tests: false``
    are legitimate (those tiers make no claim about a runtime gate).
    A non-empty ``unit_test_targets`` names existing tests that must pass,
    which is evidence — not flagged.

    See decomposition-principles.md §12.
    Measured: kira-cloudflare issue-5445-work, 2026-09-15.
    """
    # A batch-verification sub-plan is exempt, and this is correctness, not a
    # weakening. Its evidence is the SUITE it runs, declared in its step-0 gate
    # — not a test it adds. `must_add_tests: false` and an empty
    # `unit_test_targets` are exactly how that type is supposed to be declared,
    # so the contradiction the rule detects does not exist for it.
    #
    # MEASURED 2026-09-16 over 697 sub-plans in 11 projects: 59 flagged, and
    # **49 of them (83%) were batch_verification: true**. Without this, the rule
    # fires on every verification sub-plan in every project — a false-positive
    # class large enough to train readers to ignore it. With it: 10 genuine
    # hits of 697 (1.4%), which are ordinary work sub-plans that really do claim
    # loop-verified while forbidding their own evidence.
    if re.search(r"^batch_verification:\s*true\s*$", text, re.MULTILINE | re.IGNORECASE):
        return []
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return []
    fm = m.group(1)

    # Extract verification_tier.
    tier_match = re.search(r"^verification_tier:\s*(\S+)", fm, re.MULTILINE)
    if not tier_match:
        return []
    tier = tier_match.group(1).strip().strip("'\"")
    if tier != "loop-verified":
        return []

    # Extract must_add_tests.
    mat_match = re.search(r"^must_add_tests:\s*(\S+)", fm, re.MULTILINE)
    if not mat_match:
        return []
    mat = mat_match.group(1).strip().strip("'\"").lower()
    if mat not in ("false", "no", "0"):
        return []

    # Extract unit_test_targets — check for empty list or absent.
    # An empty YAML list is ``unit_test_targets: []`` or a bare key followed
    # by no items.  A non-empty list has ``- "..."`` entries indented below.
    utt_match = re.search(r"^unit_test_targets:\s*(\[.*?\])?\s*$", fm, re.MULTILINE)
    if utt_match:
        inline = utt_match.group(1)
        if inline is not None:
            # Inline form: ``unit_test_targets: []`` or ``["a", "b"]``.
            if inline.strip() not in ("[]", ""):
                return []  # non-empty inline list = evidence
            # empty inline list → continue to flag
        else:
            # Block form: check for indented list items below.
            after_pos = utt_match.end()
            remaining = fm[after_pos:]
            # A list item starts with ``  - `` (indented hyphen).
            if re.match(r"\s+-\s+\S", remaining):
                return []  # non-empty block list = evidence
            # No items below → empty list → continue to flag
    else:
        # absent → equivalent to empty → continue to flag
        pass

    return [
        f"HARD {slug}: verification_tier is 'loop-verified' but "
        f"must_add_tests is 'false' and unit_test_targets is empty. "
        f"The tier claims 'runtime gate proves correctness' while both "
        f"fields forbid the evidence that claim requires. Either "
        f"**lower the tier** to 'compile-only' (honest about what the "
        f"gate proves), or **arm the evidence** (set must_add_tests: true "
        f"or name a unit_test_targets entry)."
    ]


def lint_duplicate_frontmatter_key(text: str, slug: str) -> list[str]:
    """HARD when a top-level key appears twice in the frontmatter block.

    ``grep -m1 '^status:'`` returns the first occurrence; ``parse_frontmatter``
    returns the last.  A file with two ``status:`` keys reads differently to
    the two instruments, and a session that reads it twice with different tools
    reports a phantom rewrite.  Measured on gh-resolve 2026-09-16: 1 of 27
    ``2026-09-15*`` files had this defect.
    """
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return []
    fm_block = m.group(1)
    # Scan top-level keys only: lines with no leading whitespace that match
    # ``key: value``.  Skip blank lines and ``#`` comments (matching
    # ``parse_frontmatter``'s own skip logic).
    seen: dict[str, tuple[int, str]] = {}  # key -> (line_no, value)
    findings: list[str] = []
    for i, line in enumerate(fm_block.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        if line[0].isspace():
            continue  # indented = list item or nested mapping, not top-level
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key in seen:
            first_line, first_value = seen[key]
            findings.append(
                f"HARD {slug}: duplicate frontmatter key {key!r} — "
                f"first at line {first_line} ({key}: {first_value!r}), "
                f"second at line {i} ({key}: {value!r}). "
                f"The runtime (parse_frontmatter) uses the LAST value "
                f"({value!r}); grep -m1 uses the FIRST ({first_value!r})."
            )
        else:
            seen[key] = (i, value)
    return findings


# ── Slug-identity mismatch guard ──────────────────────────────────────────────
#
# A sub-plan has two derivable identities:
#   1. ``_slug_from_filename(fname)`` — strips date prefix + .md extension
#   2. ``frontmatter plan:`` — the explicit field
#
# When they disagree, readers that pick different identities silently diverge.
# This parked the pv-5611 batch for ~19 hours (measured 2026-09-22): the
# filename was ``2026-09-21-pv5-verify.md`` (slug ``pv5-verify``) but the
# frontmatter said ``plan: pv5-round5-verify``.  The ledger row was keyed
# ``pv5-verify`` (the filename slug), but ship_integrity looked up
# ``pv5-round5-verify`` (the frontmatter slug) and reverted the work.
#
# This lint catches the divergence at plan time so new plans cannot introduce
# the split.  The union in ship_integrity (step 1) rescues existing plans
# already on disk.


def _extract_plan_field(text: str) -> str:
    """Return the frontmatter ``plan:`` value, or empty string if absent."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return ""
    fm = text[m.start():m.end()]
    for line in fm.splitlines():
        s = line.strip()
        if s.startswith("plan:"):
            return s[len("plan:"):].strip().strip("'\"")
    return ""


def lint_slug_identity_mismatch(text: str, slug: str) -> list[str]:
    """HARD finding when frontmatter plan: differs from filename-derived slug.

    *slug* is the filename stem (e.g. ``2026-09-21-pv5-verify``).  The
    filename-derived slug strips the date prefix: ``pv5-verify``.
    """
    findings: list[str] = []
    plan_field = _extract_plan_field(text)
    if not plan_field:
        return findings  # no plan: field — other lints catch that
    filename_slug = _strip_date_prefix(slug)
    if not filename_slug:
        return findings  # no date prefix to strip — not a dated sub-plan
    if filename_slug == plan_field:
        return findings  # identities agree
    findings.append(
        f"HARD {slug}: sub-plan's two slug identities disagree — "
        f"filename-derived '{filename_slug}' vs frontmatter plan: '{plan_field}'. "
        f"Readers that pick different identities silently diverge "
        f"(pv-5611 parked 19h). Either rename the file to match plan:, "
        f"or change plan: to match the filename."
    )
    return findings


# A commit line in a step (the ``- Commit: `...` `` pattern).
_COMMIT_LINE_RE = re.compile(
    r"^\s*-\s*Commit:\s*`([^`]+)`", re.MULTILINE
)


def _extract_step_sections(body: str) -> list[tuple[int, str, str]]:
    """Return (step_no, heading, section_text) for each ``### Step N`` section.

    A step's section ends at the earliest of:
    - the next ``### Step N`` heading;
    - the next ``## `` (level-2) heading that is NOT inside a fenced code block;
    - the end of the body.

    Sub-headings (``### `` other than Step, ``#### ``, etc.) stay inside
    the step.  A ``## `` line inside a ``` fence does NOT end the section.

    See sub-plan a-last-step-ends-at-the-next-section.
    """
    steps: list[tuple[int, str, str]] = []
    for m in re.finditer(r"^###\s+Step\s+(\d+)\b[^\n]*", body, re.MULTILINE):
        step_no = int(m.group(1))
        heading = m.group(0).strip()
        start = m.start()
        # Scan forward from after the heading line to find the boundary.
        end = len(body)
        in_fence = False
        pos = m.end()
        while pos < len(body):
            # Find the next line starting at pos.
            nl = body.find("\n", pos)
            if nl == -1:
                line_start = pos
                line = body[pos:]
                pos = len(body)
            else:
                line_start = pos
                line = body[pos:nl]
                pos = nl + 1
            stripped = line.lstrip()
            # Toggle fence state on lines starting with ``` (after lstrip).
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            # A ### Step N heading ends the section.
            if re.match(r"^###\s+Step\s+\d+\b", stripped):
                end = line_start
                break
            # A ## heading (level-2) outside a fence ends the section.
            if re.match(r"^##\s", stripped):
                end = line_start
                break
        section = body[start:end]
        steps.append((step_no, heading, section))
    return steps


def _step_first_yaml_fence(body: str, step_no: int) -> str | None:
    """Text of *step_no*'s first fenced block, or None if there is none.

    Mirrors the driver's window exactly — ``step_declares_gate_first``
    (run_ilk_loop_claude.sh:1519-1552) reads ONE fence per step: the first
    ``` block after the ``### Step N`` heading, ending at the next ``### ``
    heading.  A marker or gate outside that window is invisible to the
    runtime, so it must not count here either.
    """
    pat = re.compile(
        r"^###\s+Step\s+" + re.escape(str(step_no)) + r"(?![0-9])", re.MULTILINE
    )
    m = pat.search(body)
    if not m:
        return None
    after = body[m.end():]
    next_heading = re.search(r"^###\s+", after, re.MULTILINE)
    region = after[: next_heading.start()] if next_heading else after
    fence = re.search(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```", region, re.MULTILINE | re.DOTALL
    )
    return fence.group(1) if fence else None


def lint_redfirst_step0_per_step_gate_demands_green(text: str, slug: str) -> list[str]:
    """HARD when a red-first step 0's per-step gate demands green on its own tests.

    A red-first step 0 writes a failing test.  If the same step's per-step
    ``local_checks`` runs that test file with plain exit-0 semantics, the step
    is required to fail: the driver exits ``local_checks_failed`` and the
    scheduler blacklists the project.

    Exempt when the step instructs ``xfail(strict=True)`` (the pin will XPASS
    the moment the fix lands, so the gate stays green without demanding exit 0),
    or when the gate asserts a red count (``grep -q 'N failed'``).
    """
    findings: list[str] = []

    body = _strip_frontmatter(text)
    step0 = _extract_step_section(body, 0)
    if not step0:
        return findings

    # Is step 0 red-first?  Check heading + commit lines for markers that
    # _RED_FIRST_RE does not cover ("pin", "reproduce"), plus the body regex.
    # Pin/reproduce words in the heading alone are NOT sufficient — "pinned
    # assertions" is an adjective, not a red-first signal.  They count only
    # when the step body also contains a failing-test signal.
    heading_m = re.search(r"^###\s+Step\s+0\b.*?$", body, re.MULTILINE)
    heading = heading_m.group(0) if heading_m else ""
    commit_lines = _COMMIT_LINE_RE.findall(step0)
    extra_text = heading + " " + " ".join(commit_lines)
    red_first = bool(
        _RED_FIRST_RE.search(step0)
        or re.search(r"red-first|\bred\b", extra_text, re.IGNORECASE)
        or (
            re.search(r"\bpin(?:s|ned|ning)?\b|\breproduc", extra_text, re.IGNORECASE)
            and re.search(
                r"\bfail(?:ing|ed)?\b|\bred\b|\bxfail\b"
                r"|(?:must|should|expected\s+to)\s+fail"
                r"|assert(?:s|ing)?\s+the\s+red"
                r"|records?\s+(?:the\s+)?failing",
                step0, re.IGNORECASE,
            )
        )
    )
    if not red_first:
        return findings

    # Exempt: the step instructs xfail(strict=True)
    if "xfail(strict=True" in step0:
        return findings

    fence = _step_first_yaml_fence(body, 0)
    if not fence:
        return findings

    for entry in parse_local_checks_block(fence):
        cmd = entry.get("command", "") if isinstance(entry, dict) else str(entry)
        if "pytest" not in cmd:
            continue
        test_paths = _test_paths_in(cmd)
        if not test_paths:
            continue
        # Exempt: the gate asserts a failure count (red-count grep).
        # Matches both literal "4 failed" and regex patterns like "[0-9]+ failed".
        if re.search(r"\d+\s+failed|\[\d[\d-]*\]\+?\s+failed", cmd):
            continue
        findings.append(
            f"HARD {slug}: step 0 is red-first but its per-step gate "
            f"`{cmd.strip()}` demands green (exit 0) on the tests it makes "
            f"red. Write the pins as @pytest.mark.xfail(strict=True) or "
            f"assert the red count."
        )

    return findings


#: ``xfail(strict=True ...)`` with its argument list, up to the closing paren.
_STRICT_XFAIL_CALL = re.compile(r"xfail\(\s*strict\s*=\s*True[^)]*\)")


def lint_strict_pin_names_its_failure(text: str, slug: str) -> list[str]:
    """Warn when step 0 pins with a strict xfail that names no expected failure.

    A bare ``xfail(strict=True)`` accepts ANY exception as the expected red, so
    a broken pin (a JSONDecodeError, a TypeError, a fixture error) passes step
    0 as "not built yet".  gh-resolve 09b, 2026-10-09: that pin then had to be
    fixed in step 1, which its pin-diff gate forbids (backlog b212f859).
    Naming the failure with ``raises=`` turns an unrelated exception red while
    fixing the pin is still legal.
    """
    step0 = _extract_step_section(_strip_frontmatter(text), 0)
    if not step0:
        return []
    bare = [m.group(0) for m in _STRICT_XFAIL_CALL.finditer(step0)
            if "raises" not in m.group(0)]
    if not bare:
        return []
    return [
        f"{slug}: step 0 pins with `{bare[0]}`, which accepts any exception as "
        f"the expected failure. Name it: xfail(strict=True, raises=(AssertionError, "
        f"ImportError, AttributeError), ...) or narrower, so a broken pin fails "
        f"step 0 while fixing it is still allowed."
    ]


ALL_CHECKS = (
    lint_envprereq_fallback_contradiction,
    lint_block_when_default_exists,
    lint_brittle_exact_list_assertion,
    lint_escaped_bug_regression_gate,
    lint_frontmatter_path_created_later,
    lint_e2e_check_without_env_prereq,
    lint_posix_only_test_assertion,
    lint_network_tool_mock_only_gate,
    lint_vertical_slice_ac,
    lint_anti_hardcode_integration,
    lint_ui_promise_wiring,
    lint_balance_regression_flag,
    lint_unverifiable_test_selector,
    lint_duplicate_import_basename,
    lint_redundant_gate,
    lint_shared_module_gate,
    lint_gate_extractable,
    lint_gate_executable_on_driver_path,
    lint_gate_placeholder_unresolved,
    lint_redfirst_step0_under_frontmatter_gate,
    lint_redfirst_step0_per_step_gate_demands_green,
    lint_strict_pin_names_its_failure,
    lint_exit_status_discarded,
    lint_broken_process_wait,
    lint_tier_forbids_its_evidence,
    lint_duplicate_frontmatter_key,
    lint_slug_identity_mismatch,
    lint_gate_misses_a_test_that_pins_its_file,
)


# ── Spec pillar → outcome-AC traceability (--spec mode) ─────────────────────
#
# A spec document's pillar blocks must each carry a ``verification_tier`` tag
# AND at least one outcome-level AC line (player/user-facing verb, not just
# "compiles" / "unit test passes").  This enforces the spec→plan handoff
# discipline: a pillar is not "done" when only its model layer is gated.
#
# See: commands/ilk-spec.md, commands/ilk-plan.md
# See: decomposition-principles.md §8 (spec pillar->outcome-AC traceability).
# Part of sub-plan 2026-06-28-spec-ac-traceability.

# A pillar heading: a markdown heading line containing bold text, e.g.
# ``## **Pillar: Tower Upgrades**``.  The ``#`` prefix distinguishes headings
# from AC list items (``- **AC-1**: ...``) which also contain ``**``.
_PILLAR_HEADING_RE = re.compile(r"^#+\s.*\*\*[^*]+\*\*.*$", re.MULTILINE)

# Verification tier tag: ``verification_tier: <value>`` or ``tier: <value>``.
_VERIFICATION_TIER_RE = re.compile(
    r"verification_tier:\s*\S+|tier:\s*\S+", re.IGNORECASE
)

# An AC line marker: lines starting with `- **AC`, `- AC:`, or similar.
# Use [ \t]* (not \s*) to avoid consuming the preceding newline with MULTILINE,
# which would push match.start() back and break the line-slice extraction.
_AC_LINE_RE = re.compile(r"^[ \t]*-[ \t]+\*?\*?AC\b", re.IGNORECASE | re.MULTILINE)

# Compile-only / unit-test-only markers — these are NOT outcome-level ACs.
# No trailing \b: "unit test" must match in "unit tests", "compile" in "compiles".
_COMPILE_ONLY_AC_RE = re.compile(
    r"\b(compil|unit\s*test|mypy|tsc|cargo\s+build|"
    r"npm\s+run\s+build|typecheck|lint|green)",
    re.IGNORECASE,
)


def lint_spec_pillar_traceability(text: str, slug: str) -> list[str]:
    """Flag spec pillars that lack a verification_tier tag or outcome-level AC."""
    findings: list[str] = []
    body = _strip_frontmatter(text)

    # Find all pillar headings and their positions.
    pillars = list(_PILLAR_HEADING_RE.finditer(body))
    if not pillars:
        return findings  # No pillars — not a spec doc, or empty.

    for i, pillar_match in enumerate(pillars):
        heading = pillar_match.group(0).strip()
        start = pillar_match.start()
        end = pillars[i + 1].start() if i + 1 < len(pillars) else len(body)
        block = body[start:end]

        # Check for verification_tier tag.
        has_tier = bool(_VERIFICATION_TIER_RE.search(block))

        # Check for at least one AC line.
        ac_lines = list(_AC_LINE_RE.finditer(block))
        has_ac = len(ac_lines) > 0

        # Check if any AC line is outcome-level (not compile-only).
        has_outcome_ac = False
        for ac_match in ac_lines:
            # Grab the rest of the line after the AC marker.
            line_start = ac_match.start()
            line_end = block.find("\n", line_start)
            if line_end == -1:
                line_end = len(block)
            ac_line_text = block[line_start:line_end]
            if not _COMPILE_ONLY_AC_RE.search(ac_line_text):
                has_outcome_ac = True
                break

        if not has_tier:
            findings.append(
                f"{slug}: pillar '{heading}' has no verification_tier tag. "
                f"Each spec pillar must declare its verification tier "
                f"(loop-verified / compile-only / device-manual)."
            )
        if not has_outcome_ac:
            if not has_ac:
                findings.append(
                    f"{slug}: pillar '{heading}' has no AC lines. "
                    f"Each spec pillar must map to at least one outcome-level AC "
                    f"that asserts the player/user-facing outcome."
                )
            else:
                findings.append(
                    f"{slug}: pillar '{heading}' has AC lines but none are "
                    f"outcome-level (all are compile/unit-test only). A pillar's "
                    f"AC must assert the player/user-facing outcome, not just "
                    f"that the artifact compiles."
                )

    return findings


# ── Sub-plan slug == master_plan slug collision ──────────────────────────────
#
# A sub-plan whose slug equals the master's ``master_plan`` value creates
# a naming collision: ``extract_master_order`` must exclude the master_plan
# slug (to suppress phantom title-line references), which means the
# sub-plan would also be excluded unless it exists on disk.  This is an
# authoring footgun — warn at plan-lint time so the planner renames the
# sub-plan before files land.  See 2026-06-22 slug-collision incident.

_MASTER_PLAN_RE = re.compile(r"^master_plan:\s*(.+)$", re.MULTILINE)


def lint_slug_collision(text: str, slug: str, master_plan_slug: str) -> list[str]:
    """Warn when a sub-plan slug equals the master's master_plan value."""
    findings: list[str] = []
    if not master_plan_slug:
        return findings
    # slug is the filename stem (e.g. "2026-06-22-tray-idle-filter").
    # master_plan_slug is the frontmatter value (e.g. "2026-06-22-tray-idle-filter").
    if slug == master_plan_slug:
        findings.append(
            f"{slug}: sub-plan slug equals the master's master_plan value "
            f"('{master_plan_slug}').  This collides with extract_master_order's "
            f"phantom-suppression logic — the sub-plan may be dropped from the "
            f"registry.  Rename the sub-plan to a distinct slug."
        )
    return findings


_BASE_BRANCH_RE = re.compile(r"^base_branch:\s*(.+)$", re.MULTILINE)
_TRUTHY = ("true", "yes", "1")


def _extract_base_branch(master_text: str) -> str | None:
    """Return the master's raw ``base_branch`` value, or *None* if absent."""
    m = re.match(r"^---\n.*?\n---\n", master_text, re.S)
    fm = master_text[m.start():m.end()] if m else master_text
    bm = _BASE_BRANCH_RE.search(fm)
    if not bm:
        return None
    return bm.group(1).split("#", 1)[0].strip().strip("\"'")


def lint_one_batch_one_branch(
    master_text: str,
    subplans: list[tuple[str, str]],
) -> list[str]:
    """Master-level check: all sub-plans' scope_paths resolve to base_branch.

    *subplans* is a list of ``(slug, text)`` pairs for the batch's sub-plans.

    AC-3: absent ``base_branch:`` → HARD finding (no fallback to HEAD).
    AC-4: unresolvable ref → HARD finding naming git's stderr.
    AC-2: sub-plans straddle >1 branch → HARD finding naming each branch.
    """
    findings: list[str] = []
    if not master_text:
        return findings

    base_branch = _extract_base_branch(master_text)

    # AC-3: no base_branch declared.
    if base_branch is None:
        findings.append(
            "MASTER: no `base_branch:` declared. The one-batch-one-branch gate "
            "requires a fixed ref to validate scope_paths against — deriving it "
            "from the checked-out branch would silently pass when a human switches "
            "branches mid-batch, which is the scenario the gate exists to catch. "
            "HARD FINDING: add `base_branch: <ref>` to the MASTER frontmatter."
        )
        return findings

    # AC-4: unresolvable ref.
    cwd = _resolve_git_cwd()
    if not (cwd / ".git").exists():
        # Not a git repo — nothing to validate.
        return findings

    resolved_ref, is_remote = _resolve_base_ref(base_branch, cwd)
    if resolved_ref is None:
        findings.append(
            f"MASTER: `base_branch: {base_branch}` does not resolve "
            f"(tried origin/{base_branch} and {base_branch}). "
            f"HARD FINDING: fix the ref or add the branch."
        )
        return findings

    # AC-2: check each sub-plan's scope_paths against the base branch.
    # Collect (slug, path, branch) for any path absent on base but present
    # on another ref.
    offenders: list[tuple[str, str, list[str]]] = []
    for slug, text in subplans:
        scope_paths = _extract_scope_paths(text)
        for p in scope_paths:
            if not _is_validatable_scope_path(p):
                continue
            rc_list, out_list, err_list = _run_git(
                ["rev-list", "--all", "--", p], cwd,
            )
            if rc_list != 0:
                continue  # git failure — the per-path lint handles this
            if not out_list:
                continue  # new file, nowhere in history — OK

            rc_base, _, err_base = _run_git(
                ["cat-file", "-e", f"{resolved_ref}:{p}"], cwd,
            )
            if rc_base == 0:
                continue  # present on base — OK
            # Same rule as the per-path check: present on HEAD is not off-base.
            rc_head_m, _, _ = _run_git(["cat-file", "-e", f"HEAD:{p}"], cwd)
            if rc_head_m == 0:
                continue

            is_absent = rc_base == 1 or (
                rc_base == 128
                and ("does not exist in" in err_base or "but not in" in err_base)
            )
            if not is_absent:
                continue  # unresolvable — per-path lint handles this

            # Path absent on base, present elsewhere — find which ref.
            rc_refs, out_refs, err_refs = _run_git(
                ["branch", "--contains", out_list.splitlines()[0]], cwd,
            )
            ref_names = (
                [r.strip().lstrip("* ") for r in out_refs.splitlines()
                 if r.strip()]
                if rc_refs == 0 and out_refs
                else ["<unknown ref>"]
            )
            offenders.append((slug, p, ref_names))

    if offenders:
        # Group by branch for the finding message.
        by_branch: dict[str, list[str]] = {}
        for slug, path, refs in offenders:
            for ref in refs:
                by_branch.setdefault(ref, []).append(f"{slug} → {path}")
        branch_summary = "; ".join(
            f"{ref}: {', '.join(paths)}"
            for ref, paths in sorted(by_branch.items())
        )
        findings.append(
            f"HARD MASTER: sub-plans' scope_paths straddle multiple branches. "
            f"Base branch is '{base_branch}'. Off-base: {branch_summary}. "
            f"This would route commits to the wrong branch."
        )

    return findings


def _has_batch_verification_marker(text: str) -> bool:
    """True if *text*'s frontmatter declares ``batch_verification: true``."""
    lines = text.split("\n")
    in_fm = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if in_fm:
                break  # end of frontmatter
            in_fm = True
            continue
        if in_fm and stripped.lower().startswith("batch_verification:"):
            val = stripped.split(":", 1)[1].strip().strip("\"'").lower()
            return val in _TRUTHY
    return False


def _check_split(text: str, slug: str) -> tuple[list[str], list[str]]:
    """Run ``ALL_CHECKS`` over one sub-plan body, split (blocking, advisory).

    ``lint_shared_module_gate`` is the only advisory check.  Its findings are
    demoted to ``INFO:`` — and dropped from the exit code — when the batch is
    verified; see ``main`` for why.  Everything else is blocking regardless.
    """
    blocking: list[str] = []
    advisory: list[str] = []
    for check in ALL_CHECKS:
        bucket = advisory if check is lint_shared_module_gate else blocking
        bucket.extend(check(text, slug))
    return blocking, advisory


def lint_subplan(body: str, slug: str) -> list[str]:
    """Run all sub-plan checks against a body string (no file I/O).

    Convenience wrapper for tests and callers that already have the text.
    """
    blocking, advisory = _check_split(body, slug)
    return blocking + advisory


def lint_file_split(
    path: str | Path, master_text: str = "",
) -> tuple[list[str], list[str]]:
    """Run all checks against one sub-plan file, split (blocking, advisory).

    When *master_text* is provided, the slug-collision check is also run,
    and the base-ref resolution check uses the master's declared
    ``base_branch:``.
    """
    p = Path(path)
    slug = p.stem
    text = p.read_text(encoding="utf-8-sig")
    blocking, advisory = _check_split(text, slug)
    # Slug-collision check requires master_text context.
    if master_text:
        master_plan_slug = ""
        m = _MASTER_PLAN_RE.search(master_text)
        if m:
            master_plan_slug = m.group(1).strip()
        blocking.extend(lint_slug_collision(text, slug, master_plan_slug))
        # Thread base_branch into the off-base check.
        base_branch = _extract_base_branch(master_text)
        is_default = base_branch is None
        blocking.extend(lint_scope_path_off_base_branch(
            text, slug,
            base_ref=base_branch or "main",
            _is_default_base=is_default,
        ))
    else:
        # No master_text — run the off-base check with default "main".
        blocking.extend(lint_scope_path_off_base_branch(text, slug))
    return blocking, advisory


def lint_file(path: str | Path, master_text: str = "") -> list[str]:
    """Run all checks against one sub-plan file. Returns finding messages.

    When *master_text* is provided, the slug-collision check is also run,
    and the base-ref resolution check uses the master's declared
    ``base_branch:``.
    """
    blocking, advisory = lint_file_split(path, master_text=master_text)
    return blocking + advisory


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan-quality authoring checks for ilk2 plans."
    )
    parser.add_argument("paths", nargs="*", help="Sub-plan .md file(s) to lint.")
    parser.add_argument("--master", help="MASTER plan file (enables slug-collision check).")
    parser.add_argument(
        "--git-cwd", default=None,
        help="Working directory for git operations (default: cwd).",
    )
    parser.add_argument(
        "--project-root", default=None,
        help="Project root for importer-lookup grep (default: cwd).",
    )
    parser.add_argument(
        "--spec", action="store_true",
        help="Run spec pillar traceability check instead of per-sub-plan checks.",
    )
    parser.add_argument(
        "--verified", action="store_true",
        help=(
            "The batch carries a verification sub-plan: "
            "lint_shared_module_gate findings are advisory (INFO), not HARD."
        ),
    )
    args = parser.parse_args()

    # Override the git working directory if requested.
    if args.git_cwd:
        global _GIT_CWD
        _GIT_CWD = Path(args.git_cwd).resolve()

    # Override the project root for importer lookup if requested.
    if args.project_root:
        global _PROJECT_ROOT
        _PROJECT_ROOT = Path(args.project_root).resolve()

    if not args.paths:
        parser.error("at least one file path is required")

    if args.spec:
        # Spec mode: run lint_spec_pillar_traceability on each file.
        total = 0
        for path in args.paths:
            p = Path(path)
            slug = p.stem
            text = p.read_text(encoding="utf-8-sig")
            for msg in lint_spec_pillar_traceability(text, slug):
                print(f"WARN: {msg}")
                total += 1
        if total == 0:
            print("OK: plan_check spec clean")
        return 1 if total else 0

    master_text = ""
    if args.master:
        master_text = Path(args.master).read_text(encoding="utf-8-sig")

    # ── --verified: D9's importer-coverage rule becomes advisory ──────────
    #
    # ``lint_shared_module_gate`` (V1's D9: a step's last gate must also run
    # the tests of every module that imports what the step changes) gave 7
    # HARD findings on gh-resolve's first converted batch (18b), each asking
    # a step gate to run 155-177 of 652 test files — about a quarter of the
    # suite per step.  That contradicts the planner's own rule that only the
    # verification sub-plan runs a broad gate.
    #
    # Under ilk2 the rule is redundant WHEN THE BATCH HAS A VERIFICATION
    # SUB-PLAN: ilk2's verification step runs the full suite at the batch
    # head and attributes any new failure to the step that caused it, which
    # gets a repair plan.  So under --verified the finding is INFO, not HARD.
    # Auto-detect the same condition from the batch itself: --master plus a
    # linted sub-plan declaring ``batch_verification: true`` is exactly "this
    # batch has a verification sub-plan".  WITHOUT one, nothing else catches
    # an importer's breakage, so the rule stays hard.
    verified = args.verified
    if not verified and master_text:
        for path in args.paths:
            if _has_batch_verification_marker(
                Path(path).read_text(encoding="utf-8-sig")
            ):
                verified = True
                break

    total = 0
    subplans: list[tuple[str, str]] = []
    for path in args.paths:
        blocking, advisory = lint_file_split(path, master_text=master_text)
        for msg in blocking:
            print(f"WARN: {msg}")
            total += 1
        for msg in advisory:
            if verified:
                # Advisory: keep the finding visible, drop its severity and
                # its vote on the exit code.  The leading ``HARD`` goes with
                # the severity — ``INFO: HARD ...`` would contradict itself.
                print(f"INFO: {msg.removeprefix('HARD ')}")
            else:
                print(f"WARN: {msg}")
                total += 1
        p = Path(path)
        subplans.append((p.stem, p.read_text(encoding="utf-8-sig")))

    # Master-level checks need every sub-plan's scope_paths at once.
    if master_text:
        for msg in lint_one_batch_one_branch(master_text, subplans):
            print(f"WARN: {msg}")
            total += 1

    if total == 0:
        print("OK: plan_check clean")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
