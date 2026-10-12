---
title: Cut iteration wall-clock — the ilk-skills half
slug: cut-iteration-wall-clock
master_plan: 2026-08-26-execution
batch_date: 2026-08-26
source_status: measured — gh-resolve run 20260825-234253, analysed 2026-08-26
total_tickets: 5
created: 2026-08-26T11:05:00+08:00
status: shipped
priority: P0
pause_after_ship: false
supervised_only: false  # Mechanical §13 test, applied not assumed. The enumeration is loop_status.py / scheduler_scan.py / promote_next_master.py / plan_status.py / scheduler.*. This batch's scope_paths are hooks/, install.sh, gate_cost.py, plan_lint.py — NONE is on that list. The parent batch (2026-08-25c) made the same call for the same reason and deliberately kept the runner rather than promote_next_master.py in scope to preserve autonomous dispatch; this batch inherits that and must not undo it.
base_branch: main
branch: null
goal: Cut ilk-loop iteration wall-clock by removing redundant model round-trips and by making expensive test gates visible at plan time, using only levers measured on a real run.
out_of_scope:
  - Repeat-suppression for test commands. **Measured and rejected**: 41s of
    the 6889s run = 0.6%. The analysis session nearly built this before
    measuring it. Do not add it.
  - Further loop-harness optimisation. **Measured**: agent `[done]` time and
    loop `duration` differ by 6.3s across 6 iterations = 0.09%. There is
    nothing left there.
  - gh-resolve's own slow tests (826s of the run's 1394s pytest time, in
    tests/test_drain.py, tests/test_pr_closes_issue_guard.py,
    tests/test_pass_reports_failure.py). Another session owns that repo.
  - The iteration-prompt bookkeeping lever (was SP5). **Dropped 2026-08-26 by
    operator decision**: its ~198s is a proportional share, not a
    measurement, and it touches a §7h contract-governed file. SP3/SP4 make
    the real measurement cheap; re-plan it then, with a number.
  - loop_status.py. Touching it flips supervised_only and permanently
    removes autonomous dispatch. The runner already reads sub-plan files.
  - Fixing the known-red tests listed under "Inherited red" below.
cross_cutting_invariants: []
---

# MASTER plan: Cut iteration wall-clock — the ilk-skills half

## Goal

Remove the two costs that a measured loop run showed ilk-skills actually
controls: redundant `Read` round-trips, and test gates nobody knew were
expensive until after they ran.

## The measurement this batch is built on

gh-resolve run `20260825-234253` — 6 iterations, 6889s, 21 commits, 7
sub-plans shipped. Parsed from
`~/.ilk-data/projects/users-chad-projects-github-inluck-net-gh-resolve/logs/launcher/users-chad-projects-github-inluck-net-gh-resolve-20260825-234253.log`.

| bucket | seconds | % of run | owner |
|---|---|---|---|
| loop harness (git, status, sentinels) | 6.3 | 0.09% | ilk-skills — nothing left |
| pytest execution | 1394 | 20.2% | mostly gh-resolve |
| — 4 slowest calls | 826 | 12.0% | gh-resolve |
| — repeated commands | 41 | 0.6% | rejected, see out_of_scope |
| model time | 5594 | 79.7% | **ilk-skills, via prompt + guardrails** |
| — duplicate-read decide time | 773 | 11.2% | **SP1 + SP2** |

**Do not re-derive these numbers.** The analysis is done; re-deriving it
costs an iteration and produces the same answer.

The 773s is a **floor**, not a point estimate: it counts only the model's
decide-time before each of the 99 redundant reads (of 157 `Read` calls
total). It excludes what re-ingested file content costs on every later turn,
which the launcher log cannot separate. Do not restate it as "we will save
773s".

## Workstream map

```
  SP1  a-reread-is-refused                    [the guard itself]
        hooks/no-duplicate-read.sh — deny a Read of a path already read
        this session; allow again once the file's mtime changes

  SP2  every-hook-is-registered               [depends on SP1]
        install.sh reconcile is hard-coded to ONE hook and matcher "Bash";
        generalise it, then register the Read guard on the worker host

  SP3  a-slow-test-file-is-measured           [independent]
        gate_cost.py emits per-test-file wall-clock from the launcher
        logs it already writes

  SP4  an-expensive-gate-is-flagged-at-plan-time   [depends on SP3]
        plan_lint names a step whose local_checks runs a file measured
        over budget, so the planner picks a -k selection instead

  SP5  a-unit-test-cannot-touch-the-host        [appended — independent]
        a unit test must not bootout the production launchd scheduler;
        guard at the Popen boundary, deny launchctl, allow git
```

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [2026-08-26-a-reread-is-refused.md](./2026-08-26-a-reread-is-refused.md) | payload shape proven; session-keyed ledger; mtime re-read allowance; deny message names the path | 5 | shipped |
| 2 | 2 | [2026-08-26-every-hook-is-registered.md](./2026-08-26-every-hook-is-registered.md) | reconcile generalised to a (hook, matcher) table; Read guard registered on the worker host | 4 | shipped |
| 3 | 3 | [2026-08-26-a-slow-test-file-is-measured.md](./2026-08-26-a-slow-test-file-is-measured.md) | per-test-file wall-clock from launcher logs; stable output contract | 4 | shipped |
| 4 | 4 | [2026-08-26-an-expensive-gate-is-flagged-at-plan-time.md](./2026-08-26-an-expensive-gate-is-flagged-at-plan-time.md) | budget finding in plan_lint; names the file and its measured cost | 4 | shipped |
| 5 | 5 | [2026-08-26-a-unit-test-cannot-touch-the-host.md](./2026-08-26-a-unit-test-cannot-touch-the-host.md) | Popen-boundary guard; launchctl denied, git allowed; marker exemption; report-only sweep; denominator pinned | 5 | shipped |

