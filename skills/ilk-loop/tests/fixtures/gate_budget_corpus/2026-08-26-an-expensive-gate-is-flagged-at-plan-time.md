---
plan: an-expensive-gate-is-flagged-at-plan-time
status: shipped
current_step: 3
tickets:
  - PLAN-LINT-GATE-BUDGET
priority: P1
estimated_steps: 4
last_updated: 2026-08-26
verification_tier: loop-verified
regression_for:
depends_on:
  - a-slow-test-file-is-measured
data_prereqs:
  - description: "per-test-file timings available from gate_cost --by-test-file --json (produced by SP3)"
    verify_cmd: "python3 skills/ilk-loop/scripts/gate_cost.py --by-test-file --json >/dev/null"
env_prereqs: []
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/plan_lint.py"
  - "skills/ilk-loop/tests/test_plan_lint_gate_budget.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_plan_lint_gate_budget.py"
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

# Sub-plan: an expensive gate is flagged at plan time, not after it runs

Part of [MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan](./MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan.md).
**Order #4** — last, because it consumes SP3's measurements.

## Before you start

Read the MASTER plan, then SP3
([2026-08-26-a-slow-test-file-is-measured.md](./2026-08-26-a-slow-test-file-is-measured.md)) —
especially its "attribution decision" section, which defines what the
numbers this lint reads do and do not mean. Then read an existing finding in
`skills/ilk-loop/scripts/plan_lint.py` to match its finding shape.

## Why this exists

SP3 makes the cost of a test file knowable. This closes the loop: the
planner picks gate commands, so the planner is where a measured cost should
change a decision. Without this, SP3 produces a report nobody reads at the
moment it matters.

The target shape, from the measured run: a step declaring
`pytest tests/test_drain.py -q` costs ~199s every time it runs. A `-k`
selection over the same file would cost seconds. Nothing today tells the
planner the difference.

## The budget default — a choice, labelled

**Judgment call.** Default budget: **60s** per gate command.

Basis, from run `20260825-234253`: 78 pytest invocations totalling 1394s →
mean 17.9s. The four outliers were 197-225s. A 60s threshold is ~3.4x the
observed mean, so it flags the tail without firing on the norm.

Wrong if a project's normal gate legitimately costs more than a minute —
then the number belongs in that project's config, not in a global default.
Make it overridable from the start so the answer to "this fires too often
here" is configuration, not raising a global constant.

## The negative must carry its denominator

A file with **no measurement** is not a cheap file. When the lint cannot
find timing data for a named file, it must say so — not pass silently.
Silence and "measured, cheap" must be distinguishable in the output, or the
lint teaches the planner to trust an absence of data as evidence of low
cost.

## Objectives

1. `plan_lint` emits a finding when a step's `local_checks` runs a test file
   whose measured cost exceeds the budget.
2. The finding names the file, its measured cost, the budget, and the
   cheaper alternative.
3. Unmeasured files are reported as unmeasured, never as passing.
4. The budget is configurable per project.

## Acceptance criteria

- **AC-1** A step whose `local_checks` names a test file measured above
  budget produces a finding naming the file, its measured seconds, the
  budget, and suggesting a `-k` selection.
- **AC-2** A step naming a file measured below budget produces no finding.
- **AC-3** A step naming a file with no measurement produces a distinct
  `unmeasured` note — different text from both AC-1 and AC-2, so the three
  states are separable in the output.
- **AC-4** With no timing data at all, the lint says so once, naming what it
  searched, and does not emit per-file findings.
- **AC-5** The budget is overridable per project; the override is honoured
  and the effective value appears in the finding text.
- **AC-6** The finding is a **warning**, not a hard finding — an expensive
  gate is sometimes the right call, and a hard block would make the planner
  weaken a correct gate to get past it.
- **AC-8** **Baseline regression check.** Before step 1, record the complete
  set of `plan_lint` findings over a fixed corpus (this batch's four
  sub-plans plus the 2026-08-25c sub-plans) into a committed fixture. After
  the change, assert the set is identical **except** for the new budget
  findings. Introducing a 60s threshold changes what the linter says about
  every plan in the repo; without this before-and-after comparison the
  change silently reclassifies existing plans and nobody notices.
