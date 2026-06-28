#!/usr/bin/env python3
"""
ship_integrity.py — pure validator: shipped-vs-gate honesty check.

Given a sub-plan's status, its declared local_checks, and the last gate
outcome, decide whether a `shipped` status is honest (gate green) or a
violation (gate red).

Pure logic — ``evaluate_ship`` has no side effects.  The CLI reads a
sub-plan file on disk; the runner calls ``evaluate_ship`` directly.

Usage (CLI, file mode):
  python ship_integrity.py --subplan path/to/subplan.md \
      --gate-json '{"all_passed":true}'

Usage (CLI, arg mode):
  python ship_integrity.py --status shipped \
      --checks-json '[{"command":"pytest -q","timeout":120}]' \
      --gate-json '{"all_passed":true}'

Exit 0 if honest, exit 1 if violation, exit 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShipVerdict:
    ok: bool
    reason: str


def evaluate_ship(
    subplan_status: str,
    declared_checks: list[dict[str, Any]],
    last_gate_result: dict[str, Any] | None,
) -> ShipVerdict:
    """Decide whether a shipped status is honest given the gate outcome.

    Parameters
    ----------
    subplan_status : str
        The sub-plan's ``status`` field (``"shipped"``, ``"pending"``, etc.).
    declared_checks : list[dict]
        The sub-plan's top-level ``local_checks`` entries (each has at least
        ``command``).  An empty list means no gate was declared.
    last_gate_result : dict | None
        The runner's last gate result dict (``{"all_passed": bool, ...}``).
        ``None`` means no gate was recorded (treated as green when no checks
        are declared, violation when checks are declared).

    Returns
    -------
    ShipVerdict
        ``ok=True`` if the ship is honest; ``ok=False`` with a reason string
        naming the violation otherwise.
    """
    # Only enforce on shipped sub-plans.
    if subplan_status != "shipped":
        return ShipVerdict(ok=True, reason="not shipped — no gate to enforce")

    # No declared gate → nothing to enforce (no-gate sub-plan).
    if not declared_checks:
        return ShipVerdict(ok=True, reason="no gate declared — nothing to enforce")

    # Gate declared but no result recorded → violation (gate was never run).
    if last_gate_result is None:
        return ShipVerdict(
            ok=False,
            reason="gate declared but no gate result recorded — ship is dishonest",
        )

    # Gate declared + result recorded → check all_passed.
    if not last_gate_result.get("all_passed", False):
        # Try to name the failing checks for a clearer message.
        failing = [
            r.get("command", "?")
            for r in last_gate_result.get("results", [])
            if not r.get("passed", False)
        ]
        detail = "; ".join(failing) if failing else "(unknown)"
        return ShipVerdict(
            ok=False,
            reason=f"gate is red — ship blocked. Failing checks: {detail}",
        )

    return ShipVerdict(ok=True, reason="gate green — ship is honest")


# ── sub-plan file reader ─────────────────────────────────────────────────────

_FRONTMATTER_RE = __import__("re").compile(r"^---\s*\n(.*?)\n---\s*\n", __import__("re").DOTALL)


def read_subplan_status_and_checks(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Extract ``status`` and top-level ``local_checks`` from a sub-plan file.

    Returns ``(status, declared_checks)``.  Uses the same frontmatter parser
    as ``run_local_checks.py``.
    """
    # Import the heavyweight parser only when reading files (CLI --subplan).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_local_checks import (
        split_frontmatter as _split_fm,
        parse_local_checks_block as _parse_checks,
        read_text as _read_text,
    )

    body = _read_text(path)
    fm_text, _ = _split_fm(body)

    # Extract status from frontmatter.
    status = ""
    for line in fm_text.splitlines():
        s = line.strip()
        if s.startswith("status:"):
            status = s[len("status:"):].strip().strip("'\"")
            break

    checks = _parse_checks(fm_text)
    return status, checks


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Validate ship-integrity: shipped sub-plan must have green gate.",
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument(
        "--subplan",
        type=Path,
        help="path to the sub-plan .md file (reads status + local_checks from it)",
    )
    group.add_argument(
        "--status",
        help="sub-plan status field (use with --checks-json)",
    )
    ap.add_argument(
        "--checks-json",
        default="[]",
        help="JSON array of declared local_checks (each has 'command')",
    )
    ap.add_argument(
        "--gate-json",
        default="null",
        help="JSON object of last gate result (null if none)",
    )
    args = ap.parse_args(argv)

    # Resolve status + checks from file or explicit args.
    if args.subplan:
        try:
            status, checks = read_subplan_status_and_checks(args.subplan)
        except FileNotFoundError:
            print(f"error: sub-plan file not found: {args.subplan}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: failed to read sub-plan: {exc}", file=sys.stderr)
            return 2
    else:
        status = args.status or ""
        try:
            checks = json.loads(args.checks_json)
        except json.JSONDecodeError as exc:
            print(f"error: invalid --checks-json: {exc}", file=sys.stderr)
            return 2

    # Parse gate result.
    try:
        gate = json.loads(args.gate_json)
        if gate is None:
            gate_result: dict[str, Any] | None = None
        elif isinstance(gate, dict):
            gate_result = gate
        else:
            print(f"error: --gate-json must be a JSON object or null, got {type(gate).__name__}", file=sys.stderr)
            return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid --gate-json: {exc}", file=sys.stderr)
        return 2

    verdict = evaluate_ship(status, checks, gate_result)
    if verdict.ok:
        print(f"OK: {verdict.reason}")
        return 0
    else:
        print(f"VIOLATION: {verdict.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
