---
plan: the-record-becomes-authoritative
status: shipped
current_step: 3
tickets:
  - READER-NOT-AUTHORITATIVE
priority: P0
estimated_steps: 4
last_updated: 2026-08-25
verification_tier: loop-verified
regression_for:
depends_on:
  - a-gate-record-is-validated
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/ship_audit.py"
  - "skills/ilk-loop/tests/test_ship_audit_authoritative.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_ship_audit_authoritative.py"
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

# Sub-plan: The record becomes authoritative

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #3 — must not start before SP2 is shipped.**

## Before you start

**Verify `a-gate-record-is-validated` is `shipped` before doing anything.** If
it is not, stop and say so. This sub-plan makes `ship_audit` trust the batch
record; doing that before validation exists opens exactly the rubber-stamp
window the parent batch was written to close.

Verified 2026-08-25: `ship_audit` today reports `final_gate: fail` (*"gate
declared but no gate result recorded"*) **while a stale `verdict: pass` sits on
disk** — so the plumbing from the parent batch's step 1 landed, but the record
is not authoritative. That is why the stale record is currently harmless.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| READER-NOT-AUTHORITATIVE | `ship_audit` references the record but still needs `--gate-passed` asserted by hand | gap | P0 | `skills/ilk-loop/scripts/ship_audit.py` |

## The finding this sub-plan closes

The parent batch's SP3 stopped at step 1. Trailers in `git log`:
`#step-0`, `#step-1` — and nothing else. What never landed:

- the staleness comparison (its AC-2),
- the explicit `--gate-passed` override that announces itself (its AC-5),
- the caller gate on `test_loop_status.py` (its AC-7).

The sub-plan was marked `shipped` regardless.

So `--gate-passed` still defaults to `unknown` and every Phase 0 this session
was run with `--gate-passed true` typed by hand — the operator asserting the
answer the audit exists to establish.

## Objectives

1. Take the gate verdict from the validated record, not from a flag.
2. Refuse on any untrusted outcome, using SP2's vocabulary unchanged.
3. Keep `--gate-passed` as an override that is visible when used.

## Acceptance criteria

- **AC-1**: with a **fresh, valid** record present, `ship_audit` derives the
  verdict from it with no `--gate-passed` supplied. Asserted for both `pass`
  and `fail` records.
- **AC-2**: on **stale**, **invalid**, or **absent** (SP2's three untrusted
  outcomes), the audit **refuses** and names which one. It must never read as a
  pass, and never as a plain `fail` — a reader has to tell "the gate failed"
  from "the record cannot be trusted".
- **AC-3**: the audit **reuses SP2's validator** rather than re-deriving
  staleness. Asserted structurally — a second staleness implementation is how
  the two will disagree later, which is the v0.9.70 defect class.
- **AC-4**: `--gate-passed` still overrides, and the output **says it was
  overridden**. A manual override must not be indistinguishable from a real
  read in the transcript.
- **AC-5**: the existing `test_ship_audit.py` stays green, including the
  `#ship`-satisfies-final-step behaviour from v0.9.72.
- **AC-6**: `loop_status.py` imports `ship_audit`, so its tests run in this
  sub-plan's gates. A verdict change that breaks the status surface is the two
  readers disagreeing again.
- **AC-7**: the stale production record on this host makes the audit **refuse**
  — asserted against SP2's committed fixture. Today it is inert; after this
  sub-plan it must be actively refused rather than quietly consumed.

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

- Validation itself (SP2). Consume it; do not reimplement it.
- Fixing "Phase 0 cannot distinguish *gates passed* from *no gates existed*"
  (filed high/high, 2026-08-25). Adjacent, separate, **not closed here** — this
  sub-plan makes the batch verdict trustworthy; the ungated-sub-plan case needs
  its own fix. Do not conflate them.
- `/ilk-ship`'s phase text — that is SP4.
- Re-opening the parent batch's sub-plans.

## Steps

### Step 0 — Red-first: every way an untrusted record could look verified
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_ship_audit_authoritative.py -q 2>&1 | tail -5 | grep -qE '[4-9] failed|1[0-9] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_ship_audit_authoritative.py`.
- Write AC-1 (fresh pass and fresh fail), AC-2 (stale / invalid / absent each
  refused and named), AC-7 (the real record refused). All red.
- Follow the fixture conventions in `test_ship_audit.py`; do not invent a new
  harness.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(ship): red-first for an authoritative batch verdict [plan:the-record-becomes-authoritative#step-0]`

### Step 1 — Derive the verdict from the validated record
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_audit_authoritative.py -q -k 'fresh or refuses' --timeout=60 --timeout-method=signal
    timeout: 180
```
- Call SP2's validator (AC-3) and branch on its outcome. Do not re-derive
  staleness or re-read the record's fields independently.
- Keep `evaluate_ship`'s existing vocabulary — `unknown` remains a violation;
  that behaviour is load-bearing.
- Commit: `feat(ship): the validated batch record decides the gate verdict [plan:the-record-becomes-authoritative#step-1]`

### Step 2 — The override announces itself
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_audit_authoritative.py skills/ilk-loop/tests/test_ship_audit.py skills/ilk-loop/tests/test_loop_status.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```
- AC-4: `--gate-passed` overrides and the output states it. Its default stops
  being the effective answer whenever a record exists.
- AC-5 / AC-6: `test_ship_audit.py` and `test_loop_status.py` (the caller) stay
  green — both are in the gate above, not merely asserted in prose.
- Commit: `feat(ship): an overridden gate verdict says so [plan:the-record-becomes-authoritative#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_ship_audit_authoritative.py skills/ilk-loop/tests/test_ship_audit.py skills/ilk-loop/tests/test_loop_status.py skills/ilk-loop/tests/test_batch_gate_validation.py -q --timeout=60 --timeout-method=signal
    timeout: 600
```
- Confirm step-0 tests green.
- Record in Findings the audit's output for **each** of: fresh pass, fresh fail,
  stale, invalid, absent, and manual override — six lines, visibly different.
- Record what the audit now says about the real production record (AC-7).
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): the-record-becomes-authoritative shipped [plan:the-record-becomes-authoritative#ship]`

## Findings

Six outcomes, six distinct verdicts — verified 2026-08-25:

1. `fresh pass:      verdict=pass, reason=None`
2. `fresh fail:      verdict=fail, reason=batch gate recorded: fail`
3. `stale:           verdict=stale_head, reason=batch-gate record is stale_head`
4. `invalid:         verdict=incomplete, reason=batch-gate record is incomplete`
5. `absent:          verdict=absent, reason=batch-gate record is absent`
6. `manual override: verdict=pass, reason=gate verdict overridden to true (manual --gate-passed flag)`

AC-7 real production record: **stale_head** — actively refused, not silently consumed.

## Reference reading

- SP2 `2026-08-25c-a-gate-record-is-validated.md` — the validator and its five
  outcomes.
- `skills/ilk-loop/scripts/ship_audit.py` — `--gate-passed` at ~250,
  `_evaluate_gate`, `audit_ship`.
- `skills/ilk-loop/scripts/ship_integrity.py` — `evaluate_ship`; why `unknown`
  is a violation.
- MASTER's "sequencing constraint" — why SP2 precedes this.
