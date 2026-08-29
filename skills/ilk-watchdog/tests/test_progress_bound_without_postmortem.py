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
