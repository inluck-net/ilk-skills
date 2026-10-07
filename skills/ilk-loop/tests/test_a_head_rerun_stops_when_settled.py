"""Pin the settled-rerun contract for run_head_reruns + classify_flaky.

AC-1  xdist kept (invocation passed through unchanged)
AC-2  settled ids dropped (an id that passes leaves the set after that pass)
AC-3  verdict equivalence — exhaustive 16-case table
AC-4  touched ids are not rerun (computed before reruns)
AC-5  bound fails closed (unsettled ids at the budget are attributed)
AC-6  gate agrees (verify_attribution's N/K parser derives the same verdict)

AC-1, AC-2, AC-4, AC-5 are xfail at base (the current code strips xdist,
always runs K passes, computes touched after, and has no total bound).
AC-3 and AC-6 characterise pure semantics that hold before and after.
"""

from __future__ import annotations

import itertools
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fake pytest output: given a per-pass script of {node_id: "failed"|"passed"},
# produce the stdout text a real pytest would emit.
def _fake_pytest_output(pass_results: dict[str, str]) -> str:
    """Build pytest-style stdout for one rerun pass."""
    lines = ["=" * 60]
    for nid, result in pass_results.items():
        if result == "failed":
            lines.append(f"FAILED {nid}")
    lines.append("=" * 60)
    failed = sum(1 for v in pass_results.values() if v == "failed")
    passed = sum(1 for v in pass_results.values() if v == "passed")
    lines.append(f"{failed} failed, {passed} passed")
    return "\n".join(lines)


class FakeRerunSequence:
    """Records each subprocess.run call and returns scripted results.

    Setup: ``script`` is a list of dicts, one per pass.
    Each dict maps ``{node_id: "failed"|"passed"}``.
    A pass that times out is represented by ``TimeoutExpired``.
    """

    def __init__(self, script: list[dict[str, str] | Exception]):
        self.script = list(script)
        self.call_log: list[str] = []

    def __call__(self, cmd, **kwargs):
        import subprocess as _sp

        self.call_log.append(cmd)
        if not self.script:
            raise AssertionError("FakeRerunSequence: more calls than scripted passes")
        result = self.script.pop(0)
        if isinstance(result, Exception):
            raise result
        stdout = _fake_pytest_output(result)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# AC-1: xdist flags are kept in every rerun command
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="base strips -n/--dist from invocation (verification_record.py:1328)",
)
def test_ac1_xdist_kept(tmp_path: Path):
    """Every rerun command must contain the suite's -n and --dist flags."""
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"]
    # Script: both pass on pass 1 (so settled), no pass 2 needed.
    fake = FakeRerunSequence([
        {"tests/test_foo.py::test_a": "passed", "tests/test_foo.py::test_b": "passed"},
    ])

    invocation = "python3 -m pytest --timeout=17 -n 8 --dist loadfile"

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result = vr.run_head_reruns(tmp_path, ids, invocation)

    # Every captured command must contain -n 8 and --dist loadfile.
    assert len(fake.call_log) >= 1, "expected at least one rerun command"
    for cmd in fake.call_log:
        assert "-n 8" in cmd, f"missing -n 8 in: {cmd}"
        assert "--dist loadfile" in cmd, f"missing --dist loadfile in: {cmd}"


# ---------------------------------------------------------------------------
# AC-2: settled ids are dropped after their first passing rerun
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="base always runs K passes regardless of settlement (range(K) loop)",
)
def test_ac2_settled_ids_dropped(tmp_path: Path):
    """Ids that pass leave the rerun set; subsequent passes skip them.

    A=(red,red,red), B=(red,pass,...), C=(pass,...)
    Expected: pass 1 runs A,B,C; pass 2 runs A,B; pass 3 runs A only.
    Returns A=(3,3), B=(1,2), C=(0,1).
    """
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b",
           "tests/test_foo.py::test_c"]
    fake = FakeRerunSequence([
        # pass 1: A red, B red, C pass → C settled
        {"tests/test_foo.py::test_a": "failed",
         "tests/test_foo.py::test_b": "failed",
         "tests/test_foo.py::test_c": "passed"},
        # pass 2: A red, B pass → B settled
        {"tests/test_foo.py::test_a": "failed",
         "tests/test_foo.py::test_b": "passed"},
        # pass 3: A red → A settled (red_count==runs)
        {"tests/test_foo.py::test_a": "failed"},
    ])

    result = vr.run_head_reruns(tmp_path, ids, "python3 -m pytest -n 8")

    # New signature returns {nid: (red_count, runs)}.
    assert result["tests/test_foo.py::test_a"] == (3, 3)
    assert result["tests/test_foo.py::test_b"] == (1, 2)
    assert result["tests/test_foo.py::test_c"] == (0, 1)

    # Pass 2 should NOT include C, pass 3 should NOT include B or C.
    # We verify by checking the number of subprocess calls.
    assert len(fake.call_log) == 3
    # Pass 2 should not have test_c
    assert "test_c" not in fake.call_log[1]
    # Pass 3 should not have test_b or test_c
    assert "test_b" not in fake.call_log[2]
    assert "test_c" not in fake.call_log[2]


# ---------------------------------------------------------------------------
# AC-3: verdict equivalence — exhaustive 16-case table
# ---------------------------------------------------------------------------

