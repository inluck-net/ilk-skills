# Retro 2026-09-21 — the gate could not pass from the only place the loop was allowed to run it

The `worker-model-switch` batch parked **four times in four hours** on
2026-09-21. Each park looked like a different problem — a stale lock, a dirty
clone, a stale gate fact, a red test file — and three of them were. The fourth
is structural and blocks **every** self-modifying batch: a selfmod worktree
lives *inside* the ilk data root, so any `pytest` run from it trips the
conftest data-root leak guard, which fails the session no matter how many
tests pass.

Sibling retro: [`retro-2026-09-21-switching-the-worker-model.md`](retro-2026-09-21-switching-the-worker-model.md)
— the manual switch that created this batch in the first place.

## Timeline

| Time | Event | Proximate cause | Real cause |
|---|---|---|---|
| 14:26 | GLM returns its weekly cap (`[1310] …重置 2026-09-22 15:16:35`) | quota | — |
| 14:31 | operator switches all worker homes to mimo-v2.5-pro | — | — |
| 15:58 | **park 1** `ship_integrity_violation` | `suite-greens-last-reds` shipped with 3 of 4 steps committed; step 2 was skipped in prose | step-commit enforcement counts commits, a Findings paragraph is not a skip |
| 16:59 | **park 2** `selfmod_merge_failed` | clone held uncommitted copies of 4 files the merge had to overwrite | earlier in-clone runs of the same batch left drafts behind |
| 17:24 | **park 3** `ship_integrity_violation` | `tray-names-what-it-names` gate red | **the data-root guard** (below) |
| 17:35 | **quarantine 4** | same sub-plan, same gate, two more strikes | same |
| 17:48 | batch reaches `shipped` on all 6 sub-plans | — | proof record still points at an unmerged SHA |

Interleaved, and worth separating out: a *fifth* stall that was not a park —
`registry-honest-after-switch` was quarantined three times because its step-2
gate asserted `show | grep -q 'glm-5.3'`. That gate encoded a fact about the
live environment that the operator changed at 14:31. See "stale gate facts".

## Root cause: the guard and the worktree disagree about what a worktree is

A self-modifying batch runs in a git worktree created at
`~/.ilk-data/projects/<key>/runtime/launcher/worktrees/selfmod-batch` —
**inside the data root**. `conftest.py`'s data-root leak guard snapshots
`~/.ilk-data/projects` before and after a test session and fails the session
when a real project key gains files (`conftest.py:691-726`, `session.exitstatus
= 1` at `:724`). Its intent is right: a test that writes into a real project's
runtime can have its own debris read back as proof.

But `pytest` writes `.pytest_cache` into its working directory, and when the
working directory *is* inside the data root, the guard counts the runner's own
cache as a leak.

Measured three ways at the same HEAD (`3019617`), same four test files, same
command:

| cwd | tests | exit |
|---|---|---|
| the clone | 69 passed | **0** |
| `/tmp/ilk-traycheck` (detached worktree) | 69 passed | **0** |
| a worktree under `~/.ilk-data/projects/<key>/runtime/…` | 69 passed | **1** — `DATA-ROOT GUARD VIOLATION … GAINED FILES files=3381 (was 3377)` |

The only variable is the working directory. `.pytest_cache` was still sitting
in the selfmod worktree when this was written.

**The consequence is a closed loop.** The gate exits 1 → `local_checks FAIL` →
two strikes quarantine the sub-plan → shipping it trips ship-integrity → the
master parks. The loop cannot fix this from inside the loop, because the fix
would itself have to pass a gate run from the worktree.

This is almost certainly also the `gate record unreadable (no results field,
all_passed=false)` that reverted `suite-greens-last-reds` at 15:58: a session
that dies on `exitstatus = 1` after its tests pass produces exactly that
shape — a red verdict with no failing check to name.

## Contributing defects, each verified in-session

1. **A prose skip is not a skip.** `suite-greens-last-reds` concluded step 2
   was unnecessary and wrote that in Findings. Enforcement counts commits, so
   it shipped at 3 of 4 and was reverted. Either the step ends in a commit
   (even a docs-only one, which is what fixed it) or the plan should not
   author it.
2. **The park reason names innocents.** `run_ilk_loop_claude.sh:3538-3543`
   builds `slugs=[…]` from *every* slug in the gate-results file, not from the
   violating ones. Park 1 named three sub-plans; exactly one had violated.
3. **Stale gate facts.** `registry-honest-after-switch` step 2 gated on
   `show | grep -q 'glm-5.3'`. A gate that asserts which provider is live is a
   gate that the operator can invalidate by doing their job. The rewrite that
   fixed it asserts the *property* instead — `grep -q 'model='` for
   non-vacuity, `grep -q '!! MISMATCH'` for the honesty claim.
4. **A case-insensitive needle matched its own tmpdir.** The same sub-plan's
   step-0 test asserted `'mismatch' not in show_output`, and pytest's tmpdir
   is named after the test — `test_show_no_**mismatch**_after_registry_sync`.
   It could never pass under that name. Assert on the emitted marker, not a
   lowercase substring.
5. **A dead lock blocks the merge silently.** `selfmod-merge.lock` held pid
   28304, long dead; the merge reported `MERGE FAILED: lock contention` and
   exited 5. The lock has no liveness check — the same pattern as
   `bounce_daemons.sh` matching by name alone.
6. **`merge exited 5` reports two different failures identically.** Park 2's
   log shows `MERGE FAILED: lock contention` *and* a plain
   `error: Your local changes … would be overwritten by merge`. One of those
   is a stale lock, the other a dirty clone; the exit code and the headline
   do not distinguish them.
7. **Drafts left in the clone.** Five files carried uncommitted work in the
   clone from earlier in-clone runs of this same batch. Three were byte-identical
   to what the worktree later committed, two were superseded drafts of it. A
   selfmod batch that ever runs un-isolated leaves this behind, and the next
   merge-back trips over it.

## Cost

Four parks, five relaunch cycles, roughly four hours of wall clock, and about
`$13` of worker spend across runs `20260921-142242` through `20260921-174916`.
Every one of the four operator interventions supplied evidence the gate should
have produced itself. The batch's own work was never wrong: at the end, all six
sub-plans were shipped and the only failing test file in the whole affair
(`test_ilk_worker_model.py`) failed on an assertion about its own directory
name.

## Fixes

1. **Move the selfmod worktree out of the data root.** A git checkout is not
   data. Putting it under `~/.ilk-data/projects/<key>/runtime/` is what makes
   every write there look like a leak. This is the architectural fix and it
   retires the closed loop.
2. **Teach the guard to ignore the runner's own artifacts** — `.pytest_cache`
   and `__pycache__` under a worktree path. Narrow: the guard still catches a
   test writing real project state, which is the thing it exists for.
3. **Give the merge lock a liveness check**, and split its failure message
   from the dirty-tree one.
4. **Name only the violators in the park reason.**

Fixes 1 and 2 are the ones that matter; without them the next selfmod batch
parks the same way on its first gated step.

## What would have caught this sooner

The three-context measurement — clone, detached worktree, worktree under the
data root — took about ninety seconds and isolated the variable exactly. It
was run after the fourth park. The tell was available at the first:
`gate record unreadable (no results field, all_passed=false)` says *the session
failed without a failing check*, which is not something a red test file can do.
A gate that fails with zero named failures is a gate that failed for a reason
other than the tests, and that deserves a direct measurement before any
relaunch.
