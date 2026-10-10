# Retro 2026-10-10: a verify that cannot fit inside its own gate

## What happened

Two batch verifies on chad-mbp ran for hours on 2026-10-10. One shipped only
after owner intervention; the other could never pass on its own.

- **ilk-skills 10b-a** (`missing-baseline-verify`), run 20261010-082322 and later:
  - Step 0, iter 5: 09:19-10:01, 2522 s. Record attempt 1: suite 465 s,
    29 failing, at load 2.4→9.3 with 2 other suites running.
  - Step 1, iter 6: 10:04-10:53, 2822 s. **2067 s (73%) was model thinking**,
    across 99 turns, for one 3-line fix (`c48ffc68`). That fix's "Fix B",
    labelled a choice, caused 2 new attributed reds: `_resolve_invocation()`
    returns `''`, so `measure` stored nothing (A/B on an identical repo:
    `{"stored": true}` before, `{"stored": false}` after).
  - The post-fix gate re-measured the whole tree (attempt 2, 787 s). It went
    red, the run stopped `local_checks_failed`, and triage parked and
    escalated it. Triage's finding was stale: it repeated iter 6's
    "9 timeout kills, none fixable" while the newer record showed 2
    attributed rows, both fixable.
  - The owner wrote the diagnosis into Findings. The next worker fixed it in
    about 10 min (`860d0631`), versus 49 min unaided.
  - Attempt 3 went red on 4 rows that were "3/3 red at head, passed at
    base". All 4 pass alone in about 7 s and under `-n auto` in 12-14 s,
    against a 17 s timeout: contention, not code. The sub-plan was
    auto-quarantined.
  - The owner re-measured on a quiet machine at 12:00, reading the suite from
    the ledger: `24 failure(s), none attributed`. The owner un-quarantined it
    and it shipped at 12:13 (`ad622743`).
- **gh-resolve 09c** (`held-and-named-batch-verify`):
  - At 08:48 the per-project watchdog relaunched the run **without
    `--run-local-checks`**: watchdog.sh:658 passes only
    `--project-path/--engine` and relies on launch.sh auto-detect, which
    printed no `Gates: ON` that time.
  - 7 of 7 iterations had `local_checks=null`, yet 2 sub-plans shipped and
    ship-proof gained rows for all 6 step transitions (gh-resolve-a1's audit).
    One worker wrote its own "reviewed" re-pin file for its own pin gate
    (1b341f37). Another made an AC-7 control vacuous (4ea39123).
  - The gates-off verify could not run step 0: the worker got
    `unmeasured refused in a worker session`. Owner stop at 11:53, scheduler
    redispatch with gates at 12:01.
  - The gate-first step 0 run was then **killed at its 1860 s gate cap**,
    still inside owner resolution. The 12:34 re-gate started that phase from
    zero. It can never pass, so the owner ran it uncapped and bumped step 0.

## Root causes

1. **Owner resolution is unbounded and linear** (`suite_ledger.owner_of`,
   suite_ledger.py:587-640). For each failing id that passed at base, it
   clones and checks out every first-parent commit in base..head, oldest
   first. At each one it runs the suite's full xdist invocation on that one
   id, for up to 120 s. There is no phase budget, and a flaky id walks the
   whole range. That is 21 ids × 19 commits on 09c, and 558 of 584 s on the
   10b-a re-measure. It is the phase behind the open "verify exceeded 15 min"
   detector rows: `at_base` 871 s, 1008 s and 1778 s. Row 7e5fcc21.
2. **Rerun arms measure under different conditions.** At-base strips xdist
   (verification_record.py:1119-1120); head reruns keep it (:1550-1551).
   Contention timeouts become "attributed". Row d0eaf52a.
3. **A fix inside a verify re-measures everything.** The record is keyed by
   tree, and a fix is a new tree. Rows 5e28d081 and 6118449a, the latter
   open since 2026-10-03; 03i shipped without its "targeted re-verify"
   piece.
4. **A relaunch can silently drop the gates**, and the driver then writes
   ship-proof for ungated steps. Rows 2207b94c and a96455e3.
5. **Signals with no consumer.**
   - The slow-verify detector filed 4 rows (10-07, 10-09, 10-10 ×2). All 4
     are open, and none was planned.
   - The 10-07 retro says its rule is "held" by
     `tests/invariants/test_a_batch_runs_one_suite.py`. That file has never
     existed: `git log --all` shows 0 add/delete commits.
   - Triage escalated on a stale finding instead of the newest record.

Cause 5 is why 1-4 kept recurring: each was measured, sometimes filed, and
then nothing turned the measurement into a batch.

## The rule

**A verify must fit inside its gate by construction.** Every phase has a
budget, the budgets sum below the gate cap, and a phase that hits its budget
yields a classified verdict, never a kill. A rule a retro calls "held" names
a file that exists.

## How it is held

| Cause | Fix (RSI batch) | Held by |
|---|---|---|
| 1 | binary search, ids without xdist, only reliably-red ids, phase budget | test: 21-id/19-commit fixture makes ≤ 21×5 runner calls and finishes the owner phase under budget |
| 2 | same rerun shape at base and head; serial-green/xdist-red = contention | test: a serial-green/xdist-red id is reported as contention, not attributed |
| 3 | stale-by-new-commits re-measures failing ids plus touched files | test: a fix touching a shared module still triggers the full pass |
| 4 | gates decision persisted in last-launch.json; driver refuses ship-proof for ungated steps | tests named in rows 2207b94c and a96455e3 |
| 5 | retro lint: every path a retro cites as "held by" must exist; slow-verify detector rows rank as smoothness work in the RSI queue | the lint test itself, plus a red control on this file before the fixes land |

## How to judge a future change to the verify path

Compute the worst case first: ids × commits × reruns × per-run cost, against
the gate cap. If the worst case does not fit, the change is not done, however
fast the median run is.
