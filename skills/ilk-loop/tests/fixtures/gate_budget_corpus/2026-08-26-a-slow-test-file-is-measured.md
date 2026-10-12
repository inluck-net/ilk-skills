---
plan: a-slow-test-file-is-measured
status: shipped
current_step: 4
tickets:
  - GATE-COST-PER-TEST-FILE
priority: P1
estimated_steps: 4
last_updated: 2026-08-26
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
local_checks: []
scope_paths:
  - "skills/ilk-loop/scripts/gate_cost.py"
  - "skills/ilk-loop/tests/test_gate_cost_per_file.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_gate_cost_per_file.py"
  - "skills/ilk-loop/tests/test_gate_cost.py"
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

# Sub-plan: a slow test file is measured, not discovered after the fact

Part of [MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan](./MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan.md).
**Order #3** — independent of SP1/SP2; must precede SP4, which has nothing
to compare against without this data.

## Before you start

Read the MASTER plan, then `skills/ilk-loop/scripts/gate_cost.py` — in
particular `_calls()` at line 94, which already yields
`(duration_sec, command)` for every paired Bash call from
`iter-*.log.jsonl`. **The data source already exists.** This sub-plan
aggregates what `_calls` yields; it does not add a new source.

## Why this exists

On gh-resolve run `20260825-234253`, pytest execution was 1394s across 78
invocations (20.2% of the run), and **826s of that was 4 invocations** —
all hitting `tests/test_drain.py`, `tests/test_pr_closes_issue_guard.py`,
`tests/test_pass_reports_failure.py`:

```
225s  pytest -q tests/test_pass_reports_failure.py tests/test_pr_closes_issue_guard.py ...
205s  pytest tests/test_pr_closes_issue_guard.py tests/test_drain.py -q
199s  pytest tests/test_drain.py -q
197s  pytest tests/test_pass_reports_failure.py tests/test_drain.py -q
```

ilk-skills has timed every one of those calls in its own logs since the day
they ran, and has never surfaced it. The planner picks gate commands blind,
so it keeps choosing whole slow files when a `-k` selection would do.

## The attribution decision — read before writing code

A command naming three files yields **one** duration. There is no honest way
to split it. Three options, and the choice is already made:

- Attribute the full duration to each named file → over-counts, and the
  over-count is invisible in the output.
- Split evenly → asserts equal cost, which is exactly the thing being
  measured and therefore unknown.
- **Chosen: derive per-file cost only from invocations that name exactly one
  test file.** Multi-file invocations are reported separately, as a set,
  never folded into a per-file number.

