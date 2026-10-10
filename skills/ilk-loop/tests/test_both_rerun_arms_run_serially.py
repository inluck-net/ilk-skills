"""Red-first pins: both rerun arms run serially, and serial-green is contention.

Retro 2026-10-10 root cause 2 (`retro-2026-10-10-a-verify-that-cannot-
fit-its-own-gate.md`): at-base strips xdist, head reruns kept it, so a
contention timeout at head read as an attributed regression.

AC-1  ``run_head_reruns`` strips ``-n`` / ``--dist`` from the invocation it
      actually issues — both arms measure under one shape.
AC-2  ``classify_flaky`` gains ``contention`` (the falsifier): at base
      ``passed`` + 0 red in its serial head rerun is ``contention`` whatever
      ``batch_touched`` says; serial K/K red stays ``attributed``.
AC-3  a rendered record whose only row is contention passes the step-1 table
      re-derivation in ``verify_attribution`` (no attributed row), while a
      record with one serial-red K/K row fails it.
AC-4  end to end: a test that sleeps past its ``--timeout`` only when
      ``PYTEST_XDIST_WORKER`` is set is red under ``-n 2``, green serially,
      and the recorder classifies it ``contention``.
AC-5  ``baseline_diff``'s evidence reader counts ``contention`` exactly as it
      counts ``flaky_owed`` — not attributed (design item 3, no AC of its own).
"""

from __future__ import annotations

import os
import shlex
import site
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# pytest is often a ``--user`` install.  Pinning HOME moves user-site
# resolution, so any subprocess running ``python3 -m pytest …`` raises
# ``ModuleNotFoundError`` and every red in it is an environment artefact.
# Captured at import, while HOME is still the real one.
_USER_SITE = site.getusersitepackages()

LOOP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHIP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-ship" / "scripts"
for _p in (str(LOOP_SCRIPTS), str(SHIP_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REDS_FIRST = pytest.mark.xfail(
    strict=True,
    raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError),
    reason="not built yet",
)


# ---------------------------------------------------------------------------
# Shared helpers
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
    """Script one result per ``subprocess.run`` call; log every command."""

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
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout=_fake_pytest_output(result), stderr="")


def _render_record(path: Path, *, at_base: dict[str, str],
                   head_reruns: dict[str, tuple[int, int]],
                   batch_touched: dict[str, bool]) -> Path:
    """Render a signed record exactly as the recorder would, to *path*."""
    import verification_record as vr

    failed = len(at_base)
    results = {
        "counts": {"total": 10, "passed": 10 - failed, "failed": failed,
                   "errors": 0, "skipped": 0},
        "failing_nodes": sorted(at_base),
    }
    text = vr.render_record(
        batch="both-rerun-arms",
        head="a" * 40,
        tree="b" * 40,
        base_sha="c" * 40,
        invocation="python3 -m pytest -q",
        scope={"mode": "full", "reason": "step-0 pin", "count": 10},
        results=results,
        at_base=at_base,
        base_red=[],
        head_red=[],
        head_reruns=head_reruns,
        batch_touched=batch_touched,
    )
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# AC-1: head reruns strip xdist, so both arms measure under one shape
# ---------------------------------------------------------------------------

@REDS_FIRST
def test_ac1_head_reruns_strip_xdist(tmp_path: Path) -> None:
    """Every rerun command is serial: no ``-n``, no ``--dist``."""
    import verification_record as vr

    nid = "tests/test_foo.py::test_a"
    fake = FakeRerunSequence([{nid: "passed"}])
    invocation = "python3 -m pytest --timeout=17 -n 8 --dist loadfile -q"

    with patch.object(vr.subprocess, "run", side_effect=fake):
        result, bound_hit = vr.run_head_reruns(tmp_path, [nid], invocation)

    assert len(fake.call_log) >= 1, "expected at least one rerun command"
    for cmd in fake.call_log:
        assert "-n" not in cmd, f"xdist worker flag survived strip_xdist: {cmd}"
        assert "--dist" not in cmd, f"xdist dist flag survived strip_xdist: {cmd}"
    assert result[nid] == (0, 1)
    assert bound_hit is False


# ---------------------------------------------------------------------------
# AC-2: contention label + its falsifier
# ---------------------------------------------------------------------------

@REDS_FIRST
def test_ac2_contention_overrides_batch_touched() -> None:
    """Serial-green at base-passed is contention; serial K/K stays attributed."""
    from verification_record import classify_flaky

    # Falsifier 1: green in its serial head rerun ⇒ contention, touched or not.
    assert classify_flaky("t.py::x", "passed", 0, 3, True) == "contention"
    assert classify_flaky("t.py::x", "passed", 0, 3, False) == "contention"

    # Falsifier 2: red on every serial pass ⇒ attributed, untouched or not.
    assert classify_flaky("t.py::x", "passed", 3, 3, False) == "attributed"
    assert classify_flaky("t.py::x", "passed", 3, 3, True) == "attributed"

    # Unchanged cases: at-base verdicts and intermittent reds.
    assert classify_flaky("t.py::x", "failed", 0, 3, False) == "pre-existing"
    assert classify_flaky("t.py::x", "declared-at-base", 0, 3, False) == "pre-existing"
    assert classify_flaky("t.py::x", "absent-at-base", 0, 3, False) == "flaky-owed"
    assert classify_flaky("t.py::x", "passed", 1, 3, False) == "flaky-owed"
    assert classify_flaky("t.py::x", "passed", 1, 3, True) == "attributed"


