# Retro 2026-10-07 — one suite per batch, and nothing waits for one

## What kept happening

Nine retros since 2026-09-16 describe slow or stuck batch verifies. Each fixed
the mechanism in front of it; the same three shapes kept returning:

- **Repeated full suites** (09-16, 09-24, 09-25, 10-03, 10-06Z). "One suite
  invocation" was stated on 09-16 and bypassed four times.
- **A wait on a background suite** (10-06 ledger pipe, 10-07 dead drainer, and
  three more on 2026-10-07 alone: 12:15-12:21, 15:23-15:32 on a stale tree,
  16:43-16:49 on a run that then timed out unmeasured and was re-run).
- **Timeouts as the release valve** (09-25, 10-03, 10-07): raise a bound, wait
  longer, re-run on expiry.

On 2026-10-07 the toolkit started 17 background full suites (one per committing
iteration, `ledger_spawn_for_head`), and 10 of the last 12 ended `unmeasured
suite exceeded 600s timeout`. Four verifies needed a human that day.

## The rule (Chad, 2026-10-07)

"I don't want any unnecessary full suite run, especially the repeated ones.
By extending any waiting timeout which then call a more longer wait until
timeout is not acceptable." … "harden the idea … rather than keep fixing this."

**A batch runs exactly one full suite: its verify, at normal priority, measured
once and reused on relaunch. Nothing in the loop starts another, and nothing
waits for one.**

## How it is held, not just stated

1. Producers removed (MASTER-2026-10-07k #3): no per-commit background suite;
   the verify never waits on the ledger.
2. Owner invariant `planned:tests/invariants/test_a_batch_runs_one_suite.py` (safety
   kernel `rules` tier — no loop build may change it). Not built as of
   2026-10-10 (rules tier; owner build). Row 47f8dad8. I8a: only
   `verification_record.py` / `suite_ledger.py` may start a suite. I8b: no
   `wait_for(` in the verify. I8c: an in-process measure is written to the
   ledger. I8d: worker/manager sessions carry the no-full-suite hook. Red
   control on 2026-10-07 HEAD ae57b774: I8a fails on run_ilk_loop_claude.sh
   :4961/:4972/:5991, I8b fails; I8c, I8d pass.
3. Detection (07k #4): every record carries `phase_seconds`; a verify over
   900 s files a supervisor backlog row naming the phase that grew.

## How to judge a future change to the verify path

Count full-suite runs per batch first; the answer must be one. If a proposal
waits, find what it waits for and remove that instead. A timeout raised to make
a wait pass is a regression, not a fix.
