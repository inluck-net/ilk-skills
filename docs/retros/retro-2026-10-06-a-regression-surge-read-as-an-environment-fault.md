# Retro 2026-10-06: a regression surge read as an environment fault

gh-resolve on chad-mbp, batch `MASTER-2026-10-06a` (D-498 "someone else's PR
wins"), run `20261006-213748`, verify sub-plan
`someone-elses-pr-wins-batch-verify`. Evidence comes from the run's
`iter-02.log`, its launcher log, `runtime/launcher/gate-history.jsonl`, the
ledger output `logs/verification/ledger/7598de20….output.txt`, and per-commit
reruns done in `git archive` copies (not `git bisect`). The investigation was
by ilk-skills-dc, at Chad's request ("stuck in verification subplan for more
than an hour"). gh-resolve-d9 owns the project and kept its hands off
throughout.

**Headline.** The batch broke about 30 tests that no step gate ran. The verify
step found 34 failures. Because that was more than 20, the recorder refused
before measuring anything and called it an "environment fault, not a batch
issue". A single worker iteration then redid the attribution by hand: 3 full
suites (7663 tests), 69 min, and no commit until minute 73. It ended the
iteration by deleting the verification record, with a commit trailer that
named the plan's file instead of its slug. The driver ran gates for that
unknown slug, got exit 2, and stopped the run, so the real gate never
re-measured the fixed tree.

## What happened (CST)

| time | event | evidence |
|---|---|---|
| 19:24-21:06 | Five sub-plans ship, each behind change-scoped step gates. | git log 83c1ac55..52c617ee |
| 21:36 | d9's owner fix 0f2effc8 routes the D-498 holder check through the injected runner. It was verified against one sub-plan's 15-file gate (688 passed), not the suite. | d9 |
| 21:43-21:52 | Verify step 0 (gate_first) runs the FULL suite, because `tests/_registry_guard.py` changed and test infrastructure forces full scope. 7627 passed, **34 failed**. | record `suite_scope_reason` |
| 21:52:17 | The recorder writes `at_base_cap_exceeded: 34 non-declared failures exceeds the 20 surge cap; environment fault, not a batch issue` and exits 1, with no at-base rerun. | gate-history; `verification_record.py` (pre-02d84506) surge block |
| 21:52-23:05 | The worker runs the at-base check itself ("5 failed (pre-existing), 30 passed: attributed") and fixes the test fakes. It runs 3 full suites and about 8 targeted runs, and commits nothing. Phase reads `agent` the whole time. | iter-02.log :182, :78-:1472 |
| 23:05:32 | Commit `5361b432` "fix 30 attributed regressions", trailer `[plan:2026-10-06a-someone-elses-pr-wins-batch-verify#step-0]`, which is the FILE name. | git log |
| 23:07:34 | The worker `rm`s the verification record. | iter-02.log :2527 |
| 23:08:25 / :29 | The driver: `[trailer-slug] unknown slug '2026-10-06a-…'`, then `[local_checks ERR] … step 0 -> error` twice (exit 2, empty command), then `Loop stopped: local_checks not passing`. Ship-proof: `0 of 1 commits carry its trailer`. | launcher log :2560-2580 |
| 23:1x | d9 parks the batch at dc's request, amends the trailer (`5361b432` → `84805b15`, message-only), and unparks it. | d9 |

## The failures were the batch's own (measured)

The same 34 node ids were run at each batch commit, each in its own archive
copy:

| commit | failing (of 34) |
|---|---:|
| 83c1ac55 base | 5 |
| e365a0da (D-500 admission) | 3 |
| e4b4c0d6 (holder predicate) | 3 |
| 370318ee (drain yields) | 11 |
| 0e257258 (in-flight yield) | 23 |
| 52c617ee | 23 |
| 0f2effc8 (holder check via runner) | 33 |
| 5361b432 (worker's fake updates) | 0 |

Each run takes 50-66 s for 34 tests, and the 3-5 failures at the start are
flaky in both directions, which fits network-touching tests. The leftover
`test_plan_lint_findings_identical` (tests/test_reap_reads_the_run_result.py)
fails at base and at HEAD, so it is pre-existing. The worker changed 6
expectations from `dropped:pr-already-open` to `yielded:pr-held`. d9
confirmed against ADR 0104 D1-D3 that this is the intended verdict: in those
fixtures the PR is on `fix/<n>`, not this fleet's `resolver-<N>`.

## Root causes

1. **Step gates never ran the tests the batch broke.** Every sub-plan gated
   on its own files. The new holder check reaches drain, reap and claim paths
   whose tests use fakes that do not model the timeline. No gate ran them
   until batch verification ran the full suite.
2. **The surge stop presumed an environment fault instead of measuring.**
   `FAILURE_SURGE_THRESHOLD = 20` (e746f855, earlier the same day) was a
   labelled judgment call. Its own falsifier: *"Wrong if a genuine batch
   regression of more than 20 non-declared failures is refused as an
   environment fault"*. That fired. `run_at_base` already batches every id
   into one pytest process, so measuring would have cost one rerun (66 s
   here). Refusing cost one hand-attribution iteration (69 min).
3. **A refused gate hands attribution to a worker.** With no at-base table,
   the worker had to recompute what the recorder had declined to measure, and
   it did so with full suites.
4. **An unknown trailer slug becomes a gate `error`, not a refusal that names
   the fix.** The driver prints the nearest real slug
   (`run_ilk_loop_claude.sh:3068-3072`), but then still runs gates keyed by
   the unknown slug, which have an empty command and exit 2, and stops the
   run. Owner note: I did not trace the line that turns the unknown slug into
   the `error` row; this is taken from the log, not from code.
5. **The worker deleted its own verification record.** Here it read as an
   attempt to force a re-measure, not a forgery (compare
   `worker-forged-verification-record`). But the record is the proof, and a
   worker should never remove it.

## Fixes

| commit / row | what |
|---|---|
| **02d84506** (ilk-skills main, unreleased) | A surge goes through the batched at-base rerun. It is an environment fault only when at least half its non-declared ids also fail at base (a labelled judgment call), and the record carries `N of M also fail at base`. Ids over `AT_BASE_CAP` (50) still stop. Red-first: 2 new tests failed before the change. 296 passed across the 34 test files that use the recorder. |
| 84805b15 (gh-resolve, d9) | The trailer is amended to the slug. |
| backlog (filed with this retro) | An unknown trailer slug should refuse and name the nearest slug, not run empty gates. Workers must not delete verification records. |
| gh-resolve (d9's call) | Widen step gates for shared-predicate changes to the callers' tests. That is the plan_lint shared-module finding, applied to the holder check's call sites. |

## Rules

- A count is not a classification. Before calling a set of failures
  environmental, run them at base; when the runner batches, that costs one
  process.
- A phase that reads `agent` for an hour with no commit is a stall in
  progress, even when the log shows activity. Look at what the gate handed
  the worker.

## Addendum: the re-dispatch was refused by a lock nobody held

| time | event | evidence |
|---|---|---|
| 23:13:07 | The scheduler re-dispatches the unparked batch. | scheduler.log |
| 23:13:18 | The new runner exits at once: `ilk_run_lock: another runner holds this lock (pid=69752)`. 69752 had exited at 23:09:54. | launcher log `…-20261006-231318.log` |
| 23:14 | `lsof run.lock`: the only holder is `suite_ledger.py measure` (pid 48934, ppid 1), on **fd 3u**. That is the background measurement the runner spawned after the 23:08 commit. | lsof |
| 23:15 | The owner kills its process group; the lock has 0 holders and no ledger processes are left. | lsof / ps |

**Mechanism.** The runner re-execs under `ilk_run_lock.py`, which holds an
exclusive flock on `run.lock` (`run_ilk_loop_claude.sh:5060-5085`).
`suite_ledger._spawn_detached` double-forks and `setsid`s; since 1b6e5c4e
(earlier the same day) it redirects fds 0-2, but every other fd stayed
inherited. The measurement therefore kept the flock for its whole full-suite
run, and every dispatch in that window refused. The same shape probably
explains ilk-skills' instant `another runner holds this lock` exit at 19:54
(not re-measured).

**Fix: 71ea2b8b** (ilk-skills main, unreleased). The grandchild runs
`os.closerange(3, SC_OPEN_MAX)` before exec. The red-first test holds a
flocked, inheritable fd across `spawn()` and then takes the lock from a second
process while the measurement still runs. The class guard now flags any
`setsid` child that does not close fds >= 3; it flags exactly the pre-fix
`_spawn_detached` and nothing else in the repo.

**Rule.** Detaching a child means closing everything it does not need, not
just stdio. A lock is an fd.
