---
plan: a-failed-gate-is-reported
status: shipped
current_step: 4
tickets:
  - GATE-EXIT-DEAD-BRANCH
priority: P0
estimated_steps: 4
last_updated: 2026-08-25
verification_tier: loop-verified
regression_for: GATE-EXIT-DEAD-BRANCH
depends_on: []
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
  - "skills/ilk-loop/tests/test_batch_gate_exit_reporting.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_batch_gate_exit_reporting.py"
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

# Sub-plan: A failed gate is reported

Part of [MASTER-2026-08-25c-finish-the-batch-gate-execution-plan](./MASTER-2026-08-25c-finish-the-batch-gate-execution-plan.md).
**Order #1** — independent and cheap. Every later verification reads a runner
that currently cannot report a gate failure; fix the reporting first.

## Before you start

`run_ilk_loop_claude.sh` is **contract-governed** (§7h). Read
`skills/ilk-loop/references/detached-component-contracts.md` — the "Adding a
new reader or writer" checklist — and
`skills/ilk-loop/references/orchestration-collaboration.md` L1-L4 first.

**Do not re-wire the call sites.** Verified: `invoke_batch_gate` is already
called at lines 1836 and 2315, immediately before the `ALL SHIPPED` prints at
1838/2317 — both sites. That work is done.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| GATE-EXIT-DEAD-BRANCH | `$?` after `\|\| true` is always 0, so the gate-failure branch never runs | escaped-bug | P0 | `run_ilk_loop_claude.sh` |

## The finding this sub-plan closes

In `invoke_batch_gate`:

```
gate_output=$(python3 "$batch_gate_script" ... --run 2>&1) || true
local gate_exit=$?
if [[ $gate_exit -ne 0 ]]; then
  echo "[batch-gate] Gate exited with code $gate_exit — output:"
  ...
fi
```

`|| true` swallows the failure, so `$?` reads **the exit of `true`**, not of the
gate. Proven directly: `out=$(false) || true; echo $?` prints **`0`**.

Consequence: `[[ $gate_exit -ne 0 ]]` can never be true. A batch gate that
fails, errors, or cannot run is **never reported by the runner**. The record on
disk may say `fail` while the run's own output says nothing — and it is the
runner's output an operator reads first.

Two requirements pull in opposite directions and both must hold:

- **AC-6 of the parent batch**: the gate must never prevent the runner from
  terminating (`return 0` at the end is deliberate).
- **This defect**: a failure must be *reported*.

Reporting a failure and not blocking termination are different things. The
current code achieves the second by destroying the first.

## Objectives

1. Capture the gate's real exit status.
2. Report a non-zero status visibly in the runner's output.
3. Keep the runner terminating regardless — no hang, no early exit.

## Acceptance criteria

- **AC-1**: when the batch gate exits non-zero, the runner prints a failure line
  naming the exit code and the gate's output. Asserted by driving
  `invoke_batch_gate` with a stub gate script that exits non-zero and grepping
  the captured output.
- **AC-2**: when the gate exits zero, the runner reports success and prints no
  failure line. Asserted separately, so the fix cannot degrade into "always
  reports failure".
- **AC-3**: in **both** cases `invoke_batch_gate` still returns 0, so the runner
  terminates normally. Asserted for both, because this is the AC-6 obligation
  the current code was protecting.
- **AC-4**: when the gate script is **missing**, the existing warning path still
  fires and still returns 0. Asserted — this path already works and must not
  regress.
- **AC-5**: `bash -n skills/ilk-loop/scripts/run_ilk_loop_claude.sh` stays
  clean.

## Out of scope

- The call sites (verified correct — see "Before you start").
- Suite resolution, the record schema, or record validation (SP2).
- `promote_next_master.py`.

## Steps

### Step 0 — Red-first: a failing gate must be reported
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_batch_gate_exit_reporting.py -q 2>&1 | tail -5 | grep -qE '[1-4] failed'"
    timeout: 120
```
- Create `skills/ilk-loop/tests/test_batch_gate_exit_reporting.py`.
- Drive `invoke_batch_gate` by dot-sourcing the runner with
  `ILK_DOTSOURCE_ONLY=1` — follow the existing harness in
  `skills/ilk-loop/tests/test_ship_audit.py` (`_source_runner_and_call`) rather
  than inventing a second one.
- Write AC-1 (non-zero gate → failure line) and AC-3 (returns 0 anyway). Red
  today: the branch is unreachable.
- Expected-red; the gate asserts the **red count**, not exit 0.
- Commit: `test(loop): red-first — a failing batch gate must be reported [plan:a-failed-gate-is-reported#step-0]`

### Step 1 — Pin what must not change
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_exit_reporting.py -q -k 'success or missing or returns_zero' --timeout=60 --timeout-method=signal
    timeout: 180
```
- Add AC-2 (zero exit → success, no failure line), AC-4 (missing script → warn,
  return 0). These must be green **before and after** step 2 — they are the
  regression net.
- Commit: `test(loop): pin success and missing-script gate paths [plan:a-failed-gate-is-reported#step-1]`

### Step 2 — Capture the real exit status
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_exit_reporting.py -q --timeout=60 --timeout-method=signal
    timeout: 300
  - command: bash -n skills/ilk-loop/scripts/run_ilk_loop_claude.sh
    timeout: 30
```
- Replace `|| true` + `$?` with a form that captures the real status — e.g.
  `gate_output=$(...) || gate_exit=$?` with `gate_exit` defaulted to 0 first,
  or `set +e` around the call. Whichever you choose, the assignment must not
  itself reset `$?`.
- Keep the terminal `return 0` (AC-3 / parent AC-6). Report the failure; do not
  propagate it.
- Commit: `fix(loop): capture the batch gate's real exit status [plan:a-failed-gate-is-reported#step-2]`

### Step 3 — Verification and ship
```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_batch_gate_exit_reporting.py skills/ilk-loop/tests/test_batch_gate.py -q --timeout=60 --timeout-method=signal
    timeout: 600
  - command: bash -n skills/ilk-loop/scripts/run_ilk_loop_claude.sh
    timeout: 30
```
- Confirm step-0 tests green and step-1's net still green.
- Record in Findings: the runner's output verbatim for a failing gate and for a
  passing one, so a reader can see they differ.
- Set `status: shipped`, update `last_updated`.
- Commit: `chore(plans): a-failed-gate-is-reported shipped [plan:a-failed-gate-is-reported#ship]`

## Findings

**Runner output — failing gate (exit 1):**
```
[batch-gate] Running batch-end gate...
[batch-gate] Gate exited with code 1 — output:
FAIL: tests broke
[batch-gate] Continuing despite gate failure (AC-6).
```

**Runner output — passing gate (exit 0):**
```
[batch-gate] Running batch-end gate...
[batch-gate] Gate completed.
all passed
```

The two outputs differ: the failure case names the exit code and prints the gate's
output; the success case reports completion only. Both return 0.

## Reference reading

- `skills/ilk-loop/references/detached-component-contracts.md` — REQUIRED.
- `skills/ilk-loop/references/orchestration-collaboration.md` L1-L4.
- `skills/ilk-loop/scripts/run_ilk_loop_claude.sh` — `invoke_batch_gate` at
  1315; call sites 1836 / 2315.
- `skills/ilk-loop/tests/test_ship_audit.py` — `_source_runner_and_call`, the
  dot-source harness to reuse.