## Execution rationale

**SP1 first.** It produces the artifact everything else in that chain
registers and tests. Its step 0 is not a diagnostic — it produces a
committed characterisation test that pins the hook payload's shape.

**SP2 second, and it is not a formality.** `install.sh:719` hard-codes
`no-full-suite.sh`, and `:737` hard-codes `matcher: "Bash"`. A new hook file
is symlinked by the `*.sh` glob but **never registered**, and a Read guard
needs `matcher: "Read"`. Without SP2, SP1 ships an inert file. This
contradicts ilk-ship/SKILL.md's "there is no registration list to edit",
which is true for skills and commands and false for hooks.

**SP3 before SP4** — SP4's finding needs a measured number to compare
against. Reversing them means a lint with no data.

**SP3/SP4 are independent of SP1/SP2** and can run in either order relative
to them.

**SP5 appended 2026-08-26**, after the batch was queued and before the batch
gate started — legitimate under the append-freeze, which keys on the *gate*
running, not the batch. Independent of SP1-SP4; ordered last so it disturbs
nothing in flight. It comes from
`gh-resolve/docs/testing-principles.md`, a portable document written to be
adopted by other projects. Its headline defect (190 tests shelling to the
real GitHub CLI) is **not** ours — 0 of 179 test files here invoke
gh/claude/curl/ssh — but its §1 point is: our
`test_install_scheduler_autostart.py` drives the live production launchd
label and unloads the running scheduler. Same class, different binary.

## Cross-workstream dependencies

| Dependency | Consumer | Nature |
|---|---|---|
| `hooks/no-duplicate-read.sh` exists | SP2 | artifact — the reconcile needs something to register |
| per-test-file timings (SP3) | SP4 | data — the budget lint has nothing to compare without it |

## Rollout strategy

All four sub-plans are `loop-verified`: every AC is checkable with pytest or
by feeding JSON to a hook on stdin. The worker MCP surface is **empty**
(`worker_mcp.py list` → `mcpServers: []`), so no AC may depend on an MCP.

**Staged install for the Read guard — operator decision 2026-08-26.** SP2
registers the guard on `~/.claude-worker` ONLY. It is deliberately not
installed to `~/.claude` in this batch. A wrongly-denied read fails
*silently* — the agent proceeds down a wrong path — where a denied `pytest`
is loud and self-correcting. Prove it on a real loop run first; adding the
interactive host is a separate, later decision.

## Repos in scope

Single-repo project (`project_kind: single`) — no `Repo` column applies.

## Inherited red — NOT this batch's to fix

Do not "fix" these; they predate the batch and belong to other work.

- 6 tests in `skills/ilk-loop/tests/test_ship_audit_reads_record.py`
- 2 tests in `skills/ilk-loop/tests/test_plan_lint_shared_module_gate.py`
- inherited failures in ilk-lark-tickets, ilk-watchdog, and 7 collection
  errors in `skills/ilk-loop/scripts/test_meta_paths.py`

## Gate policy for this batch

Every step gate is **change-scoped** by design. The batch-end gate runs the
whole suite once (`ship.suite.timeout: 900`; the suite measures 327.42s), so
no step may declare a broad-suite gate.

### Known plan_lint false positive on this batch — do NOT "fix" it

`plan_lint` reports **balance-drift** on
`2026-08-26-a-reread-is-refused`. It is a false positive, and the cause is
known: `plan_lint.py:1205` includes `\bpath\b` in `_MECHANIC_NOUN_RE`, so
any sub-plan whose prose contains both "change" and "path" fires. A read
guard changes no coefficient, threshold, or rate.

**Do not reword the sub-plan to dodge the keyword** — that trains the next
reader to game the linter instead of reading the finding. Leave it, and
leave the finding in the report.

(Fixing `_MECHANIC_NOUN_RE` is a real but separate defect. It is deliberately
not in this batch's scope; file it rather than folding it into SP4.)

Expect `plan_lint`'s **shared-module gate** finding on steps touching
`plan_lint.py` and `gate_cost.py` — both are imported elsewhere. That
finding is correct and is answered by the batch-end gate. **Do not weaken a
correct change-scoped gate to silence it**, and do not widen a step gate to
the whole suite.

## Progress log

| Date | Event |
|---|---|
| 2026-08-26 | Batch authored from the run-20260825-234253 analysis. SP5 (iteration-prompt bookkeeping) dropped before authoring — proportional estimate, not a measurement. |
