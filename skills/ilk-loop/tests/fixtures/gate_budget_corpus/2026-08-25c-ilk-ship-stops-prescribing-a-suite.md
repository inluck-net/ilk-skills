---
plan: ilk-ship-stops-prescribing-a-suite
status: shipped
current_step: 4
tickets:
  - SHIP-DOC-CONTRADICTION
priority: P1
estimated_steps: 4
last_updated: 2026-08-25
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-ship/SKILL.md"
  - "skills/ilk-loop/tests/test_ship_skill_no_suite.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_ship_skill_no_suite.py"
e2e_test_targets: []
must_add_tests: true
ci_required: false
ci_status_endpoint: github
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: /ilk-ship stops prescribing a suite

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #4** — independent of SP1-SP3; a docs reconciliation. Last so the three
behavioural fixes are settled before the documentation describes them.

## Before you start

This sub-plan changes what a **document** prescribes. The failure mode it
addresses is two parts of one contract disagreeing — so the test must assert
the *absence* of the contradiction, not merely the presence of the new text.
Presence-only was how the parent batch left this half-done.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| SHIP-DOC-CONTRADICTION | Phase 1 says it does not run a suite; the tier table still prescribes one | gap | P1 | `skills/ilk-ship/SKILL.md` |

## The finding this sub-plan closes

The parent batch's SP4 correctly added, at `skills/ilk-ship/SKILL.md:142-148`:

> *"Phase 1 does **not** run the test suite. It **verifies** the recorded batch
> verdict … reads the record, checks `head_sha` against the current HEAD, runs
> the collection floor, and baseline-diffs. Phase 1 **refuses to release** on a
> **missing**, **failed**, or **stale** verdict."*

It also removed §12's old *"LAST step must run the FULL test suite"* — verified,
0 matches remain.

But **line 86 still reads**:

```
| 3 | contract-governed file OR a shared path/schema OR oracle failed | whole suite |
```

and the tier ladder above it is untouched. The document now asserts both "tier 3
runs the whole suite" and "Phase 1 does not run the suite." That is SP4's own
AC-6 unmet — *"no rule is left self-contradicting"* — and it is the v0.9.70
shape, where three readers of one contract each behaved differently.

Operator decision, 2026-08-25: **`/ilk-ship` does verification, small fixes,
then commit / push / tag / distribute. It does not run the suite.**

**What the tier ladder is still for.** Tier selection is not dead: it still
chooses a *scope* for the collection floor and for baseline-diff attribution.
What it must stop doing is prescribing a suite **run**. Say that explicitly, or
the next reader deletes the ladder entirely and loses the attribution scoping
with it.

## Objectives

1. The tier table stops prescribing a suite run.
2. What tier selection still governs is stated, so the ladder is not read as
   dead weight.
3. A test asserts the contradiction is absent — not merely that the new text is
   present.

## Acceptance criteria

- **AC-1**: the tier table no longer prescribes running a suite for any tier.
  Asserted by a structural test on the file.
- **AC-2**: the document states what tier selection **does** still govern
  (collection-floor scope and baseline-diff attribution). Asserted, so the
  ladder is not left looking vestigial.
- **AC-3**: **no contradiction remains** — a test asserts the file does not
  simultaneously say Phase 1 skips the suite and that a tier runs one. This is
  the AC the parent batch missed; presence of the new text is not sufficient.
- **AC-4**: Phase 1's three refusal words — **missing**, **failed**, **stale** —
  survive the edit. Asserted, so reconciling the table cannot quietly weaken the
  refusal.
- **AC-5**: `skills/ilk-ship/tests/test_ship_skill_contract.py` stays green — it
  already asserts things about this file, and this edit must not break them.

## Out of scope

- Removing tier 3 or the ladder from `gate_scope.py`. Behaviour is unchanged
  here; this is documentation reconciliation. Whether the ladder still earns its
  keep is a separate question with its own evidence — measured 2026-08-25, tier
  3 fired in **11 of the last 12 releases (91%)**, and its trigger was a
  committed test fixture twice. Both filed.
