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
import json
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

def _resolve_expected_invocation(project_path: Path) -> str:
    """Build the expected invocation from ship.suite.

    Reuses the same construction path as batch_gate._run_gate_inner so
    the validator and the writer cannot drift (AC-3).
    """
    try:
        _scripts_dir = str(Path(__file__).resolve().parent)
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        # batch_gate._skill_root() resolves the skills/ directory
        from batch_gate import _skill_root  # type: ignore[import-untyped]
        sys_path_backup = list(sys.path)
        try:
            sys.path.insert(0, str(_skill_root() / "ilk-ship" / "scripts"))
            from ship_config import NotConfigured, load_ship_config  # type: ignore[import-untyped]
        finally:
            sys.path[:] = sys_path_backup
        config = load_ship_config(project_path)
        if isinstance(config, NotConfigured):
            return ""
        invocation = config.ship["suite"]["command"]
        flags = config.ship["suite"].get("flags", [])
        return invocation if not flags else f"{invocation} {' '.join(flags)}"
    except (ImportError, FileNotFoundError, KeyError):
        return ""


def _resolve_batch_record(
    runtime_dir: Path | None,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """Read the persisted batch-gate verdict using SP2's validator.

    Returns ``(gate_verdict, reason)`` where verdict is one of:
    ``"pass"``, ``"fail"``, ``"stale_head"``, ``"stale_invocation"``,
    ``"absent"``, ``"incomplete"``, or ``None`` (no runtime_dir supplied).

    AC-1: reads the verdict from the record.
    AC-2: stale / invalid / absent each its own outcome (validator vocabulary).
    AC-3: reuses batch_gate.validate_record — no second staleness implementation.
    """
    if runtime_dir is None:
        return None, None  # no record available — fall back to legacy path

    try:
        _scripts_dir = str(Path(__file__).resolve().parent)
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        from batch_gate import (  # type: ignore[import-untyped]
            record_path, validate_record, validate_record_detail,
        )
    except ImportError:
        return None, None

    rp = record_path(runtime_dir)

    # Resolve expected head and invocation for the validator.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        current_head = result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        current_head = ""

    project_path = cwd or Path.cwd()
    expected_invocation = _resolve_expected_invocation(project_path)

    # AC-3: delegate to SP2's validator.
    outcome = validate_record(rp, current_head, expected_invocation)

    if outcome == "fresh":
        # Record is trustworthy — read its verdict.
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
            verdict = data.get("verdict", "fail") if isinstance(data, dict) else "fail"
        except (OSError, json.JSONDecodeError):
            verdict = "fail"
        if verdict == "pass":
            return "pass", None
        return "fail", f"batch gate recorded: {verdict}"

    # AC-2: stale_head / stale_invocation / incomplete / absent — refuse.
    #
    # The five-word outcome is the machine-readable verdict; the reason uses
    # validate_record_detail so the operator is told WHICH field is missing or
    # WHICH sha mismatched.  "batch-gate record is incomplete" names a class
    # and leaves them to go find the instance.
    detail = validate_record_detail(rp, current_head, expected_invocation)
    return outcome, f"batch-gate record is {detail}"


def _evaluate_gate(
    status: str,
    declared_checks: list[dict[str, Any]],
    gate_passed: str,
    runtime_dir: Path | None = None,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """Run the gate half via ``ship_integrity.evaluate_ship``.

    Returns ``(gate_verdict, reason)`` where verdict is ``None`` for no-gate
    sub-plans, ``"pass"`` or ``"fail"`` for trusted records, or one of
    ``"stale_head"``, ``"stale_invocation"``, ``"incomplete"``, ``"absent"``
    for untrusted records (validator vocabulary from SP2).

    When *runtime_dir* is provided, reads the persisted batch-gate verdict
    via SP2's ``batch_gate.validate_record`` (AC-1 through AC-3).  Falls
    back to the legacy *gate_passed* argument only when no record exists
    or no runtime_dir is supplied (AC-5).
    """
    from ship_integrity import evaluate_ship

    if not declared_checks:
        return None, None

    # AC-4: explicit --gate-passed overrides the record.
    if gate_passed in ("true", "false"):
        override_reason = (
            f"gate verdict overridden to {gate_passed} "
            f"(manual --gate-passed flag)"
        )
        if gate_passed == "true":
            return "pass", override_reason
        return "fail", override_reason

    # AC-1..3: no explicit override — use the validated record.
    record_verdict, record_reason = _resolve_batch_record(runtime_dir, cwd=cwd)

    if record_verdict is not None:
        # We have a record (or a named absence).
        if record_verdict == "pass":
            return "pass", None
        if record_verdict == "fail":
            return "fail", record_reason
        # stale_head / stale_invocation / incomplete / absent — refuse.
        return record_verdict, record_reason

    # AC-5: no record available, no override — no gate result recorded.
    gate_result: dict[str, Any] | None = None

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
    runtime_dir: Path | None = None,
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
    runtime_dir : Path | None
        Path to the project's runtime directory.  When provided,
        ``ship_audit`` reads the persisted batch-gate verdict from it
        instead of relying on the ``gate_passed`` argument.

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
    gate_verdict, gate_reason = _evaluate_gate(
        status, declared_checks, gate_passed,
        runtime_dir=runtime_dir, cwd=cwd,
    )

    reasons: list[str] = []
    if missing:
        step_word = "step" if len(missing) == 1 else "steps"
        reasons.append(
            f"missing commit for {step_word} {', '.join(str(s) for s in missing)}"
        )
    if gate_verdict == "fail":
        reasons.append(gate_reason or "gate is red")
    elif gate_verdict in ("stale_head", "stale_invocation", "incomplete", "absent"):
        reasons.append(gate_reason or f"gate is {gate_verdict}")

    proven = not missing and gate_verdict in (None, "pass")
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
    ap.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="where the batch-gate record lives (default: resolved from the "
             "project via batch_gate.resolve_runtime_dir)",
    )
    ap.add_argument(
        "--project",
        type=Path,
        default=None,
        help="project root (default: cwd) — used to resolve the runtime dir "
             "and to search git history for step commits",
    )
    args = ap.parse_args(argv)

    project = (args.project or Path.cwd()).resolve()

    # D6: without this the CLI passed runtime_dir=None to audit_ship, so
    # _resolve_batch_record short-circuited and EVERY shipped sub-plan read as
    # "gate declared but no gate result recorded" — regardless of what the
    # gate had actually written.  SP3 wired the library; the CLI never
    # reached it.  Resolve through the same function the writer uses (D5).
    runtime_dir = args.runtime_dir
    if runtime_dir is None:
        try:
            _scripts_dir = str(Path(__file__).resolve().parent)
            if _scripts_dir not in sys.path:
                sys.path.insert(0, _scripts_dir)
            from batch_gate import (  # type: ignore[import-untyped]
                resolve_runtime_dir,
            )
            runtime_dir = resolve_runtime_dir(project)
        except ImportError:
            runtime_dir = None

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
        cwd=project,
        runtime_dir=runtime_dir,
    )

    if result["proven"]:
        print(f"PROVEN: {result}")
        return 0
    else:
        print(f"UNPROVEN: {result}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
