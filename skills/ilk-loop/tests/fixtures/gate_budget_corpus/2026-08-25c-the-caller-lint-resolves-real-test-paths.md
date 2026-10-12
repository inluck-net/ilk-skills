---
plan: the-caller-lint-resolves-real-test-paths
status: shipped
current_step: 3
tickets:
  - LINT-PATH-CONVENTION
priority: P1
estimated_steps: 4
last_updated: 2026-08-25
verification_tier: loop-verified
regression_for: LINT-PATH-CONVENTION
depends_on: []
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/plan_lint.py"
  - "skills/ilk-loop/tests/test_plan_lint_caller_paths.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_plan_lint_caller_paths.py"
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

# Sub-plan: The caller lint resolves real test paths

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #5** — independent. Discovered while QC-ing this very batch.

## Before you start

This fixes a lint that the **previous** batch introduced. Read
`lint_shared_module_gate` in `plan_lint.py` before editing; its intent is right
and its unit tests pass. The defect is in how it resolves test-file paths.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| LINT-PATH-CONVENTION | `lint_shared_module_gate` resolves callers as `tests/test_<module>.py`, which does not exist in this repo | escaped-bug | P1 | `skills/ilk-loop/scripts/plan_lint.py` |

## The finding this sub-plan closes

`MASTER-2026-08-25b` SP4 taught `lint_shared_module_gate` to accept a
change-scoped gate: *"A gate that covers both the changed module's tests AND the
resolved importers' tests satisfies the rule — a whole-suite gate is not
required."* The intent is correct and its own tests pass.

But it resolves test files by the convention `tests/test_<module>.py`, relative
to the project root. **Measured on this repo:**

```
tests/test_ship_audit.py    ABSENT
tests/test_loop_status.py   ABSENT
tests/test_batch_gate.py    ABSENT
```

They live at `skills/ilk-loop/tests/`. So `_resolve_test_paths` returns empty,
`_gate_covers_module_and_callers` can never be satisfied, and the lint fires no
matter what a sub-plan gates.

Demonstrated during QC of this batch: `the-record-becomes-authoritative` names
`test_loop_status.py` — its caller's tests — in **4** places, and still trips
the lint. Adding `test_ship_audit.py` to `a-gate-record-is-validated` likewise
changed nothing.

**A lint that cannot pass on the repo that ships it is worse than no lint**: it
trains the reader to ignore a class of finding, which is how the shared-module
warning has been dismissed by hand three times today.

## Objectives

1. Resolve a module's tests wherever they actually live, not by one hardcoded
   convention.
2. Let a gate naming module + caller tests actually clear the lint.
3. Keep the finding firing when the callers really are ungated.

## Acceptance criteria

- **AC-1**: for a module at `skills/<skill>/scripts/<m>.py`, the resolver finds
  `skills/<skill>/tests/test_<m>.py`. Asserted for `ship_audit`, `loop_status`
  and `batch_gate` — the three that are ABSENT under the current convention.
- **AC-2**: a sub-plan whose gate names the changed module's tests **and** its
  resolved callers' tests **clears** the lint. Asserted using this batch's own
  `the-record-becomes-authoritative` as the fixture — the case that exposed the
  bug is the case under test.
- **AC-3**: a sub-plan gating **only** the module's own tests **still fires**.
  Asserted, so the fix cannot degrade into "the lint never fires".
- **AC-4**: a top-level `tests/test_<module>.py` layout **still resolves** —
  other projects use it, and the current convention must keep working. Asserted
  with a synthetic fixture, not against this repo.
- **AC-5**: when a module's tests cannot be located at all, the lint **reports
  nothing** rather than firing. The existing oracle already follows this rule
  ("if the oracle cannot run, it reports nothing rather than firing"); an
  unresolvable path must not read as an ungated caller.
- **AC-6**: the existing `plan_lint` suites stay green —
  `test_plan_lint.py`, `test_plan_lint_shared_module_scoped.py`,
  `test_plan_lint_foreground_gate.py`.

> **Known lint false positives — do not weaken a gate to silence them.**
> `plan_lint` reports the following on this batch; each is a detector
> limitation, and **SP5 of this batch fixes the shared-module one**:
>
> - **shared-module gate**: `lint_shared_module_gate` resolves callers as
>   `tests/test_<module>.py` at project root, which is ABSENT in this repo
>   (they live at `skills/*/tests/`). Both sub-plans it fires on already gate
>   their callers. That bug IS this batch's SP5.
> - **balance-drift**: fires on *threshold / rate / bound* in prose; no
>   coefficient or tunable formula changes here. Filed separately.
> - **orphaned model**: the detector looks for UI / CLI-verb / HTTP / e2e
>   keywords; the consumer here is `plan_lint` itself, exercised by its tests.