# ---------------------------------------------------------------------------
# AC-3: the step-1 gate re-derives contention as not-attributed
# ---------------------------------------------------------------------------

@REDS_FIRST
def test_ac3_contention_row_passes_the_step1_gate(tmp_path: Path) -> None:
    """A contention row is no attributed row; a serial-red K/K row is."""
    import verify_attribution as va

    # Row 1: passed at base, 0/3 in its serial head rerun, batch touched the
    # file.  Under the old rule `batch_touched` alone attributed it.
    contention_rec = _render_record(
        tmp_path / "contention-batch.md",
        at_base={"t.py::x": "passed"},
        head_reruns={"t.py::x": (0, 3)},
        batch_touched={"t.py::x": True},
    )
    raised = None
    try:
        va.verify_detailed(contention_rec, project=None)
    except va.VerificationError as exc:
        raised = exc
    assert raised is None, f"contention row read as attributed: {raised}"

    bad, flaky_owed = va.derive_attributed(
        [["t.py::x", "passed", "no", "0/3", "yes"]])
    assert bad == [], f"contention row in the attributed list: {bad}"
    assert "t.py::x" in flaky_owed, (
        "contention must be counted as not attributed, not dropped")

    # Control: one serial-red K/K row must fail the same re-derivation.
    serial_red_rec = _render_record(
        tmp_path / "serial-red-batch.md",
        at_base={"t.py::x": "passed"},
        head_reruns={"t.py::x": (3, 3)},
        batch_touched={"t.py::x": False},
    )
    raised = None
    try:
        va.verify_detailed(serial_red_rec, project=None)
    except va.VerificationError as exc:
        raised = exc
    assert raised is not None, "a serial-red K/K row must fail the gate"


# ---------------------------------------------------------------------------
# AC-4: end to end in a tiny tmp project
# ---------------------------------------------------------------------------

@REDS_FIRST
def test_ac4_xdist_only_timeout_is_contention(tmp_path: Path) -> None:
    """Red under ``-n 2``, green serially ⇒ the recorder says contention."""
    import verification_record as vr

    pytest.importorskip("xdist", reason="AC-4 skips when pytest-xdist is absent")

    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "test_xdist_only.py"
    test_file.write_text(
        "import os\n"
        "import time\n"
        "\n"
        "\n"
        "def test_red_only_under_xdist():\n"
        "    if os.environ.get(\"PYTEST_XDIST_WORKER\"):\n"
        "        time.sleep(6)\n"
        "    assert True\n",
        encoding="utf-8",
    )
    nid = "test_xdist_only.py::test_red_only_under_xdist"
    python = shlex.quote(sys.executable)
    base_inv = f"{python} -m pytest --timeout=2 --timeout-method=signal -q -rf"
    xdist_inv = base_inv + " -n 2"
    env = {**os.environ,
           "HOME": str(tmp_path / "home"),
           "ILK_DATA_HOME": str(tmp_path / "ilk-data"),
           "ILK_DATA_DIR": str(tmp_path / "ilk-data")}
    if _USER_SITE and os.path.isdir(_USER_SITE):
        prior = env.get("PYTHONPATH") or ""
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (_USER_SITE, prior) if p])
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    # Premise: the very same test is red under xdist and green serially.
    xdist_run = subprocess.run(xdist_inv + f" {nid}", shell=True, cwd=project,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, env=env)
    assert xdist_run.returncode != 0, (
        "the xdist-only sleeper must be red under -n 2 to prove the premise")
    serial_run = subprocess.run(base_inv + f" {nid}", shell=True, cwd=project,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60, env=env)
    assert serial_run.returncode == 0, (
        f"the sleeper must be green serially:\n{serial_run.stdout[-2000:]}")

    # The recorder's own classification path, on the recorder's own reruns.
    with patch.dict(os.environ, env):
        results, bound_hit = vr.run_head_reruns(project, [nid], xdist_inv, K=3)
    red_count, runs = results[nid]
    assert red_count == 0, (
        f"serial head rerun still red {red_count}/{runs} — the arm kept xdist")
    # A green pass settles the id out of the set, so runs is 1..K.
    assert 1 <= runs <= 3, f"expected 1..3 passes, got {runs}"
    assert bound_hit is False
    assert vr.classify_flaky(nid, "passed", red_count, runs, False) == "contention"


# ---------------------------------------------------------------------------
# AC-5: baseline_diff's evidence reader counts contention like flaky_owed
# ---------------------------------------------------------------------------

@REDS_FIRST
def test_ac5_baseline_diff_counts_contention_as_not_attributed() -> None:
    """``contention`` evidence is inherited, never left as a regression."""
    import baseline_diff as bd

    invocation = "python3 -m pytest -q"
    entries = [{
        "node_id": "t.py::x",
        "evidence": {"contention": True, "invocation": invocation,
                     "serial_green": True},
    }]

    new, inherited = bd._apply_evidence_policy(
        frozenset({"t.py::x"}), frozenset(), entries, invocation)
    assert "t.py::x" not in new, "contention evidence left a regression"
    assert "t.py::x" in inherited, "contention evidence was not inherited"

    assert bd.check_baseline_red_evidence(entries, invocation) == (), (
        "contention evidence with a matching invocation must validate")
