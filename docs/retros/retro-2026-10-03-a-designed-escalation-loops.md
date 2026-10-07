# Retro 2026-10-03: a designed escalation loops

**Batch:** I2 = MASTER-2026-10-02c (the-loop-remainders-are-finished), run
20261003-020915.
**Written by:** ilk-skills owner session (took over from ilk-skills-e5 at
~04:10).
**Outcome:** the verify sub-plan cycled about 35 minutes a round on a stop the
code itself calls a human escalation. A person parked it at 04:16. The 9
attributed reds were fixed by hand in df8412d.

## What happened

1. **The verify measured 70 failed of 4509 at e08e3592** (record
   `logs/verification/batch-2026-10-02c-…-batch.md`, 03:28-03:46; again
   at 04:07).
2. **70 is over `AT_BASE_CAP = 50`** (`verification_record.py:653`, check at
   `:702`). The record says `at_base_cap_exceeded`, which the comment at
   `:1313` calls a "designed human-escalation".
3. **The runner treated it as an ordinary red gate.**
   - Gate-first fell through to a worker.
   - The step is driver-owned (`--run-suite` refuses in
     `ILK_WORKER_SESSION`), so the worker could do nothing. It idled for
     1236 s with 0 commits (launcher log, iteration 7, ended 03:49).
   - The driver then re-ran the ~16-min suite. Repeat.

## Attribution: where the 70 came from

Isolated full suites (archive copies, HOME + ILK_DATA_HOME pinned):

| tree | failed | passed |
|---|---|---|
| HEAD e08e3592 | 22 | 4443 |
| base 565d0846 | 13 | 4409 |

- **9 HEAD-only, 0 base-only, 13 in both.** The 9 fell into four families,
  all fixed in df8412d and verified by a targeted run (351 passed; the
  1 red, `test_all_code_labels_documented`, is base-red):
  - `plan-amended` was a new driver stop with no collect.py mapping (2
    tests);
  - the amendment watcher's `loop_status` call was not anchored to a cwd
    (1 test);
  - 3 fixtures said step 0 was done but had no step-0 commit. Passing
    `--repo` (176b6e3) made ship-integrity actually check it;
  - the AC7 test still expected exit 0 after 176b6e3 / 2424370 made an
    unresolvable root exit 3 (unmeasured, non-blocking).
  - The 2 amendment-test failures were 17 s timeouts under load from two
    parallel suites. They pass at 120 s, so they are not code reds.
