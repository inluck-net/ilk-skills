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

Reads files with ``utf-8-sig`` (zh-CN Windows configs may carry a BOM).
"""

from __future__ import annotations

import argparse
import re
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
    commands = _extract_local_checks_commands(text)
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
    # Frontmatter local_checks
    cmds = _extract_local_checks_commands(text)
    if cmds:
        return True
    # Per-step local_checks blocks containing at least one command:
    body = _strip_frontmatter(text)
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        block = block_match.group(1)
        if re.search(r"^\s+-\s+command:", block, re.MULTILINE):
            return True
    return False


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
    # Collect commands from both frontmatter and per-step blocks.
    fm_cmds = _extract_local_checks_commands(text)
    body = _strip_frontmatter(text)
    step_cmds: list[str] = []
    for block_match in _STEP_LOCAL_CHECKS_BLOCK_RE.finditer(body):
        block = block_match.group(1)
        step_cmds.extend(re.findall(r"command:\s*(.+)", block))
    all_cmds = fm_cmds + step_cmds
    if not all_cmds:
        return findings
    # Fast-exit: env_prereqs present or preflight referenced -> no finding.
    if _extract_env_prereqs(text):
        return findings
    if _has_preflight_ref(text):
        return findings
    for cmd in all_cmds:
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


def _is_whole_suite_command(cmd: str) -> bool:
    """True if *cmd* runs a pre-existing whole test suite (no file scope)."""
    cmd_stripped = cmd.strip()
    if not _WHOLE_SUITE_CMD_RE.search(cmd_stripped):
        return False
    # For pytest/vitest/jest: any non-flag positional arg scopes the run
    # (a file path, a directory, a node id).  Only truly bare invocations
    # (nothing but flags after the runner) count as whole-suite.
    if re.search(r"\b(?:pytest|py\.test|vitest|jest)\b", cmd_stripped, re.IGNORECASE):
        tokens = cmd_stripped.split()
        positional = [
            t for t in tokens[1:]  # skip the runner itself
            if not t.startswith("-") and t not in _NON_PATH_TOKENS
        ]
        if positional:
            return False  # has a positional arg → scoped
    return True


def lint_wholesuite_gate_baseline(text: str, slug: str) -> list[str]:
    """Flag a whole-suite local_check that lacks a baseline-green note."""
    findings: list[str] = []
    commands = _extract_local_checks_commands(text)
    body = _strip_frontmatter(text)
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
    commands = _extract_local_checks_commands(text)
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
    commands = _extract_local_checks_commands(text)
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
)


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
    parser.add_argument("paths", nargs="+", help="Sub-plan .md file(s) to lint.")
    parser.add_argument("--master", help="MASTER plan file (enables slug-collision check).")
    args = parser.parse_args()

    master_text = ""
    if args.master:
        master_text = Path(args.master).read_text(encoding="utf-8-sig")

    total = 0
    for path in args.paths:
        for msg in lint_file(path, master_text=master_text):
            print(f"WARN: {msg}")
            total += 1
    if total == 0:
        print("OK: plan_lint clean")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