## Out of scope

- Changing what the lint *means*, or when it should fire. The rule from the
  previous batch is correct; only path resolution is wrong.
- The balance-drift false positive (fires on the words *threshold / rate /
  bound* in prose). Separate, filed, and not this sub-plan's concern.
- `gate_scope.py`'s tier ladder.

## Steps

### Step 0 — Red-first: the three ABSENT modules, and the no-regress direction
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_plan_lint_caller_paths.py -q 2>&1 | tail -5 | grep -qE '[2-9] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_plan_lint_caller_paths.py`.
- Write AC-1 (resolve `ship_audit` / `loop_status` / `batch_gate` to their real
  `skills/*/tests/` paths) and AC-2 (a module+caller gate clears). Both red.
- Write AC-3 and AC-4 now too — they are green today and are the regression net
  that stops the fix from disabling the lint.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(lint): red-first — resolve caller tests where they live [plan:the-caller-lint-resolves-real-test-paths#step-0]`

### Step 1 — Resolve tests by search, not by one convention
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_caller_paths.py -q --timeout=60 --timeout-method=signal
    timeout: 180
```
- Extend `_resolve_test_paths` to locate `test_<module>.py` under the project,
  keeping the existing top-level convention as one candidate (AC-4).
- Prefer a test dir that is a sibling of the module's own directory
  (`skills/<skill>/scripts/<m>.py` → `skills/<skill>/tests/`), so a same-named
  test in an unrelated skill is not mistaken for the caller's.
- AC-5: unresolvable ⇒ report nothing. Follow the oracle's existing rule; do
  not invent a second failure posture.
- Commit: `fix(lint): resolve a module's tests where they actually live [plan:the-caller-lint-resolves-real-test-paths#step-1]`

### Step 2 — Prove it on this batch's own sub-plans
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_caller_paths.py skills/ilk-loop/tests/test_plan_lint_shared_module_scoped.py skills/ilk-loop/tests/test_plan_lint.py skills/ilk-loop/tests/test_plan_lint_foreground_gate.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```
- AC-6: all three existing plan_lint suites green.
- Run `plan_lint.py --master` over this batch's own master and five sub-plans.
  The two shared-module findings on `a-gate-record-is-validated` and
  `the-record-becomes-authoritative` must be **gone**, because both already gate
  their callers. Record the before/after counts in Findings.
- If either still fires, the resolver is still wrong — do not paper over it by
  editing the sub-plan's gates.
- Commit: `test(lint): the caller lint clears on this repo's own layout [plan:the-caller-lint-resolves-real-test-paths#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_caller_paths.py skills/ilk-loop/tests/test_plan_lint_shared_module_scoped.py skills/ilk-loop/tests/test_plan_lint.py skills/ilk-loop/tests/test_plan_lint_foreground_gate.py -q --timeout=60 --timeout-method=signal
    timeout: 600
```
- Confirm step-0 tests green in both directions (AC-2 clears, AC-3 still fires).
- Record in Findings: the resolved path for each of the three ABSENT modules,
  and the `plan_lint --master` finding count for this batch before and after.
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): the-caller-lint-resolves-real-test-paths shipped [plan:the-caller-lint-resolves-real-test-paths#ship]`

## Findings

**Before fix** (step 0 baseline): `plan_lint --master` reported 2 shared-module
findings — on `a-gate-record-is-validated` and `the-record-becomes-authoritative`.
Both already gated their callers but the resolver couldn't find the test files.

**After fix** (step 1): 0 shared-module findings on those two sub-plans. The
resolver now finds `skills/ilk-loop/tests/test_<module>.py` via sibling-dir
search. One real shared-module finding remains on `a-batch-with-no-suite-says-so`
(SP6's scope — `batch_gate` imported by `ship_audit`, gate only covers
`batch_gate`'s tests). Known false positives (balance-drift, orphaned model)
unchanged.

**Resolved paths** for the three ABSENT modules:
- `ship_audit` → `skills/ilk-loop/tests/test_ship_audit.py`
- `loop_status` → `skills/ilk-loop/tests/test_loop_status.py`
- `batch_gate` → `skills/ilk-loop/tests/test_batch_gate.py`

**AC-6**: 38/38 existing plan_lint tests green across all four suites.

## Reference reading

- `skills/ilk-loop/scripts/plan_lint.py` — `lint_shared_module_gate` (~1748),
  `_resolve_test_paths`, `_gate_covers_module_and_callers`, `_find_importers`.
- `skills/ilk-loop/tests/test_plan_lint_shared_module_scoped.py` — the previous
  batch's tests, which pass because their fixtures use a top-level `tests/`.
- `skills/ilk-loop/references/decomposition-principles.md` §8 (shared-module
  rule) and §12.
