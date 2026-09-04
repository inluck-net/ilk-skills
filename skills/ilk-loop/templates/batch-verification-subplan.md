---
plan: <short-slug>-verify
batch_verification: true
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: YYYY-MM-DD
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
recommended_iteration_timeout_min: 90
local_checks: []
scope_paths:
  - "<glob/prefix for files this batch touched>"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: github
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: batch verification — full suite

Part of [MASTER-YYYY-MM-DD-execution-plan](./MASTER-YYYY-MM-DD-execution-plan.md).
**Order #N (last)** — runs the full test suite for the batch. No other
sub-plan runs the full suite; they stay change-scoped.

This sub-plan is **not optional**. A master without a batch-verification
sub-plan is a HARD lint finding (`lint_master_has_verification_subplan`).

## Before you start

1. The MASTER plan referenced above — workstream map, cross-cutting rules,
   execution rationale.
2. `skills/ilk-ship/SKILL.md` Phase 1-2 — the baseline-diff and
   attributed-regression rule this sub-plan reuses.

## The exit condition

**"No failure attributed to this batch"**, not "zero failures".

`.ilk-launch.json` carries a `baseline_red` list — entries that fail on
this platform and are not caused by this batch (e.g. Windows-only tests
on macOS). "Until all pass" would send an agent chasing those, and the
cheapest way to "succeed" is to weaken a test.

**Attribution rule** (from `/ilk-ship` Phase 1): a failure is attributed
to this batch iff it **fails now**, **passed at the batch's base commit**,
and is **not in `baseline_red`**.

**Fix-until-green needs no new machinery.** A step whose gate is red does
not advance — `current_step` stays put, the failure output lands in
Findings, and the next iteration retries. The bound is
`quarantine_subplan.py`'s threshold of 2 — two confirmed reds flip the
sub-plan to `status: blocked` with the failures named.

## Objectives

1. Run the full test suite for this batch.
2. Record the result and the base-commit comparison.
3. Fix every attributed failure until zero remain.

## Steps

### Step 0 — Run the full suite, record the result

```yaml
local_checks:
  - command: "<run the full test suite per .ilk-launch.json ship.suite>"
    timeout: <suite timeout>
```

- Run the project's full test suite (from `.ilk-launch.json` → `ship.suite`,
  or `python3 -m pytest --timeout=60 --timeout-method=signal` if unconfigured).
- Record the result: which tests failed, which passed, which were skipped.
- Compare against the batch's base commit to identify attributed regressions:
  tests that fail now but passed at the base commit.
- Compare against `.ilk-launch.json`'s `baseline_red` list to exclude
  pre-existing platform failures.
- **Write the record to the external logs directory** (not into the project tree):
  ```python
  from skills.ilk_loop.scripts.ilk_paths import external_logs_dir, resolve_project_key
  key = resolve_project_key(Path.cwd())
  verification_dir = external_logs_dir(key) / "verification"
  verification_dir.mkdir(parents=True, exist_ok=True)
  ```
  Write `<batch-slug>-baseline.md` and `<batch-slug>-batch.md` to that directory.
- **Commit an empty marker** (the record lives outside the repo):
  `git commit --allow-empty -m "test(verify): record full suite result for <batch-slug> [plan:<slug>#step-0]"`
- The gate asserts **the external record exists and is non-empty**, not that the
  suite is green — otherwise step 0 can never pass when there is something
  to fix.

### Step 1 — Fix every attributed failure

```yaml
local_checks:
  - command: "<run the full test suite; assert zero attributed failures>"
    timeout: <suite timeout>
```

- Fix every failure attributed to this batch (fails now, passed at base
  commit, not in `baseline_red`).
- The gate re-runs the full suite and asserts **zero attributed failures**.
- Red gate ⇒ retried next iteration (current_step stays at 1).
- Two confirmed reds ⇒ `status: blocked`, naming the failures.
- Commit: `fix(verify): resolve attributed regressions [plan:<slug>#step-1]`

## Findings

_(filled by the loop during execution)_

## Reference reading

- `skills/ilk-ship/SKILL.md` Phase 1-2 — baseline-diff and attributed regressions.
- `skills/ilk-loop/scripts/quarantine_subplan.py` — the bound on the fix loop.
- `.ilk-launch.json` — `ship.suite` command and `baseline_red` list.
