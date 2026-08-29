"""Red-first: this iteration's gate results must outlive the JSONL write.

``run_ilk_loop_claude.sh:2235-2240`` deletes the results file while building
the ``.ilk-loop.log`` record::

    if [[ -n "$local_checks_results" && -s "$local_checks_results" ]]; then
      local_checks_json=$(python3 -c "..." < "$local_checks_results")
      rm -f "$local_checks_results"          # <- :2240
    fi

``test_ship_integrity`` is then called with that same path at ``:2312``.  Its
gate lookup does ``Path(lc_file).read_text()`` inside
``except (OSError, json.JSONDecodeError): pass``, so the missing file is
swallowed, ``gate_passed`` stays ``'skip'``, and the caller's guard at
``:1259`` skips the sub-plan entirely.

The guard is correct and must stay: it is the 2026-08-20 cross-run scoping fix
that stopped 69 of 150 sub-plans being reverted.  The bug is that a *deleted
file* and a *sub-plan not gated this iteration* arrive at it looking identical.
Do not loosen the guard; make the file survive.

AC-3, AC-4 of sub-plan ``a-red-gate-cannot-ship-a-subplan``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")


# ── helpers ──────────────────────────────────────────────────────────────────

def _runner_lines() -> list[str]:
    return RUNNER.read_text(encoding="utf-8").splitlines()


def _line_of(pattern: str) -> int:
    """1-indexed line number of the first line matching ``pattern``."""
    rx = re.compile(pattern)
    for n, line in enumerate(_runner_lines(), start=1):
        if rx.search(line):
            return n
    raise AssertionError(
        f"no line in {RUNNER.name} matches {pattern!r} — the runner was "
        "restructured and this test's anchors need re-deriving"
    )


def _dotsource(script: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run ``script`` in a shell that has the runner's functions but not its main."""
    return subprocess.run(
        ["bash", "-c", f"export ILK_DOTSOURCE_ONLY=1; source '{RUNNER}'; {script}"],
        capture_output=True, text=True, timeout=120, cwd=str(cwd),
        env={"ILK_DOTSOURCE_ONLY": "1", "PATH": _PATH, "HOME": str(cwd)},
    )



def _shipped_subplan(plans: Path, slug: str, *, gated: bool = True) -> Path:
    """A `shipped` sub-plan whose every step has a commit, so only the gate is at issue."""
    gate = ("local_checks:\n"
            "  - command: python3 -c 'raise SystemExit(1)'\n"
            "    timeout: 60\n") if gated else "local_checks: []\n"
    sp = plans / f"{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        f"{gate}"
        "---\n\n"
        f"# {slug}\n\n"
        "### Step 0 — do the thing\n\nBody.\n",
        encoding="utf-8",
    )
    return sp


def _status_of(sp: Path) -> str:
    m = re.search(r"^status:\s*(\S+)", sp.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else "<none>"


# ── AC-3: the file is still there when enforcement reads it ─────────────────

def test_results_file_outlives_the_ship_integrity_call(tmp_path: Path) -> None:
    """AC-3 — every ``rm -f`` of ``$local_checks_results`` comes AFTER enforcement.

    Asserted on the runner's own source because the defect is purely one of
    ordering inside the iteration body: the function is correct, it is simply
    handed a path that no longer exists.  The dot-source below proves the
    anchors name a real function rather than a string that happens to appear.
    """
    probe = _dotsource("declare -F test_ship_integrity && echo OK", tmp_path)
    assert "OK" in probe.stdout, (
        "test_ship_integrity is not defined after dot-sourcing the runner: "
        f"{probe.stdout!r} {probe.stderr!r}"
    )

    call_line = _line_of(r'test_ship_integrity "\$\(get_plans_dir\)"')
    rm_lines = [
        n for n, line in enumerate(_runner_lines(), start=1)
        if re.search(r'rm -f "\$local_checks_results"', line)
    ]
    assert rm_lines, (
        "no `rm -f \"$local_checks_results\"` found — if the cleanup moved, "
        "re-derive this test's anchor rather than deleting the assertion"
    )
    early = [n for n in rm_lines if n < call_line]
    assert not early, (
        f"$local_checks_results is deleted at line(s) {early} but "
        f"test_ship_integrity reads it at line {call_line}. The gate lookup "
        "hits OSError, gate_passed stays 'skip', and the :1259 guard skips "
        "the sub-plan — a red gate ships as verified."
    )


# ── AC-4: the cross-run scoping guard is unchanged ──────────────────────────

def test_subplan_absent_from_this_iterations_jsonl_is_left_alone(
    tmp_path: Path,
) -> None:
    """AC-4 — regression guard for the 2026-08-20 mass revert (69 of 150).

    Two shipped sub-plans, one results file naming only the first.  ``red``
    must be reverted (enforcement works); ``prior-run`` must be untouched (the
    guard holds).  Making the results file survive AC-3 must not turn "not
    gated this iteration" into "gate unknown ⇒ revert".
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    red = _shipped_subplan(plans, "red-this-iteration")
    prior = _shipped_subplan(plans, "shipped-in-a-prior-run")

    lc = tmp_path / "local_checks_results.jsonl"
    lc.write_text(json.dumps({
        "slug": "red-this-iteration", "step": 0,
        "outcome": "fail", "exit_code": 1,
    }) + "\n", encoding="utf-8")

    proc = _dotsource(
        f"set +e; test_ship_integrity '{plans}' '{lc}'; echo \"RC=$?\"",
        tmp_path,
    )

    assert _status_of(prior) == "shipped", (
        "a sub-plan with NO result in this iteration's JSONL was reverted. "
        "That is the 2026-08-20 mass revert: the :1259 scoping guard "
        f"(`!= true && != false -> continue`) must stay.\n{proc.stderr}"
    )
    assert _status_of(red) == "in-progress", (
        "the sub-plan whose gate was red THIS iteration was left at "
        f"`shipped`.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "RC=1" in proc.stdout, (
        "test_ship_integrity must report a non-zero violation count so the "
        f"caller can stop the run; got {proc.stdout!r}"
    )
