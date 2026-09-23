# Batch verification: smooth, fast, and unexcusable

**Standing goal (operator, 2026-09-24).** The batch-verification sub-plan that
ends every master must be **smooth and fast on every project**: it does not get
stuck. It must also be **unexcusable**: the batch it judges cannot talk its way
past it. Judge every change to `verification_record.py`,
`verify_attribution.py`, `batch_gate.py` or
`skills/ilk-loop/templates/batch-verification-subplan.md` against both halves:

- A correctness fix that adds a new human stop for ordinary noise (a flaky
  test) fails **smooth**.
- A speed-up that re-opens a path for the worker to excuse a failure fails
  **unexcusable**.

## Baseline to beat (measured 2026-09-24)

A verify's cost is the sum of `duration_sec` over iterations whose
`local_checks[].slug` names a `batch_verification: true` sub-plan, from each
project's `~/.ilk-data/projects/<key>/logs/.ilk-loop.log`.

| host / project | gated verifies | min per verify | 1-iteration | last gate not pass |
|---|---|---|---|---|
| chad-mbp gh-resolve | 40 | 31.7 | 35 | 12 |
| chad-mbp ilk-skills | 18 | 42.3 | 13 | 6 |
| chad-mbp kira-cloudflare | 6 | 57.0 | 1 | 2 |
| **chad-mbp total** | **64** | **37.0** | 49 | 20 |
| rezmac resolver per-issue verifies | 84 | 14.5 | 82 | 3 |

- On chad-mbp, 15 of the 25 non-pass outcomes are `inconclusive/124`
  (timeouts).
- gh-resolve's suite takes about 4 minutes (6010 tests, 232s at `-n 8`), so the
  worker session is most of the 32 minutes.
- 20 of 64 verifies shipped with a non-pass last gate. That path is not yet
  explained (v0.9.124's #41 fix covers the timeout share).

## Plan of record

1. **`the-batch-cannot-excuse-itself` (unexcusable, without new stops).**
   Source: `docs/retros/retro-2026-09-24-the-batch-wrote-its-own-amnesty.md`.
   - Only the base commit's `baseline_red` excuses a failure.
   - A skipped rerun is recorded as `declared-at-base`, not `failed`.
   - The record keeps an append-only attempt history and is digested.
   - A **flaky classifier** replaces worker narration. Only a failure that is
     red at HEAD every time, or intermittent in a file the batch touched,
     blocks. Other intermittent failures are recorded as "flaky, fix owed" and
     do not stop the batch.
2. **`verify-is-fast` (smooth).** Planned once batch 1 has shipped, against
   its code:
   - no worker session on the happy path (the driver records and attributes);
   - suite budget from measured durations, not a hand-typed `--suite-timeout`;
   - one suite run per verify.

   Target: a clean verify costs about one suite run.
3. Then re-measure with the method above and update this table.

## Found while shipping batch 1 (v0.9.125, 2026-09-24)

Batch 1's own verify took 14 min: the full suite once, run by a worker. These
feed batch 2:

- **A step whose gate extracts 0 checks "passes".**
  `extract_step_local_checks` (`run_local_checks.py:443`) does not match
  `### Step N:`, so the step's `local_checks` fence is invisible and the helper
  records `pass` on 0 checks, with no `command`. On chad-mbp that is 13 steps
  in 6 of 1132 plan files: 7 from batch 1 (the author's headings), 3 in
  gh-resolve 23c, and 3 with another cause. `plan_lint` and `plan_preflight`
  did not flag it. This is **unexcusable** (a vacuous gate) and **smooth**
  (see the next point) at once.
- **The gate-first fast path falls through silently.**
  `gate_first_results_are_green` (`run_ilk_loop_claude.sh:1587-1616`)
  requires a `command`. On the vacuous gate it returned 1, printed nothing,
  and dispatched a worker, which reran the full recorder itself (12 min).
- **The driver's batch-end gate is a second suite and a second writer.**
  After `all-shipped`, `invoke_batch_gate` runs `batch_gate.py --run`: the full
  suite again, excusing only via the working tree's `baseline_red`. It
  overwrote `verify_attribution`'s pass with `fail` (the 2 tests that failed
  at base). It appears in 87 launcher logs since 2026-08-25, and 11 of those
  show a pass.
- **The classifier reruns more than it needs to.** HEAD reruns also ran for
  rows that failed at base or were declared, where the class is already
  decided.

Design input for item 2 and the classifier: gh-resolve-21's proposal
(2026-09-24), items 1-4 and the file-touched rule.
