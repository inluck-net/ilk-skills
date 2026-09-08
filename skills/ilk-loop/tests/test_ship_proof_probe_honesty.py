"""Red-first: a failed status probe must not become a ledger row.

``write_ship_proof_records`` resolves each worked slug's post-iteration
``current_step`` by shelling out to ``loop_status.py --json``.  Until this
change that call read::

    status_json=$(cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" --json 2>/dev/null) || true

``|| true`` discards the exit status and ``2>/dev/null`` discards the reason,
so a probe that never answered is indistinguishable from one that answered
"no advance".  The ``if [[ -n "$status_json" ]]`` guard below it then never
fires, ``to_val`` keeps its pre-iteration value, and the writer emits a record
asserting ``step_to == step_from`` — **zero progress attributed on evidence
that does not exist**.

That matters because of ``ship_audit.py:204``: the ledger union is gated on
the slug carrying no commit trailers, which is exactly the shared-remote
regime the ledger was built for.  There the ledger is the *only* evidence, so
a false zero-progress row is not a harmless no-op — it is the record an
unattended publisher reads before refusing to ship work that shipped fine.

The rule this restores is already stated 30 lines further down the same
function, guarding the ledger-directory resolve::

    # Do NOT silence stderr: get_ilk_runtime_dir reports a missing resolver or
    # a failed resolve there, and a swallowed probe failure is not data

AC-1  probe fails  ⇒ zero ledger rows, and the reason is reported on stderr
AC-2  probe succeeds ⇒ rows carry ``provenance: loop-executed``
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_ship_proof_ledger_writer import (
    RUNNER,
    WRITER_FUNC,
    _git,
    _make_project,
    _read_ledger,
    _sandbox_env,
)


def _run_writer_with_status(
    project: Path,
    env: dict[str, str],
    *,
    loop_status_script: Path | str,
    pre_iter_target: str,
    before: str,
    after: str,
    run_id: str,
    iteration: int,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call the writer with a chosen status probe.

    Mirrors ``test_ship_proof_ledger_writer._run_writer`` but lets the caller
    point ``LOOP_STATUS_SCRIPT`` somewhere that fails, which is the condition
    under test.
    """
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")

    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='{run_id}'
LOOP_STATUS_SCRIPT='{loop_status_script}'
PRE_ITER_TARGET=$'{pre_iter_target}'
set +e
declare -F {WRITER_FUNC} >/dev/null || {{ echo "WRITER_MISSING"; exit 90; }}
{WRITER_FUNC} '{heads_dir / "before"}' '{heads_dir / "after"}' {iteration}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(project),
    )


def _productive_iteration(project: Path) -> tuple[str, str]:
    """Make two trailerless commits; return (before, after)."""
    before = _git(project, "rev-parse", "HEAD")
    for n in (1, 2):
        (project / f"file{n}.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", f"fix(app): change {n}")
    return before, _git(project, "rev-parse", "HEAD")


# ── AC-1 ─────────────────────────────────────────────────────────────────────

def test_failed_status_probe_writes_no_ledger_row(tmp_path: Path) -> None:
    """A probe that could not answer must produce no attribution at all.

    The sub-plan really did advance (``current_step: 3``) and the iteration
    really did commit, but the probe is unavailable, so the driver cannot know
    ``step_to``.  Writing ``step_from == step_to`` here would assert zero
    progress on a question that was never answered.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (3, 3)})
    env = _sandbox_env(tmp_path)
    before, after = _productive_iteration(project)

    missing_probe = project.parent / "no-such-loop-status.py"
    assert not missing_probe.exists()

    proc = _run_writer_with_status(
        project, env,
        loop_status_script=missing_probe,
        pre_iter_target="gate-work 0",
        before=before, after=after,
        run_id="20260909-090000", iteration=4,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    assert records == [], (
        "a failed loop_status probe must write NO ledger row; got "
        f"{records}. A row whose step_to equals step_from asserts zero "
        "progress on evidence that does not exist — and on a shared remote "
        "(ship_audit.py:204) that row is the only evidence a publisher has."
        f"\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    combined = proc.stdout + proc.stderr
    assert "ship-proof" in combined, (
        "the writer must say why it wrote nothing — a silent skip reads "
        f"identically to 'no commits this iteration'.\n{combined}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_written_records_carry_loop_executed_provenance(tmp_path: Path) -> None:
    """Rows the driver writes must identify themselves as loop-executed.

    A hand-executed batch writes no rows at all, so without this field an
    empty ledger is ambiguous between "nobody ran the loop" and "the writer
    is broken".  The driver can only speak for itself, so it stamps the rows
    it does write.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (3, 3)})
    env = _sandbox_env(tmp_path)
    before, after = _productive_iteration(project)

    proc = _run_writer_with_status(
        project, env,
        loop_status_script=RUNNER.parent / "loop_status.py",
        pre_iter_target="gate-work 0",
        before=before, after=after,
        run_id="20260909-090001", iteration=4,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected one record for one worked sub-plan, got {records}"
        f"\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert records[0].get("provenance") == "loop-executed", (
        "a driver-written row must carry provenance='loop-executed' so an "
        f"empty ledger is diagnosable; got {records[0]!r}"
    )
    # The honest path must still attribute correctly.
    assert records[0]["step_from"] == 0
    assert records[0]["step_to"] == 3
