# Retro 2026-10-10: a gate that could not pass, read as a timeout

Requested by Chad through gh-resolve-a1, who reported the stall and then
hand-shipped the stuck sub-plan with Chad's OK.

## What happened

gh-resolve batch MASTER-2026-10-10a (pin correction) shipped its two work
sub-plans by 07:49. Its batch-verification sub-plan `pin-correction-batch-verify`
then stalled.

- **Step 0 passed.** The gate-first suite record finished at 08:20 on head
  `e7f61efc`, tree `c1b73918`.
- **Step 1 could never pass.** Its gate is `verify_attribution.py`.
  `gate-history.jsonl` shows it red at 08:22:14 and again at 08:33:50, on the
  same tree both times.
- **Five barren runs followed:** the 08:22 iteration of run 20261010-054633, then
  runs 083327, 083830, and those at 08:42. Each:
  - exited 0 after 19-26 s;
  - started no worker and wrote no `iter-NN.log`;
  - recorded stop_reason `timeout`.

  The scheduler redispatched about every 5 minutes, and `no-progress.json` stayed
  at count 1.
- **The way out was by hand.** At 08:45 gh-resolve-a1, with Chad's OK, ran
  `ship_transition.py --ship pin-correction-batch-verify` (marker `10d2995c`).
  First a1 re-ran each of the 17 reds alone on the byte-identical tree:
  - 15 were contention (an ilk-skills run was live, load 5.46);
  - 1 was a sleep race in the test itself (`test_32f`, 1 of 3 alone);
  - 1 was red at base 9a9a918.

  None was attributable to 10a.
- **The same minute, ilk v0.9.191 went live** on both hosts, fixing links 1 and 2
  below.

## The chain

Each link turned a correct refusal into a silent loop.

1. **The record check dropped file-level rows.**
   - What happened: `verify_attribution._verify_ledger_citations`
     (v0.9.190 `verify_attribution.py:160-175`) built the record's id set from
     table lines containing `::` only.
   - Why it mattered: under `--dist loadfile`, a file whose tests failed together
     is a row keyed by its path (`tests/test_25b_….py`). The ledger entry had 17
     ids and the record set had 12. A step-0 record could never pass its own
     step-1 check.
   - Measured on the real record: v0.9.190 raises
     `head_source: ledger entry's failing_nodes (17) do not match the record's
     at-base rows (12)`, and v0.9.191 verifies it.
2. **Nothing to return was reported as "returned".**
   - What happened: on a red gate-first verify, the runner asks
     `suite_ledger.py return-reds` to send attributed reds back to their owners.
     With 0 of 17 rows attributed (16 flaky-owed, 1 failed-at-base) it hit
     `if not bad_rows: return 0` (v0.9.190 `suite_ledger.py:1156-1158`).
   - Why it mattered: exit 0 is documented as "returned successfully", and the
     runner (`run_ilk_loop_claude.sh:2898-2900`) then set
     `GATE_FIRST_NO_DISPATCH=1`. The one actor that could have changed anything,
     the verify worker, was never started.
3. **A run where nothing was dispatched was labelled `timeout`.**
   - What happened: with no agent run, `ITER_COMPLETED=0` and no commits.
     `_decide_iter_stop_reason` (`run_ilk_loop_claude.sh:4725-4727`) maps that to
     `timeout` ("boundary kill with no new commits").
   - Why it mattered: there was no boundary and no kill.
4. **The postmortem read the label as the cause.**
   - What happened: ilk-feedback classified the runs `timeout-bound` and
     recommended a timeout change.
   - Why it mattered: that is the wrong lever, and one Chad's standing rule
     forbids reaching for.
5. **`timeout-bound` is not a blacklist class, and the no-progress streak never
   counted.**
   - What happened: the `timeout` branch returns before the streak increment, so
     `no-progress.json` stayed at 1.
   - Why it mattered: nothing in the pipeline could ever stop the redispatch.

## Why it was not caught

- **Every test of link 1 used `::` ids.** The record format allows file-level
  rows; `suite_ledger._parse_at_base_table` already parsed them correctly. Two
  readers of one table disagreed, and no test ran the two against the same
  record.
- **Link 2's tests covered real returns only.** Both `rc == 0` assertions in
  `test_a_red_goes_back_to_its_owner.py` have an attributed red. The
  nothing-attributed path was never pinned, so a fail-open exit read as success.
- **Links 3-5 are each correct in isolation.** The defect is in their
  composition: a no-dispatch iteration has no name, so it borrows `timeout`, and
  every downstream reader trusts the label.

## Fixed

- **v0.9.191** (2326484a), direct release cut from a side ref on v0.9.190,
  deployed on chad-mbp and rezmac at 08:45:
  - (1) The record check reads rows with `suite_ledger._parse_at_base_table`,
    the ledger's own parser.
  - (2) `return-reds` exits **5**, not 0, when nothing is attributed. The runner
    then falls through to the verify worker.
  - Pins: `test_a_file_level_red_is_a_record_row.py` (the 2 defect pins are red
    at v0.9.190).

## Open (filed)

- **Links 3 and 5 (kernel, owner):** the no-dispatch path needs its own stop
  reason, and it must count toward the no-progress streak. Otherwise any other
  `GATE_FIRST_NO_DISPATCH` path, for example a red gate on a finished
  batch-verify (`:2893`), loops the same way.
- **Link 4 (`collect.py`, not kernel; RSI):** a zero-agent iteration is not
  `timeout-bound`.

## Lessons

- **When two readers parse one artifact, test them against the same artifact.**
  The record's at-base table had two parsers, and one had a stricter row shape.
- **"Nothing to do" is its own exit code.** The same rule as
  readers that return empty for input they cannot parse: a reader that returns
  its success value for input it did not act on is a fail-open.
- **A stop reason is evidence.** It must describe what happened. A label chosen
  by elimination ("no completed turn and no commits, so timeout") sends the
  postmortem, the blacklist and the operator to the wrong lever.
