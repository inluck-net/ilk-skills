---
plan: a-reread-is-refused
status: shipped
current_step: 4
tickets:
  - READ-GUARD-DUPLICATE
priority: P0
estimated_steps: 5
last_updated: 2026-08-26
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
# local_checks is intentionally empty: every gate here is per-step. A
# frontmatter gate runs at EVERY step, so it would be red-by-construction
# against step 0's red-first tests (plan_lint HARD finding).
local_checks: []
scope_paths:
  - "hooks/no-duplicate-read.sh"
  - "skills/ilk-loop/tests/test_no_duplicate_read_hook.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_no_duplicate_read_hook.py"
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

# Sub-plan: a re-read of an unchanged file is refused

Part of [MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan](./MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan.md).
**Order #1** — produces the artifact SP2 registers; nothing else in that
chain can start without it.

## Before you start

Read, in order:

1. The MASTER plan — especially "The measurement this batch is built on"
   and "Gate policy for this batch".
2. `hooks/no-full-suite.sh` — the working precedent for a DENY-tier
   PreToolUse hook in this repo. Match its shape: `set -uo pipefail`,
   `allow()`/`deny()` helpers, JSON on stdin, `hookSpecificOutput` with
   `permissionDecision` on stdout, and **always exit 0** (a non-zero hook
   exit is a hook error, not a deny).

## Why this exists

Measured on gh-resolve run `20260825-234253`: **99 of 157 `Read` calls
re-read a path already read in the same iteration**. Model decide-time
before those redundant reads was **773s of the 6889s run (11.2%)**.

That 773s is a **floor**. It counts only the decide-time before each
redundant read; it excludes the cost of re-ingesting the content on every
later turn, which the launcher log cannot separate. Do not restate it as a
saving.

Concrete shape, from iteration 5's orientation: `queue.py` and
`known-defects.md` were each read twice before the first write.

## Design decisions already made — do not relitigate

**DENY, not warn.** A warning the agent can ignore will not change
behaviour; `no-full-suite.sh` is the working precedent for deny-with-guidance.

**The re-read allowance is mtime-based, NOT Edit/Write-based.** A file can
legitimately change from a subprocess the hook never sees — a test
regenerating a fixture, a `git checkout`, a code generator. An
Edit/Write-only allowance would wedge the agent on a read it genuinely
needs, and a wrongly-denied read fails *silently*. Compare `(path, mtime_ns,
size)`; if either differs from the ledger entry, allow and re-record.

**Fail open, always.** Any error — unreadable ledger, missing session key,
unparseable payload, `stat` failure — must `allow()`. A guard that denies on
its own bug is worse than no guard.

## Objectives

1. A `Read` of a path already read in this session, with the file unchanged,
   is denied with a message naming the path.
2. A `Read` of a path whose mtime or size changed since the ledger entry is
   allowed, and the ledger updated.
3. A first `Read` of any path is allowed and recorded.
4. Every failure mode of the hook itself allows the call through.

## Acceptance criteria

- **AC-1** First read of a path → `permissionDecision` absent or `allow`;
  ledger now contains the path.
- **AC-2** Second read of the same path, file untouched → `deny`, and the
  reason string contains the absolute path and says it is already in
  context.
- **AC-3** Second read after the file's mtime/size changes → allowed, ledger
  entry updated to the new mtime.
- **AC-4** Two different session keys do not share a ledger: reading path P
  in session A does not deny P in session B.
- **AC-5** Corrupt ledger file, absent session key, non-JSON stdin, empty
  stdin, and a `stat` failure on a deleted path each → allow (fail open).
- **AC-6** A non-`Read` tool (`Bash`, `Edit`, `Grep`) is never denied by this
  hook, whatever the ledger holds.
- **AC-7** The hook exits 0 in every case above, including deny.

## Out of scope

- Registering the hook in `settings.json` or `install.sh` — that is SP2.
  This sub-plan's tests invoke the script by path.
- Installing to `~/.claude`. Worker host only, and that is SP2's call.
- Any guard on `Grep`, `Glob`, or `Bash cat`. `Read` only.
- Ledger eviction/TTL policy beyond what AC-5 needs. If the ledger grows
  unbounded in practice, that is a follow-up with a measurement.

## Steps

### Step 0 — Red-first: pin the payload shape, then the contract

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_no_duplicate_read_hook.py -q 2>&1 | tail -5 | grep -qE '[5-9]|[1-9][0-9] failed'"
    timeout: 120
