---
plan: a-batch-with-no-suite-says-so
status: shipped
current_step: 4
tickets:
  - SILENT-NOT-CONFIGURED
priority: P0
estimated_steps: 4
last_updated: 2026-08-25
shipped_date: 2026-08-25
verification_tier: loop-verified
regression_for: SILENT-NOT-CONFIGURED
depends_on: []
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/plan_lint.py"
  - "skills/ilk-loop/scripts/batch_gate.py"
  - "skills/ilk-loop/tests/test_batch_no_suite.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_batch_no_suite.py"
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

# Sub-plan: A batch with no suite says so

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #6 — appended 2026-08-25 after a measured coverage regression on
gh-resolve.** Independent of SP1-SP5.

## Before you start

This closes a gap **this batch's parent created**. Read the finding below
before touching anything: the two changes involved are each correct in
isolation, and the defect is only in how they compose. Do not "fix" either one.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| SILENT-NOT-CONFIGURED | A project can lose all suite coverage and every tool reports clean | escaped-bug | P0 | `plan_lint.py`, `batch_gate.py` |

## The finding this sub-plan closes

Measured on gh-resolve, 2026-08-25, after its batch was re-planned under the
relaxed §12:

- **0 broad-suite gates** across **all 7** sub-plans (was 2 — a `2 × 550`
  split-halves gate). §12 worked: sub-plans now gate change-scoped.
- **The batch gate cannot run.** gh-resolve's `.ilk-launch.json` has no `ship`
  key, so `load_ship_config` returns `NotConfigured` and `batch_gate` records
  `verdict: not_configured` having executed **nothing**.

Net: **the full suite would never run — not per sub-plan, not at batch end.**
3,229 tests, zero executed.

And every tool reports clean. Verified:

```
$ plan_lint --master MASTER-2026-08-25-execution-plan.md 2026-08-25-*.md
OK: plan_lint clean          EXIT=0
# findings mentioning ship.suite / not configured / no suite: 0
```

**Why nothing catches it.** Every suite-related lint is conditioned on a broad
gate *existing*:

```python
def lint_unbounded_broad_suite(text, slug):
    has_broad = any(_is_whole_suite_command(cmd) for cmd in commands)
    if not has_broad:
        return findings          # silent
```

`lint_foreground_whole_suite_gate` opens the same way
(`if not _is_whole_suite_command(cmd): continue`). So **removing the gates
removed the trigger for every check that would have noticed the missing
`ship.suite`.** The lints ask *"is this broad gate safe?"*; nobody asks
*"should there be one, and can it run?"*

Three reinforcing reasons a session cannot reach this today:

1. `plan_lint` is per-sub-plan; "the batch has no suite anywhere" is a property
   of the batch **plus project config**, which no check has scope over.
2. `not_configured` only appears at batch end, long after planning.
3. `not_configured` is a deliberate *degrade* (SP1 chose it over inventing an
   invocation — correct). But nothing distinguishes *"this project legitimately
   has no suite"* from *"this project just lost its only coverage"*, and after
   §12 relaxed, those became very different situations.

## Objectives

1. Make "no suite anywhere in this batch" visible at **plan** time.
2. Make a `not_configured` batch verdict a **reported** condition, not a quiet
   pass, at **batch end**.
3. Keep a genuinely suite-less project workable — report, do not block.

## Acceptance criteria

- **AC-1**: `plan_lint --master` reports a finding when a batch declares **no**
  broad-suite gate in any sub-plan **and** the project resolves to
  `NotConfigured`. The finding names the project's config path and says the
  suite will not run at batch end. Asserted with a fixture batch.
- **AC-2**: **no finding** when the project declares a `ship.suite` — the batch
  gate will run it. Asserted, so the check cannot degrade into "always warn".
- **AC-3**: **no finding** when a sub-plan still declares a broad-suite gate —
  coverage exists by the old route. Asserted; this is the back-compat direction
  for projects that have not re-planned.
- **AC-4**: this is a **master-level** check (needs `--master`), because the
  condition is a property of the batch, not of any one sub-plan. Asserted by
  confirming it does not fire when sub-plans are linted without `--master`.
- **AC-5**: at batch end, a `not_configured` verdict is **reported by the
  runner** with the config path and the fact that nothing ran. Asserted against
  `batch_gate`'s output for a project with no `ship` block.