- **AC-7** Existing `plan_lint` findings and
  `skills/ilk-loop/tests/test_plan_lint*.py` are unchanged and green, except
  the 2 known-red in `test_plan_lint_shared_module_gate.py` (see MASTER
  "Inherited red") which must stay exactly as they are — neither fixed nor
  newly broken.

## Out of scope

- Rewriting any existing gate command. The lint reports; the planner decides.
- Making the finding hard/blocking. See AC-6.
- Timing data collection — SP3 owns it.
- Fixing `test_plan_lint_shared_module_gate.py`'s 2 known-red tests.

## Steps

### Step 0 — Red-first: three states, not two

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_plan_lint_gate_budget.py -q 2>&1 | tail -5 | grep -qE '[4-9]|[1-9][0-9] failed'"
    timeout: 120
```

- **First, record the baseline for AC-8**: run `plan_lint` over the fixed
  corpus and commit its full finding set as a fixture. This must happen
  before any change to `plan_lint.py`, or there is nothing to compare
  against.
- Create `skills/ilk-loop/tests/test_plan_lint_gate_budget.py` with
  AC-1..AC-6 and AC-8 as failing tests.
- Inject timing data as a fixture; do **not** shell out to `gate_cost.py`
  from the tests, and do not read real logs.
- Assert AC-1/AC-2/AC-3 produce three *distinguishable* outputs. A test that
  only checks "finding" vs "no finding" cannot catch the failure this
  sub-plan is guarding against.
- Expected-red; the gate asserts the red count.
- Commit: `test(lint): red-first — an over-budget gate must be flagged [plan:an-expensive-gate-is-flagged-at-plan-time#step-0]`

### Step 1 — Read the measurements, emit the finding

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_gate_budget.py -q -k 'over_budget or under_budget or suggests' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Consume SP3's `--json` output via its `schema: 1` contract. Cache or read
  on demand, but never let a slow/failed read turn into a silent pass — a
  read failure is the AC-4 case.
- Emit the AC-1 finding (AC-1, AC-2).
- Commit: `feat(lint): flag a gate whose test file is measured over budget [plan:an-expensive-gate-is-flagged-at-plan-time#step-1]`

### Step 2 — The unmeasured state, and the configurable budget

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_gate_budget.py -q -k 'unmeasured or no_data or budget_override or warning' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Distinct `unmeasured` note (AC-3); the no-data-at-all message names the
  search space (AC-4).
- Per-project budget override, effective value in the text (AC-5).
- Confirm the finding is warning-tier (AC-6).
- Commit: `feat(lint): unmeasured is not cheap, and the budget is configurable [plan:an-expensive-gate-is-flagged-at-plan-time#step-2]`

### Step 3 — Verification and ship

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_plan_lint_gate_budget.py skills/ilk-loop/tests/test_plan_lint_shared_module_gate.py -q --timeout=60 --timeout-method=signal 2>&1 | tail -5 | grep -qE '2 failed'
    timeout: 300
```

- AC-1..AC-7 green. The gate above asserts **exactly** the 2 inherited red
  tests remain — that pins AC-7 in both directions: not fixed, not widened.
- Run `plan_lint --master` over **this batch's own four sub-plans** with the
  real measurements and record the output in "Findings". This batch's gates
  are change-scoped and fast, so the budget finding should be silent here;
  if it fires on this batch, the threshold or the parser is wrong.
- `plan_lint.py` is imported elsewhere, so the shared-module gate finding is
  expected. It is answered by the batch-end gate. **Do not widen this step's
  gate to the whole suite to silence it** (see
  `plan-lint-shared-module-needs-whole-suite`).
- Commit: `chore(plans): an-expensive-gate-is-flagged-at-plan-time shipped [plan:an-expensive-gate-is-flagged-at-plan-time#ship]`

## Findings

Ran `plan_lint --master` over this batch's four sub-plans with real measurements:
- Only existing finding: balance-drift on `a-reread-is-refused` (expected, see MASTER "Known plan_lint false positive").
- No budget findings fire — this batch's gates are change-scoped and fast (under 60s).
- Baseline regression check (AC-8): all 5 pre-existing findings over the fixed corpus are preserved. No findings lost or gained.

## Reference reading

- SP3's "attribution decision" — what the numbers mean.
- `skills/ilk-loop/scripts/plan_lint.py` — existing finding shape.
- `decomposition-principles.md` §8 — the gate-discipline findings this joins.
