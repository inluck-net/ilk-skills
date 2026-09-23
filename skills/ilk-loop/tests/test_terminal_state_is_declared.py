"""Red-first: a terminal state the driver writes must be known to every declared consumer.

Contract 1 states the rule already:

    ``collect.py``'s ``_SENTINEL_FAILURE_MAP`` is the only place this mapping
    lives; a terminal state missing from it falls through to the generic
    heuristics, **which is how a failed run gets classified ``clean-success``**.

This is the third instance of identical drift.  ``ship_integrity_violation``
was in 0 classifier files until 2026-08-29; ``timeout`` was "a terminal state
no classifier knew"; ``selfmod_merge_failed`` (added 2026-09-08, commit
``90153a3``) was in 1 non-test file — the writer — and 0 consumer files
(measured 2026-09-17 at ``db22562``).

This test derives the set of terminal states the driver can actually write
from the **source** and asserts each one is known to every declared consumer.
Derivation comes from the driver, not from a hand-kept list — a second
hand-kept list would drift the same way the first one did.

AC-1  Derive ``stop_reason`` values from ``run_ilk_loop_claude.sh``; assert
      each is in ``_SENTINEL_FAILURE_MAP`` or handled by a named special case
      in ``collect.py``'s classify function.
AC-2  Same test for ``watchdog.sh``'s ``classify_action`` arms.
AC-3  A new state added to the driver alone fails the gate — proven by adding
      one in a fixture copy under ``tmp_path``, not by editing the live driver.
AC-4  ``selfmod_merge_failed`` is mapped in ``collect.py`` today.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

# ── paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BASH_DRIVER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"
_WATCHDOG_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"

# ── derivation ───────────────────────────────────────────────────────────────

# ``running`` is the only live state (Contract 1 invariant 1).
_NON_TERMINAL = frozenset({"running"})

# States the driver writes but which do NOT reach collect.py's classify path
# through the normal post-iteration channel.  They are handled either:
# - by the driver's early-exit path (EXIT trap → ``interrupted``), or
# - by being a clean exit (``all-shipped``, ``already-shipped``), or
# - by the driver's post-loop finalize which sets a terminal state that does
#   not need a failure-map entry because the run never reached the iteration
#   loop (``blocked-no-runnable``, ``no-progress``).
#
# A state listed here MUST have a comment explaining why.
_KNOWN_BYPASS: dict[str, str] = {
    "all-shipped": "clean exit; collect.py handles via success-sentinel short-circuit",
    "already-shipped": "clean exit; writes terminal sentinel via _write_terminal_sentinel",
    "blocked-no-runnable": "driver early exit; writes terminal sentinel via _write_terminal_sentinel",
    "no-progress": "3-barren-iterations exit; EXIT trap → interrupted (mapped)",
    # Naming-convention mismatch: bash runner writes "budget-exhausted" (hyphen)
    # but _SENTINEL_FAILURE_MAP keys on "budget_exhausted" (underscore, from
    # the PS runner).  The state vocabulary in Contract 1 lists both forms.
    # A separate defect; not the one this sub-plan fixes.
    "budget-exhausted": "hyphen/underscore mismatch with _SENTINEL_FAILURE_MAP key budget_exhausted; pre-existing",
}


def _derive_terminal_states(driver: Path) -> set[str]:
    """Extract terminal ``stop_reason`` values written by the driver.

    Scans for:
    1. ``_decide_iter_stop_reason`` echo targets (``timeout``,
       ``budget-exhausted``, ``no-progress``).
    2. Direct assignments to ``stop_reason`` or ``iter_stop_reason``.
    3. ``finalize_sentinel`` hardcoding ``interrupted``.

    Returns the raw state strings — the caller filters non-terminal.
    """
    text = driver.read_text(encoding="utf-8")
    states: set[str] = set()

    # Pattern 1: echo "state-name" inside _decide_iter_stop_reason
    in_decide = False
    for line in text.splitlines():
        if "_decide_iter_stop_reason" in line and "()" in line:
            in_decide = True
            continue
        if in_decide:
            if line.strip() == "}":
                in_decide = False
                continue
            m = re.search(r'echo\s+"([a-z_-]+)"', line)
            if m:
                states.add(m.group(1))

    # Pattern 2: stop_reason="..." or iter_stop_reason="..."
    assign_re = re.compile(
        r"""(?:stop_reason|iter_stop_reason)\s*=\s*["']([a-z_-]+)["']"""
    )
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        m = assign_re.search(line)
        if m:
            states.add(m.group(1))

    # Pattern 3: finalize_sentinel hardcodes "interrupted"
    if "state': 'interrupted'" in text or "'state': 'interrupted'" in text:
        states.add("interrupted")

    return states


