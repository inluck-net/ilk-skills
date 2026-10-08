"""Every label in CLASSIFICATION_LABELS has a narrative and a recommendation.

Sub-plan every-stop-label-has-a-narrative, step 0 (red-first pins).

Five labels fall through _label_narrative to '(no narrative for this label)'
and recommend_params to 'no specific recommendation' at base 6bfeead3:
  merge-conflict, merge-deferred, yielded, local-checks-unchanged,
  blocked-no-runnable.

AC-1 (totality), AC-2 (local-checks-unchanged detail), AC-3 (merge-conflict
routes), AC-4 (rationale), AC-5 (relaunch stance).  AC-6 (taxonomy) is the
full gate — test_taxonomy_documented.py covers it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# Labels that have no narrative/rationale at base 6bfeead3.
_RED_AT_BASE: set[str] = {
    "merge-conflict",
    "merge-deferred",
    "yielded",
    "local-checks-unchanged",
    "blocked-no-runnable",
}

_XFAIL_REASON = "label has no narrative/rationale"


# ── AC-1: totality ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label",
    [
        pytest.param(
            lbl,
            marks=pytest.mark.xfail(strict=True, reason=_XFAIL_REASON),
        )
        if lbl in _RED_AT_BASE
        else lbl
        for lbl in collect.CLASSIFICATION_LABELS
    ],
)
def test_label_narrative_not_placeholder(label):
    """_label_narrative must return real text, not the placeholder."""
    narrative = collect._label_narrative(label, {})
    assert narrative != "(no narrative for this label)", (
        f"{label}: _label_narrative returned the placeholder"
    )
    assert "None" not in narrative, (
        f"{label}: narrative contains literal None: {narrative}"
    )


# ── AC-2: local-checks-unchanged detail ────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
def test_local_checks_unchanged_with_iter():
    """With iter_at_stop in facts, narrative mentions the iteration."""
    facts = {"iter_at_stop": 1}
    narrative = collect._label_narrative("local-checks-unchanged", facts)
    assert "iter 1" in narrative, (
        f"expected 'iter 1' in narrative, got: {narrative}"
    )
    assert "no new commits" in narrative.lower(), (
        f"expected 'no new commits' in narrative, got: {narrative}"
    )
    assert "do not auto-relaunch" in narrative.lower(), (
        f"expected 'Do not auto-relaunch' in narrative, got: {narrative}"
    )


def test_local_checks_unchanged_without_iter():
    """Without iter_at_stop, narrative omits the iter clause."""
    narrative = collect._label_narrative("local-checks-unchanged", {})
    assert "iter " not in narrative, (
        f"unexpected 'iter' clause without iter_at_stop: {narrative}"
    )
    assert "None" not in narrative, (
        f"narrative contains literal None: {narrative}"
    )


# ── AC-3: merge-conflict names its route ───────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
def test_merge_conflict_live_clone():
    facts = {"stop_reason": "selfmod_live_clone_touched"}
    narrative = collect._label_narrative("merge-conflict", facts)
    assert "live clone" in narrative.lower(), (
        f"expected 'live clone' for selfmod_live_clone_touched: {narrative}"
    )


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
def test_merge_conflict_merge_failed():
    facts = {"stop_reason": "selfmod_merge_failed"}
    narrative = collect._label_narrative("merge-conflict", facts)
    assert "merge-back" in narrative.lower(), (
        f"expected 'merge-back' for selfmod_merge_failed: {narrative}"
    )


# ── AC-4: rationale ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label",
    [
        pytest.param(
            lbl,
            marks=pytest.mark.xfail(strict=True, reason=_XFAIL_REASON),
        )
        if lbl in _RED_AT_BASE
        else lbl
        for lbl in _RED_AT_BASE
    ],
)
def test_recommend_params_has_rationale(label):
    """recommend_params returns a real rationale, not the placeholder."""
    _, _, rationale = collect.recommend_params(label, [], None, {})
    assert rationale != "no specific recommendation", (
        f"{label}: recommend_params returned the placeholder rationale"
    )


# ── AC-5: relaunch stance agrees with the watchdog ────────────────────────
# watchdog.sh:423-427 (stop-clean), :461 (relaunch), :470 (block)
#
# label → watchdog action:
#   local-checks-unchanged → block (do NOT relaunch)
#   merge-conflict         → block (do NOT relaunch)
#   blocked-no-runnable    → stop-clean (do NOT relaunch)
#   merge-deferred         → relaunch
#   yielded                → relaunch


_BLOCK_LABELS = {"local-checks-unchanged", "merge-conflict", "blocked-no-runnable"}
_RELAUNCH_LABELS = {"merge-deferred", "yielded"}


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("label", sorted(_BLOCK_LABELS))
def test_block_stance_narrative(label):
    """Block/stop-clean labels must say 'Do not auto-relaunch' in narrative."""
    narrative = collect._label_narrative(label, {"iter_at_stop": 1})
    assert "do not auto-relaunch" in narrative.lower(), (
        f"{label} (watchdog block/stop-clean): "
        f"expected 'Do not auto-relaunch' in narrative"
    )


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("label", sorted(_BLOCK_LABELS))
def test_block_stance_rationale(label):
    """Block/stop-clean labels must say 'Do not auto-relaunch' in rationale."""
    _, _, rationale = collect.recommend_params(label, [], None, {})
    assert "do not auto-relaunch" in rationale.lower(), (
        f"{label} (watchdog block/stop-clean): "
        f"expected 'Do not auto-relaunch' in rationale"
    )


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("label", sorted(_RELAUNCH_LABELS))
def test_relaunch_stance_narrative(label):
    """Relaunch labels must have a narrative (not the placeholder)."""
    narrative = collect._label_narrative(label, {"iter_at_stop": 1})
    assert narrative != "(no narrative for this label)", (
        f"{label}: _label_narrative returned the placeholder"
    )


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("label", sorted(_RELAUNCH_LABELS))
def test_relaunch_stance_rationale(label):
    """Relaunch labels must have a rationale (not the placeholder)."""
    _, _, rationale = collect.recommend_params(label, [], None, {})
    assert rationale != "no specific recommendation", (
        f"{label}: recommend_params returned the placeholder rationale"
    )