def test_ac3_verdict_equivalence_exhaustive():
    """For every outcome sequence in {red,pass}^3 and touched ∈ {T,F},
    the new classification equals the old one (inlined reference).

    This is a pure-function test that holds before AND after the change,
    because the contract is designed so that (red_count == runs) is
    equivalent to (red_count == K) when runs == K (the old code).
    """
    from verification_record import classify_flaky

    # Reference: old fixed-K classify_flaky semantics inlined.
    def old_classify_flaky(
        node_id: str, at_base: str, head_red_count: int,
        K: int, batch_touched: bool,
    ) -> str | None:
        if at_base in ("failed", "declared-at-base"):
            return "pre-existing"
        if head_red_count == K:
            return "attributed"
        if batch_touched:
            return "attributed"
        return "flaky-owed"

    K = 3
    outcomes = list(itertools.product(["red", "pass"], repeat=3))

    for seq in outcomes:
        for touched in (True, False):
            for at_base in ("passed", "absent-at-base"):
                # Simulate: an id runs all K passes (no bound, no early stop).
                # red_count = number of "red" in the sequence.
                red_count = sum(1 for o in seq if o == "red")
                runs = K  # old code always runs K passes

                old_verdict = old_classify_flaky(
                    "nid", at_base, red_count, K, touched)

                # New classify_flaky takes (red_count, runs) instead of
                # (head_red_count, K) — but when runs==K the result is identical.
                # We test the pure-function contract: attributed iff red==runs.
                new_verdict = classify_flaky(
                    "nid", at_base, red_count, K, touched)

                assert old_verdict == new_verdict, (
                    f"mismatch for seq={seq}, touched={touched}, "
                    f"at_base={at_base}: old={old_verdict}, new={new_verdict}"
                )


# ---------------------------------------------------------------------------
# AC-4: touched ids are not rerun (computed before reruns)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="base computes batch_touched_files AFTER run_head_reruns (call site :2293-2298)",
)
def test_ac4_touched_not_rerun(tmp_path: Path):
    """An id whose file the batch touched appears in 0 rerun commands,
    renders 0/0 with 'batch touched file: yes', and classifies attributed.
    """
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a"]

    # Script: empty — no passes should run because the only id is touched.
    fake = FakeRerunSequence([])

    invocation = "python3 -m pytest -n 8"

    # Monkeypatch batch_touched_files to say test_a's file is touched.
    def fake_batch_touched(project, base_sha, node_ids, batch_slugs=None):
        return {nid: True for nid in node_ids}

    with patch.object(vr.subprocess, "run", side_effect=fake), \
         patch.object(vr, "batch_touched_files", side_effect=fake_batch_touched):
        # Call the function that holds the call site (or the call-site helper).
        # For now we test run_head_reruns itself: touched ids should not appear.
        result = vr.run_head_reruns(tmp_path, ids, invocation)

    # The id should NOT appear in any rerun command.
    assert len(fake.call_log) == 0, (
        f"touched id should not be rerun, but got commands: {fake.call_log}"
    )

    # The result should be (0, 0) — never ran.
    assert result["tests/test_foo.py::test_a"] == (0, 0)


# ---------------------------------------------------------------------------
# AC-5: bound fails closed — unsettled ids at the budget are attributed
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="base has no total budget; each pass has its own timeout, total is unbounded",
)
def test_ac5_bound_fails_closed(tmp_path: Path):
    """Budget 10s; pass 1 takes 11s (TimeoutExpired) → bound-hit True;
    every id is (0, 0); all classify attributed; record has the bound line.
    """
    import subprocess as _sp

    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"]

    # Script: pass 1 times out.
    fake = FakeRerunSequence([
        _sp.TimeoutExpired(cmd="pytest", timeout=10),
    ])

    invocation = "python3 -m pytest -n 8"

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result = vr.run_head_reruns(
            tmp_path, ids, invocation, budget_s=10)

    # New signature returns (results_dict, bound_hit).
    assert isinstance(result, tuple), "expected (results, bound_hit) tuple"
    results, bound_hit = result

    assert bound_hit is True, "bound should be hit when pass times out"
    # Both ids should be (0, 0) — never completed a pass.
    assert results["tests/test_foo.py::test_a"] == (0, 0)
    assert results["tests/test_foo.py::test_b"] == (0, 0)

    # Classification: (0, 0) → red_count==runs → attributed.
    cls_a = vr.classify_flaky(
        "tests/test_foo.py::test_a", "passed", 0, 3, False)
    cls_b = vr.classify_flaky(
        "tests/test_foo.py::test_b", "passed", 0, 3, False)
    assert cls_a == "attributed"
    assert cls_b == "attributed"


# ---------------------------------------------------------------------------
# AC-6: gate agrees — verify_attribution's N/K parser derives the same verdict
# ---------------------------------------------------------------------------

def test_ac6_gate_agrees():
    """Feed a rendered table with rows 0/0|yes, 2/2|no, 1/2|no through
    verify_attribution's derive_attributed → first two attributed, third flaky_owed.
    """
    import verify_attribution as va

    # Simulate the table rows that render_record would produce.
    # Row format: | node_id | at_base | in_baseline_red | head reruns | batch touched file |
    rows = [
        ["tests/test_foo.py::test_a", "passed", "no", "0/0", "yes"],
        ["tests/test_foo.py::test_b", "passed", "no", "2/2", "no"],
        ["tests/test_foo.py::test_c", "passed", "no", "1/2", "no"],
    ]

    bad, flaky_owed = va.derive_attributed(rows)

    # test_a: 0/0 + touched=yes → attributed (red==runs, 0==0)
    # test_b: 2/2 + touched=no → attributed (red==K, 2==2)
    # test_c: 1/2 + touched=no → flaky_owed (red!=K, not touched)
    bad_ids = [r[0] for r in bad]
    # flaky_owed is a list of node-id strings, not a list of rows.
    owed_ids = list(flaky_owed)

    assert "tests/test_foo.py::test_a" in bad_ids
    assert "tests/test_foo.py::test_b" in bad_ids
    assert "tests/test_foo.py::test_c" in owed_ids