#!/usr/bin/env python3
"""
ship_audit.py — pure predicate: did every step commit, and was the gate green?

Composes with ``ship_integrity.py`` for the gate half and adds the step-commit
half that ``ship_integrity.py`` lacks.  No side effects — importable and
unit-testable.

Returns per-sub-plan:
  {proven: bool, missing_steps: [int], final_gate: str|None, reasons: [str]}

Usage (CLI):
  python ship_audit.py --subplan path/to/subplan.md [--gate-passed true|false|unknown]

Exit 0 if proven, exit 1 if unproven, exit 2 on bad input.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── pure helpers ──────────────────────────────────────────────────────────────

_STEP_HEADING_RE = re.compile(r"^### Step (\d+)", re.MULTILINE)


def count_authored_steps(body: str) -> list[int]:
    """Return sorted list of step numbers from ``### Step N`` headings.

    Uses the sub-plan body, NOT ``estimated_steps`` (which the agent also
    authors and which already disagrees with reality).
    """
    return sorted(int(m) for m in _STEP_HEADING_RE.findall(body))


def check_step_commits(
    slug: str,
    expected_steps: list[int],
    cwd: Path | None = None,
) -> tuple[list[int], list[int]]:
    """Check which steps have a commit trailer in git history.

    Searches the **full** commit message (``%s%n%b``) so body-placed trailers
    count — a subject-only predicate is the one failure mode that would revert
    correct work (AC-6).

    Returns ``(present, missing)`` — both lists of step numbers.
    """
    if not expected_steps:
        return [], []

    try:
        result = subprocess.run(
            ["git", "log", "--format=%s%n%b", "--all"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if result.returncode != 0:
            # git error — treat all as missing (not a false positive)
            return [], list(expected_steps)
    except FileNotFoundError:
        return [], list(expected_steps)

    # Match [plan:<slug>#step-N] or [plan:<slug>#step-N,step-M,...]
    # The trailer may be embedded in the subject line.
    trailer_re = re.compile(
        rf"\[plan:{re.escape(slug)}#((?:step-\d+(?:,step-\d+)*))\]"
    )
    committed = set()
    for line in result.stdout.splitlines():
        for m in trailer_re.finditer(line):
            for part in m.group(1).split(","):
                part = part.strip()
                if part.startswith("step-"):
                    try:
                        committed.add(int(part[5:]))
                    except ValueError:
                        pass

    # The template mandates that the FINAL step commits with
    # ``[plan:<slug>#ship]`` rather than ``#step-N`` (subplan-template.md:
    # "### Step N — E2E + handoff … chore(plans): <slug> shipped
    # [plan:<slug>#ship]").  A template-conformant sub-plan must audit PROVEN,
    # so a #ship trailer satisfies the last declared step -- and only that one:
    # a gap earlier in the sequence is still a gap.
    ship_re = re.compile(rf"\[plan:{re.escape(slug)}#ship\]")
    if ship_re.search(result.stdout):
        committed.add(max(expected_steps))

    present = [s for s in expected_steps if s in committed]
    missing = [s for s in expected_steps if s not in committed]
    return present, missing


# ── compose with ship_integrity for the gate half ────────────────────────────

def _evaluate_gate(
    status: str,
    declared_checks: list[dict[str, Any]],
    gate_passed: str,
) -> tuple[str | None, str | None]:
    """Run the gate half via ``ship_integrity.evaluate_ship``.

    Returns ``(gate_verdict, reason)`` where verdict is ``None`` for no-gate
    sub-plans, ``"pass"`` or ``"fail"`` otherwise.
    """
    from ship_integrity import evaluate_ship

    if not declared_checks:
        return None, None

    gate_result: dict[str, Any] | None
    if gate_passed == "unknown":
        gate_result = None
    else:
        gate_result = {"all_passed": gate_passed == "true"}

    verdict = evaluate_ship(status, declared_checks, gate_result)
    if verdict.ok:
        return "pass", None
    return "fail", verdict.reason


# ── main predicate ────────────────────────────────────────────────────────────

def audit_ship(
    status: str,
    body: str,
    declared_checks: list[dict[str, Any]],
    gate_passed: str,
    slug: str,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Audit a shipped sub-plan: step commits + gate outcome.

    Parameters
    ----------
    status : str
        The sub-plan's ``status`` field.
    body : str
        The sub-plan body (after frontmatter).
    declared_checks : list[dict]
        Top-level ``local_checks`` entries.
    gate_passed : str
        ``"true"``, ``"false"``, or ``"unknown"``.
    slug : str
        The sub-plan slug (``plan:`` value).
    cwd : Path | None
        Working directory for ``git log``.  ``None`` = current dir.

    Returns
    -------
    dict
        ``{proven, missing_steps, final_gate, reasons}``
    """
    # Only audit shipped sub-plans.
    if status != "shipped":
        return {
            "proven": True,
            "missing_steps": [],
            "final_gate": None,
            "reasons": [],
        }

    authored = count_authored_steps(body)
    present, missing = check_step_commits(slug, authored, cwd=cwd)

    # Gate half (AC-8: exempt no-gate sub-plans from gate check only).
    gate_verdict, gate_reason = _evaluate_gate(status, declared_checks, gate_passed)

    reasons: list[str] = []
    if missing:
        step_word = "step" if len(missing) == 1 else "steps"
        reasons.append(
            f"missing commit for {step_word} {', '.join(str(s) for s in missing)}"
        )
    if gate_verdict == "fail":
        reasons.append(gate_reason or "gate is red")

    proven = not missing and gate_verdict != "fail"
    final_gate: str | None
    if declared_checks:
        final_gate = gate_verdict  # "pass" or "fail"
    else:
        final_gate = None

    return {
        "proven": proven,
        "missing_steps": missing,
        "final_gate": final_gate,
        "reasons": reasons,
    }


