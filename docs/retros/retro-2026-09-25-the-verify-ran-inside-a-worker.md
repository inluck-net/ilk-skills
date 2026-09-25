# Retro 2026-09-25: the verify ran inside a worker

Two batches reached their verification sub-plan on chad-mbp on the night of
Sep 24-25, and both were slow. Standing goal:
`docs/future-work/batch-verification-smooth-and-fast.md`, with a baseline of
37.0 min per verify on chad-mbp. Timestamps are local (+08). Logs are under
`~/.ilk-data/projects/<key>/logs/launcher/`.

- **ilk-skills** `MASTER-2026-09-24e-unattended-runs-end-in-a-result`,
  verify `unattended-result-verify`. Runs `20260925-004035` and
  `20260925-005553`.
- **gh-resolve** `MASTER-2026-09-24f`, verify
  `unverified-publication-batch-verify`. Run `20260924-221409`, iteration 6.

## What went well

- **Gate-first worked where it was reached.** ilk-skills step 0 ran the suite
  with no worker and committed `1d8507b chore(loop): gate-first marker
  [plan:unattended-result-verify#step-0]` at 00:52, about 12 min after the run
  began at 00:40. The at-base attribution then did its job: step 1's gate was
  red on 3 regressions the batch had actually caused.

## Findings

### F1. The worker measured an uncommitted tree, so one suite was wasted (ilk-skills)

Step 1's gate was red (00:56). The worker fixed the 3 attributed failures,
then ran step 0's `verification_record.py --run-suite` itself, from 00:59:58
to 01:15:51 (16 min), and only afterwards committed the fix: `e530124` at
01:16. The record was therefore for the previous HEAD, so it read stale, and
the worker started a third suite at 01:16:18.

- **Cause 1:** the sub-plan text tells the worker to do it: step 1 says "fix …
  then rerun step 0's command". That puts the suite inside a worker session:
  harness-backgrounded, babysat, and priced in model turns.
- **Cause 2:** `verification_record.py` measures a dirty tree without saying
  so. The record names HEAD, but the tests ran against HEAD plus uncommitted
  edits.

### F2. One worker session ran four work sub-plans and then the verify (gh-resolve)

Iteration 6 (session `b6b4de9f`, from 00:00:20) shipped:

- `a-publish-refusal-is-bounded` (00:30)
- `reap-reads-the-run-result` (00:39)
- `a-human-verb-records-an-intervention` (00:51)
- `a-configured-ship-suite-is-probed` (00:55)

It then ran the batch suite itself (00:55 → 01:13, 18 min; the 600s harness
timeout backgrounded it and the worker waited on it with
`wait_for_background_output.sh`). After that it ran `verify_attribution.py`
(backgrounded twice at the 120s harness timeout, 01:18 and 01:22). At 01:26
the iteration was still going, with a 90 min iteration budget.

- **Cause:** the worker shipped by **editing the sub-plan frontmatter
  directly** (e.g. `Edit(…/plans/2026-09-24f-a-publish-refusal-is-bounded.md)`
  at 00:27:06) plus an empty `chore(plans): … shipped` commit. It never
  called `ship_transition.py`. v0.9.128's "one iteration ships one sub-plan"
  refusal lives only in `ship_transition.py --ship`, so a hand edit bypasses
  it. The batch-24d case study (one session ran #1-#3) has recurred.
- **Consequence:** the verify never reached gate-first. It ran at worker
  speed, and it risks the iteration timeout. A timeout here costs a
  relaunch.

### F3. Two full suites ran at once on one host (inference, not measured)

ilk-skills' suite (00:59:58-01:15:51) and gh-resolve's (00:55-01:13) overlapped
almost exactly. gh-resolve's suite is ~4 min at `-n 8` per the standing-goal
baseline, but took 18 min here. CPU contention is the likely cause. **Not
verified:** I did not compare load or the per-test durations of that run.
Measure it before acting.

### F4. The batch could not land: 7 of 7 runs ended `selfmod_merge_failed` (ilk-skills)

Every 24e run from `20260924-205959` to `20260925-004035` ended
`merge exited 5`: "Your local changes to the following files would be
overwritten by merge: detached-component-contracts.md,
run_ilk_loop_claude.ps1". At 22:58:36-22:59:34 (run `205959`, log lines
2520-2543), sub-plan 2's worker had edited those two files through
`/Users/chad/.claude/skills/ilk-loop/…`. That path is the installed skill
symlink, which points into the LIVE clone. The worktree guard (it did block
some direct reads of the worktree path) does not resolve symlinks. Each failed
merge ended the run after one iteration, and the scheduler relaunched it, so
a 5-sub-plan batch spread over 7 runs and 4.5 h.

## Improvements (candidates, most leverage first)

| # | Change | Goal half | Evidence |
|---|---|---|---|
| 1 | The driver enforces one ship per iteration from the plan diff: any sub-plan whose status became `shipped` this iteration and is not `ILK_ITERATION_SUBPLAN` is reverted to its prior status and logged. `ship_transition.py`'s refusal stays, but is no longer the only guard | smooth | F2 |
| 2 | A worker never runs the suite. The verify template's step 1 becomes "fix, commit, end your turn". The driver sees a new commit after step 0's record, resets the verify to step 0, and gate-first re-measures with no worker | smooth, fast | F1, F2 |
| 3 | `verification_record.py --run-suite` refuses a dirty tree (`ILK-CHECK: unmeasured dirty tree`), so a record always names what it measured | unexcusable | F1 |
| 4 | The live-clone guard resolves symlinks (`~/.claude/skills/*` → clone) before comparing paths. The merge-back reports "live clone dirty in files X" as its own exit state, not the generic 5 | smooth | F4 |
| 5 | At most one broad gate (full suite) per host at a time: a host-level suite lock the scheduler or `verification_record` takes | fast | F3 (measure first) |

Items 1-4 touch the same driver region as batch 24e. Plan them after 24e
lands.
