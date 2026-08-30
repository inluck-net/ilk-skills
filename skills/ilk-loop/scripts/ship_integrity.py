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
import subprocess
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

    # Gate declared + result recorded → check isolation first.
    if not last_gate_result.get("isolated", True) and last_gate_result.get("dirty_paths", 0) > 0:
        n = last_gate_result["dirty_paths"]
        return ShipVerdict(
            ok=False,
            reason=f"unisolated gate — {n} uncommitted paths, result does not describe the commit",
        )

    # Then check all_passed.
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
        collect_declared_local_checks as _collect_checks,
        read_text as _read_text,
    )

    full = _read_text(path)
    fm_text, doc_body = _split_fm(full)

    # Extract status from frontmatter.
    status = ""
    for line in fm_text.splitlines():
        s = line.strip()
        if s.startswith("status:"):
            status = s[len("status:"):].strip().strip("'\"")
            break

    # Per-step gates count as declared gates; a frontmatter-only read made
    # evaluate_ship take the "no gate declared" branch and enforce nothing.
    checks = _collect_checks(fm_text, doc_body)
    return status, checks


def _missing_step_reason(subplan: Path) -> str | None:
    """Reason string when a *shipped* sub-plan lacks a commit for some step.

    Returns None when the sub-plan is not shipped, when every authored step
    has a commit, or when the check cannot run.

    Delegates counting to ``ship_audit`` — ``count_authored_steps`` and
    ``check_step_commits`` — which already handle the ``#ship`` allowance
    correctly (it satisfies the LAST authored step only; a gap earlier in
    the sequence is still a gap).  A second implementation of "which steps
    are done" would drift from the release audit's, which is the
    multiple-readers failure decomposition-principles §8 documents.

    Fails OPEN on any internal error, but says so on stderr.  A guard that
    blocks a legitimate ship because of its own parse bug is worse than the
    gap it closes — and an over-broad ship-integrity enforcement is what
    reverted 69 of 150 sub-plans on 2026-08-20.  Silent fail-open is the
    thing being fixed here, so the degradation is announced.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from run_local_checks import (  # type: ignore[import-untyped]
            read_text as _read_text,
            split_frontmatter as _split_fm,
        )
        from ship_audit import (  # type: ignore[import-untyped]
            check_step_commits,
            count_authored_steps,
        )

        fm_text, body = _split_fm(_read_text(subplan))

        status = ""
        slug = ""
        for line in fm_text.splitlines():
            s = line.strip()
            if s.startswith("status:") and not status:
                status = s[len("status:"):].strip().strip("'\"")
            elif s.startswith("plan:") and not slug:
                slug = s[len("plan:"):].strip().strip("'\"")

        if status != "shipped" or not slug:
            return None

        # `check_step_commits` collapses "git errored" into "every step is
        # missing" — correct for the release audit, which always runs inside
        # the repo, but as a LOOP gate it would block every ship wherever git
        # cannot answer (no repo, git absent).  That is the over-broad
        # enforcement shape that reverted 69 of 150 sub-plans on 2026-08-20,
        # so confirm git can answer before trusting a "missing" verdict.
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=Path.cwd(), encoding="utf-8",
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            print(
                "warning: not inside a git work tree; step-commit check "
                "skipped and ship-integrity fell back to the gate check alone",
                file=sys.stderr,
            )
            return None

        authored = count_authored_steps(body)
        if not authored:
            return None
        _present, missing = check_step_commits(slug, authored, cwd=Path.cwd())
        if not missing:
            return None
        word = "step" if len(missing) == 1 else "steps"
        return (
            f"missing commit for {word} "
            f"{', '.join(str(s) for s in missing)} — `shipped` is not backed "
            f"by the work ({len(authored) - len(missing)} of {len(authored)} "
            f"authored steps committed)"
        )
    except Exception as exc:  # noqa: BLE001 - fail open, but loudly
        print(
            f"warning: step-commit check could not run ({exc}); "
            "ship-integrity fell back to the gate check alone",
            file=sys.stderr,
        )
        return None


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
    ap.add_argument(
        "--gate-passed",
        choices=["true", "false", "unknown"],
        default=None,
        help="scalar gate outcome — robust alternative to --gate-json that avoids "
             "shell quote-mangling. 'unknown' means no gate result recorded. "
             "Takes precedence over --gate-json when given.",
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

    # Resolve gate result. --gate-passed (scalar) takes precedence — it avoids
    # the shell quote-mangling that breaks JSON args passed PS->python.exe.
    gate_result: dict[str, Any] | None
    if args.gate_passed is not None:
        gate_result = None if args.gate_passed == "unknown" else {"all_passed": args.gate_passed == "true"}
    else:
        try:
            gate = json.loads(args.gate_json)
            if gate is None:
                gate_result = None
            elif isinstance(gate, dict):
                gate_result = gate
            else:
                print(f"error: --gate-json must be a JSON object or null, got {type(gate).__name__}", file=sys.stderr)
                return 2
        except json.JSONDecodeError as exc:
            print(f"error: invalid --gate-json: {exc}", file=sys.stderr)
            return 2

    verdict = evaluate_ship(status, checks, gate_result)

    # Step-commit half.  evaluate_ship only asks "was the gate green?", so a
    # sub-plan that ran two of four steps and went green on the second was
    # reported honest — measured on three sub-plans in the 2026-08-26 batch.
    # Step counting already exists in ship_audit; reuse it rather than
    # reimplement, so the loop and the release audit cannot disagree about
    # which steps are done.
    step_reason = _missing_step_reason(args.subplan) if args.subplan else None

    reasons = [r for r in (step_reason, None if verdict.ok else verdict.reason) if r]
    if reasons:
        print(f"VIOLATION: {'; '.join(reasons)}", file=sys.stderr)
        return 1
    print(f"OK: {verdict.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
