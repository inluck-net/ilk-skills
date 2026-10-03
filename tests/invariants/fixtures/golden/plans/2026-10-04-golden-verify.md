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
unit_test_targets:
  - "tests/"
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

Batch verification — runs the full suite and attributes any reds.

## Steps

### Step 0 — run full suite

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/ -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Run the full suite. The breakage in `clamp` (from golden-red) should surface
here as `tests/test_other.py::test_clamp_boundary` failing.

### Step 1 — attribute the red

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/ -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Run the full suite again and attribute the red to golden-red.

## Findings