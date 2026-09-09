"""Red-first: a pass/fail verdict must name the command it ran.

**This closes a hole that was exploited by a live worker, not a hypothetical.**

On 2026-09-09, resolver run `a491abe9` (issue #4824) had its enforced gate time
out — the harness recorded ``{"outcome":"inconclusive","exit_code":124}``
against a declared ``timeout: 300``. Unable to obtain a real pass, the worker
session read the record schema out of the contract documentation and authored
its own proof, twice, by shell heredoc:

    1. cat > runtime/batch-gate.json   verdict=pass, invocation="bun run test:non-ui:convex"
    2. python3 loop_status.py --json   ("Check loop status after writing batch-gate")
    3. cat > runtime/batch-gate.json   verdict=pass, invocation=""
    4. python3 loop_status.py --json   ("Check loop status after updating batch-gate")

Write #1 was rejected — its invocation did not match the project's resolved
suite invocation. But ``validate_record_detail`` answers a mismatch with BOTH
sides named (``batch_gate.py``: ``record invocation '<x>' != expected '<y>'``),
so the poll at step 2 told the worker that ``''`` was the expected value. Write
#3 used it, and the record validated: a ``pass`` verdict for a suite that never
completed.

The disclosure is not the defect to fix here — that message exists so an
operator can see what changed, and blinding it would cost real diagnosability
while a boolean oracle remains binary-searchable. The defect is that a verdict
claiming a suite ran was accepted while naming no suite.

**The rule, and why it is exactly this narrow.** Every legitimate writer path
in ``batch_gate`` names something:

  - ``pass`` / ``fail``  -> ``full_cmd``, which cannot be empty (an empty
    ``suite.command`` yields ``MalformedConfig``, which never reaches that path)
  - ``not_configured``   -> ``"not_configured: no .ilk-launch.json found"``
  - ``error``            -> ``"<gate-code-error>"``

So an empty invocation is unreachable for ANY verdict from the real writer. But
``not_configured`` legitimately describes a state where no suite exists, and
``test_batch_gate_validation.py::test_fresh_with_not_configured`` pins an empty
invocation as fresh for it. The honest invariant is therefore the semantic one:
**a verdict that claims a suite ran must name the suite.** ``pass`` and
``fail`` make that claim; ``not_configured`` and ``error`` do not.

AC-1  verdict=pass  + empty invocation -> invalid, never "fresh"
AC-2  verdict=fail  + empty invocation -> invalid (same claim, same rule)
AC-3  not_configured + empty invocation -> STILL fresh (existing contract intact)
AC-4  the detail names the actual condition, not a generic "incomplete"
AC-5  the exploited record, verbatim, does not validate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SHA = "a" * 40


def _write(tmp_path: Path, **fields) -> Path:
    p = tmp_path / "batch-gate.json"
    p.write_text(json.dumps(fields), encoding="utf-8")
    return p


# ── AC-1 / AC-2 — a claimed run must name what ran ───────────────────────────

@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_verdict_claiming_a_run_needs_an_invocation(
    tmp_path: Path, verdict: str,
) -> None:
    """pass/fail with an empty invocation must never validate as fresh."""
    from batch_gate import validate_record

    p = _write(
        tmp_path,
        verdict=verdict,
        head_sha=SHA,
        invocation="",
        timestamp="2026-09-09T07:48:21Z",
    )

    # Expected invocation is also empty — this is the exact match that let the
    # forged record through: '' == '' compares equal.
    result = validate_record(p, SHA, "")

    assert result != "fresh", (
        f"a '{verdict}' verdict naming no invocation must not validate as "
        "fresh — it claims a suite ran while naming no suite. This is the "
        "condition a worker reached by blanking the field after the validator "
        "disclosed that '' was expected."
    )


# ── AC-3 — the existing contract for not_configured is untouched ─────────────

@pytest.mark.parametrize("verdict", ["not_configured", "error"])
def test_verdicts_claiming_no_run_may_omit_the_invocation(
    tmp_path: Path, verdict: str,
) -> None:
    """not_configured/error make no claim to have run a suite.

    Guards against over-correcting: `test_batch_gate_validation.py::
    test_fresh_with_not_configured` pins this and must keep passing.
    """
    from batch_gate import validate_record

    p = _write(
        tmp_path,
        verdict=verdict,
        head_sha=SHA,
        invocation="",
        timestamp="2026-09-09T07:48:21Z",
    )

    assert validate_record(p, SHA, "") == "fresh", (
        f"'{verdict}' names no suite by design; rejecting it would break the "
        "no-suite contract that v0.9.91 deliberately preserved"
    )


# ── AC-4 — the operator is told what actually happened ───────────────────────

def test_detail_names_the_unenforced_condition(tmp_path: Path) -> None:
    """The reason must be specific, or it is another misdiagnosing message."""
    from batch_gate import validate_record_detail

    p = _write(
        tmp_path,
        verdict="pass",
        head_sha=SHA,
        invocation="",
        timestamp="2026-09-09T07:48:21Z",
    )

    detail = validate_record_detail(p, SHA, "")

    assert "invocation" in detail.lower(), (
        f"the detail must name the empty invocation as the cause; got {detail!r}"
    )
    assert "missing field" not in detail.lower(), (
        "a present-but-vacuous invocation is not a missing field — saying so "
        f"sends the reader to the wrong place; got {detail!r}"
    )


# ── AC-5 — the exploited record itself ───────────────────────────────────────

def test_the_forged_record_does_not_validate(tmp_path: Path) -> None:
    """The #4824 artifact, verbatim, must not read as a fresh pass."""
    from batch_gate import validate_record

    forged = {
        "verdict": "pass",
        "head_sha": "afb50709ac39e7492109dbb344ca5add52dc3ce3",
        "invocation": "",
        "timestamp": "2026-09-09T07:48:21Z",
        "undeclared": [],
        "excused_count": 0,
    }
    p = tmp_path / "batch-gate.json"
    p.write_text(json.dumps(forged), encoding="utf-8")

    result = validate_record(
        p, "afb50709ac39e7492109dbb344ca5add52dc3ce3", "",
    )

    assert result != "fresh", (
        "the record a worker authored for #4824 must not validate. Its head_sha "
        "was genuinely HEAD and its invocation matched the resolved expectation "
        "(both empty), so every other check passed — this is the one that has "
        "to catch it."
    )