# ── sub-plan file reader ─────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_subplan_for_audit(path: Path) -> dict[str, Any]:
    """Read a sub-plan file and extract everything ``audit_ship`` needs.

    Returns a dict with ``status``, ``body``, ``declared_checks``,
    ``slug``, suitable for passing to ``audit_ship``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_local_checks import (
        split_frontmatter as _split_fm,
        collect_declared_local_checks as _collect_checks,
        read_text as _read_text,
    )

    full = _read_text(path)
    fm_text, body = _split_fm(full)

    # Extract status + slug from frontmatter.
    status = ""
    slug = ""
    for line in fm_text.splitlines():
        s = line.strip()
        if s.startswith("status:") and not status:
            status = s[len("status:"):].strip().strip("'\"")
        elif s.startswith("plan:") and not slug:
            slug = s[len("plan:"):].strip().strip("'\"")

    # Frontmatter gates + per-step gates, via the single oracle in
    # run_local_checks.  Do not re-parse here: the audit must not disagree
    # with what the driver actually ran.
    checks = _collect_checks(fm_text, body)
    return {"status": status, "body": body, "declared_checks": checks, "slug": slug}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Audit a shipped sub-plan: step commits + gate outcome.",
    )
    ap.add_argument(
        "--subplan",
        type=Path,
        required=True,
        help="path to the sub-plan .md file",
    )
    ap.add_argument(
        "--gate-passed",
        choices=["true", "false", "unknown"],
        default="unknown",
        help="scalar gate outcome (default: unknown)",
    )
    args = ap.parse_args(argv)

    try:
        info = read_subplan_for_audit(args.subplan)
    except FileNotFoundError:
        print(f"error: sub-plan file not found: {args.subplan}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: failed to read sub-plan: {exc}", file=sys.stderr)
        return 2

    result = audit_ship(
        status=info["status"],
        body=info["body"],
        declared_checks=info["declared_checks"],
        gate_passed=args.gate_passed,
        slug=info["slug"],
    )

    if result["proven"]:
        print(f"PROVEN: {result}")
        return 0
    else:
        print(f"UNPROVEN: {result}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
