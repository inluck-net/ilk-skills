---
plan: golden-red
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: "2026-10-04"
verification_tier: loop-verified
regression_for: ""
depends_on: []
data_prereqs: []
env_prereqs: []
recommended_iteration_timeout_min: 5
local_checks: []
scope_paths:
  - "tests/test_calc.py"
unit_test_targets:
  - "tests/test_calc.py"
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

# Sub-plan: golden-red

Fix `add` but break `clamp`. The gate runs only `test_calc.py`, so the
breakage in `test_other.py` is invisible to this sub-plan's own gate
(the decay (d) shape).

## Steps

### Step 0 — pin add

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/test_calc.py::test_add -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Pin `add` with `xfail(strict=True)` — it must fail with `AssertionError`.

### Step 1 — fix add, break clamp

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/test_calc.py::test_add -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Fix `add` and break `clamp` (change `max` to `min`). Remove the xfail mark.
The gate runs only `test_calc.py`, so the breakage in `test_other.py` is
invisible here.

## Findings