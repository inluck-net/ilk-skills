---
plan: every-hook-is-registered
status: shipped
current_step: 3
tickets:
  - INSTALL-HOOK-RECONCILE-HARDCODED
priority: P0
estimated_steps: 4
last_updated: 2026-08-26
verification_tier: loop-verified
regression_for:
depends_on:
  - a-reread-is-refused
data_prereqs: []
env_prereqs:
  - description: "interactive Claude config dir exists (install target)"
    verify_cmd: "test -d $HOME/.claude/hooks"
  - description: "worker Claude config dir exists (install target)"
    verify_cmd: "test -d $HOME/.claude-worker/hooks"
local_checks: []
scope_paths:
  - "install.sh"
  - "skills/ilk-loop/tests/test_hooks_install.py"
unit_test_targets:
  - "skills/ilk-loop/tests/test_hooks_install.py"
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

# Sub-plan: every hook is registered, not just the first one

Part of [MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan](./MASTER-2026-08-26-cut-iteration-wall-clock-execution-plan.md).
**Order #2** — without it SP1 ships a file that is symlinked and never
invoked.

## Before you start

Read the MASTER plan, then `install.sh` lines 705-770
(`reconcile_hooks_settings`) and 380-400 (`HOOKS_SRC` discovery).

## Why this exists

Measured by reading `install.sh` on 2026-08-26:

- `install.sh:392` sets `HOOKS_SRC="$REPO_ROOT/hooks"` and the file glob
  finds every `*.sh` — so a new hook **is** symlinked.
- `install.sh:719` hard-codes `local hook_cmd="$hooks_dir/no-full-suite.sh"`.
- `install.sh:737` hard-codes `{"matcher": "Bash", "hooks": []}`.

So `reconcile_hooks_settings` can only ever register **one** hook, under
**one** matcher. Confirmed against the installed state: the worker's
`settings.json` `PreToolUse` block contains exactly one entry, matcher
`Bash`, command `no-full-suite.sh`.

A `Read` guard needs `matcher: "Read"`. Under the current code it would be
symlinked into `hooks/` and never fire — indistinguishable, from the
outside, from a guard that works.

This also corrects a stated assumption: `skills/ilk-ship/SKILL.md` says
"There is **no** registration list to edit". That is true for skills and
commands (both glob-discovered) and **false for hooks**.

## Objectives

1. `reconcile_hooks_settings` registers an arbitrary set of hooks, each with
   its own matcher, from one declaration in `install.sh`.
2. The Read guard from SP1 is registered on the **worker** host only.
3. Re-running the installer is idempotent — no duplicated entries.
4. `no-full-suite.sh` keeps working exactly as before, on both hosts.

## Acceptance criteria

- **AC-1** A declaration table in `install.sh` maps hook filename → matcher;
  adding a row is the only edit needed to register a new hook.
- **AC-2** After `--apply`, the worker's `settings.json` `PreToolUse`
  contains both a `Bash`/`no-full-suite.sh` entry and a
  `Read`/`no-duplicate-read.sh` entry.
- **AC-3** `~/.claude`'s `settings.json` gains **no** Read entry —
  deliberately worker-only this batch (see MASTER "Rollout strategy").
- **AC-4** Running `--apply` twice leaves exactly one entry per (matcher,
  command) pair — asserted by count, not by eyeball.
- **AC-5** An unrelated pre-existing `PreToolUse` entry written by someone
  else survives the reconcile untouched.
- **AC-6** `--dry-run` reports what it would change and writes nothing —
  asserted by comparing the settings file's bytes before and after.
- **AC-7** The `no-full-suite.sh` Bash entry is byte-identical to its
  pre-change form on both hosts.

## Out of scope

- Changing what any hook *does*. SP1 owns the Read guard's behaviour.
- Installing the Read guard to `~/.claude`. Operator decision: worker
  first, prove it on a real run, decide separately.
- PostToolUse, Stop, or any other hook event. `PreToolUse` only.
- Removing hooks that are no longer in the repo. Pruning is a separate
  concern with its own failure modes.

## Steps

### Step 0 — Red-first: pin today's behaviour, then the contract

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_hooks_install.py -q 2>&1 | tail -5 | grep -qE '[3-9]|[1-9][0-9] failed'"
    timeout: 180
```

- Extend `skills/ilk-loop/tests/test_hooks_install.py`. It already exists and
  already covers the current install path — **read it before adding**, and
  keep its existing assertions green (AC-7).
- Every test must run the installer against a `tmp_path` fake HOME via
  `--claude-home`, never the real `~/.claude` or `~/.claude-worker`.
- Add AC-1..AC-6 as failing tests.
- Expected-red; the gate asserts the red count.
- Commit: `test(install): red-first — every hook must reach settings.json [plan:every-hook-is-registered#step-0]`

### Step 1 — Generalise the reconcile to a (hook, matcher) table

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_hooks_install.py -q -k 'table or matcher or both_hooks' --timeout=120 --timeout-method=signal
    timeout: 240
```

- Replace the hard-coded `hook_cmd` and `matcher` with a declared table
  (a bash array of `name:matcher` pairs is enough).
- Preserve the existing dedupe semantics: an entry is replaced by (matcher,
  command), not appended (AC-4), and foreign entries survive (AC-5).
- Commit: `feat(install): register each hook under its own matcher [plan:every-hook-is-registered#step-1]`

### Step 2 — Register the Read guard on the worker host only

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_hooks_install.py -q -k 'read_guard or worker_only or dry_run' --timeout=120 --timeout-method=signal
    timeout: 240
```

- Add the Read guard row, scoped so it lands on the worker home and not the
  interactive one (AC-2, AC-3).
- Encode the scoping as data next to the table, not as an `if` buried in the
  reconcile — the next hook will need the same choice, and a one-off branch
  makes the third hook rewrite it again.
- Verify `--dry-run` still writes nothing (AC-6).
- Commit: `feat(install): install the read guard on the worker host [plan:every-hook-is-registered#step-2]`

### Step 3 — Verification and ship

```yaml
local_checks:
  - command: python3 -m pytest skills/ilk-loop/tests/test_hooks_install.py -q --timeout=120 --timeout-method=signal
    timeout: 300
```

- AC-1..AC-7 green.
- Then verify on the **real** hosts, because the tests use a fake HOME and a
  fake HOME cannot prove the real one is right:
  - `bash install.sh --dry-run --only-claude --claude-home "$HOME/.claude-worker"`
    shows the Read entry as a create.
  - `bash install.sh --dry-run --only-claude --claude-home "$HOME/.claude"`
    shows **no** Read entry.
  - Record both outputs in "Findings". Do not run `--apply` here: the guard
    takes effect immediately and would change the behaviour of the very loop
    executing this step. Installing for real is an operator action after the
    batch ships.
- Commit: `chore(plans): every-hook-is-registered shipped [plan:every-hook-is-registered#ship]`

## Findings

- Worker dry-run: `would update: ~/.claude-worker/settings.json` — Read guard would be registered.
- Interactive dry-run: `skip: ~/.claude/settings.json already up to date` — no Read entry added.
- AC-1..AC-7 all green (21 tests pass).
- Steps 1 and 2 shipped in a single commit (step 1 implemented the table + host scoping together).

## Reference reading

- `install.sh:380-400` (hook discovery), `:705-770` (settings reconcile).
- `skills/ilk-loop/tests/test_hooks_install.py` — existing coverage to
  preserve.
- `skills/ilk-ship/SKILL.md` → "install.sh discovery" — the section this
  sub-plan corrects for hooks.
