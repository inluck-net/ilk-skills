#!/usr/bin/env python3
"""Planner-side QC lints that enforce the loop's degrade discipline.

These were prose guidance in ``/ilk-plan`` step 7 that the planning agent could
skip. The uccargo figma incident (2026-06-13) showed unenforced prose guards do
get skipped, so the two highest-value degrade checks are extracted here as
deterministic, unit-tested functions and wired into step 7.

Checks (each takes a sub-plan file's text + slug, returns a list of finding
messages):

1. ``lint_envprereq_fallback_contradiction`` — a sub-plan that hard-gates on an
   MCP capability X via an ``env_prereqs`` ``claude mcp list | grep -q X`` probe
   AND documents a fallback/degrade path for the *same* X. The env_prereq
   fast-fails to ``blocked`` BEFORE the fallback can run, so the gate and the
   fallback contradict. X is optional => it must not be a hard env_prereq.

2. ``lint_block_when_default_exists`` — a step instructs ``set status: blocked``
   while the sub-plan documents a safe default/fallback pattern, so blocking is
   avoidable. On a headless loop, ``blocked`` = stall + human; prefer
   degrade-to-default (decomposition-principles).

CLI:
    python plan_lint.py <subplan.md> [<subplan.md> ...]
        prints ``WARN: <slug>: <msg>`` lines (ASCII); exit 1 if any finding.

    python plan_lint.py --source-hygiene <scripts...>
        Scans .py/.ps1 toolkit scripts for native-IO convention violations.
        See native-io-conventions.md.

Reads files with ``utf-8-sig`` (zh-CN Windows configs may carry a BOM).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

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


def _strip_frontmatter(text: str) -> str:
    """Return the body after the leading ``---`` frontmatter block (if any)."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


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


# ── Contract-change review (modes A/C/D guard) ────────────────────────────────
#
# A sub-plan whose scope_paths touch a contract-governed file must reference
# the contract docs so a new reader/writer can't be authored blind.  See
# orchestration-collaboration.md L1-L4 and detached-component-contracts.md.

# Files whose contracts are documented in detached-component-contracts.md.
# Matching is by filename suffix (the path may be absolute or project-relative).
_CONTRACT_GOVERNED_FILES = frozenset({
    "collect.py",
    "watchdog.ps1",
    "watchdog.sh",
    "scheduler.ps1",
    "scheduler.sh",
    "run_ilk_loop_claude.ps1",
    "run_ilk_loop_claude.sh",
    "loop_status.py",
    "promote_next_master.py",
    "plan_status.py",
    "status_all.py",
    "render_tray.py",
})

# Contract documentation filenames — a sub-plan body must mention at least one.
_CONTRACT_DOC_NAMES = (
    "orchestration-collaboration.md",
    "detached-component-contracts.md",
)

_SCOPE_PATHS_RE = re.compile(r"^scope_paths:\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s+-\s+\"?([^\"]+)\"?\s*$", re.MULTILINE)


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
    return _LIST_ITEM_RE.findall(after)


def _path_is_contract_governed(path: str) -> bool:
    """True if *path* ends with a contract-governed filename."""
    p = path.replace("\\", "/")
    return any(p.endswith("/" + name) or p == name for name in _CONTRACT_GOVERNED_FILES)


def _body_references_contract_doc(text: str) -> bool:
    """True if *text* mentions at least one contract documentation file."""
    lower = text.lower()
    return any(doc in lower for doc in _CONTRACT_DOC_NAMES)


