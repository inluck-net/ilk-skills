"""Gate-retry decision logic for the ilk-loop runner.

The runner calls ``decide_gate_retry`` after a confirmed-red post-iteration
gate to decide whether to break the run or continue with a retry.

Contract (sub-plan a-failed-gate-is-retried-in-run):

1. On the FIRST confirmed-red for a given (slug, step) within a run, do NOT
   set the stop reason.  Write the failing checks' tail to
   ``<run dir>/gate-red-<iteration>.txt``, append a prompt line for the next
   iteration, and continue the loop.
2. The SECOND consecutive red on the same (slug, step) takes today's path
   (quarantine count + ``local_checks_failed`` exit).
3. ``environment_fault`` and ``no_commits`` variants keep today's behaviour
   (immediate stop).
4. No timeout, iteration budget or threshold value changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateRetryDecision:
    """Return value from ``decide_gate_retry``."""

    should_stop: bool
    """If True, the runner sets ``iter_stop_reason`` and breaks.
    If False, the runner writes a gate-red file, appends a prompt line,
    and continues the loop.
    """

    stop_reason: str | None
    """The stop reason to set (only meaningful when ``should_stop`` is True).
    One of ``local_checks_failed``, ``local_checks_failed_no_commits``,
    ``local_checks_environment_fault``, or None.
    """

    write_gate_red: bool
    """If True, the runner writes the failing checks' tail to
    ``gate-red-<iteration>.txt``.
    """

    append_prompt_line: bool
    """If True, the runner appends a prompt line for the next iteration."""


def decide_gate_retry(
    *,
    red_count: int,
    is_environment_fault: bool = False,
    is_no_commits: bool = False,
    same_failing_set: bool = False,
) -> GateRetryDecision:
    """Decide whether to retry or stop after a confirmed-red gate.

    Parameters
    ----------
    red_count:
        The number of consecutive red gates for this (slug, step) in the
        current run.  1 = first red, 2 = second red, etc.
    is_environment_fault:
        True when any blocking record has reason starting with
        ``environment-fault:``.
    is_no_commits:
        True when ``total_new == 0`` (gate red on an unchanged tree).
    same_failing_set:
        True when the current red gate has the same failing test ids as
        the previous red gate for this (slug, step).  When True and
        ``red_count >= 2``, the run stops (no progress).  When False and
        ``red_count >= 2``, the run retries (the worker made progress).

    Returns
    -------
    GateRetryDecision
        The decision about whether to stop or retry.
    """
    # Bullet 3: environment_fault and no_commits keep today's behaviour.
    if is_environment_fault:
        return GateRetryDecision(
            should_stop=True,
            stop_reason="local_checks_environment_fault",
            write_gate_red=False,
            append_prompt_line=False,
        )
    if is_no_commits:
        return GateRetryDecision(
            should_stop=True,
            stop_reason="local_checks_failed_no_commits",
            write_gate_red=False,
            append_prompt_line=False,
        )

    # Bullet 1: first red → retry (don't stop).
    if red_count <= 1:
        return GateRetryDecision(
            should_stop=False,
            stop_reason=None,
            write_gate_red=True,
            append_prompt_line=True,
        )

    # Bullet 2: second red → stop only if same failing set (no progress).
    # If the failing set changed, the worker made progress → retry.
    if same_failing_set:
        return GateRetryDecision(
            should_stop=True,
            stop_reason="local_checks_failed",
            write_gate_red=False,
            append_prompt_line=False,
        )

    # Different failing set → progress was made, retry.
    return GateRetryDecision(
        should_stop=False,
        stop_reason=None,
        write_gate_red=True,
        append_prompt_line=True,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point.  Prints JSON to stdout."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Decide whether to retry or stop after a red gate."
    )
    parser.add_argument(
        "--red-count", type=int, required=True,
        help="Number of consecutive red gates for this (slug, step).",
    )
    parser.add_argument(
        "--is-env-fault", type=str, default="false",
        help="True if environment fault detected.",
    )
    parser.add_argument(
        "--is-no-commits", type=str, default="false",
        help="True if gate red on unchanged tree (0 new commits).",
    )
    parser.add_argument(
        "--same-failing-set", type=str, default="false",
        help="True if current red has same failing ids as previous red.",
    )
    args = parser.parse_args()

    d = decide_gate_retry(
        red_count=args.red_count,
        is_environment_fault=args.is_env_fault.lower() == "true",
        is_no_commits=args.is_no_commits.lower() == "true",
        same_failing_set=args.same_failing_set.lower() == "true",
    )
    print(json.dumps({
        "should_stop": d.should_stop,
        "stop_reason": d.stop_reason,
        "write_gate_red": d.write_gate_red,
        "append_prompt_line": d.append_prompt_line,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()