- **~48 appear only in the verify's own run on the real host** (70 − 22).
  - The verify's pytest ran with the real `HOME=/Users/chad` and no
    `ILK_DATA_HOME` (read from pid 74253's environment at 04:14).
  - A leaked fixture runner, pid 28477, had been running since 01:55. It was
    a `run_ilk_loop_claude.sh` against a deleted tmp project, looping
    `sleep 1`, and it spanned both 70-red runs. Killed at 04:15.
  - Killing the verify's pytest at 04:17 orphaned another one (pid 76756).
    **Fixture runners outlive a killed suite.**
  - Two stray data dirs, `private-tmp-claude-501-…-ilk-skills-{1dbc49b,cfc51c3}`,
    each held only `runtime/launcher/ship-reverts.jsonl` (slug `test-slug`).
    That is a ship-integrity test writing to the real ILK_DATA_HOME, even
    from a run with HOME + ILK_DATA_HOME pinned (04:06). Removed by name.
  - The host re-measurement at df8412d is in progress. See "Measured after".

## Defects (seed material for R3 / RSI)

1. **The runner loops on a designed escalation.** `at_base_cap_exceeded`
   should park the master with a reason and notify. It should never fall
   through to a worker or re-run the suite unchanged. Same class as
   "relaunch fixes state, not step design": the second identical failure
   at the same step is a design stop.
2. **The cap message misreports its count.** `:1315` prints
   `c['failed'] + c['errors']` as "uncovered". The cap at `:702` compares
   the ids left after removing base `baseline_red`. The record should
   print the number the cap actually tested.
3. **The cap branch keeps no evidence.** No `.suite-output.txt` was written
   for the 02c batch (the `logs/verification` listing at 04:15 has none). The
   one run that needs a human leaves that human nothing to attribute with.
   Write the suite output before raising.
4. **A worker is dispatched onto a driver-owned step.** The worker idles
   about 20 min. If the step is gate-first, driver-owned and red, the runner
   should not spend an agent iteration on it.
5. **The verify suite is not isolated from the host.** It runs on the real
   HOME and data home, so live loops, leaked fixture runners and real data
   all show up as reds. The stored v0.9.137 baseline was measured isolated,
   so the comparison is apples to oranges.
6. **Fixture runners leak.**
   - A killed suite leaves `run_ilk_loop_claude.sh` fixture processes
     reparented to launchd, looping.
   - They match every `pgrep run_ilk_loop` liveness check, including the
     selfmod merge guard (memory merge-guard-matches-any-command-line).
   - Fixtures should run runners in their own process group and kill the
     group in teardown.
7. **A ship-integrity test writes to the real ILK_DATA_HOME** (the
   `ship-reverts.jsonl` debris above), even with ILK_DATA_HOME pinned.
8. **`plan-amended` is overwritten before it is recorded.**
   - `run_ilk_loop_claude.sh:5684` sets `iter_stop_reason="plan-amended"`.
   - In the same `main()` (from `:4814`), `:5784` re-declares it and `:5785`
     replaces it with `_decide_iter_stop_reason`, which is never told about
     the flag.
   - The collect/watchdog mapping added in df8412d is therefore correct but
     currently unreachable.
   - WIP is still preserved, because `ITER_COMPLETED=0` survives.

9. **AC4/AC6 amendment tests take ~61 s.** The iteration ends at
   `--iteration-timeout-min 1`, not at the watcher's kill (AC1, same env,
   ends in ~6 s). The WIP assertion may pass via the timeout path, which
   would make the pin vacuous. Under the 17 s cap they were killed, and their
   fixture runner was orphaned: this is the source of 28477 and 76756.
   73b8619 gives them `@pytest.mark.timeout(90)` (a labelled judgment call).
10. **Driver debug output left in.** `run_ilk_loop_claude.sh:4103-4107`
    echoes `[DBG] kill -0 OK` and similar to stderr.

## Measured after

- **The "host-only" reds were not host noise. They were an I2 regression.**
  - Host full suite at df8412d: 55 failed, 4416 passed (17m12s). 45 of the
    55 do not fail in isolation; 52 failure messages say
    `/Library/Developer/CommandLineTools/usr/bin/python3: No module named
    pytest`.
  - Cause: `test_a_run_is_classified_by_its_own_stop_reason.py` (3ef45be, an
    I2 sub-plan) sets `os.environ["HOME"]` and never restores it, so every
    later subprocess lost the user-site pytest.
  - e5's isolated runs exported `PYTHONUSERBASE`, which **masked** it. An
    isolated harness that pins more than the verify does can hide a
    pollution regression. Seed: the attribution recipe should pin exactly
    what the verify pins.
  - Fixed in 73b8619. Positive control: that file followed by
    `test_a_red_names_its_owner`, in order, gave 3 failed before and 9 passed
    after.
- **So defect 5 (verify not isolated) is weaker than first written.** The
  real-host run is still exposed to live loops and leaks, but this round's
  48 were the batch's own.
- **df8412d** cleared all 9 isolated HEAD-only reds on the host too (10 of the
  22 isolated reds pass on the host). It introduced 1 new red
  (`test_classification_mapping[plan-amended]`: the raw state needed a
  watchdog arm), which 73b8619 fixes.
- I2 unparked about 05:00 for the driver's own verify at 73b8619.