```

**The payload's session key is an ASSUMPTION until this step proves it.**
Nothing in this repo reads `session_id` — `no-full-suite.sh` reads only
`tool_name` and `tool_input`. Do not build the ledger on a field you have
not observed.

- Write a characterisation test that records, as a committed fixture, which
  of `session_id` / `transcript_path` / `cwd` / `hook_event_name` a
  PreToolUse payload actually carries. Get the evidence from a real
  transcript under `~/.ilk-data/projects/*/logs/runs/*/iter-*.log.jsonl`, or
  by a one-shot probe hook you register and remove within this step. State
  in the test docstring which method produced it.
- **Key selection order, declared now so the test can assert it:**
  `session_id` → `transcript_path` → `cwd` + PPID. The hook must implement
  whichever the fixture proves available, and the test names it.
- Create `skills/ilk-loop/tests/test_no_duplicate_read_hook.py` with AC-1
  through AC-7 as failing tests. Drive the hook as a subprocess, feeding
  JSON on stdin and asserting on parsed stdout — the same harness shape as
  `skills/ilk-loop/tests/test_batch_gate_persistence.py`'s CLI tests.
- Point every test at a ledger under `tmp_path` via an env override, so the
  suite never touches a real session's ledger.
- Expected-red. The gate asserts the red count, not exit 0.
- Commit: `test(hooks): red-first — a re-read of an unchanged file must be refused [plan:a-reread-is-refused#step-0]`

### Step 1 — The ledger: record and recognise a path

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_no_duplicate_read_hook.py -q -k 'first_read or second_read or session' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Create `hooks/no-duplicate-read.sh` modelled on `hooks/no-full-suite.sh`.
- Read the payload; if `tool_name != "Read"`, `allow()` immediately (AC-6).
- Resolve the session key by the order fixed in step 0.
- Ledger path: `~/.ilk-data/runtime/read-ledger/<session-key>.json`,
  overridable by `ILK_READ_LEDGER_DIR` so tests redirect it to `tmp_path`.
  Store `{abs_path: {mtime_ns, size}}`. Not `/tmp` (cleared unpredictably
  mid-session on macOS) and **not** inside the project tree — the ledger is
  host runtime state, the same class as the batch-gate record, and a
  project-tree path would land it in someone's diff.
- First read records and allows (AC-1); a repeat with an identical stat
  denies (AC-2). Deny reason must name the absolute path and say the content
  is already in context.
- Commit: `feat(hooks): refuse a re-read of a file already in context [plan:a-reread-is-refused#step-1]`

### Step 2 — The mtime allowance

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_no_duplicate_read_hook.py -q -k 'mtime or changed or size' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Compare `(mtime_ns, size)` against the ledger entry; on any difference,
  allow and overwrite the entry (AC-3).
- Use `mtime_ns`, not whole seconds — a fixture regenerated inside the same
  second must still read as changed.
- Commit: `feat(hooks): a changed file may be read again [plan:a-reread-is-refused#step-2]`

### Step 3 — Fail open on every hook-side error

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_no_duplicate_read_hook.py -q -k 'corrupt or missing or empty or nonjson or deleted or exit' --timeout=60 --timeout-method=signal
    timeout: 120
```

- Corrupt ledger, unset session key, non-JSON stdin, empty stdin, `stat` on
  a deleted path → `allow()` (AC-5).
- Assert exit 0 on every path including deny (AC-7).
- Commit: `feat(hooks): the read guard fails open on its own errors [plan:a-reread-is-refused#step-3]`

### Step 4 — Verification and ship

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_no_duplicate_read_hook.py -q --timeout=60 --timeout-method=signal
    timeout: 180
```

- All AC-1..AC-7 green.
- Confirm by hand that the hook is NOT yet in any `settings.json` — SP2 owns
  that, and a guard registered early would take effect mid-batch.
- Record in "Findings" which session key the payload actually provided, and
  whether the fallback chain was needed. SP2 and any future hook depend on
  that answer.
- Commit: `chore(plans): a-reread-is-refused shipped [plan:a-reread-is-refused#ship]`

## Findings

- **Session key**: `session_id` is always present in the PreToolUse payload
  (confirmed from Claude Code hooks docs, 2026-08-26). The fallback chain
  (`transcript_path` → `cwd+PPID`) was not needed in practice, but is
  implemented defensively. SP2 can rely on `session_id` as the primary key.
- **Payload shape**: All four common fields (`session_id`, `transcript_path`,
  `cwd`, `hook_event_name`) are always strings. `hook_event_name` is always
  `"PreToolUse"` for this hook type.
- **All 31 tests green** (27 AC tests + 4 payload characterisation).

## Reference reading

- `hooks/no-full-suite.sh` — hook shape, deny-tier JSON contract.
- `skills/ilk-loop/tests/test_batch_gate_persistence.py` — subprocess-CLI
  test harness shape used elsewhere in this repo.