- **AC-6**: `not_configured` is still **not a failure** — it must not block the
  batch or return non-zero. A project may legitimately have no suite; the fix
  is visibility, not obstruction. Asserted explicitly.
- **AC-7**: the real gh-resolve config is the fixture for AC-1 and AC-5 — the
  case that motivated this sub-plan is the case under test. Commit a copy of
  its `.ilk-launch.json` (3 keys, no `ship`) rather than a synthetic one.

## Out of scope

- Editing gh-resolve's `.ilk-launch.json`. The operator adds its `ship` block
  separately; this sub-plan makes the *class* of problem visible so no other
  project repeats it.
- Re-tightening §12, or reintroducing per-sub-plan whole-suite gates. The
  relaxation is correct; it just needs a companion check.
- Changing `NotConfigured` semantics in `ship_config.py`. The degrade is right.
- Inventing a default suite invocation for an unconfigured project — explicitly
  rejected in the parent batch's SP1 and still rejected.

## Steps

### Step 0 — Red-first: the gh-resolve shape must be flagged
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_batch_no_suite.py -q 2>&1 | tail -5 | grep -qE '[3-9] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_batch_no_suite.py`.
- Commit gh-resolve's real `.ilk-launch.json` as a fixture (AC-7) — 3 keys,
  no `ship`.
- Write AC-1 (no broad gates + NotConfigured → finding), AC-2 (ship.suite
  declared → none), AC-3 (a broad gate present → none), AC-5 (batch-end
  report). All red.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(lint): red-first for a batch with no suite anywhere [plan:a-batch-with-no-suite-says-so#step-0]`

### Step 1 — The master-level lint
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_no_suite.py -q -k 'lint or declared or broad_present' --timeout=60 --timeout-method=signal
    timeout: 180
```
- Add `lint_batch_has_no_suite` to `plan_lint.py`, run under `--master` (AC-4).
- Resolve the project's config with `ship_config.load_ship_config` — the same
  reader `batch_gate` uses. A second config reader is how the two would later
  disagree about whether a project is configured.
- The finding must name the config path and state the consequence — *"the batch
  gate will record not_configured and run nothing"* — not merely "no suite".
- Commit: `feat(lint): flag a batch whose suite will never run [plan:a-batch-with-no-suite-says-so#step-1]`

### Step 2 — Report it at batch end too
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_no_suite.py skills/ilk-loop/tests/test_batch_gate.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```
- AC-5: `batch_gate` surfaces `not_configured` with the config path and "nothing
  ran". Plan time is the cheaper interception, but a batch that never went
  through `/ilk-plan` must still say so.
- AC-6: still not a failure — no block, no non-zero exit. Assert both.
- Commit: `feat(loop): a not_configured batch verdict names what did not run [plan:a-batch-with-no-suite-says-so#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_no_suite.py skills/ilk-loop/tests/test_batch_gate.py skills/ilk-loop/tests/test_plan_lint.py skills/ilk-loop/tests/test_plan_lint_unbounded_suite.py -q --timeout=60 --timeout-method=signal
    timeout: 600
```
- Confirm step-0 tests green, including AC-2 and AC-3's no-false-positive
  directions.
- **Run `plan_lint --master` over gh-resolve's live batch** (7 sub-plans,
  `MASTER-2026-08-25-execution-plan.md`). Before this sub-plan it reports
  `OK: plan_lint clean`; after, it must report the missing suite. Record both
  outputs in Findings — that before/after is the whole proof.
- Run `plan_lint --master` over **this** batch's own master and sub-plans;
  ilk-skills declares a `ship.suite`, so the new lint must stay silent here
  (AC-2 on a real project).
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): a-batch-with-no-suite-says-so shipped [plan:a-batch-with-no-suite-says-so#ship]`

## Findings

_(filled by the loop during early steps)_

## Reference reading

- `skills/ilk-loop/scripts/plan_lint.py` — `lint_unbounded_broad_suite` and
  `lint_foreground_whole_suite_gate`; both open by requiring a broad gate to
  exist, which is the blindness this closes.
- `skills/ilk-ship/scripts/ship_config.py` — `load_ship_config`,
  `NotConfigured`.
- `skills/ilk-loop/scripts/batch_gate.py` — the `NotConfigured` branch that
  records `verdict: not_configured`.
- `skills/ilk-loop/references/decomposition-principles.md` §12 (relaxed by the
  parent batch) and §16.
