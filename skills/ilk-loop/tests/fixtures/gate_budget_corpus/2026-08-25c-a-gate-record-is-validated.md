---
plan: a-gate-record-is-validated
status: shipped
current_step: 4
tickets:
  - RECORD-UNVALIDATED
priority: P0
estimated_steps: 4
last_updated: 2026-08-25
verification_tier: loop-verified
regression_for:
depends_on:
  - a-failed-gate-is-reported
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/batch_gate.py"
  - "skills/ilk-loop/tests/test_batch_gate_validation.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_batch_gate_validation.py"
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

# Sub-plan: A gate record is validated

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #2 — and this ordering is a constraint, not a preference.** SP3 makes
the reader authoritative; validation must exist *first*, or the reader starts
trusting records nothing can judge.

## Before you start

Read the MASTER's "sequencing constraint" section. Verified 2026-08-25:
`ship_audit` currently reports `final_gate: fail` while a stale `verdict: pass`
sits on disk — so the record is **not yet authoritative** and the stale one is
**inert**. This sub-plan closes the window before SP3 opens it.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| RECORD-UNVALIDATED | A batch-gate record is accepted with no check that it describes this project, at this HEAD | gap | P0 | `skills/ilk-loop/scripts/batch_gate.py` |

## The finding this sub-plan closes

The production record on this host:

```
verdict:    pass
head_sha:   879f33f          (HEAD is 9+ commits ahead)
invocation: python3 -m pytest skills/ilk-loop/tests/test_batch_gate.py -q --timeout=60 --timeout-method=signal
```

Two independent reasons it must not be trusted:

1. **`head_sha` is stale** — the gate ran against a tree 9+ commits behind.
2. **`invocation` is impossible** — the project declares
   `python3 -m pytest --timeout=60 --timeout-method=signal`, and `batch_gate.py`
   has **no suite-override flag**. Verified in 0.3s by importing
   `load_ship_config` and printing what the code builds. A record naming a
   single test file cannot have come from the current code, so it is an artifact
   of some other version.

Staleness alone would not have caught the second. A record can be at the right
HEAD and still describe a suite the project no longer runs — for example after
`ship.suite` changes. **Both fields must be validated.**

## Objectives

1. Judge a record against the project as it is *now*: HEAD and declared suite.
2. Make "cannot be trusted" its own outcome, never a pass and never a plain fail.
3. Report rather than delete — a record that cannot be trusted should say why.

## Acceptance criteria

- **AC-1**: a record whose `head_sha` differs from current HEAD validates as
  **stale**, naming both shas so the reader can see how far behind it is.
- **AC-2**: a record whose `invocation` differs from what the project's
  `ship.suite` currently builds validates as **stale** (or an equivalently
  named untrusted outcome), naming both invocations. Asserted **independently of
  AC-1** — a record at the correct HEAD with a wrong invocation must still fail.
- **AC-3**: a record matching on **both** validates as fresh. Asserted, so the
  validator cannot degrade into "everything is stale".
- **AC-4**: an **incomplete** record (any of `verdict` / `head_sha` /
  `invocation` / `timestamp` missing) validates as **invalid**, named per field,
  and is never a pass.
- **AC-5**: a **missing** record validates as **absent** — distinct from
  invalid and from fail. Three different reasons a verdict is unusable must be
  three different words, or the reader cannot act on them.
- **AC-6**: validation **never mutates or deletes** the record. Asserted by
  checking the file is byte-identical after validating a stale record. Deleting
  a bad record destroys the evidence that something wrote it.
- **AC-8**: `batch_gate`'s **caller** is exercised. `ship_audit.py` imports
  `batch_gate`, so a change to the validator's shape can break the audit. The
  step-3 gate runs `test_ship_audit.py` alongside batch_gate's own tests.
- **AC-7**: the real production record on this host validates as **stale** —
  asserted against a committed copy of it as a fixture, so the case that
  motivated this sub-plan is the case under test.

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

- Making the reader trust the record — that is SP3, and it must come after.
- Changing the record's schema or how it is written. The parent batch's writer
  is correct.
- Suite resolution (verified correct) or the gate's call sites (verified
  correct).
- Deleting the stale production record. AC-6 forbids it; the operator can
  remove it once it reports as stale.

## Steps

### Step 0 — Red-first: five outcomes, five words
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_batch_gate_validation.py -q 2>&1 | tail -5 | grep -qE '[4-9] failed|1[0-9] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_batch_gate_validation.py`.
- Commit a copy of the real stale record as a fixture (AC-7) — anonymise
  nothing; its literal content is the point.
- Write AC-1 (stale head), AC-2 (stale invocation, **fresh head**), AC-3
  (fresh), AC-4 (incomplete per field), AC-5 (absent). All red.
- Weight the tests toward the untrusted directions: a bug that makes a bad
  record look good is the one that matters.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(loop): red-first for batch-gate record validation [plan:a-gate-record-is-validated#step-0]`

### Step 1 — Validate head_sha and completeness
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_validation.py -q -k 'stale_head or incomplete or absent or fresh' --timeout=60 --timeout-method=signal
    timeout: 180
```
- Add a validator to `batch_gate.py` returning a named outcome, not a boolean.
  A boolean cannot carry five distinct reasons.
- Resolve HEAD with the module's existing `_git_head_sha`; do not add a second
  git helper.
- Commit: `feat(loop): validate a batch-gate record's head sha and completeness [plan:a-gate-record-is-validated#step-1]`

### Step 2 — Validate the invocation
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_validation.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```
- Rebuild the expected invocation the same way the writer does — via
  `ship_config.load_ship_config`, command + flags — and compare. Reuse the
  writer's construction path so the two cannot drift; a second implementation is
  how a false mismatch appears later.
- `NotConfigured` today ⇒ a record with a non-empty invocation is untrusted,
  and vice versa. Say which in the outcome.
- AC-6: read-only. No writes, no deletes.
- Commit: `feat(loop): a record whose invocation the project cannot produce is stale [plan:a-gate-record-is-validated#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_validation.py skills/ilk-loop/tests/test_batch_gate.py skills/ilk-loop/tests/test_batch_gate_freeze.py skills/ilk-loop/tests/test_ship_audit.py -q --timeout=60 --timeout-method=signal
    timeout: 600
```
- Confirm step-0 tests green, including AC-3's fresh case and AC-6's
  no-mutation check.
- Record in Findings the validator's output for **each** of the five outcomes —
  five lines. If any two read alike, fix the wording before shipping: the whole
  point is that a reader can act on the difference.
- Record the outcome for the real production record (AC-7).
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): a-gate-record-is-validated shipped [plan:a-gate-record-is-validated#ship]`

## Findings

Five outcomes, five distinct words — verified 2026-08-25:

1. `stale_head: record sha 879f33f != current HEAD deb7d56`
2. `stale_invocation: record invocation 'wrong.py -q' != expected 'python3 -m pytest --timeout=60 --timeout-method=signal'`
3. `fresh`
4. `incomplete: missing field(s): invocation, timestamp`
5. `absent: no batch-gate record found`

AC-7 real production record: **stale_head** (`879f33f` vs current `deb7d56`).

## Reference reading

- `skills/ilk-loop/scripts/batch_gate.py` — the writer, `_git_head_sha`, and
  the `ship_config` resolution at ~328-352 whose construction step 2 must reuse.
- `skills/ilk-ship/scripts/ship_config.py` — `load_ship_config`, `ShipConfig`,
  `NotConfigured`.
- MASTER's "sequencing constraint" — why this precedes SP3.
