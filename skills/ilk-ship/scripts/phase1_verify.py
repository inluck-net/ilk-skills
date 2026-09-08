"""Phase 1 verification — refuse rather than substitute.

Implements the refusal contract from sub-plan
``phase-1-refuses-rather-than-substitutes``: a missing, stale or failed
batch-gate verdict, or a ``could_not_compare`` baseline, halts and files.
It does not ship, and it does not accept an assembled alternative.

This is deliberately stricter than what a human release does today.  A human
may knowingly proceed on substitute evidence and label it; that judgment is
not available to an unattended run.

v0.9.86 and v0.9.87 both shipped on substitute evidence — the batch verdict
held the tip of a batch released five tags earlier, and no baseline existed
for the previous tag on that host.  Each release proceeded on evidence
assembled and labelled by the same agent doing the release.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from batch_gate import (
    BatchGateRecord,
    record_path,
    validate_record_detail,
)
from baseline_diff import BaselineReport


# ── Result type ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Phase1Verdict:
    """Result of Phase 1 verification.

    ``action`` is ``"proceed"`` or ``"refuse"``.
    ``engine`` names which engine caused the refusal (``"batch_verdict"``
    or ``"baseline"``), or ``""`` when proceeding.
    ``filed`` is True when a refusal artifact was written to disk — a
    refusal that only prints is not a refusal an unattended pipeline
    can act on.
    """
    action: str
    reason: str
    engine: str
    filed: bool


# ── Refusal artifact ────────────────────────────────────────────────────────

def _file_refusal(runtime_dir: Path, reason: str, engine: str) -> None:
    """Write a refusal artifact to disk.

    An unattended pipeline needs a file it can act on — a refusal that
    only prints to stdout vanishes with the session.
    """
    artifact = runtime_dir / "phase1-refusal.json"
    data = {
        "action": "refuse",
        "engine": engine,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    artifact.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── Verification ────────────────────────────────────────────────────────────

def verify_phase1(
    runtime_dir: Path,
    expected_head_sha: str,
    expected_invocation: str,
    baseline_report: Optional[BaselineReport] = None,
) -> Phase1Verdict:
    """Verify Phase 1 preconditions and refuse if either engine is unavailable.

    Checks two engines in order:
      1. Batch verdict — must be present, fresh, and passing.
      2. Baseline diff — must not be ``could_not_compare``.

    Returns ``Phase1Verdict(action="proceed")`` only when both are healthy.
    On any refusal, writes a ``phase1-refusal.json`` artifact to
    ``runtime_dir`` so the unattended pipeline has a file to act on.

    Args:
        runtime_dir: where batch-gate.json and the refusal artifact live.
        expected_head_sha: the current HEAD the batch ran against.
        expected_invocation: the resolved ship.suite command.
        baseline_report: pre-computed baseline-diff result (optional; if
            None, baseline verification is skipped — the caller is
            responsible for running baseline_diff first).
    """
    # ── Engine 1: batch verdict ──────────────────────────────────────────
    verdict_path = record_path(runtime_dir)
    detail = validate_record_detail(
        verdict_path, expected_head_sha, expected_invocation,
    )

    if detail != "fresh":
        # Name the engine and the specific condition
        if detail.startswith("absent"):
            reason = f"batch verdict absent: {detail}"
        elif detail.startswith("stale_head"):
            reason = f"batch verdict stale: {detail}"
        elif detail.startswith("stale_invocation"):
            reason = f"batch verdict stale: {detail}"
        elif detail.startswith("incomplete"):
            reason = f"batch verdict incomplete: {detail}"
        else:
            reason = f"batch verdict not fresh: {detail}"

        _file_refusal(runtime_dir, reason, "batch_verdict")
        return Phase1Verdict(
            action="refuse",
            reason=reason,
            engine="batch_verdict",
            filed=True,
        )

    # The record is structurally fresh (sha + invocation match), but we
    # must still check the verdict value — a "fail" or "error" verdict
    # at the correct sha is still a refusal condition.
    if verdict_path.is_file():
        try:
            data = json.loads(verdict_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("verdict") in ("fail", "error"):
                reason = (
                    f"batch verdict recorded: {data['verdict']} — "
                    f"head_sha={data.get('head_sha', '?')[:7]}, "
                    f"invocation='{data.get('invocation', '?')}'"
                )
                _file_refusal(runtime_dir, reason, "batch_verdict")
                return Phase1Verdict(
                    action="refuse",
                    reason=reason,
                    engine="batch_verdict",
                    filed=True,
                )
        except (OSError, json.JSONDecodeError):
            pass  # validate_record_detail already checked this

    # ── Engine 2: baseline diff ──────────────────────────────────────────
    #
    # An engine that never RAN is not an engine that passed.  This guard read
    # `if baseline_report is not None and ...`, so a caller supplying no report
    # reached the same `proceed` as one whose baseline compared clean -- two
    # different states with one observable outcome, and the benign-looking one
    # was the default.
    #
    # Found 2026-09-09 by running /ilk-ship on this repo: the first
    # verification returned `proceed` having run engine 1 only, and would have
    # been reported as a verified Phase 1.  That is precisely the v0.9.86 /
    # v0.9.87 shape this module was written to prevent -- a release proceeding
    # on evidence that was never gathered -- reproduced inside the preventer.
    #
    # Fail closed, matching the batch-verdict engine above: no report means the
    # baseline engine did not run, which is a refusal, not a pass.  The caller
    # that wants to proceed must supply a report that compared.
    if baseline_report is None:
        reason = (
            "baseline engine did not run: no baseline report supplied — "
            "refusing rather than treating an ungathered engine as a passing "
            "one (a half-run Phase 1 must not report a whole one)"
        )
        _file_refusal(runtime_dir, reason, "baseline")
        return Phase1Verdict(
            action="refuse",
            reason=reason,
            engine="baseline",
            filed=True,
        )

    if baseline_report.diff.could_not_compare:
        reason = (
            f"baseline could_not_compare: no baseline for "
            f"{baseline_report.diff.ref.tag} — refusing rather than "
            f"substituting (regression_count is meaningless in this state)"
        )
        _file_refusal(runtime_dir, reason, "baseline")
        return Phase1Verdict(
            action="refuse",
            reason=reason,
            engine="baseline",
            filed=True,
        )

    # ── Both engines healthy ─────────────────────────────────────────────
    return Phase1Verdict(action="proceed", reason="", engine="", filed=False)
