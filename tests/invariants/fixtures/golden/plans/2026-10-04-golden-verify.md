---
plan: golden-verify
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: "2026-10-04"
verification_tier: loop-verified
regression_for: ""
depends_on: ["golden-red"]
data_prereqs: []
env_prereqs: []
batch_verification: true
recommended_iteration_timeout_min: 5
local_checks: []
scope_paths:
  - "tests/"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: ""
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
auto_block_fails: 0
---

# Sub-plan: golden-verify

Batch verification — runs the full suite and attributes any reds.  Uses the
shipped verification machinery (verification_record.py + verify_attribution.py)
so the golden batch proves the verify pipeline catches a planted red.

## Steps

### Step 0 — run full suite, record the result

```yaml
gate_first: true
local_checks:
  - command: "python3 $ILK_SKILL_HOME/ilk-loop/scripts/verification_record.py --project . --batch golden --run-suite --scope full --ledger prefer --base-sha $BASE_SHA"
    timeout: 300
```

Run the full suite via verification_record.py.  The breakage in `clamp`
(from golden-red) should surface here as `tests/test_other.py::test_clamp_boundary`
failing.  The record is written to the external logs directory.

### Step 1 — verify attribution

```yaml
gate_first: true
local_checks:
  - command: "python3 $ILK_SKILL_HOME/ilk-loop/scripts/verify_attribution.py --batch golden --project ."
    timeout: 300
```

Re-derive the verdict from the at-base rerun table.  The planted clamp red
must be attributed to golden-red; no other test is attributed.

## Findings