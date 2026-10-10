"""Pin the settled-rerun contract for run_head_reruns + classify_flaky.

AC-1  xdist stripped (both rerun arms measure under the same shape)
AC-2  settled ids dropped (an id that passes leaves the set after that pass)
AC-3  verdict equivalence — exhaustive 16-case table against the spec rule
AC-4  touched ids are not rerun (computed before reruns at the call site)
AC-5  bound fails closed (unsettled ids at the budget are attributed)
AC-6  gate agrees (verify_attribution's N/K parser derives the same verdict)
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        self.call_log.append(cmd)
        if not self.script:
            raise AssertionError("FakeRerunSequence: more calls than scripted passes")
        result = self.script.pop(0)
        if isinstance(result, Exception):
            raise result
        stdout = _fake_pytest_output(result)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# AC-1: xdist flags are stripped from every rerun command
# ---------------------------------------------------------------------------

def test_ac1_xdist_stripped(tmp_path: Path):
    """Every rerun command must be serial: no -n, no --dist.

    Contract updated by sub-plan ``both-rerun-arms-run-serially`` (step 1),
    whose design item 1 is the evidence: the at-base arm strips xdist
    (``verification_record.run_at_base``), so the head arm must too — both
    arms measure under the same shape (row d0eaf52ab42bf5e6).  The rest of
    the invocation still passes through unchanged.
    """
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"]
    # Script: both pass on pass 1 (so settled), no pass 2 needed.
    fake = FakeRerunSequence([
        {"tests/test_foo.py::test_a": "passed",
         "tests/test_foo.py::test_b": "passed"},
    ])

    invocation = "python3 -m pytest --timeout=17 -n 8 --dist loadfile"

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result, bound_hit = vr.run_head_reruns(tmp_path, ids, invocation)

    assert len(fake.call_log) >= 1, "expected at least one rerun command"
    for cmd in fake.call_log:
        assert "-n 8" not in cmd, f"xdist -n 8 survived strip: {cmd}"
        assert "--dist loadfile" not in cmd, f"xdist --dist survived strip: {cmd}"
        # The non-xdist flags are still the suite's own.
        assert "--timeout=17" in cmd, f"non-xdist flag lost: {cmd}"


# ---------------------------------------------------------------------------
# AC-2: settled ids are dropped after their first passing rerun
# ---------------------------------------------------------------------------

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

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result, bound_hit = vr.run_head_reruns(
            tmp_path, ids, "python3 -m pytest -n 8")

    # New signature returns {nid: (red_count, runs)}.
    assert result["tests/test_foo.py::test_a"] == (3, 3)
    assert result["tests/test_foo.py::test_b"] == (1, 2)
    assert result["tests/test_foo.py::test_c"] == (0, 1)
    assert bound_hit is False

    # Pass 2 should NOT include C, pass 3 should NOT include B or C.
    assert len(fake.call_log) == 3
    assert "test_c" not in fake.call_log[1]
    assert "test_b" not in fake.call_log[2]
    assert "test_c" not in fake.call_log[2]


# ---------------------------------------------------------------------------
# AC-3: verdict equivalence — exhaustive 16-case table
# ---------------------------------------------------------------------------

def test_ac3_verdict_equivalence_exhaustive():
    """For every outcome sequence in {red,pass}^3 and touched ∈ {T,F},
    the classification equals the specified rule (inlined reference).

    Contract updated by sub-plan ``both-rerun-arms-run-serially`` (step 1):
    the rule gained a ``contention`` clause (at base ``passed`` + 0 reds in
    its serial head rerun), so the inlined reference carries that clause too.
    Every other case keeps the pre-change verdict, and the comparison stays
    exhaustive over all 16 sequences x touched x at-base.

    The ``seen_contention`` tally is asserted so the new clause cannot be
    dropped from BOTH the reference and the function without this test
    noticing — the equivalence alone would still hold if both went away.
    """
    from verification_record import classify_flaky

    # Reference: the specified classify_flaky semantics inlined.
    def spec_classify_flaky(
        node_id: str, at_base: str, head_red_count: int,
        K: int, batch_touched: bool,
    ) -> str | None:
        if at_base in ("failed", "declared-at-base"):
            return "pre-existing"
        if head_red_count == K:
            return "attributed"
        if at_base == "passed" and head_red_count == 0:
            return "contention"
        if batch_touched:
            return "attributed"
        return "flaky-owed"

    K = 3
    outcomes = list(itertools.product(["red", "pass"], repeat=3))
    seen_contention = 0

    for seq in outcomes:
        for touched in (True, False):
            for at_base in ("passed", "absent-at-base"):
                red_count = sum(1 for o in seq if o == "red")
                runs = K  # the call site always runs K passes

                spec_verdict = spec_classify_flaky(
                    "nid", at_base, red_count, K, touched)
                new_verdict = classify_flaky(
                    "nid", at_base, red_count, K, touched)

                assert spec_verdict == new_verdict, (
                    f"mismatch for seq={seq}, touched={touched}, "
                    f"at_base={at_base}: spec={spec_verdict}, "
                    f"new={new_verdict}"
                )
                if at_base == "passed" and red_count == 0:
                    seen_contention += 1
                    assert new_verdict == "contention", (
                        f"0 red at base-passed must be contention, got "
                        f"{new_verdict} (touched={touched})")

    # all-pass x {T, F} x "passed" — the exhaustive set the new clause owns.
    assert seen_contention == 2, (
        f"expected exactly 2 contention cells in the table, saw "
        f"{seen_contention} — the exhaustive loop stopped covering it")


# ---------------------------------------------------------------------------
# AC-4: touched ids are not rerun (computed before reruns at the call site)
# ---------------------------------------------------------------------------

def test_ac4_touched_not_rerun(tmp_path: Path):
    """An id whose file the batch touched is excluded from run_head_reruns.

    The call site computes batch_touched_files BEFORE calling run_head_reruns
    and passes only untouched ids.  Touched ids get (0, 0) and classify
    attributed.
    """
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"]

    # Script: only test_b runs (test_a is touched and excluded by the caller).
    fake = FakeRerunSequence([
        {"tests/test_foo.py::test_b": "passed"},
    ])

    invocation = "python3 -m pytest -n 8"

    # Monkeypatch batch_touched_files to say test_a's file is touched.
    def fake_batch_touched(project, base_sha, node_ids, batch_slugs=None):
        return {"tests/test_foo.py::test_a": True,
                "tests/test_foo.py::test_b": False}

    with patch.object(vr.subprocess, "run", side_effect=fake), \
         patch.object(vr, "batch_touched_files", side_effect=fake_batch_touched):
        batch_touched = vr.batch_touched_files(
            tmp_path, "a" * 40, ids, batch_slugs=None)
        untouched = [nid for nid in ids if not batch_touched.get(nid, False)]
        result, bound_hit = vr.run_head_reruns(
            tmp_path, untouched, invocation)
        # Merge: touched ids get (0, 0).
        for nid in ids:
            if nid not in result:
                result[nid] = (0, 0)

    # test_a should NOT appear in any rerun command.
    assert len(fake.call_log) >= 1
    for cmd in fake.call_log:
        assert "test_a" not in cmd, f"touched id in rerun command: {cmd}"

    # test_a is (0, 0), test_b is (0, 1).
    assert result["tests/test_foo.py::test_a"] == (0, 0)
    assert result["tests/test_foo.py::test_b"] == (0, 1)

    # Classification: (0, 0) → attributed (red==runs).
    cls = vr.classify_flaky(
        "tests/test_foo.py::test_a", "passed", 0, 0, True)
    assert cls == "attributed"


# ---------------------------------------------------------------------------
# AC-5: bound fails closed — unsettled ids at the budget are attributed
# ---------------------------------------------------------------------------

def test_ac5_bound_fails_closed(tmp_path: Path):
    """Budget 10s; pass 1 takes 11s (TimeoutExpired) → bound-hit True;
    every id is (0, 0); all classify attributed; record has the bound line.
    """
    import verification_record as vr

    ids = ["tests/test_foo.py::test_a", "tests/test_foo.py::test_b"]

    # Script: pass 1 times out.
    fake = FakeRerunSequence([
        subprocess.TimeoutExpired(cmd="pytest", timeout=10),
    ])

    invocation = "python3 -m pytest -n 8"

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result, bound_hit = vr.run_head_reruns(
            tmp_path, ids, invocation, budget_s=10)

    assert bound_hit is True, "bound should be hit when pass times out"
    # Both ids should be (0, 0) — never completed a pass.
    assert result["tests/test_foo.py::test_a"] == (0, 0)
    assert result["tests/test_foo.py::test_b"] == (0, 0)

    # Classification: (0, 0) → red_count==runs → attributed.
    cls_a = vr.classify_flaky(
        "tests/test_foo.py::test_a", "passed", 0, 0, False)
    cls_b = vr.classify_flaky(
        "tests/test_foo.py::test_b", "passed", 0, 0, False)
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