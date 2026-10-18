"""Classify a batch from ilk2's report JSON alone.

docs/07-retiring-v1.md §7.4.3 rebuilds ``ilk-feedback`` on ilk2's versioned
JSON (``ilk2 report --batch --json``, ``status``, ``events``) so that no
postmortem reader needs V1's run files under ``~/.ilk-data``, or ilk2's run
directories either.  ``skills/ilk2-feedback/scripts/classify.py`` is that
classifier: it takes already-parsed dicts and decides the kept subset of V1's
taxonomy from them.

Each label has a hand-written JSON fixture under
``tests/fixtures/ilk2_feedback/`` — synthetic plan names, no real project data
(docs/07 §7.4.1: real masters and reports are not copied into tests).  The
tests load each fixture and assert its labels, and then pin the three cases
the taxonomy wording leaves easy to get wrong:

* a ``red`` attempt with ``termination: timeout`` is ``timeout-bound``;
* null timing fields are unknown, never zero;
* a gates-first measurement is not ``stuck-no-progress``.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSIFY_PY = REPO_ROOT / "skills" / "ilk2-feedback" / "scripts" / "classify.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ilk2_feedback"


def _load_classify():
    spec = importlib.util.spec_from_file_location("ilk2_feedback_classify", CLASSIFY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


classify_mod = _load_classify()
classify = classify_mod.classify


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _attempt(**overrides) -> dict:
    """A minimal well-formed attempt record; override what the case needs."""
    record = {
        "n": 1,
        "outcome": "accepted",
        "reason": "synthetic",
        "termination": "done",
        "gates_first": False,
        "minutes": 5.0,
        "edits": {"Edit": 1},
        "requests": 4,
        "long_requests": 0,
        "long_request_min": 0.0,
        "longest_request_min": 0.0,
        "model_min": 3.0,
        "tool_min": 1.0,
        "first_edit_start_min": 1.0,
        "first_edit_end_min": 2.0,
        "gates": {},
    }
    record.update(overrides)
    return record


def _report(*attempts, status="red", plan="synth-unit", report_version=2, batch="b-synth-unit"):
    return {
        "batch": batch,
        "report_version": report_version,
        "steps": [
            {
                "plan": plan,
                "idx": 1,
                "title": "synthetic step",
                "kind": "work",
                "status": status,
                "attempts": len(attempts),
                "red": 0,
                "interrupted": 0,
                "minutes": 5.0,
                "first_edit_min": 1.0,
                "carried": 0,
                "peak_context_k": 10,
                "turns": 4,
                "s_per_turn": 30.0,
                "attempt_records": list(attempts),
            }
        ],
        "refusals": [],
        "accepted": 1 if status == "accepted" else 0,
        "of": 1,
        "minutes_per_accepted_step": None,
        "first_attempt_share": None,
        "phase_minutes": {},
    }


# One hand-written fixture per label in the kept subset, plus the null-timing
# case (a clean batch recorded before report_version 2).
FIXTURE_LABELS = {
    "api_blocked.json": ["api-blocked"],
    "api_flaky.json": ["api-flaky"],
    "clean.json": ["clean"],
    "interrupted.json": ["interrupted"],
    "local_checks_stuck.json": ["local-checks-stuck"],
    "null_timing.json": ["clean"],
    "quota_exhausted.json": ["quota-exhausted"],
    "stuck_no_progress.json": ["stuck-no-progress"],
    "timeout_bound.json": ["timeout-bound"],
}


def test_every_fixture_on_disk_is_in_the_table() -> None:
    """A new fixture must declare its labels here, not be silently ignored."""
    on_disk = {path.name for path in FIXTURES.glob("*.json")}
    assert on_disk == set(FIXTURE_LABELS), (
        "fixtures/ilk2_feedback and FIXTURE_LABELS disagree: "
        "only on disk %r, only in the table %r"
        % (sorted(on_disk - set(FIXTURE_LABELS)), sorted(set(FIXTURE_LABELS) - on_disk))
    )


@pytest.mark.parametrize("name,expected", sorted(FIXTURE_LABELS.items()))
def test_fixture_classifies_from_its_json_alone(name: str, expected: list) -> None:
    result = classify(_fixture(name))
    assert result["labels"] == expected


def test_result_carries_labels_evidence_and_notes() -> None:
    result = classify(_fixture("timeout_bound.json"))
    assert set(result) == {"labels", "evidence", "notes"}
    assert isinstance(result["labels"], list)
    assert isinstance(result["evidence"], dict)
    assert isinstance(result["notes"], list)


def test_a_red_attempt_with_termination_timeout_is_timeout_bound() -> None:
    """The timed-out attempt is stored ``outcome: red``; only ``termination``
    says timeout (docs/07 §7.4.1, finding 11)."""
    result = classify(_fixture("timeout_bound.json"))
    assert result["labels"] == ["timeout-bound"]
    evidence = result["evidence"]["timeout-bound"]
    assert evidence, "the label must carry evidence naming the attempt"
    assert any("synth-timeout-bound step 1 attempt 1" in item for item in evidence)


def test_a_timed_out_attempt_with_no_edits_is_also_stuck_no_progress() -> None:
    report = _report(_attempt(outcome="red", termination="timeout", edits={}))
    result = classify(report)
    assert result["labels"] == ["timeout-bound", "stuck-no-progress"]


# ── null timing fields: unknown, never zero ──────────────────────────────────


def test_null_timing_fields_do_not_crash_or_count_as_zero() -> None:
    report = _fixture("null_timing.json")
    result = classify(report)  # must not raise on the nulls
    assert result["labels"] == ["clean"]
    notes = " ".join(result["notes"]).lower()
    # The nulls are reported as unknown ...
    assert "unknown" in notes, result["notes"]
    # ... and never laundered into a zeroed tally.
    assert not re.search(r"\b0\s+(requests?|minutes?)\b", notes), result["notes"]


def test_null_timing_does_not_make_a_productive_attempt_stuck() -> None:
    """If null read as zero, ``requests: null`` could look like "no work"."""
    report = _report(
        _attempt(
            outcome="red",
            termination=None,
            edits={"Write": 1},
            requests=None,
            minutes=None,
            long_requests=None,
            long_request_min=None,
            longest_request_min=None,
            model_min=None,
            tool_min=None,
            first_edit_start_min=None,
            first_edit_end_min=None,
        )
    )
    result = classify(report)
    assert "stuck-no-progress" not in result["labels"]


def test_an_empty_edit_map_is_a_proven_zero_and_stuck() -> None:
    report = _report(
        _attempt(
            outcome="red",
            termination=None,
            edits={},
            requests=None,
            minutes=None,
        )
    )
    result = classify(report)
    assert "stuck-no-progress" in result["labels"]


def test_unknown_edit_counts_are_not_charged_with_no_edits() -> None:
    """``edits: {"Edit": null}`` is unknown, not a zero."""
    report = _report(
        _attempt(outcome="red", termination=None, edits={"Edit": None})
    )
    result = classify(report)
    assert "stuck-no-progress" not in result["labels"]
    notes = " ".join(result["notes"]).lower()
    assert "unknown edit counts" in notes
    assert '"no edits"' in notes


# ── gates-first measurements are not stuck ───────────────────────────────────


def test_a_gates_first_measurement_is_not_stuck_no_progress() -> None:
    """Even red and edit-less: a gates-first attempt measures the base, it is
    not a verdict on the worker (docs/07 §7.4.3, ``stuck-no-progress``)."""
    report = _report(
        _attempt(outcome="red", termination=None, gates_first=True, edits={})
    )
    result = classify(report)
    assert "stuck-no-progress" not in result["labels"]
    assert result["labels"] == []


def test_the_clean_fixture_gates_first_attempt_is_not_stuck() -> None:
    result = classify(_fixture("clean.json"))
    assert result["labels"] == ["clean"]
    assert "stuck-no-progress" not in result["labels"]


# ── api-flaky / api-blocked ──────────────────────────────────────────────────


def test_api_flaky_is_one_or_two_api_attempts() -> None:
    result = classify(_fixture("api_flaky.json"))
    assert result["labels"] == ["api-flaky"]
    assert "api-blocked" not in result["labels"]


def test_api_blocked_is_three_or_more_api_attempts() -> None:
    result = classify(_fixture("api_blocked.json"))
    assert result["labels"] == ["api-blocked"]
    assert "api-flaky" not in result["labels"]


def test_a_batch_blocked_with_an_api_reason_is_api_blocked() -> None:
    """The second api-blocked route (docs/07 §7.4.3), read from status."""
    report = _report(_attempt(outcome="red", termination=None, edits={}))
    status = {
        "batches": [
            {
                "id": "b-synth-unit",
                "project": "synth",
                "status": "blocked",
                "terminal_reason": None,
                "blocked_reason": "api_error: upstream refused every request",
                "consumer_ref": None,
                "plans": [],
                "refusals": [],
                "shipped_at": None,
            }
        ],
        "needs_human": [],
    }
    result = classify(report, status)
    assert "api-blocked" in result["labels"]
    assert "api-flaky" not in result["labels"]


def test_a_blocked_reason_that_only_contains_the_letters_api_is_not_api() -> None:
    """"rapid" must not read as "api"."""
    report = _report(_attempt(outcome="accepted", edits={"Edit": 1}))
    status = {
        "batches": [
            {
                "id": "b-synth-unit",
                "project": "synth",
                "status": "blocked",
                "terminal_reason": None,
                "blocked_reason": "the step stayed rapid but never landed",
                "consumer_ref": None,
                "plans": [],
                "refusals": [],
                "shipped_at": None,
            }
        ],
        "needs_human": [],
    }
    result = classify(report, status)
    assert "api-blocked" not in result["labels"]


def test_an_api_refusal_on_a_blocked_batch_is_an_api_reason() -> None:
    report = _report(_attempt(outcome="red", termination=None, edits={}))
    report["refusals"] = [
        {
            "reason": "api_error",
            "detail": "provider rejected the request",
            "opened_at": "2026-10-18T00:00:00Z",
            "cleared_at": None,
        }
    ]
    status = {
        "batches": [
            {
                "id": "b-synth-unit",
                "project": "synth",
                "status": "blocked",
                "terminal_reason": "blocked on refusals",
                "blocked_reason": "blocked on refusals",
                "consumer_ref": None,
                "plans": [],
                "refusals": [],
                "shipped_at": None,
            }
        ],
        "needs_human": [],
    }
    result = classify(report, status)
    assert "api-blocked" in result["labels"]


# ── quota / interrupted ──────────────────────────────────────────────────────


def test_quota_exhausted_is_distinct_from_the_other_stops() -> None:
    result = classify(_fixture("quota_exhausted.json"))
    assert result["labels"] == ["quota-exhausted"]


def test_a_quota_termination_alone_is_quota_exhausted() -> None:
    report = _report(_attempt(outcome="red", termination="quota", edits={"Edit": 1}))
    result = classify(report)
    assert result["labels"] == ["quota-exhausted"]


def test_any_interrupted_attempt_is_interrupted() -> None:
    result = classify(_fixture("interrupted.json"))
    assert result["labels"] == ["interrupted"]


# ── local-checks-stuck ───────────────────────────────────────────────────────


def test_one_gate_red_on_three_attempts_of_one_step_is_local_checks_stuck() -> None:
    result = classify(_fixture("local_checks_stuck.json"))
    assert result["labels"] == ["local-checks-stuck"]
    evidence = result["evidence"]["local-checks-stuck"]
    assert len(evidence) == 3, evidence
    assert any("attempt 3 gate unit" in item for item in evidence)


def test_one_gate_red_on_two_attempts_is_not_stuck() -> None:
    def gate(fail: bool) -> dict:
        return {
            "unit": {
                "adapter": "pytest",
                "command_digest": "sha256:synth-unit",
                "outcome": "fail" if fail else "pass",
                "new": [],
                "possibly_inherited": [],
                "attributed": [],
            }
        }

    report = _report(
        _attempt(n=1, outcome="red", termination=None, edits={"Edit": 1}, gates=gate(True)),
        _attempt(n=2, outcome="red", termination=None, edits={"Edit": 1}, gates=gate(True)),
    )
    result = classify(report)
    assert "local-checks-stuck" not in result["labels"]


def test_the_same_gate_name_with_a_different_digest_is_a_different_gate() -> None:
    def gate(digest: str) -> dict:
        return {
            "unit": {
                "adapter": "pytest",
                "command_digest": digest,
                "outcome": "fail",
                "new": [],
                "possibly_inherited": [],
                "attributed": [],
            }
        }

    report = _report(
        _attempt(n=1, outcome="red", termination=None, edits={"Edit": 1}, gates=gate("sha256:a")),
        _attempt(n=2, outcome="red", termination=None, edits={"Edit": 1}, gates=gate("sha256:b")),
        _attempt(n=3, outcome="red", termination=None, edits={"Edit": 1}, gates=gate("sha256:a")),
    )
    result = classify(report)
    assert "local-checks-stuck" not in result["labels"]


# ── clean ────────────────────────────────────────────────────────────────────


def test_clean_is_none_of_the_above_and_every_step_accepted() -> None:
    result = classify(_fixture("clean.json"))
    assert result["labels"] == ["clean"]


def test_a_step_that_is_not_accepted_is_not_clean() -> None:
    report = _report(_attempt(outcome="red", termination=None, edits={"Edit": 1}), status="red")
    result = classify(report)
    assert "clean" not in result["labels"]
    assert result["labels"] == []


# ── the classifier reads the JSON alone ──────────────────────────────────────


def test_classify_reads_the_json_alone() -> None:
    """No filesystem, no V1 data home, no ilk2 run directories (docs/07
    §7.4.3: "reads only versioned ilk2 JSON").

    Checked on the code, not the prose: the docstring is allowed to name the
    homes this classifier refuses to read.
    """
    source = CLASSIFY_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "", 1)
    code = re.sub(r"#[^\n]*", "", code)
    for forbidden in (".ilk-data", "ilk2/data/runs", "ILK_DATA_HOME", "ILK_DATA_DIR"):
        assert forbidden not in code, "code mentions %s" % forbidden
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            assert name not in {"os", "pathlib", "io", "glob", "subprocess"}, (
                "imports %s; classify() takes dicts, not paths" % name
            )
    assert "open(" not in code


def test_labels_are_the_kept_subset_of_v1_taxonomy() -> None:
    assert classify_mod.LABELS == (
        "timeout-bound",
        "stuck-no-progress",
        "api-flaky",
        "api-blocked",
        "quota-exhausted",
        "interrupted",
        "local-checks-stuck",
        "clean",
    )
    for name, expected in FIXTURE_LABELS.items():
        result = classify(_fixture(name))
        assert result["labels"] == expected
        assert set(result["evidence"]) == set(expected)