- Phase 0, Phase 2, Phase 3, Phase 4 text.
- The batch gate itself (SP1-SP3).
- `hooks/no-full-suite.sh` and `pytest.ini` (v0.9.71 / v0.9.72). Both stay.

## Steps

### Step 0 — Red-first: assert the contradiction exists, then that it is gone
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_ship_skill_no_suite.py -q 2>&1 | tail -5 | grep -qE '[1-4] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_ship_skill_no_suite.py`.
- Write AC-1 (no tier prescribes a suite run), AC-3 (no contradiction), AC-4
  (the three refusal words survive). AC-1 and AC-3 are red today; AC-4 is green
  and is the regression net.
- Express AC-3 as a real predicate over the file — e.g. "if the file says Phase
  1 does not run the suite, then no tier row may prescribe running one" — not as
  a grep for one phrase. A one-phrase grep is what let the contradiction ship.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(ship): red-first — the tier table must not prescribe a suite run [plan:ilk-ship-stops-prescribing-a-suite#step-0]`

### Step 1 — Reconcile the tier table
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_skill_no_suite.py -q --timeout=60 --timeout-method=signal
    timeout: 180
```
- Rewrite the tier table's scope column so it describes what the tier selects —
  collection-floor scope and baseline-diff attribution — rather than a suite to
  run (AC-1, AC-2).
- Keep the tier triggers themselves unchanged; `gate_scope.select_tier` still
  returns them and its measured behaviour is documented below the table.
- Commit: `docs(ship): the tier ladder scopes attribution, not a suite run [plan:ilk-ship-stops-prescribing-a-suite#step-1]`

### Step 2 — Say what the ladder still governs, and keep the refusals
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_skill_no_suite.py skills/ilk-ship/tests/test_ship_skill_contract.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```
- Add the sentence AC-2 requires, adjacent to the table, so a reader who starts
  at the ladder is not left thinking it is dead.
- AC-4: confirm **missing / failed / stale** are intact in Phase 1.
- AC-5: `test_ship_skill_contract.py` green.
- Commit: `docs(ship): state what tier selection still governs [plan:ilk-ship-stops-prescribing-a-suite#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_skill_no_suite.py skills/ilk-ship/tests/test_ship_skill_contract.py -q --timeout=60 --timeout-method=signal
    timeout: 600
```
- Confirm step-0 tests green, AC-3 included.
- Record in Findings: the tier table's new scope column and Phase 1's refusal
  sentence, side by side, so a reader can confirm they agree without opening
  the file.
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): ilk-ship-stops-prescribing-a-suite shipped [plan:ilk-ship-stops-prescribing-a-suite#ship]`

## Findings

**Tier table's new scope column** (line 86):
```
| 3 | contract-governed file OR a shared path/schema OR oracle failed | widest attribution scope (collection-floor + baseline-diff) |
```

**Phase 1's refusal sentence** (lines 142-148):
> Phase 1 does **not** run the test suite. It **verifies** the recorded batch verdict — the record persisted by the batch gate at the end of the loop run. The verification reads the record, checks `head_sha` against the current HEAD, runs the collection floor, and baseline-diffs. Phase 1 **refuses to release** on a **missing**, **failed**, or **stale** verdict.

**Agreement**: The tier table now describes what each tier **selects** (attribution scope), not what suite to **run**. Phase 1's text explicitly says it does not run the suite and refuses to release on missing/failed/stale verdicts. The contradiction is resolved: the ladder scopes attribution, not a suite run.

## Reference reading

- `skills/ilk-ship/SKILL.md` — the tier table at line 86, Phase 1's
  verify-only text at 142-148, and "Measured behaviour and known limits".
- `skills/ilk-ship/tests/test_ship_skill_contract.py` — existing assertions on
  this file.
- `skills/ilk-loop/references/decomposition-principles.md` §12 (already
  rewritten by the parent batch) and §16.
