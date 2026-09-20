#!/usr/bin/env python3
"""RED-first pin — raw sentinel states and taxonomy labels are one action set.

`watchdog.sh` classifies a terminal run in two different vocabularies
depending on whether `collect.py` succeeded:

  * **the taxonomy label** (`collect.py`'s `CLASSIFICATION_LABELS`) on the
    normal path, and
  * **the raw sentinel state** (`run_ilk_loop_claude.sh`'s `stop_reason`,
    written verbatim to `last-exit.json` `state` at `:3404`) on the fallback
    path at `watchdog.sh:975-977`:

        write_log "collect.py produced no classification; falling back to raw
                   sentinel state: $sentinel_state"
        classification="$sentinel_state"

Both then go to the same `classify_action`. They are **not the same set of
words**, so the fallback silently changes what the watchdog *does* about a
run — not merely how it names it. Measured at HEAD, 2026-09-20:

    raw state                 action | normalized label        action
    ------------------------- ------ | ---------------------- ---------
    max-iterations            block  | max-iter-bound          relaunch
    ship_integrity_violation  block  | shipped-unverified      needs-human
    shipped-unproven          block  | shipped-unverified      needs-human

Every divergence fails in the safe direction (it refuses to relaunch rather
than wrongly relaunching), so the cost is a stalled unattended recovery that
needs a human — which is exactly what unattended operation is meant to remove.

`timeout` → `timeout-bound` looks like a fourth divergence and is not one; see
`CONDITIONALLY_NORMALIZED` below. It is pinned as an exclusion instead.

**This file is deliberately RED at step 0.** Its step-0 gate asserts only that
it COLLECTS. It goes green at step 1, when both vocabularies are routed through
one normalization.

Neither vocabulary is hand-listed here: raw states are scanned out of the
runner, and the normalization is read out of `collect.py` with `ast`. A state
added to either producer later shows up in this test without anyone editing it.

Part of sub-plan `2026-09-08b-one-classification-mapping` (C4).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_WATCHDOG_SH = _SKILLS / "ilk-watchdog" / "scripts" / "watchdog.sh"
_RUNNER_SH = _SKILLS / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_COLLECT_PY = _SKILLS / "ilk-feedback" / "scripts" / "collect.py"

# Every action `classify_action` is allowed to echo.  Wider than
# test_label_action_totality.py's set because the raw-state vocabulary reaches
# arms the taxonomy never does (`running` → sleep, `all-shipped` → promote).
VALID_ACTIONS = frozenset(
    {"relaunch", "block", "stop-clean", "needs-human", "triage", "sleep", "promote"}
)

# `running` is the non-terminal state; the watchdog polls it rather than
# classifying it, so it has no taxonomy counterpart by construction.
NON_TERMINAL_STATES = frozenset({"running"})

# Raw states whose taxonomy label collect.py only assigns CONDITIONALLY, on
# evidence the fallback path does not have.  They are excluded from the
# agreement assertion and pinned as exclusions instead — normalizing them would
# assert something the watchdog cannot know at the moment it decides.
#
#   timeout → timeout-bound  requires `bool(iters)`:
#       collect.py:1358  _timeout_authoritative = sentinel_state == "timeout" \
#                                                 and bool(iters)
#   The raw-state fallback fires exactly when collect.py produced NO
#   classification, i.e. when a record-bearing run is exactly what we do not
#   have.  `timeout-bound` relaunches; the evidence-free case must not.
#   watchdog.sh's `no-evidence|never-ran|timeout) → triage` arm says the same.
CONDITIONALLY_NORMALIZED = {"timeout": "timeout-bound"}

# Anchors, measured at HEAD on 2026-09-20.  These are a FLOOR on what the scan
# must find, not the enumeration itself — if the scanner regresses to matching
# nothing, an empty result would otherwise pass every assertion below.
_SCAN_ANCHORS = frozenset(
    {"max-iterations", "timeout", "all-shipped", "ship_integrity_violation"}
)


# ── Vocabulary 1: raw sentinel states, scanned out of the runner ─────────────
def raw_sentinel_states() -> frozenset[str]:
    """Every literal value `run_ilk_loop_claude.sh` can put in `state`.

    `last-exit.json`'s `state` is written from `$stop_reason` verbatim
    (`:3404`), plus the two literals the sentinel writer emits directly
    (`running` at `:2624`, `interrupted` from `finalize_sentinel` at `:1407`).
    `$stop_reason` is only ever assigned a literal or `$iter_stop_reason`, and
    `_decide_iter_stop_reason` returns its values by `echo`.

    Interpolated assignments (`stop_reason="$iter_stop_reason"`) are skipped:
    they carry a value another branch already contributed.
    """
    try:
        src = _RUNNER_SH.read_text(encoding="utf-8")
    except OSError:
        return frozenset()

    states: set[str] = set()
    # stop_reason="literal" / iter_stop_reason="literal" — no $, ` or \.
    states.update(re.findall(r'\b(?:iter_)?stop_reason="([^"$`\\]+)"', src))
    # 'state': 'literal' inside the python heredocs that write the sentinel.
    states.update(re.findall(r"'state':\s*'([^'$]+)'", src))
    # The decision helper returns by echo rather than by assignment.
    fn = re.search(r"^_decide_iter_stop_reason\(\)\s*\{(.*?)^\}", src, re.S | re.M)
    if fn:
        states.update(re.findall(r'^\s*echo\s+"([^"$`\\]+)"', fn.group(1), re.M))
    return frozenset(states)


# ── Vocabulary 2: the normalization collect.py already knows ─────────────────
def sentinel_normalizations() -> dict[str, str]:
    """raw sentinel state → taxonomy label, read out of `collect.py`.

    `_SENTINEL_FAILURE_MAP` is a function-local annotated assignment, so it is
    lifted with `ast` rather than imported.  Two states are normalized in code
    rather than in that dict (`classify_run`, collect.py:1345-1385);
    `local_checks_failed` is added here, and `timeout` is deliberately not —
    see `CONDITIONALLY_NORMALIZED`.  `test_inline_specials_still_exist` keeps
    both decisions honest by failing if either in-code special disappears.
    """
    try:
        tree = ast.parse(_COLLECT_PY.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "_SENTINEL_FAILURE_MAP":
                try:
                    mapping.update(ast.literal_eval(node.value))
                except (ValueError, TypeError):
                    return {}
    if not mapping:
        return {}

    # Normalized in code, not in the dict.  `local_checks_failed` splits into
    # local-checks-stuck / local-checks-broken on evidence the fallback lacks,
    # but both are blacklist labels with the same action, so the split does not
    # change what the watchdog does and either stands in for the pair here.
    mapping.setdefault("local_checks_failed", "local-checks-stuck")
    for state in CONDITIONALLY_NORMALIZED:
        mapping.pop(state, None)
    return mapping


def _inline_specials_present() -> bool:
    """True while collect.py still normalizes `timeout` / `local_checks_failed`
    in code — the premise of the two `setdefault` entries above."""
    try:
        src = _COLLECT_PY.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'label = "timeout-bound"' in src and 'sentinel_state == "local_checks_failed"' in src


# ── The watchdog under test ──────────────────────────────────────────────────
def classify_action(label: str) -> str:
    """Call the real `classify_action` out of `watchdog.sh`.

    Extracted with `sed` + `eval` (the pattern `test_watchdog_action_vocab.sh`
    and `test_classification_stdout_contract.py` already use) because sourcing
    `watchdog.sh` runs its CLI argument parsing.
    """
    return _call_watchdog_fn("classify_action", label)


def describe_classification(value: str, source: str) -> str:
    """Call the real `describe_classification` out of `watchdog.sh`."""
    return _call_watchdog_fn("describe_classification", value, source)


_EXTRACT = "\n".join(
    f'eval "$(sed -n \'/^{fn}()/,/^}}/p\' {_WATCHDOG_SH})"'
    for fn in ("normalize_classification", "describe_classification", "classify_action")
)


def _call_watchdog_fn(fn: str, *args: str) -> str:
    script = f'{_EXTRACT}\n{fn} "$@"\n'
    proc = subprocess.run(
        ["/bin/bash", "-c", script, "_", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{fn}{args!r} exited {proc.returncode}: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return proc.stdout.strip()


# Computed at collection time so the divergences appear as named test ids.
# Both helpers return empty on any read/parse failure rather than raising, so
# `--collect-only` can never fail on a malformed source file; the emptiness is
# itself asserted below.
_RAW_STATES = raw_sentinel_states()
_NORMALIZATIONS = sentinel_normalizations()


class TestVocabulariesAreDiscoverable:
    """The premise: both word lists come from their producers, not from here."""

    def test_raw_states_scan_is_not_empty(self):
        assert _RAW_STATES, (
            f"Scanned 0 sentinel states out of {_RUNNER_SH} — the scanner no "
            f"longer matches the runner's stop_reason assignments, so every "
            f"agreement assertion below would pass vacuously."
        )

    def test_raw_states_scan_finds_the_known_anchors(self):
        missing = sorted(_SCAN_ANCHORS - _RAW_STATES)
        assert not missing, (
            f"Scanner lost states it found on 2026-09-20: {missing}. "
            f"Found {len(_RAW_STATES)}: {sorted(_RAW_STATES)}"
        )

    def test_normalization_map_is_not_empty(self):
        assert _NORMALIZATIONS, (
            f"Could not lift _SENTINEL_FAILURE_MAP out of {_COLLECT_PY}. "
            f"Without it there is no normalization to compare against."
        )

    def test_inline_specials_still_exist(self):
        assert _inline_specials_present(), (
            "collect.py no longer normalizes `timeout` / `local_checks_failed` "
            "in code, so the two setdefault entries in sentinel_normalizations() "
            "are now fabricating a mapping that the producer does not have."
        )


class TestOneActionSet:
    """The defect: the fallback path changes the action, not just the word."""

    @pytest.mark.parametrize("raw", sorted(_NORMALIZATIONS))
    def test_raw_state_and_its_label_agree(self, raw: str):
        """RED at step 0 for max-iterations / ship_integrity_violation /
        shipped-unproven / timeout."""
        label = _NORMALIZATIONS[raw]
        raw_action = classify_action(raw)
        label_action = classify_action(label)
        assert raw_action == label_action, (
            f"Vocabulary divergence: sentinel state {raw!r} → {raw_action!r} but "
            f"its normalized label {label!r} → {label_action!r}. A failed "
            f"collect.py therefore changes what the watchdog DOES about this run."
        )

    @pytest.mark.parametrize("raw,label", sorted(CONDITIONALLY_NORMALIZED.items()))
    def test_conditionally_normalized_states_are_not_widened(self, raw: str, label: str):
        """The exclusions are exclusions, not oversights.

        A state collect.py normalizes only on evidence must NOT inherit that
        label's action on the fallback path, where the evidence is by
        definition absent. Asserting the divergence keeps someone from
        "finishing the mapping" later and quietly widening relaunch.
        """
        raw_action = classify_action(raw)
        assert raw_action != "relaunch", (
            f"Sentinel state {raw!r} now resolves to 'relaunch'. Its label "
            f"{label!r} is only assigned by collect.py when the run left "
            f"records, and the fallback path runs when it did not."
        )
        assert raw_action != classify_action(label), (
            f"Sentinel state {raw!r} and label {label!r} now agree on "
            f"{raw_action!r}. If that is intended, delete the entry from "
            f"CONDITIONALLY_NORMALIZED and say why the guard no longer applies."
        )

    def test_max_iterations_is_the_named_pair(self):
        """The pair called out in the sub-plan, pinned explicitly so the
        regression has a named test even if the scan changes shape."""
        assert classify_action("max-iterations") == classify_action("max-iter-bound"), (
            "max-iterations (raw) → "
            f"{classify_action('max-iterations')!r} vs max-iter-bound "
            f"(classified) → {classify_action('max-iter-bound')!r}. A "
            "recoverable max-iteration stop becomes a blocked one whenever "
            "classification fails."
        )


class TestFallbackSaysItIsOne:
    """A stand-in must not read like a verdict.

    Before `describe_classification`, a label collect.py had concluded and a
    raw sentinel state substituted because collect.py failed printed the same
    word in the log, the banner and the notification. The one that wants
    investigating was the one that looked identical to the routine case.
    """

    def test_postmortem_source_is_not_annotated(self):
        assert describe_classification("stuck-no-progress", "postmortem") == (
            "stuck-no-progress"
        )

    def test_empty_source_does_not_invent_an_annotation(self):
        """An un-plumbed caller loses the annotation; it never gains a false one."""
        assert describe_classification("stuck-no-progress", "") == "stuck-no-progress"

    @pytest.mark.parametrize("source", ["sentinel-fallback", "sentinel-legacy-pid"])
    def test_fallback_source_is_marked_unclassified(self, source: str):
        out = describe_classification("stuck-no-progress", source)
        assert "unclassified" in out, (
            f"describe_classification('stuck-no-progress', {source!r}) → {out!r} "
            f"is indistinguishable from a postmortem verdict."
        )
        assert source in out, f"{out!r} does not say which fallback path produced it."

    def test_fallback_shows_both_spellings_when_normalized(self):
        """The raw word is evidence — it says what the runner actually wrote."""
        out = describe_classification("max-iterations", "sentinel-fallback")
        assert "max-iter-bound" in out, f"{out!r} omits the normalized label"
        assert "max-iterations" in out, f"{out!r} omits the raw sentinel state"

    @pytest.mark.parametrize("raw", sorted(_RAW_STATES))
    def test_every_raw_state_is_marked_on_the_fallback_path(self, raw: str):
        out = describe_classification(raw, "sentinel-fallback")
        assert "unclassified" in out, (
            f"Sentinel state {raw!r} renders as {out!r} on the fallback path — "
            f"no marker that nobody classified this run."
        )


class TestTotality:
    """Whatever the mapping decides, it must decide something."""

    @pytest.mark.parametrize("raw", sorted(_RAW_STATES))
    def test_every_raw_state_resolves_to_a_valid_action(self, raw: str):
        action = classify_action(raw)
        assert action in VALID_ACTIONS, (
            f"Sentinel state {raw!r} resolved to {action!r}, which is not an "
            f"action the watchdog's dispatch understands: {sorted(VALID_ACTIONS)}"
        )

    @pytest.mark.parametrize(
        "raw", sorted(_RAW_STATES - NON_TERMINAL_STATES - frozenset(_NORMALIZATIONS))
    )
    def test_unnormalized_terminal_states_still_fail_safe(self, raw: str):
        """A terminal state with no normalization must not relaunch.

        Keeps step 2's rule enforceable: widening the mapping must never widen
        relaunch to a state nobody has classified.
        """
        assert classify_action(raw) != "relaunch", (
            f"Sentinel state {raw!r} has no normalization in collect.py yet "
            f"resolves to 'relaunch' — an unclassified stop is being retried."
        )

    @pytest.mark.parametrize(
        "unknown", ["some-future-label", "", "   ", "definitely-not-a-state"]
    )
    def test_a_word_nobody_knows_still_blocks(self, unknown: str):
        """Normalizing must not have widened relaunch to unknown input.

        `normalize_classification` passes unrecognised words through unchanged,
        so they still reach `classify_action`'s `*` fail-safe. Pinned because
        the obvious way to "finish" the mapping later — a permissive default —
        would turn every unknown stop into a retry.
        """
        assert classify_action(unknown) == "block", (
            f"Unknown classification {unknown!r} → {classify_action(unknown)!r}; "
            f"the fail-safe must stay 'block'."
        )