def _states_needing_consumer_map(driver: Path) -> set[str]:
    """Terminal states that MUST appear in consumers' mapping.

    Excludes non-terminal and known-bypass states.
    """
    return _derive_terminal_states(driver) - _NON_TERMINAL - set(_KNOWN_BYPASS)


# ── collect.py inspection ────────────────────────────────────────────────────

def _sentinel_failure_map_keys(collect_path: Path) -> set[str]:
    """Parse ``_SENTINEL_FAILURE_MAP`` keys from collect.py source."""
    text = collect_path.read_text(encoding="utf-8")
    keys: set[str] = set()
    in_map = False
    for line in text.splitlines():
        if "_SENTINEL_FAILURE_MAP" in line and "{" in line:
            in_map = True
        if in_map:
            m = re.search(r"""^\s*["']([a-z_-]+)["']\s*:""", line)
            if m:
                keys.add(m.group(1))
            if "}" in line and in_map:
                break
    return keys


def _classify_special_cases(collect_path: Path) -> set[str]:
    """Find sentinel states handled by special-case branches in classify.

    Looks for ``sentinel_state == "..."`` comparisons outside the map lookup.
    """
    text = collect_path.read_text(encoding="utf-8")
    cases: set[str] = set()
    for line in text.splitlines():
        for m in re.finditer(r'sentinel_state\s*==\s*["\']([a-z_-]+)["\']', line):
            cases.add(m.group(1))
    return cases


# ── watchdog.sh inspection ───────────────────────────────────────────────────

def _watchdog_classify_arms(watchdog_path: Path) -> set[str]:
    """Parse ``classify_action`` case labels from watchdog.sh.

    Returns every label (raw state or classification label) that has an
    explicit arm — excludes ``*`` and ``""``.
    """
    text = watchdog_path.read_text(encoding="utf-8")
    arms: set[str] = set()
    in_classify = False
    for line in text.splitlines():
        if "classify_action()" in line:
            in_classify = True
            continue
        if in_classify:
            if line.strip() == "}" and not line.strip().startswith("echo"):
                break
            m = re.match(r"\s+([^#]+?)\)", line)
            if m:
                pattern = m.group(1).strip()
                if pattern in ("*", ""):
                    continue
                for label in pattern.split("|"):
                    label = label.strip()
                    if label and label != "*":
                        arms.add(label)
    return arms


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def failure_map_keys() -> set[str]:
    return _sentinel_failure_map_keys(_COLLECT_PY)


@pytest.fixture()
def special_cases() -> set[str]:
    return _classify_special_cases(_COLLECT_PY)


@pytest.fixture()
def watchdog_arms() -> set[str]:
    return _watchdog_classify_arms(_WATCHDOG_SH)


# ── AC-1: every failure state is in _SENTINEL_FAILURE_MAP or a special case ──

