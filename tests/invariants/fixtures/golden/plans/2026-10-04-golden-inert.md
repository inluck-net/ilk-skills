---
plan: golden-inert
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

# Sub-plan: golden-inert

A vacuous pin that hand-writes the value it claims to observe.

## Steps

### Step 0 — vacuous pin

```yaml
local_checks:
  - command: "export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH && python3 -m pytest tests/test_calc.py::test_scale -q -p no:cacheprovider"
    timeout: 60
```

Replace `test_scale` with a vacuous pin that asserts `True` (the decay (a) shape).
Use `xfail(strict=True)` — the vacuous pin passes, so it XPASSes, and the strict
xfail turns that into a red gate.

### Step 1 — (never reached)

```yaml
local_checks:
  - command: "export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH && python3 -m pytest tests/test_calc.py -q -p no:cacheprovider"
    timeout: 60
```

This step is never reached because step 0's gate is red and the sub-plan
gets quarantined after two failures.

## Findings