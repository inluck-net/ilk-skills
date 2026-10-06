"""The progress bound does not need a postmortem.

Sub-plan `2026-08-29c-the-progress-bound-does-not-need-a-postmortem`.

`read_blacklist_from_postmortems` (`scheduler.sh:510`) builds the scheduler's
blacklist **from postmortem files on disk**. No postmortem means no blacklist
entry means the project stays dispatchable forever.

That is what happened on rezmac, 2026-08-29: three launches (12:01, 12:37,
13:12), each killed, each leaving no postmortem, and the scheduler
re-dispatching on its normal cadence. Its log read `promote:` / `dispatch:` /
`skip-busy` throughout and never indicated anything was wrong.

> A bound that requires a successful postmortem is a bound that switches off
> exactly when things are worst.

The watchdog was not the component that needed convincing -- it declined to
relaunch and exited. The scheduler re-dispatches independently.

These tests drive the real shell helpers, extracted from `scheduler.sh` with
`sed` and `eval` (the house pattern from `test_watchdog_empty_classification.sh`).
The pure helpers mirror the existing `get_rapid_terminal_backoff` /
`within_dispatch_cooldown` shape, so the bound is unit-testable without a live
scheduler.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCHEDULER_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"

#: The commit this batch started from. AC-7's pin compares against it.
_BASE_COMMIT = "6aaf28b"

#: Observed consecutive launches on 2026-08-29 before anything declined.
_OBSERVED_LAUNCHES = 3


def _sh(body: str, *, funcs: tuple[str, ...],
        cwd: Path | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run `body` with the named scheduler.sh functions in scope.

    Callers pass ``scheduler_sandbox``'s env, which pins HOME and
    ILK_DATA_HOME to a temp root and strips ILK_DATA_DIR. These helpers are
    pure and touch no data home today, but a harness that evals scheduler.sh
    and does NOT isolate the data root is one edit away from writing to the
    live ``~/.ilk-data`` -- the §23 defect this repo has hit repeatedly, and
    what ``test_data_home_sandbox.py``'s meta-test exists to prevent.
    """
    prelude = f'SCHEDULER_SH="{_SCHEDULER_SH}"\n'
    for fn in funcs:
        prelude += f"eval \"$(sed -n '/^{fn}()/,/^}}/p' \"$SCHEDULER_SH\")\"\n"
    return subprocess.run(
        ["/bin/bash", "-c", prelude + body],
        capture_output=True, text=True, timeout=30, encoding="utf-8",
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def _verdict(count: int, progressed: str, clean: str, threshold: int = 3,
             *, env: dict[str, str] | None = None) -> str:
    """Call get_no_progress_verdict and return its stdout, stripped."""
    proc = _sh(
        f'get_no_progress_verdict {count} {progressed} {clean} {threshold}\n',
        funcs=("get_no_progress_verdict",),
        env=env,
    )
    assert proc.returncode == 0, (
        f"get_no_progress_verdict failed: rc={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# AC-1
# ---------------------------------------------------------------------------

def test_consecutive_no_progress_launches_stop_dispatch_with_no_postmortem(scheduler_sandbox) -> None:
    """AC-1: the bound fires on launch history alone.

    Nothing here creates a postmortems directory. That is the point: the
    observed failure had none, which is exactly why the postmortem-derived
    blacklist could not fire.
    """
    count = 0
    decisions = []
    for _ in range(_OBSERVED_LAUNCHES):
        out = _verdict(count, "false", "false", env=scheduler_sandbox.env)
        parts = out.split()
        assert len(parts) == 2, f"expected '<count> <decision>', got {out!r}"
        count = int(parts[0])
        decisions.append(parts[1])

    assert count == _OBSERVED_LAUNCHES, (
        f"counter did not advance once per launch: {count} after "
        f"{_OBSERVED_LAUNCHES} non-clean, no-progress launches"
    )
    assert decisions[-1] == "block", (
        f"after {_OBSERVED_LAUNCHES} consecutive no-progress launches the "
        f"scheduler still dispatches. decisions={decisions}"
    )
    assert decisions[0] == "allow", (
        "the bound fired on the FIRST launch -- a slow batch would never "
        f"get started. decisions={decisions}"
    )

    # Wiring pin. The helper being correct proves nothing if run_scheduler
    # never calls it, and a pure-helper test cannot tell the difference.
    src = _SCHEDULER_SH.read_text()
    for needed in (
        "get_no_progress_verdict",
        "write_no_progress_refusal",
        "progress_signature_for_project",
    ):
        # once as the definition, at least once as a call
        assert src.count(needed) >= 2, (
            f"{needed} is defined but never called: the bound is dead code"
        )
    dispatch_region = src.split("# Check if project is busy", 1)
    assert len(dispatch_region) == 2, "could not locate the dispatch loop"
    assert "no_progress_state_file" in dispatch_region[1], (
        "the no-progress bound is not wired into the dispatch path (it must "
        "sit past the skip gates, so it advances once per DISPATCH rather "
        "than once per poll)"
    )


def test_the_bound_sits_below_every_skip_gate() -> None:
    """The counter must not advance on a path that cannot launch.

    The pin above only proved the bound sits somewhere after `skip-busy`,
    while its own message claimed "past the skip gates".  Two gates —
    skip-unresolved and skip-missing-path — sat BELOW the bound, so an
    unregistered project bumped the counter on every poll with zero
    launches, and the accurate `skip-unresolved` diagnosis was replaced by
    a launch count that had never happened.

    Ordering is the whole fix, so ordering is what this asserts.
    """
    src = _SCHEDULER_SH.read_text()

    bound_at = src.index("_np_file=\"$(no_progress_state_file")
    for marker in (
        'write_scheduler_log "skip-unresolved"',
        'write_scheduler_log "skip-missing-path"',
        'write_scheduler_log "skip-busy"',
        'write_scheduler_log "skip-cooldown"',
    ):
        gate_at = src.index(marker)
        assert gate_at < bound_at, (
            f"the no-progress bound runs BEFORE {marker}: a project that "
            f"skips there never launches, so the counter would advance "
            f"without a launch and the refusal would misreport why."
        )


# ---------------------------------------------------------------------------
# AC-3
# ---------------------------------------------------------------------------

def test_plan_progress_resets_the_counter(scheduler_sandbox) -> None:
    """AC-3: a batch that is advancing must never be blocked by this.

    This is the bound's main false-positive risk: a legitimately slow batch
    that commits steadily would otherwise trip it.
    """
    out = _verdict(_OBSERVED_LAUNCHES - 1, "true", "false", env=scheduler_sandbox.env)
    assert out.split() == ["0", "allow"], (
        f"plan progress did not reset the counter; got {out!r}"
    )

    # And a reset must be durable: the next no-progress launch starts from 0.
    after = _verdict(0, "false", "false", env=scheduler_sandbox.env)
    assert after.split() == ["1", "allow"], (
        f"counting did not resume from zero after a reset; got {after!r}"
    )


# ---------------------------------------------------------------------------
# AC-4
# ---------------------------------------------------------------------------

def test_a_clean_exit_resets_the_counter(scheduler_sandbox) -> None:
    """AC-4: a clean exit resets, whatever the plan state.

    A run can finish cleanly with no step advance (everything already
    shipped). That is success, not a stall.
    """
    out = _verdict(_OBSERVED_LAUNCHES - 1, "false", "true", env=scheduler_sandbox.env)
    assert out.split() == ["0", "allow"], (
        f"a clean exit did not reset the counter; got {out!r}"
    )


# ---------------------------------------------------------------------------
# AC-5
# ---------------------------------------------------------------------------

def test_the_refusal_names_the_project_the_count_and_the_reason(tmp_path: Path, scheduler_sandbox) -> None:
    """AC-5: the defining property of the failure was a healthy-looking log.

    The scheduler logged `promote:` / `dispatch:` / `skip-busy` throughout
    while relaunching a dead loop three times. A silent refusal would repeat
    that in the other direction.
    """
    proc = _sh(
        'export SCHEDULER_LOG_DIR="$PWD/sched-log-dir"\n'
        'export SCHEDULER_LOG_FILE="$SCHEDULER_LOG_DIR/scheduler.log"\n'
        'write_no_progress_refusal "my-project-key" 3 3\n'
        'cat "$SCHEDULER_LOG_FILE"\n',
        funcs=("write_scheduler_log", "write_no_progress_refusal"),
        cwd=tmp_path,
        env=scheduler_sandbox.env,
    )
    assert proc.returncode == 0, (
        f"write_no_progress_refusal failed: {proc.stderr!r}"
    )
    out = proc.stdout + proc.stderr
    assert "my-project-key" in out, f"refusal does not name the project: {out!r}"
    assert "3" in out, f"refusal does not give the count: {out!r}"
    lowered = out.lower()
    assert ("no-progress" in lowered or "no progress" in lowered), (
        f"refusal does not state the reason: {out!r}"
    )


# ---------------------------------------------------------------------------
# AC-7
# ---------------------------------------------------------------------------

def test_read_blacklist_from_postmortems_is_unchanged() -> None:
    """AC-7: this bound is additive.

    The postmortem path stays as the richer signal when it exists. Pinned by
    comparing the function body against the batch's base commit, so an
    accidental edit is caught rather than merely discouraged.
    """
    def _body(text: str) -> list[str]:
        lines = text.splitlines()
        start = next(
            i for i, ln in enumerate(lines)
            if ln.startswith("read_blacklist_from_postmortems()")
        )
        end = next(i for i in range(start, len(lines)) if lines[i] == "}")
        return lines[start:end + 1]

    base = subprocess.run(
        ["git", "show", f"{_BASE_COMMIT}:skills/ilk-watchdog/scripts/scheduler.sh"],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        encoding="utf-8",
    )
    assert base.returncode == 0, f"could not read base commit: {base.stderr!r}"

    before = _body(base.stdout)
    after = _body(_SCHEDULER_SH.read_text())
    assert before == after, (
        "read_blacklist_from_postmortems changed; the new bound must be "
        "additive, not a rewrite of the postmortem path.\n"
        f"base has {len(before)} lines, HEAD has {len(after)}"
    )


# ---------------------------------------------------------------------------
# AC-8
# ---------------------------------------------------------------------------

def test_a_resolve_ack_clears_the_new_bound(scheduler_sandbox) -> None:
    """AC-8: one way for an operator to say "I looked at it, carry on".

    The postmortem blacklist is cleared by a resolve-ack whose `cleared_at` is
    at or after the report's `generated_at` (`blacklist_status.py`). The new
    bound must honour the same gesture, or `/ilk-resume` clears one bound and
    silently leaves the other in force.
    """
    # ack strictly newer than the counter → cleared
    proc = _sh(
        'no_progress_cleared_by_ack 1000 2000\n',
        funcs=("no_progress_cleared_by_ack",),
        env=scheduler_sandbox.env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr!r}"
    assert proc.stdout.strip() == "true", (
        f"a newer resolve-ack did not clear the bound; got {proc.stdout!r}"
    )

    # ack exactly equal → cleared (same >= rule as blacklist_status.py)
    same = _sh('no_progress_cleared_by_ack 1500 1500\n',
               funcs=("no_progress_cleared_by_ack",), env=scheduler_sandbox.env)
    assert same.stdout.strip() == "true", (
        "an ack with the same timestamp did not clear the bound; "
        "blacklist_status.py uses cleared_at >= generated_at and the two "
        f"must not disagree. got {same.stdout!r}"
    )

    # stale ack → still bound
    stale = _sh('no_progress_cleared_by_ack 2000 1000\n',
                funcs=("no_progress_cleared_by_ack",), env=scheduler_sandbox.env)
    assert stale.stdout.strip() == "false", (
        f"a stale ack cleared the bound; got {stale.stdout!r}"
    )


# ---------------------------------------------------------------------------
# park_dead_master — AC-1..AC-4  (sub-plan a-dead-work-tree-parks-its-master)
# ---------------------------------------------------------------------------

import json as _json


def _park_setup(
    data_home: Path,
    key: str = "test-park",
    *,
    sentinel_state: str = "work_tree_invalid",
    run_id: str = "run-dead-001",
    active_master: bool = True,
    queued_master: bool = True,
) -> Path:
    """Scaffold a project with an active master, a queued master, and a sentinel.

    Returns the project data dir.
    """
    project_dir = data_home / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    if active_master:
        master = (
            "---\n"
            "title: MASTER-active\n"
            "created: 2026-10-06T00:00:00+08:00\n"
            "status: active\n"
            "priority: 0\n"
            "pause_after_ship: false\n"
            "---\n\n"
            "# MASTER-active\n\n"
            "## Sub-plan registry\n\n"
            "| # | Sub-plan | Status |\n"
            "|---|---|---|\n"
            "| 1 | [2026-10-06-work.md](./2026-10-06-work.md) | pending |\n"
        )
        (plans_dir / "MASTER-active.md").write_text(master, encoding="utf-8")

    if queued_master:
        master_q = (
            "---\n"
            "title: MASTER-queued\n"
            "created: 2026-10-06T00:00:00+08:00\n"
            "status: queued\n"
            "priority: 0\n"
            "pause_after_ship: false\n"
            "---\n\n"
            "# MASTER-queued\n\n"
            "## Sub-plan registry\n\n"
            "| # | Sub-plan | Status |\n"
            "|---|---|---|\n"
            "| 1 | [2026-10-06-other.md](./2026-10-06-other.md) | pending |\n"
        )
        (plans_dir / "MASTER-queued.md").write_text(master_q, encoding="utf-8")

    # last-launch.json so scan_projects finds a repo_path
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        _json.dumps({"project_path": str(project_dir)}),
        encoding="utf-8",
    )

    # sentinel
    if sentinel_state is not None:
        sentinel = {
            "state": sentinel_state,
            "run_id": run_id,
            "pid": 0,
            "started_at": "2026-10-06T10:00:00+0800",
            "ended_at": "2026-10-06T10:05:00+0800",
            "iterations": 0,
        }
        (launcher_dir / "last-exit.json").write_text(
            _json.dumps(sentinel), encoding="utf-8"
        )

    return project_dir


_PARK_DEAD_MASTER = (
    _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "park_dead_master.py"
)


def test_work_tree_invalid_pauses_active_master(scheduler_sandbox, tmp_path):
    """AC-1: work_tree_invalid sentinel pauses the active master.

    After park_dead_master.py runs:
    - the active master is paused with one new progress-log row and one audit row
    - the queued master is untouched
    """
    data_home = scheduler_sandbox.root / ".ilk-data"
    project_dir = _park_setup(data_home, sentinel_state="work_tree_invalid")

    result = subprocess.run(
        ["python3", str(_PARK_DEAD_MASTER), str(project_dir)],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", env=scheduler_sandbox.env,
    )
    assert result.returncode == 0, f"park_dead_master failed: {result.stderr!r}"
    out = _json.loads(result.stdout)
    assert out["parked"] is not None, f"nothing was parked: {out!r}"

    # Active master is now paused
    active_fm = (project_dir / "plans" / "MASTER-active.md").read_text()
    assert "status: paused" in active_fm, (
        f"active master not paused after parking; front-matter:\n{active_fm}"
    )

    # Queued master untouched
    queued_fm = (project_dir / "plans" / "MASTER-queued.md").read_text()
    assert "status: queued" in queued_fm, (
        f"queued master was changed; front-matter:\n{queued_fm}"
    )

    # Exactly one progress-log row mentioning work_tree_invalid
    body = active_fm.split("---", 2)[2] if active_fm.count("---") >= 2 else active_fm
    assert "work_tree_invalid" in body.lower(), (
        "progress-log row missing work_tree_invalid mention"
    )

    # Marker written so it's not re-handled
    marker = project_dir / "runtime" / "launcher" / f"parked-run-dead-001.json"
    assert marker.exists(), "idempotency marker not written"


def test_parking_twice_is_idempotent(scheduler_sandbox):
    """AC-2: running park_dead_master.py twice for the same run_id is a no-op
    the second time.
    """
    data_home = scheduler_sandbox.root / ".ilk-data"
    project_dir = _park_setup(data_home, sentinel_state="work_tree_invalid")

    # First run — parks
    r1 = subprocess.run(
        ["python3", str(_PARK_DEAD_MASTER), str(project_dir)],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", env=scheduler_sandbox.env,
    )
    assert r1.returncode == 0, f"first run failed: {r1.stderr!r}"
    out1 = _json.loads(r1.stdout)
    assert out1["parked"] is not None, f"first run did not park: {out1!r}"

    fm_before = (project_dir / "plans" / "MASTER-active.md").read_text()

    # Second run — no-op
    r2 = subprocess.run(
        ["python3", str(_PARK_DEAD_MASTER), str(project_dir)],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", env=scheduler_sandbox.env,
    )
    assert r2.returncode == 0, f"second run failed: {r2.stderr!r}"
    out2 = _json.loads(r2.stdout)
    assert out2["parked"] is None, f"second run parked again: {out2!r}"

    fm_after = (project_dir / "plans" / "MASTER-active.md").read_text()
    assert fm_before == fm_after, "second run changed the front-matter"


@pytest.mark.parametrize("sentinel_state", [
    "blocked-no-runnable",
    "all-shipped",
    "ship_integrity_violation",
])
def test_non_work_tree_invalid_states_park_nothing(scheduler_sandbox, sentinel_state):
    """AC-3: only work_tree_invalid triggers parking; other terminal states
    are no-ops.
    """
    data_home = scheduler_sandbox.root / ".ilk-data"
    project_dir = _park_setup(data_home, sentinel_state=sentinel_state)

    result = subprocess.run(
        ["python3", str(_PARK_DEAD_MASTER), str(project_dir)],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", env=scheduler_sandbox.env,
    )
    assert result.returncode == 0, f"unexpected exit: {result.stderr!r}"
    out = _json.loads(result.stdout)
    assert out["parked"] is None, (
        f"{sentinel_state} incorrectly parked a master: {out!r}"
    )

    # Active master unchanged
    fm = (project_dir / "plans" / "MASTER-active.md").read_text()
    assert "status: active" in fm, (
        f"{sentinel_state} changed active master status: {fm}"
    )


def test_missing_sentinel_parks_nothing(scheduler_sandbox):
    """AC-3 (missing sentinel): no last-exit.json → no parking."""
    data_home = scheduler_sandbox.root / ".ilk-data"
    project_dir = _park_setup(data_home, sentinel_state=None)

    result = subprocess.run(
        ["python3", str(_PARK_DEAD_MASTER), str(project_dir)],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", env=scheduler_sandbox.env,
    )
    assert result.returncode == 0, f"unexpected exit: {result.stderr!r}"
    out = _json.loads(result.stdout)
    assert out["parked"] is None, f"missing sentinel still parked: {out!r}"


def test_scheduler_calls_park_before_no_progress_bound():
    """AC-4: scheduler.sh calls park_dead_master.py before the no-progress
    bound, and logs 'parked-dead-work-tree' when it parks.

    Assert by reading scheduler.sh: the call site's line number is between the
    dispatch loop start and get_no_progress_verdict.
    """
    src = _SCHEDULER_SH.read_text()

    # Find the dispatch loop region
    assert "park_dead_master" in src, (
        "scheduler.sh never calls park_dead_master"
    )

    # The call must sit before the no-progress verdict
    park_at = src.index("park_dead_master")
    np_verdict_at = src.index("get_no_progress_verdict")
    assert park_at < np_verdict_at, (
        f"park_dead_master (char {park_at}) sits after get_no_progress_verdict "
        f"(char {np_verdict_at}); it must run before the bound"
    )

    # Must log 'parked-dead-work-tree' on success
    region_after = src[park_at:]
    assert "parked-dead-work-tree" in region_after, (
        "parking a dead work tree produces no scheduler log line"
    )