This means the per-file table has its own denominator ("per-file cost from N
of M invocations"), and that denominator must be printed. A per-file number
without it is the failure mode this sub-plan exists to prevent.

## Objectives

1. `gate_cost.py` reports per-test-file wall-clock, per project.
2. The report states its denominator and never silently mixes multi-file
   invocations into per-file costs.
3. The output has a stable machine-readable form SP4 can consume.

## Acceptance criteria

- **AC-1** A `--by-test-file` mode prints, per project, each test file with
  its invocation count, total seconds, and max single-invocation seconds.
- **AC-2** The output states the denominator: how many of the total pytest
  invocations named exactly one file and were therefore usable.
- **AC-3** A multi-file invocation contributes to **no** per-file total; it
  appears only in a separate multi-file section keyed by the file set.
- **AC-4** `--json` emits the same data as a stable object, with an explicit
  schema version field so SP4 can depend on it.
- **AC-5** Zero measurable invocations produces "no measurements" naming the
  window and run count searched — not an empty table that reads like zero
  cost.
- **AC-6** `--since` / `--after` / `--before` filter this mode exactly as
  they filter the existing one.
- **AC-7** Existing `gate_cost.py` output and
  `skills/ilk-loop/tests/test_gate_cost.py` are unchanged and green.

## Out of scope

- Per-**test** (node-id) timing. Files only. `--durations` is the right tool
  for going finer and it belongs to whoever owns the slow repo.
- Changing the existing broad-gate metrics, the classifier, or the re-banked
  BEFORE fixture (`gate-cost-before-2026-08-25-rebanked.json`, commit
  `0dd5a0c`). Those are load-bearing for separate, ongoing work.
- Making any test faster. This sub-plan measures.
- Consuming the data in `plan_lint` — that is SP4.

## Steps

### Step 0 — Red-first: the contract, including the denominator

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_gate_cost_per_file.py -q 2>&1 | tail -5 | grep -qE '[4-9]|[1-9][0-9] failed'"
    timeout: 120
```

- Create `skills/ilk-loop/tests/test_gate_cost_per_file.py` with AC-1..AC-6
  as failing tests, driven by synthetic `iter-*.log.jsonl` fixtures under
  `tmp_path` — do **not** read the real `~/.ilk-data` logs in tests.
- Include a fixture reproducing the observed shape: one single-file call and
  one two-file call over the same file, asserting the two-file call is
  excluded from the per-file total (AC-3).
- Expected-red; the gate asserts the red count.
- Commit: `test(loop): red-first for per-test-file gate cost [plan:a-slow-test-file-is-measured#step-0]`

### Step 1 — Aggregate single-file invocations

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_gate_cost_per_file.py -q -k 'per_file or count or max' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Parse test-file paths from each command yielded by `_calls`. Handle the
  forms this repo actually produces: bare paths, `-q`/`-v` before or after
  paths, `-k` selections, and pipes to `tail`/`grep`.
- A command naming exactly one test file contributes to that file's totals;
  anything else does not (AC-1, AC-3).
- Commit: `feat(loop): aggregate wall-clock per test file [plan:a-slow-test-file-is-measured#step-1]`

### Step 2 — Denominators, the multi-file section, and JSON

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_gate_cost_per_file.py -q -k 'denominator or multi or json or empty or window' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Print the usable/total denominator (AC-2) and the separate multi-file
  section (AC-3).
- `--json` with a `schema: 1` field (AC-4).
- No-measurement case names the window and run count searched (AC-5).
- Wire `--since` / `--after` / `--before` through (AC-6).
- Commit: `feat(loop): per-file cost states its denominator and emits json [plan:a-slow-test-file-is-measured#step-2]`

### Step 3 — Verification and ship

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_gate_cost_per_file.py skills/ilk-loop/tests/test_gate_cost.py skills/ilk-loop/tests/test_iteration_timing.py -q --timeout=60 --timeout-method=signal
    timeout: 300
```

- AC-1..AC-7 green, including the existing suites unchanged (AC-7).
- Run the new mode against the **real** gh-resolve logs and record the
  output in "Findings". The three files named in "Why this exists" should
  appear at the top; if they do not, the parser is wrong and this is the
  step that catches it. That is a positive experiment, not a re-derivation.
- `gate_cost.py` is imported elsewhere, so `plan_lint`'s shared-module gate
  finding is expected here. It is answered by the batch-end gate. **Do not
  widen this step's gate to the whole suite to silence it.**
- Commit: `chore(plans): a-slow-test-file-is-measured shipped [plan:a-slow-test-file-is-measured#ship]`

## Findings

Ran `--by-test-file` against real gh-resolve logs (run 20260825-234253):
- 2116 total pytest invocations, 1705 single-file (usable denominator)
- Top single-file: `tests/` (bare dir, 170x, 17112s), `tests/test_doctor.py` (78x, 7992s)
- The 3 files from "Why this exists" confirmed:
  - `tests/test_drain.py` — 43 invocations, 713.1s
  - `tests/test_pr_closes_issue_guard.py` — 7 invocations, 46.3s
  - `tests/test_pass_reports_failure.py` — 6 invocations, 32.3s
- Multi-file section captures combined invocations correctly
- Note: bare `tests/` (directory) counts as a single "file" — _parse_test_files
  treats any path-like argument as a test path. This is correct for cost
  attribution but means directory-level runs dominate the top of the table.

## Reference reading

- `skills/ilk-loop/scripts/gate_cost.py:94` — `_calls()`, the existing
  paired-call extractor this builds on.
- `skills/ilk-loop/tests/test_gate_cost.py` — existing coverage to preserve.
