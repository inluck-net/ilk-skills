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


ALL_CHECKS = (lint_envprereq_fallback_contradiction, lint_block_when_default_exists)


def lint_file(path: str | Path) -> list[str]:
    """Run all checks against one sub-plan file. Returns finding messages."""
    p = Path(path)
    slug = p.stem
    text = p.read_text(encoding="utf-8-sig")
    findings: list[str] = []
    for check in ALL_CHECKS:
        findings.extend(check(text, slug))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Planner degrade-discipline lints.")
    parser.add_argument("paths", nargs="+", help="Sub-plan .md file(s) to lint.")
    args = parser.parse_args()

    total = 0
    for path in args.paths:
        for msg in lint_file(path):
            print(f"WARN: {msg}")
            total += 1
    if total == 0:
        print("OK: plan_lint clean")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
