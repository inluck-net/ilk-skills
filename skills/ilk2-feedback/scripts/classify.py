#!/usr/bin/env python3
"""Classify an ilk2 batch from its report JSON alone.

This is the ilk2-native classifier built *beside* V1's ``skills/ilk-feedback``
(docs/07-retiring-v1.md §7.4.3).  It answers one question — what does this
batch's own record say happened? — from the versioned ilk2 JSON alone: the
``attempt_records`` that ``ilk2 report --batch <id> --json`` carries
(report_version 2).  It never reads V1's run files under ``~/.ilk-data``, never
reads ilk2's run directories, and never touches the filesystem at all: both
arguments are already-parsed dicts.

Taxonomy subset (docs/07 §7.4.3), each from the JSON alone:

``timeout-bound``
    an attempt whose ``termination`` is ``timeout``.  A timed-out attempt is
    stored with ``outcome: "red"`` and ``termination: "timeout"`` (docs/07
    §7.4.1, finding 11), so this reads ``termination``, not ``outcome`` alone.
``stuck-no-progress``
    a judged attempt (not ``gates_first``, ``outcome`` not interrupted / api /
    quota / cancelled) that ended red, timeout or crashed with no edits.
``api-flaky`` / ``api-blocked``
    1-2 ``api`` attempts in the batch are flaky; 3 or more, or a batch that is
    blocked with an api reason, is blocked.  The two are exclusive.
``quota-exhausted``
    any ``quota`` attempt or termination.  Kept distinct from budget-exhausted,
    as V1 does.
``interrupted``
    any interrupted attempt.
``local-checks-stuck``
    one gate (same ``name`` and ``command_digest``) red on 3 or more attempts
    of one step.
``clean``
    none of the above, and every step accepted.

Null is unknown, never zero.  The request/timing fields are null on attempts
recorded before report_version 2; this classifier neither crashes on them nor
coerces them into a zero tally, and an attempt whose *edit* counts are unknown
is not charged with "no edits".

Usage::

    result = classify(report, status)   # status is optional (ilk2 status --json)
    result["labels"]     # list[str], in LABELS order
    result["evidence"]   # {label: [short strings naming the step/attempt]}
    result["notes"]      # list[str]
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["LABELS", "classify"]

#: The kept subset of V1's taxonomy (docs/07-retiring-v1.md §7.4.3), in the
#: order ``classify`` reports them.  ``api-flaky`` and ``api-blocked`` are
#: exclusive; ``clean`` never appears beside any other label.
LABELS = (
    "timeout-bound",
    "stuck-no-progress",
    "api-flaky",
    "api-blocked",
    "quota-exhausted",
    "interrupted",
    "local-checks-stuck",
    "clean",
)

# An attempt is "judged" -- and so can be stuck-no-progress -- only when it is
# not a gates-first measurement and its outcome is not one of these.  A
# gates-first attempt measures the base; it is not a verdict on the worker.
_NOT_JUDGED_OUTCOMES = frozenset({"interrupted", "api", "quota", "cancelled"})

# "ended red, timeout or crashed": the outcome values that say so, and the
# terminations that say so when the outcome is stored as "red".
_BAD_OUTCOMES = frozenset({"red", "timeout", "crashed"})
_BAD_TERMINATIONS = frozenset({"timeout", "crashed"})

# Gate outcomes that count as the gate going red.  "error" is included because
# a gate that cannot run on 3+ attempts of one step is as stuck as one that
# fails; "pass" and anything unrecognized are not red.
_RED_GATE_OUTCOMES = frozenset({"fail", "red", "error"})

# The request/timing fields report_version 2 adds.  A pre-v2 attempt carries
# them as null: unknown, never zero.
_TIMING_FIELDS = (
    "minutes",
    "requests",
    "long_requests",
    "long_request_min",
    "longest_request_min",
    "model_min",
    "tool_min",
    "first_edit_start_min",
    "first_edit_end_min",
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _step_label(step: Dict[str, Any], position: int) -> str:
    """Short name for a step: plan slug plus its index."""
    plan = step.get("plan")
    plan_s = plan if isinstance(plan, str) and plan else "?"
    idx = step.get("idx")
    if not isinstance(idx, int) or isinstance(idx, bool):
        idx = position
    return "%s step %d" % (plan_s, idx)


def _attempt_label(step_label: str, attempt: Dict[str, Any], position: int) -> str:
    """Short name for an attempt: the step plus its attempt number."""
    n = attempt.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        n = position
    return "%s attempt %d" % (step_label, n)


def _edits_total(edits: Any) -> Optional[int]:
    """Total edit count, or None when the record does not say.

    None is never coerced to 0.  An attempt whose edit counts are unknown
    cannot be charged with "no edits"; only an explicit empty (or zero) map
    can.
    """
    if not isinstance(edits, dict):
        return None
    total = 0
    for value in edits.values():
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if value < 0:
            return None
        total += value
    return total


def _api_tokens(text: Any) -> bool:
    """True when *text* names api as a word (``api``, ``api_error``, ...).

    A substring search would fire on "rapid"; tokens are split on
    non-alphanumerics so ``api_error`` and ``API-error`` both count and
    ``rapid`` does not.
    """
    if not isinstance(text, str) or not text:
        return False
    return "api" in re.split(r"[^a-z0-9]+", text.lower())


def _report_batch_id(report: Dict[str, Any]) -> Optional[str]:
    batch = report.get("batch")
    if isinstance(batch, str) and batch:
        return batch
    if isinstance(batch, dict):
        for key in ("id", "batch", "batch_id"):
            value = batch.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _refusal_api_reason(refusals: Any) -> Optional[str]:
    for refusal in _as_list(refusals):
        entry = _as_dict(refusal)
        for key in ("reason", "detail"):
            value = entry.get(key)
            if _api_tokens(value):
                return value if isinstance(value, str) else "api"
    return None


def _blocked_api_reason(
    status: Any, batch_id: Optional[str], report_refusals: Any
) -> Optional[str]:
    """The api reason if this batch is blocked for an api cause, else None.

    Drives ``api-blocked``'s second route (docs/07 §7.4.3): "or the batch
    blocked with an api reason".  Sources, in order: the batch's own
    ``blocked_reason`` / ``terminal_reason``, then its refusals, then the
    report's refusals -- only while the batch is actually blocked.
    """
    if not isinstance(status, dict):
        return None
    entries = [
        entry
        for entry in _as_list(status.get("batches"))
        if isinstance(entry, dict)
        and (batch_id is None or entry.get("id") == batch_id)
    ]
    for entry in entries:
        if entry.get("status") != "blocked":
            continue
        for key in ("blocked_reason", "terminal_reason"):
            value = entry.get(key)
            if _api_tokens(value):
                return value if isinstance(value, str) else "api"
        reason = _refusal_api_reason(entry.get("refusals"))
        if reason is not None:
            return reason
        reason = _refusal_api_reason(report_refusals)
        if reason is not None:
            return reason
    return None


def classify(report: dict, status: dict | None = None) -> dict:
    """Classify a batch from ``ilk2 report --batch --json`` alone.

    *status* is optional ``ilk2 status --batch --json``; it is consulted only
    for ``api-blocked``'s "the batch blocked with an api reason" route.  With
    *status* omitted, every label is decided from the report itself.
    """
    if not isinstance(report, dict):
        return {
            "labels": [],
            "evidence": {},
            "notes": ["report is not a dict; nothing classified"],
        }

    evidence: Dict[str, List[str]] = {}
    notes: List[str] = []

    def fire(label: str, entry: str) -> None:
        bucket = evidence.setdefault(label, [])
        if entry not in bucket:
            bucket.append(entry)

    api_attempts: List[str] = []
    unknown_timing: List[str] = []
    unknown_edits: List[str] = []

    steps = _as_list(report.get("steps"))
    all_accepted = True

    for position, raw_step in enumerate(steps, start=1):
        step = _as_dict(raw_step)
        step_label = _step_label(step, position)
        if step.get("status") != "accepted":
            all_accepted = False

        # gate identity -> attempt labels where that gate went red
        red_gates: Dict[Tuple[str, Any], List[str]] = {}
        attempts = _as_list(step.get("attempt_records"))

        for att_position, raw_attempt in enumerate(attempts, start=1):
            attempt = _as_dict(raw_attempt)
            attempt_label = _attempt_label(step_label, attempt, att_position)
            outcome = attempt.get("outcome")
            termination = attempt.get("termination")
            gates_first = bool(attempt.get("gates_first"))

            # timeout-bound: read termination, not only outcome.  An attempt
            # stored as outcome "timeout" is a timeout too.
            if termination == "timeout" or outcome == "timeout":
                fire("timeout-bound", attempt_label)

            # stuck-no-progress: a judged attempt that ended badly and made
            # no edits.
            if not gates_first and outcome not in _NOT_JUDGED_OUTCOMES:
                ended_bad = (
                    outcome in _BAD_OUTCOMES or termination in _BAD_TERMINATIONS
                )
                if ended_bad:
                    total = _edits_total(attempt.get("edits"))
                    if total == 0:
                        fire("stuck-no-progress", attempt_label)
                    elif total is None:
                        unknown_edits.append(attempt_label)

            # api / quota / interrupted signals
            if outcome == "api" or termination == "api":
                api_attempts.append(attempt_label)
            if outcome == "quota" or termination == "quota":
                fire("quota-exhausted", attempt_label)
            if outcome == "interrupted":
                fire("interrupted", attempt_label)

            # local-checks-stuck bookkeeping: same name + command_digest
            gates = _as_dict(attempt.get("gates"))
            for gate_name, raw_gate in gates.items():
                gate = _as_dict(raw_gate)
                if gate.get("outcome") in _RED_GATE_OUTCOMES:
                    key = (str(gate_name), gate.get("command_digest"))
                    red_gates.setdefault(key, []).append(attempt_label)

            # Any null/absent request-or-timing field is unknown, never zero.
            if any(attempt.get(field) is None for field in _TIMING_FIELDS):
                unknown_timing.append(attempt_label)

        for (gate_name, digest), where in red_gates.items():
            if len(where) < 3:
                continue
            for attempt_label in where:
                fire("local-checks-stuck", "%s gate %s" % (attempt_label, gate_name))
            notes.append(
                "%s gate %s (command_digest=%s) red on %d attempts (%s)"
                % (
                    step_label,
                    gate_name,
                    digest if digest is not None else "unknown",
                    len(where),
                    ", ".join(where),
                )
            )

    # api-flaky / api-blocked.  Exclusive: a batch blocked on api is blocked,
    # not flaky.
    api_count = len(api_attempts)
    blocked_reason = _blocked_api_reason(
        status, _report_batch_id(report), report.get("refusals")
    )
    if blocked_reason is not None or api_count >= 3:
        if blocked_reason is not None:
            fire("api-blocked", "batch %s blocked: %s" % (_report_batch_id(report) or "?", blocked_reason))
        for attempt_label in api_attempts:
            fire("api-blocked", attempt_label)
    elif api_count in (1, 2):
        for attempt_label in api_attempts:
            fire("api-flaky", attempt_label)

    if unknown_timing:
        notes.append(
            "%d attempt(s) with null timing fields (pre-report_version 2): %s; "
            "treated as unknown, not zero"
            % (len(unknown_timing), ", ".join(unknown_timing))
        )
    if unknown_edits:
        notes.append(
            "%d attempt(s) with unknown edit counts: %s; not charged with "
            '"no edits"' % (len(unknown_edits), ", ".join(unknown_edits))
        )

    # clean: none of the above, and every step accepted.
    failure_labels = [label for label in LABELS if label != "clean" and label in evidence]
    if not failure_labels and all_accepted:
        fire("clean", "%d step(s) accepted, no failure label in this subset" % len(steps))
    elif not evidence:
        notes.append(
            "no label in this subset matched; steps: %s"
            % (", ".join(_step_label(_as_dict(s), i) + "=" + str(_as_dict(s).get("status")) for i, s in enumerate(steps, start=1)) or "none")
        )

    labels = [label for label in LABELS if label in evidence]
    return {"labels": labels, "evidence": evidence, "notes": notes}
