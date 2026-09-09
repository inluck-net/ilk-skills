"""`shipped-unverified` must never route to relaunch — a CROSS-REPO invariant.

gh-resolve maps `_STATE_MAP["shipped-unverified"] = "escalated"`, justified by
this repo routing both `ship_integrity_violation` and `shipped-unproven` to a
single `shipped-unverified` label that both watchdogs treat as needs-human and
never relaunch.  Their recorded falsifier is:

    if ilk ever routes `shipped-unverified` to relaunch, this must become
    `failed`.

As of 2026-09-09 that falsifier lived only as a comment in their repo,
depending on a behaviour in ours — a documented hope, and exactly the kind of
thing a refactor changes silently.  These assertions make it a checked fact on
the side that owns the behaviour, so the change that would break them fails
HERE rather than surfacing as a resolver relaunching work a human was supposed
to inspect.

Why relaunching would be the bad outcome and not merely wrong: the label means
sub-plans shipped whose verification is compile-only, device-manual, or
unproven.  Relaunching such a run does not re-verify anything — it resumes
past the point a human was meant to look, which converts "needs a human" into
"shipped, apparently fine".

Deliberately does NOT spawn `powershell`: `test_label_action_totality.py` in
this directory is declared `baseline_red` because it does, and a new test
inheriting that failure would be unreadable as a signal.

Sub-plan: unassigned — landed directly, 2026-09-09.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_WATCHDOG = (Path(__file__).resolve().parent.parent / "scripts" / "watchdog.sh")
_COLLECT = (
    Path(__file__).resolve().parent.parent.parent
    / "ilk-feedback" / "scripts" / "collect.py"
)


def _classify_action(label: str) -> str:
    """Call watchdog.sh's own `classify_action`, not a copy of its table."""
    script = f"""
    set -euo pipefail
    ILK_DOTSOURCE_ONLY=1 source '{_WATCHDOG}' 2>/dev/null || true
    declare -F classify_action >/dev/null || {{ echo "FN_MISSING"; exit 90; }}
    classify_action '{label}'
    """
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60)
    out = proc.stdout.strip().splitlines()
    assert "FN_MISSING" not in proc.stdout, (
        "classify_action is not defined — this test would otherwise pass "
        f"vacuously.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert out, f"classify_action printed nothing.\nstderr: {proc.stderr}"
    return out[-1]


def test_shipped_unverified_is_needs_human() -> None:
    """The invariant gh-resolve's `escalated` mapping rests on."""
    assert _classify_action("shipped-unverified") == "needs-human", (
        "shipped-unverified must route to needs-human. gh-resolve maps it to "
        "`escalated` on this basis; if this changes, their _STATE_MAP entry "
        "must become `failed` in the same change — tell them, do not just "
        "update this assertion"
    )


def test_shipped_unverified_never_relaunches() -> None:
    """Stated as its own assertion because it is the falsifier's exact words.

    A future action vocabulary might add a value that is neither `needs-human`
    nor `relaunch`; that would be fine for the consumer. What must never
    happen is relaunch.
    """
    assert _classify_action("shipped-unverified") != "relaunch", (
        "relaunching a shipped-unverified run resumes past the point a human "
        "was meant to inspect, turning 'needs a human' into 'shipped, "
        "apparently fine' — with no re-verification of the compile-only or "
        "device-manual tiers that produced the label"
    )


@pytest.mark.parametrize("raw", ["ship_integrity_violation", "shipped-unproven"])
def test_both_raw_states_collapse_to_the_guarded_label(raw: str) -> None:
    """The mapping that makes ONE guarded label cover BOTH hazards.

    If either raw state stopped mapping here it would route by its own name
    instead, and the needs-human guarantee above would silently stop covering
    it — without this assertion or the one above failing.
    """
    src = _COLLECT.read_text(encoding="utf-8")
    assert f'"{raw}": "shipped-unverified"' in src, (
        f"{raw} no longer maps to shipped-unverified; the cross-repo "
        "needs-human guarantee no longer covers it"
    )


def test_the_consumer_side_mapping_is_recorded_here() -> None:
    """Documentation-as-assertion: the other half of the contract, in-tree.

    Nothing in this repo can enforce gh-resolve's `_STATE_MAP`. What it CAN do
    is make sure a person changing the routing above reads what depends on it,
    in the file they are editing, rather than discovering it from a resolver
    that stopped escalating.
    """
    assert __doc__ and "_STATE_MAP" in __doc__ and "escalated" in __doc__
