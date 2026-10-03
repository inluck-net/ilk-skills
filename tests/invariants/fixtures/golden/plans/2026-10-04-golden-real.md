---
plan: golden-real
status: in-progress
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
  - "tests/test_scale.py"
unit_test_targets:
  - "tests/test_scale.py"
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
auto_block_fails: 1
---

# Sub-plan: golden-real

Pin and fix `scale` in `test_scale.py`. Gate scoped to `test_scale.py` only —
not affected by golden-inert's changes to `test_calc.py`.

## Steps

### Step 0 — pin scale

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/test_scale.py::test_scale -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Pin `scale` with `xfail(strict=True)` — it must fail with `AssertionError`.

### Step 1 — fix scale

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/test_scale.py::test_scale -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Fix `scale` to divide instead of multiply. Remove the xfail mark.

## Findings