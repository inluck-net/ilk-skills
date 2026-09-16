---
plan: issue-5445-batch-verification-e0b2a837
status: shipped
current_step: 2
batch_verification: true
tickets:
  - GHR-5445
priority: P1
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
local_checks:
  - command: bunx tsc --noEmit -p convex/tsconfig.json --pretty false
    timeout: 300
scope_paths: []
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: null
ci_timeout_minutes: 30
ci_max_retries: 2
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
verified: true
---

# Batch verification — issue #5445

Closes the batch for issue #5445.

run_id: ``e0b2a837``

## Input

- Every sub-plan above this one in the registry is shipped.
- Write target: none. This sub-plan verifies the batch; it does not edit it.

## Output

- [ ] The full suite has been run and its actual result recorded here, as
      counts rather than adjectives.
- [ ] Every failure is attributed: caused by this batch, or already failing on
      the base. A baseline run is what separates the two.
- [ ] Regressions attributed to this batch are named. Fixing them is a
      follow-up, not an edit made from inside this sub-plan.

## Steps

### Step 0 — run the batch suite

Run the suite on the batch head and record the result as counts, plus the node
id of every failure.

**The record lives outside the repo, so commit an empty marker** (D-311). The
ship-proof row attributes commits to steps; it is not evidence about the gate.
Without a commit this step cannot be attributed, earns no row, and `reap`
refuses the whole run:

```bash
git commit --allow-empty -m "test(verify): record full suite result for issue-5445-batch-verification [plan:issue-5445-batch-verification-e0b2a837#step-0]"
```

### Step 1 — re-run the failures at base, and attribute from the result

**Measure the middle term; do not reason about it.** "Passed at the batch's base
commit" is the only part of the attribution rule that costs an action, so it is
the part that gets replaced by a story about whether a failure "looks related".
Re-run **only the failing node ids** at the base, in a **detached worktree** so
the batch tree is never touched:

```bash
git worktree add --detach /tmp/verify-base-5445 8975c7cfa45dc43694307b248acf21f08cda6f09
cd /tmp/verify-base-5445 && <suite runner> <failing node ids>
```

This is cheap — it is the failing selection, not the suite.

Record one row per failing node id under the exact heading `## At-base rerun`:

```markdown
## At-base rerun

Base: 8975c7cfa45dc43694307b248acf21f08cda6f09 · worktree: detached · command: <runner> <node ids>

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_foo.py::test_bar | passed | no | YES |
```

- `at base: passed` and not in `baseline_red` ⇒ **attributed**. There is no
  third column that makes it not-attributed.
- `at base: failed` ⇒ not attributed, and the row is its own evidence.
- **Exactly one row per failure.** A record reporting N failures and explaining
  them in prose does not satisfy this.

**No prose overturns a row.** "Environmental", "pre-existing", "flaky", "a
line-number shift", "the batch did not touch that file" are hypotheses about
*why* a test broke — the beginning of a fix, not grounds to set the count to
zero. If one is true, the rerun says so. If the rerun says it passed at base,
this batch broke it.

**Commit a marker either way** (D-311). Step 1 has a commit whether or not it
found anything, because "no attributed regressions" is a result and an
unattributable step blocks the whole run:

```bash
# nothing attributed — the common case, and still a finding:
git commit --allow-empty -m "fix(verify): no attributed regressions [plan:issue-5445-batch-verification-e0b2a837#step-1]"
# something attributed and fixed:
git commit -m "fix(verify): resolve attributed regressions [plan:issue-5445-batch-verification-e0b2a837#step-1]"
```

## Gate

- `bunx tsc --noEmit -p convex/tsconfig.json --pretty false`

## Done

- [ ] Suite result recorded, with counts.
- [ ] Failures attributed against the baseline.