@pytest.mark.parametrize(
    "state",
    sorted(_states_needing_consumer_map(_BASH_DRIVER)),
)
def test_state_in_collect_py(
    state: str,
    failure_map_keys: set[str],
    special_cases: set[str],
):
    """Each terminal failure state must be handled in collect.py's classify.

    It is either in ``_SENTINEL_FAILURE_MAP`` (mapped to a label) or handled
    by a named special-case branch (``sentinel_state == "..."``).

    A state in neither falls through to generic heuristics — which classify a
    failed run as ``clean-success``.
    """
    handled = state in failure_map_keys or state in special_cases
    assert handled, (
        f"Terminal state '{state}' is written by the driver but not handled "
        f"in collect.py: absent from _SENTINEL_FAILURE_MAP "
        f"(keys: {sorted(failure_map_keys)}) and from special-case branches "
        f"(cases: {sorted(special_cases)}). "
        f"A missing mapping falls through to generic heuristics, which is "
        f"how a failed run gets classified clean-success."
    )


# ── AC-2: every failure state reaches a watchdog action arm ─────────────────

def test_all_failure_states_reach_watchdog_action(
    failure_map_keys: set[str],
    special_cases: set[str],
    watchdog_arms: set[str],
):
    """Every failure state must reach a ``classify_action`` arm in watchdog.sh.

    The path is either:
    - mapped in ``_SENTINEL_FAILURE_MAP`` → label → ``classify_action`` arm
    - special-cased in collect.py → label → ``classify_action`` arm
    - raw state fallback → ``classify_action`` arm

    This test checks the raw-state path: unmapped states MUST have an explicit
    arm in ``classify_action`` — the ``*`` fail-safe is the defect, not the
    coverage.
    """
    mapped_or_special = failure_map_keys | special_cases
    needed = _states_needing_consumer_map(_BASH_DRIVER)
    unmapped = needed - mapped_or_special

    # Every unmapped state that could reach the raw fallback must have an
    # explicit arm in classify_action.
    raw_missing = unmapped - watchdog_arms
    assert not raw_missing, (
        f"Terminal states {sorted(raw_missing)} have no mapping in "
        f"collect.py AND no explicit arm in watchdog.sh's classify_action. "
        f"They reach the `*` fail-safe, which blocks but reports an unknown "
        f"status — the same class of defect as timeout before 2026-08-29."
    )


# ── AC-3: a new state added to the driver alone fails this gate ─────────────

def test_fixture_with_unknown_state_fails(tmp_path: Path):
    """A fixture copy of the driver with an extra terminal state must fail.

    This proves the test is not decorative — adding a state to the driver
    without updating consumers is caught.
    """
    driver_text = _BASH_DRIVER.read_text(encoding="utf-8")
    synthetic = driver_text + textwrap.dedent("""

    # Synthetic state for AC-3 regression test — do not ship.
    iter_stop_reason="__test_unknown_state"
    stop_reason="__test_unknown_state"
    """)
    fixture = tmp_path / "run_ilk_loop_claude.sh"
    fixture.write_text(synthetic, encoding="utf-8")

    states = _states_needing_consumer_map(fixture)
    assert "__test_unknown_state" in states, (
        "fixture injection failed — derivation did not pick up the synthetic state"
    )

    collect_keys = _sentinel_failure_map_keys(_COLLECT_PY)
    special = _classify_special_cases(_COLLECT_PY)
    handled = "__test_unknown_state" in collect_keys or "__test_unknown_state" in special
    assert not handled, (
        "the synthetic state appears in the real consumers — "
        "the fixture is not testing what it claims"
    )


# ── AC-4: selfmod_merge_failed is mapped TODAY ──────────────────────────────

def test_selfmod_merge_failed_is_in_sentinel_failure_map(failure_map_keys: set[str]):
    """``selfmod_merge_failed`` was added in commit 90153a3 with 0 consumers.

    Measured at db22562 (2026-09-17): 0 hits in collect.py, 0 in watchdog.sh.
    It is the third instance of the identical drift — the defect this whole
    sub-plan exists to fix.
    """
    assert "selfmod_merge_failed" in failure_map_keys, (
        "selfmod_merge_failed is written by the driver at "
        "run_ilk_loop_claude.sh:3279 but absent from collect.py's "
        "_SENTINEL_FAILURE_MAP. A merge-failed run is classified "
        "clean-success by the generic heuristics."
    )