def lint_contract_change_review(text: str, slug: str) -> list[str]:
    """Flag a contract-governed sub-plan that doesn't reference the contract docs."""
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    governed = [p for p in scope_paths if _path_is_contract_governed(p)]
    if not governed:
        return findings
    if _body_references_contract_doc(text):
        return findings
    findings.append(
        f"{slug}: scope_paths touch contract-governed file(s) "
        f"({', '.join(governed)}) but the sub-plan body does not reference "
        f"{' or '.join(_CONTRACT_DOC_NAMES)}. "
        f"Consult detached-component-contracts.md 'Adding a new reader or writer' "
        f"checklist and add a Reference reading entry."
    )
    return findings


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
    return re.findall(r"command:\s*(.+)", after)


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
            covered = any(
                sp.replace("\\", "/") == nt or sp.replace("\\", "/").startswith(nt + "/")
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


# ── Whole-suite-gate baseline guard ──────────────────────────────────────────
#
# A sub-plan whose ``local_checks`` run a pre-existing whole suite
# (``bash tests/<existing>.sh``, full ``pytest``/``vitest`` with no path
# scope, ``npm test``) with no baseline-green note risks false-blocking
# when that suite is baseline-red on the run platform.  Real case:
# ``test_worker_bootstrap.sh`` rw------- check on Windows (2026-06-28
# drawing-worker run, backlog 5a5092ff).
#
# Heuristics for "whole suite":
#   - ``pytest`` / ``py.test`` / ``vitest`` / ``jest`` with no positional
#     arg that looks like a specific file or directory
#   - ``bash tests/*.sh`` or ``sh tests/*.sh`` (shell glob on test dir)
#   - ``npm test`` / ``yarn test`` / ``bun test`` with no specific file arg
#
# The body must contain a "baseline-green" note referencing the platform
# (e.g. "baseline-green on Windows 2026-06-28").

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

# A path-like positional arg to pytest/vitest/jest (not a flag).
# Matches: tests/test_foo.py, apps/orders/, src/foo.spec.ts, etc.
_PATH_ARG_RE = re.compile(
    r"(?<!\S)(?:[A-Za-z]:)?(?:[^\s-][\w./\\-]*\.(?:py|ts|js|tsx|jsx|mjs)"
    r"|[^\s-][\w./\\-]*/)"
)

_BASELINE_GREEN_RE = re.compile(
    r"baseline[- ]green\s+(?:on|for)\s+\S", re.IGNORECASE
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


def _is_whole_suite_command(cmd: str) -> bool:
    """True if *cmd* runs a pre-existing whole test suite (no file scope)."""
    cmd_stripped = cmd.strip()
    if not _WHOLE_SUITE_CMD_RE.search(cmd_stripped):
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
                continue
            positional.append(tok)
        if any(_is_directory_arg(t) for t in positional):
            return True   # a directory tree → whole suite
        if positional:
            return False  # file paths / node ids only → genuinely scoped
    return True


def lint_wholesuite_gate_baseline(text: str, slug: str) -> list[str]:
    """Flag a whole-suite local_check that lacks a baseline-green note."""
    findings: list[str] = []
    # Uses the widened extractor: cares about what a gate runs, not where declared.
    commands = _extract_all_local_checks_commands(text)
    full_text = text  # markers may appear in frontmatter comments
    for cmd in commands:
        if not _is_whole_suite_command(cmd):
            continue
        if _BASELINE_GREEN_RE.search(full_text):
            continue
        findings.append(
            f"{slug}: local_check '{cmd.strip()[:80]}' runs a pre-existing "
            f"whole suite with no 'baseline-green on <platform>' note in the "
            f"sub-plan body. If this suite is baseline-red on the run platform "
            f"(e.g. POSIX-only perms check on Windows), every step will "
            f"false-block. Add a baseline-green note or scope the gate to "
            f"the changed module."
        )
    return findings


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
    |hint\b                                # hint (standalone)
    |key\s*hint                            # key hint
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
    |\bpath\b
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
        commands.extend(re.findall(r"command:\s*(.+)", block_match.group(1)))
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


# ── Budget-vs-gate-timeout warning ───────────────────────────────────────────
#
# A sub-plan's per-step ``local_checks`` declare ``timeout:`` values that sum
# to the wall-clock budget a single step may consume.  If that sum approaches
# (or exceeds) the ``iteration_timeout_min`` budget, the step will burn its
# entire budget on gates alone, leaving no time for the actual work.  The
# default iteration budget is 30 minutes.
#
# This lint warns when the sum of a step's declared gate timeouts exceeds a
# configurable fraction (default 0.8) of the iteration budget.  It does NOT
# warn when no timeouts are declared — absent is not zero.
#
# See decomposition-principles.md §16.

_DEFAULT_ITERATION_TIMEOUT_MIN = 30
_GATE_TIMEOUT_WARN_FRACTION = 0.8

_TIMEOUT_VALUE_RE = re.compile(r"timeout:\s*(\d+)")


def _extract_timeout_sum(text: str) -> int | None:
    """Sum of all ``timeout:`` values in local_checks blocks (frontmatter + per-step).

    Returns None when no timeouts are declared (absent ≠ zero).
    """
    total = 0
    found_any = False

    # Frontmatter local_checks
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if m:
        fm = text[m.start():m.end()]
        lc_match = re.search(r"^local_checks:\s*$", fm, re.MULTILINE)
        if lc_match:
            after = fm[lc_match.end():]
            for val in _TIMEOUT_VALUE_RE.findall(after):
                total += int(val)
                found_any = True

    # Per-step local_checks blocks
    body = _strip_frontmatter(text)
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        block = block_match.group(1)
        for val in _TIMEOUT_VALUE_RE.findall(block):
            total += int(val)
            found_any = True

    return total if found_any else None


def _extract_recommended_timeout(text: str) -> int:
    """Extract ``recommended_iteration_timeout_min`` from frontmatter, or default."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return _DEFAULT_ITERATION_TIMEOUT_MIN
    fm = text[m.start():m.end()]
    match = re.search(r"recommended_iteration_timeout_min:\s*(\d+)", fm)
    return int(match.group(1)) if match else _DEFAULT_ITERATION_TIMEOUT_MIN


def _extract_per_step_timeout_sums(text: str) -> list[tuple[str, int]] | None:
    """Per-step gate-timeout totals, as ``(step_label, seconds)``.

    The budget is **per iteration**, and each step runs in its own iteration, so
    summing every step's gates together (as a whole-file sum does) overstates the
    cost by the number of steps and false-warns on any healthy multi-step
    sub-plan — measured 2026-08-10: 3 of this repo's own 5 sub-plans tripped it.

    Frontmatter ``local_checks`` run at EVERY step, so a step's true cost is the
    frontmatter sum plus that step's own block.

    Returns None when no timeouts are declared anywhere (absent != zero).
    """
    found_any = False
    fm_total = 0
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if m:
        fm = text[m.start():m.end()]
        lc_match = re.search(r"^local_checks:\s*$", fm, re.MULTILINE)
        if lc_match:
            for val in _TIMEOUT_VALUE_RE.findall(fm[lc_match.end():]):
                fm_total += int(val)
                found_any = True

    body = _strip_frontmatter(text)
    per_step: list[tuple[str, int]] = []
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        step_total = 0
        for val in _TIMEOUT_VALUE_RE.findall(block_match.group(1)):
            step_total += int(val)
            found_any = True
        heads = re.findall(r"^###\s+Step\s+(\S+)", body[:block_match.start()], re.M)
        label = f"step {heads[-1]}" if heads else "a step"
        per_step.append((label, fm_total + step_total))

    if not found_any:
        return None
    return per_step or [("frontmatter gates", fm_total)]


def lint_budget_vs_gate_timeout(text: str, slug: str) -> list[str]:
    """Warn when a step's declared gate timeouts approach the iteration budget."""
    findings: list[str] = []
    sums = _extract_per_step_timeout_sums(text)
    if sums is None:
        return findings  # absent is not zero — no warning

    budget = _extract_recommended_timeout(text)
    threshold = int(budget * 60 * _GATE_TIMEOUT_WARN_FRACTION)

    label, worst = max(sums, key=lambda pair: pair[1])
    if worst > threshold:
        findings.append(
            f"{slug}: {label} declares local_checks timeouts summing to {worst}s, "
            f"which exceeds {_GATE_TIMEOUT_WARN_FRACTION:.0%} of the iteration "
            f"budget ({budget}min = {budget * 60}s). That step may burn its "
            f"entire budget on gates alone. Either reduce gate timeouts, raise "
            f"recommended_iteration_timeout_min, or split the step "
            f"(see decomposition-principles.md section 16)."
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


# ── Git-aware scope-path lint ─────────────────────────────────────────

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


def lint_scope_path_off_base_branch(
    text: str, slug: str, base_ref: str = "main",
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
    """
    findings: list[str] = []
    scope_paths = _extract_scope_paths(text)
    if not scope_paths:
        return findings

    cwd = Path.cwd()
    if not (cwd / ".git").exists():
        # Not a git repo — no branches to confuse, nothing to validate.
        return findings

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
            ["cat-file", "-e", f"{base_ref}:{p}"], cwd,
        )
        if rc_base == 0:
            # Present on base — OK.
            continue

        # rc=128 "path 'X' does not exist in 'Y'" = genuine absence → HARD.
        # rc=128 "invalid object name" / "Not a valid object" = bad ref → unknown.
        is_absent = rc_base == 1 or (
            rc_base == 128
            and "does not exist in" in err_base
        )
        if is_absent:
            # Absent on base but present elsewhere — HARD finding.
            # Find which refs carry it.
            rc_refs, out_refs, err_refs = _run_git(
                ["branch", "--contains", out_list.splitlines()[0]], cwd,
            )
            ref_names = (
                [r.strip().lstrip("* ") for r in out_refs.splitlines()
                 if r.strip()]
                if rc_refs == 0 and out_refs
                else ["<unknown ref>"]
            )
            findings.append(
                f"HARD {slug}: scope_path '{p}' is absent on "
                f"base ref '{base_ref}' but exists on: "
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


ALL_CHECKS = (
    lint_envprereq_fallback_contradiction,
    lint_block_when_default_exists,
    lint_contract_change_review,
    lint_brittle_exact_list_assertion,
    lint_escaped_bug_regression_gate,
    lint_frontmatter_path_created_later,
    lint_e2e_check_without_env_prereq,
    lint_wholesuite_gate_baseline,
    lint_posix_only_test_assertion,
    lint_network_tool_mock_only_gate,
    lint_vertical_slice_ac,
    lint_anti_hardcode_integration,
    lint_ui_promise_wiring,
    lint_balance_regression_flag,
    lint_unverifiable_test_selector,
    lint_budget_vs_gate_timeout,
    lint_redundant_gate,
    lint_scope_path_off_base_branch,
)


# ── Source-hygiene checks (--source-hygiene mode) ──────────────────────────
#
# These are NOT sub-plan lints — they scan toolkit source files (.py / .ps1)
# for violations of the native-IO convention documented in
# native-io-conventions.md.  Invoked via ``plan_lint.py --source-hygiene``.
#
# Two checks:
#   (a) stderr-in-json-path: Python file writes to sys.stderr inside a
#       json-mode code path (if args.json: / json_mode parameter).
#   (b) unguarded-native-python: .ps1 file has a `& python` line without
#       a $ErrorActionPreference='Continue' guard in scope.

# Python stderr-write patterns.
_STDERR_WRITE_RE = re.compile(
    r"""
    print\s*\([^)]*file\s*=\s*sys\.stderr    # print(..., file=sys.stderr)
    |sys\.stderr\.write\s*\(                  # sys.stderr.write(
    """,
    re.VERBOSE,
)

# JSON-mode guard: an `if args.json:` or `if json_mode:` or similar block.
_JSON_MODE_GUARD_RE = re.compile(
    r"""
    if\s+args\.json\b
    |if\s+json_mode\b
    |if\s+.*--json
    |json_mode\s*[:=]
    """,
    re.VERBOSE | re.IGNORECASE,
)

# PS1 & python invocation (bare, without Continue guard).
_PS1_AMP_PYTHON_RE = re.compile(r"&\s+python\b")

# PS1 EAP=Continue guard — function-local or save/restore pattern.
_PS1_EAP_CONTINUE_RE = re.compile(
    r"\$ErrorActionPreference\s*=\s*['\"]Continue['\"]", re.IGNORECASE
)

# Allowlist: test scaffolds that are exempt from the unguarded check.
_SOURCE_HYGIENE_ALLOWLIST = frozenset({
    "_pipeline_smoketest.ps1",
})


def _is_in_json_mode_scope(lines: list[str], target_line_idx: int) -> bool:
    """True if *target_line_idx* is inside a json-mode conditional block.

    Scans backwards from the target line looking for an ``if args.json:`` /
    ``if json_mode:`` guard.  Must verify:
    1. The guard's body extends to the target line (no early return/dedent).
    2. We're not in an ``else:`` branch of the guard (else = non-json path).
    3. We're not past the guard's early return (common pattern: if json_mode:
       print(json); return — everything after is text-mode).
    """
    if target_line_idx < 0 or target_line_idx >= len(lines):
        return False
    target_indent = len(lines[target_line_idx]) - len(lines[target_line_idx].lstrip())

    # Walk backwards looking for a json-mode guard.
    for i in range(target_line_idx - 1, max(-1, target_line_idx - 80), -1):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue
        line_indent = len(line) - len(line.lstrip())

        # Skip lines at same or higher indent (peers, not guards).
        if line_indent >= target_indent:
            continue

        # An ``else:`` or ``elif`` at a lower indent than target means we're
        # in a non-json branch — NOT inside the json guard.
        if stripped == "else:" or stripped.startswith("elif "):
            return False

        if not _JSON_MODE_GUARD_RE.search(stripped):
            continue

        # Found a json-mode guard.  Check if the guard body has an early
        # return before reaching the target line (common: ``if args.json:
        # print(json); return``).
        guard_body_indent = line_indent + 4  # Python indent
        has_return_before_target = False
        for j in range(i + 1, target_line_idx):
            body_line = lines[j]
            body_stripped = body_line.strip()
            if not body_stripped:
                continue
            body_indent = len(body_line) - len(body_line.lstrip())
            # If we've dedented back to the guard's level or lower, the
            # guard body is over.
            if body_indent <= line_indent:
                break
            if body_indent == guard_body_indent and body_stripped.startswith("return"):
                has_return_before_target = True
                break
        if has_return_before_target:
            return False

        # The target is inside this guard's body (no early return found).
        return True

    return False


def lint_stderr_in_json_path(path: Path, text: str) -> list[str]:
    """Flag Python stderr writes inside a json-mode code path."""
    findings: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _STDERR_WRITE_RE.search(line):
            continue
        if _is_in_json_mode_scope(lines, i):
            findings.append(
                f"{path.name}:{i + 1}: stderr write inside a json-mode code path. "
                f"In --json mode, fold notices into the payload (e.g. notices[]) "
                f"instead of printing to stderr. See native-io-conventions.md."
            )
    return findings


def _eap_continue_in_scope(lines: list[str], target_line_idx: int) -> bool:
    """True if a $ErrorActionPreference='Continue' guard covers *target_line_idx*.

    Checks two idioms:
    1. Function-local: a $EAP='Continue' at a lower indent within the same function.
    2. Script-level save/restore: a $savedEAP = ...; $EAP = 'Continue' + try/finally
       surrounding the target line.
    """
    if target_line_idx < 0 or target_line_idx >= len(lines):
        return False
    target_indent = len(lines[target_line_idx]) - len(lines[target_line_idx].lstrip())

    # Walk backwards looking for a $EAP='Continue' at a lower or equal indent.
    for i in range(target_line_idx - 1, max(-1, target_line_idx - 120), -1):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue
        line_indent = len(line) - len(line.lstrip())
        # Only consider guards at same or lower indentation.
        if line_indent > target_indent:
            continue
        if _PS1_EAP_CONTINUE_RE.search(stripped):
            return True
        # Stop at function boundary (different scope).
        if stripped.lower().startswith("function ") and line_indent < target_indent:
            break

    # Also check forward: a script-level save/restore wrapping the target.
    for i in range(max(0, target_line_idx - 60), target_line_idx):
        line = lines[i]
        if _PS1_EAP_CONTINUE_RE.search(line):
            # Check if there's a try block after it (save/restore pattern).
            for j in range(i + 1, min(len(lines), i + 5)):
                if lines[j].strip().lower() == "try {":
                    return True
            break

    return False


def lint_unguarded_native_python(path: Path, text: str) -> list[str]:
    """Flag .ps1 `& python` lines without $ErrorActionPreference='Continue'."""
    findings: list[str] = []
    # Allowlist check.
    if path.name in _SOURCE_HYGIENE_ALLOWLIST:
        return findings
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _PS1_AMP_PYTHON_RE.search(line):
            continue
        # Skip comment lines.
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not _eap_continue_in_scope(lines, i):
            findings.append(
                f"{path.name}:{i + 1}: `& python` without "
                f"$ErrorActionPreference='Continue' guard. PS 5.1 wraps "
                f"native stderr as NativeCommandError under $EAP='Stop'. "
                f"See native-io-conventions.md."
            )
    return findings


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


# ── supervised_only scope guard (§13) ────────────────────────────────────────
#
# `supervised_only: true` exists for ONE hazard: a batch that rewrites the
# loop's own dispatch machinery would be read by the scheduler while being
# rewritten.  The trigger is therefore mechanical — a sub-plan's `scope_paths`
# must actually *modify* one of the loop-infra files — never "this batch feels
# risky" or "we haven't verified it yet" (that is `status: draft` + verification
# tiers).  See decomposition-principles.md §13.
#
# The flag is expensive to mis-set: the scheduler and `promote_next_master`
# skip the master permanently, AND ilk-runner's preflight HARD-STOPS a manual
# `/ilk-run` while any cross-project scheduler is alive.  A stray flag costs
# both autonomy and the manual fallback, so an unwarranted one is a hard
# finding, not a nit.

# Loop-infra basenames — the narrow §13 set.
_LOOP_INFRA_BASENAMES = frozenset({
    "loop_status.py",
    "scheduler_scan.py",
    "promote_next_master.py",
    "plan_status.py",
    "scheduler.ps1",
    "scheduler.sh",
})

# Canonical toolkit-relative locations of those files, used to decide whether a
# directory/glob scope entry (e.g. `skills/ilk-loop/scripts/**`) pulls one in.
_LOOP_INFRA_CANONICAL = (
    "skills/ilk-loop/scripts/loop_status.py",
    "skills/ilk-loop/scripts/promote_next_master.py",
    "skills/ilk-loop/scripts/plan_status.py",
    "skills/ilk-watchdog/scripts/scheduler_scan.py",
    "skills/ilk-watchdog/scripts/scheduler.ps1",
    "skills/ilk-watchdog/scripts/scheduler.sh",
)

_SUPERVISED_ONLY_RE = re.compile(r"^supervised_only:\s*(.+)$", re.MULTILINE)
_TRUTHY = ("true", "yes", "1")


def _scope_entry_names_infra(path: str) -> bool:
    """True if *path* explicitly names a loop-infra file.

    Strict form: the entry's basename IS an infra file.  A test that merely
    imports `loop_status.py` (`tests/test_loop_status.py`) does not match —
    §13 requires the sub-plan to *modify* the infra file.
    """
    p = path.replace("\\", "/").rstrip()
    return any(p == name or p.endswith("/" + name) for name in _LOOP_INFRA_BASENAMES)


def _scope_entry_may_cover_infra(path: str) -> bool:
    """True if *path* names OR could glob in a loop-infra file.

    Broad form: also matches a directory/glob entry whose literal prefix
    contains a canonical infra path (`skills/ilk-loop/scripts/**`).
    """
    if _scope_entry_names_infra(path):
        return True
    p = path.replace("\\", "/").rstrip()
    # Literal prefix = everything before the first glob metacharacter.
    prefix = re.split(r"[*?\[]", p, maxsplit=1)[0]
    if not prefix:
        return True  # a bare glob covers everything
    if not prefix.endswith("/"):
        prefix += "/"
    return any(canon.startswith(prefix) for canon in _LOOP_INFRA_CANONICAL)


def _extract_supervised_only(master_text: str) -> str | None:
    """Return the master's raw `supervised_only` value, or None if absent."""
    m = re.match(r"^---\n.*?\n---\n", master_text, re.S)
    fm = master_text[m.start():m.end()] if m else master_text
    sm = _SUPERVISED_ONLY_RE.search(fm)
    if not sm:
        return None
    # Values in the wild carry trailing rationale comments.
    return sm.group(1).split("#", 1)[0].strip().strip("\"'")


def lint_supervised_only_scope(
    master_text: str,
    subplans: list[tuple[str, str]],
) -> list[str]:
    """Master-level check: is `supervised_only` warranted by scope_paths?

    *subplans* is a list of ``(slug, text)`` pairs for the batch's sub-plans.

    Two directions, deliberately asymmetric so that each errs toward autonomy:

    - flag set, no scope entry could even glob in an infra file → finding
      (broad match, so this fires only when the flag is clearly unwarranted);
    - a scope entry explicitly names an infra file, flag not set → finding
      (strict match, so we never tell a planner to set the flag on a guess).
    """
    findings: list[str] = []
    if not master_text:
        return findings

    raw = _extract_supervised_only(master_text)
    is_set = (raw or "").lower() in _TRUTHY

    covering = [
        (slug, p)
        for slug, text in subplans
        for p in _extract_scope_paths(text)
        if _scope_entry_may_cover_infra(p)
    ]
    naming = [
        (slug, p)
        for slug, text in subplans
        for p in _extract_scope_paths(text)
        if _scope_entry_names_infra(p)
    ]

    if is_set and not covering:
        findings.append(
            "MASTER: `supervised_only: true` but no sub-plan's scope_paths "
            "modifies loop-infra ("
            + ", ".join(sorted(_LOOP_INFRA_BASENAMES))
            + "). This is the ONE thing the flag is for — it is not a "
            "readiness gate, a risk gate, or a 'needs review' marker "
            "(decomposition-principles.md §13). Setting it here removes "
            "autonomous dispatch AND makes ilk-runner preflight hard-stop a "
            "manual /ilk-run while a scheduler is alive. Use `status: draft` "
            "for not-yet-released, verification tiers for trust level, and "
            "config (e.g. point `clone_path` at a throwaway clone) to "
            "neutralise local-mutation hazards. HARD FINDING: set "
            "`supervised_only: false` unless the user explicitly asked for it."
        )

    if naming and not is_set:
        offenders = ", ".join(f"{slug} → {p}" for slug, p in naming)
        findings.append(
            f"MASTER: sub-plan scope_paths modify loop-infra ({offenders}) but "
            f"`supervised_only` is not set. A batch that rewrites the loop's "
            f"own dispatch machinery must never be autonomously dispatched — "
            f"the scheduler would read code it is simultaneously rewriting. "
            f"HARD FINDING: set `supervised_only: true` on the MASTER "
            f"(decomposition-principles.md §13)."
        )

    return findings


def lint_file(path: str | Path, master_text: str = "") -> list[str]:
    """Run all checks against one sub-plan file. Returns finding messages.

    When *master_text* is provided, the slug-collision check is also run.
    """
    p = Path(path)
    slug = p.stem
    text = p.read_text(encoding="utf-8-sig")
    findings: list[str] = []
    for check in ALL_CHECKS:
        findings.extend(check(text, slug))
    # Slug-collision check requires master_text context.
    if master_text:
        master_plan_slug = ""
        m = _MASTER_PLAN_RE.search(master_text)
        if m:
            master_plan_slug = m.group(1).strip()
        findings.extend(lint_slug_collision(text, slug, master_plan_slug))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Planner degrade-discipline lints.")
    parser.add_argument("paths", nargs="*", help="Sub-plan .md file(s) to lint.")
    parser.add_argument("--master", help="MASTER plan file (enables slug-collision check).")
    parser.add_argument(
        "--spec", action="store_true",
        help="Run spec pillar traceability check instead of per-sub-plan checks.",
    )
    parser.add_argument(
        "--source-hygiene", action="store_true",
        help="Run native-IO source-hygiene checks on .py/.ps1 scripts.",
    )
    args = parser.parse_args()

    if not args.paths:
        parser.error("at least one file path is required")

    if args.source_hygiene:
        # Source-hygiene mode: scan toolkit scripts for native-IO violations.
        total = 0
        for path_str in args.paths:
            p = Path(path_str)
            if not p.exists():
                print(f"WARN: {p}: file not found", file=sys.stderr)
                continue
            text = p.read_text(encoding="utf-8-sig")
            if p.suffix == ".py":
                for msg in lint_stderr_in_json_path(p, text):
                    print(f"WARN: {msg}")
                    total += 1
            elif p.suffix == ".ps1":
                for msg in lint_unguarded_native_python(p, text):
                    print(f"WARN: {msg}")
                    total += 1
        if total == 0:
            print("OK: source-hygiene clean")
        return 1 if total else 0

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
            print("OK: plan_lint spec clean")
        return 1 if total else 0

    master_text = ""
    if args.master:
        master_text = Path(args.master).read_text(encoding="utf-8-sig")

    total = 0
    subplans: list[tuple[str, str]] = []
    for path in args.paths:
        for msg in lint_file(path, master_text=master_text):
            print(f"WARN: {msg}")
            total += 1
        p = Path(path)
        subplans.append((p.stem, p.read_text(encoding="utf-8-sig")))

    # Master-level checks need every sub-plan's scope_paths at once.
    if master_text:
        for msg in lint_supervised_only_scope(master_text, subplans):
            print(f"WARN: {msg}")
            total += 1

    if total == 0:
        print("OK: plan_lint clean")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
