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
  - "tests/test_other.py"
unit_test_targets:
  - "tests/test_other.py"
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
auto_block_fails: 2
---

# Sub-plan: golden-inert

Add a vacuous pin that XPASSes. The decay (a) shape: a hand-written test that
asserts `True` and is marked `xfail(strict=True)`, so it XPASSes and the gate
catches it. Operates on `test_other.py` (not `test_scale`) so it does not
corrupt golden-real's gate.

Two steps but only one real transformation: step 1 is a no-op that exists so
the stub does not ship until after the gate has had two chances to red.
The quarantine threshold is 2; after two consecutive XPASS gate failures the
runner quarantines this sub-plan to `blocked`.

## Steps

### Step 0 — add vacuous pin

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/ -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

Add `test_vacuous` to `tests/test_other.py`: `xfail(strict=True)`, body is
`assert True`. The gate runs the full suite; `test_vacuous` XPASSes → red.

### Step 1 — (no-op: step 0 already triggers quarantine)

```yaml
local_checks:
  - command: "/usr/bin/python3 -m pytest tests/ -q -p no:cacheprovider --timeout=30 --timeout-method=signal"
    timeout: 60
```

If step 0's gate reds twice, this sub-plan is quarantined to `blocked` before
step 1 runs. No second transformation needed.

## Findings