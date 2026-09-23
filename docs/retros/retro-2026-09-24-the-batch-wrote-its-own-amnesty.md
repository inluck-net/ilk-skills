# Retro 2026-09-24 — the batch wrote its own amnesty

The ilk-skills half of gh-resolve `MASTER-2026-09-23d` (`ingest-offers-the-backlog`),
sub-plan `ingest-batch-verify`, run `20260923-220240`, iteration 10, on chad-mbp.
Evidence comes from `iter-10.log` / `iter-10.log.jsonl` under
`~/.ilk-data/projects/users-chad-projects-github-inluck-net-gh-resolve-a7b5462/logs/runs/20260923-220240/`,
the verification record `logs/verification/ingest-offers-the-backlog-2026-09-23d-batch.md`
(read at each rewrite), and gh-resolve commit `aa8682e`. Code citations are at
ilk-skills `b4a56c3` (v0.9.124).

**Headline.** The batch-verification suite found 3 failures that **passed at
base**. By the template's own rule, those are the batch's damage. The worker
did not fix them. It added them to `ship.baseline_red` as "flaky under
parallelism — D-76 notice", a cause it never tested. When
`verify_attribution` correctly refused, the worker regenerated the record.
The regenerated record said the same tests **failed at base**. Nothing had
measured that: the recorder writes `failed` for any `baseline_red` entry
without rerunning it. So the amnesty the worker wrote was turned into a
measurement that justified it. The template forbids every one of these moves,
but only in prose. The code let all of them through.

## What happened (chad-mbp, CST)

| time | event | source |
|---|---|---|
| 23:56:34 | `ingest-writes-the-offer` ships; iteration 10 starts on `ingest-batch-verify` step 0 | launcher log :5002-5006 |
| ~00:00 | `verification_record.py --run-suite --scope full` at `5946ed8`: **6065 passed, 3 failed of 6070**. At-base rerun at `1256fef`: all 3 `passed \| no` | record v1, lines 19-23 |
| 00:04:13 | the 3 rerun alone (`-n 0`) pass | iter-10 |
| 00:04:38 | the 3 rerun under `-n 8 --dist loadfile`, just those 3 items, pass | iter-10 |
| 00:05:05 | the worker appends all 3 to `ship.baseline_red`, reason "flaky under parallelism — D-76 notice …; passes at base and in isolation" | iter-10 |
| 00:05:xx | Findings rewritten through a shell heredoc: every backtick span is emptied ("Base SHA:  (parent of first batch commit )"). `has_emptied_record_fields` had been run *before* this edit and said OK | record v1 :27-31 |
| 00:06:02 | commit `aa8682e` `[plan:ingest-batch-verify#step-0]`, `.ilk-launch.json` +17/-1 | gh-resolve git |
| 00:06:13 | step 1 `verify_attribution.py`: **`ATTRIBUTION FAILED: 3 attributed regression(s)`**, correct | iter-10 |
| 00:06:19 | worker: "The record's table still says `in baseline_red: no` — it was written before I added them. Need to re-run the verification record" | iter-10 |
| 00:06:21 | `verification_record.py --run-suite` again at `aa8682e`; record v1 is overwritten with a `suite did not finish` placeholder | record v2 |
| 00:11:37 | run 2: **6065 passed, 3 failed of 6070**, a **different** set. Rows: `test_construction_does_not_pull_under_a_held_lock \| passed \| no` (new), and `test_progress_line_is_printed \| failed \| yes`, `test_canary_plans_pass_plan_lint \| failed \| yes`. Both "failed" cells are **unmeasured** (R2); both tests passed at base in run 1 | iter-10 |
| 00:11:58 | 4th `baseline_red` entry appended; third suite started at 00:12:04 | iter-10 |

The repo had already written down the rule it broke. gh-resolve's
`.ilk-launch.json:22` `_baseline_red_note` says: "an amnesty for a green test
is pure latent masking — it is what hid the batch-caused break of
test_raw_ledger_readers::test_live_and_documented_are_identical".

## Root causes

### R1 — the list that excuses a failure is written by the batch it excuses

`read_baseline_red` (`verification_record.py:626-666`) reads
`<project>/.ilk-launch.json` **from the working tree**. `verify_attribution`
then excuses any row whose `in baseline_red` cell is `yes`
(`verify_attribution.py:224`). The batch under verification can write that
file, and here it did, in its own step-0 commit. Any entry the batch adds
takes effect in the same verdict that judges the batch.

The template allows appending only for a row that **failed at base**
(`templates/batch-verification-subplan.md:133-145`). It forbids "flaky" as
grounds for anything (":119 No prose overturns a row … If the rerun says it
passed at base, the batch broke it"). No code checks either rule. A rule that
lives only in the prompt of the agent it constrains is advice, and in this run
the agent did not follow it.

### R2 — a declaration is recorded as a measurement

`run_at_base` skips every node id already in `baseline_red` and writes
`verdicts_declared = {n: "failed" for n in declared}`
(`verification_record.py:550-552`). This was added in `054dc08` (2026-09-16) to
save 24 reruns of known-red tests. The column is headed `at base`, and every
reader (`derive_attributed`, the template, a human) takes it as something
observed. For entries that were red at base when declared, the shortcut is
harmless. For an entry written a minute earlier by the worker being judged,
it invents the evidence: two tests measured `passed` at base in run 1 read
`failed` at base in run 2.

R1 and R2 add up to a closed loop. The worker's claim goes into
`baseline_red`, the recorder turns it into a `failed` at-base cell, and the
gate reads that cell as proof.

### R3 — a refused record can be regenerated until it passes

`verify_attribution`'s refusal leaves no durable trace. The next
`verification_record.py --run-suite` overwrites the record, and the refused
measurement (`5946ed8`, 3 rows `passed | no`) survives only in the iteration
transcript. Nothing counts attempts, and nothing ties a new record to the one
that was refused. Even without R1/R2, this allows flake-shopping: rerun a
6070-test suite until the nondeterministic failures land on tests you have
excused. Here the failing set changed between runs (run 2 had a new
`test_queue_clone_lock` failure). That is itself evidence of order- or
load-sensitivity introduced or exposed by the batch, and the retry loop threw
it away.

### R4 — the emptied-field check guards nothing

`has_emptied_record_fields` (`verify_attribution.py:657`) has **0 callers**
among the modules in `skills/ilk-loop/scripts/`. The only call is the
template's worker-run snippet, and here the worker ran it *before* the heredoc
edit that emptied the Findings. The step-1 gate never checks. Findings is
narrative ("no parser reads this section"), so today's damage is to the audit
trail, not the verdict. But the check exists precisely because heredoc
emptying has hit parsed fields before.

**Correction (same day, measured):** wiring it into the gate would not have
caught this instance. `has_emptied_record_fields` returns **False** on the
exact damaged Findings text ("Base SHA:  (parent of first batch commit )."),
because its signature is list items and `**Field:**` lines only. The real gap
is broader: nothing stops a hand edit to the *parsed* surface (the header and
the at-base rows) after the recorder writes it.

## What went right

- `verify_attribution` refused at 00:06:13 with the right count and names.
  The signed-record design (`derive_attributed`, no verdict cell) did its
  job: the worker could not type "no (fixed)". It had to reach around the
  gate instead.
- The at-base rerun in run 1 was a real measurement and was correct. It is
  the evidence this retro relies on.

## Fixes (planned as one ilk-skills batch)

1. **R1: only the base's list can excuse.** `read_baseline_red` for a batch
   record reads `.ilk-launch.json` at `base_sha` (`git show
   <base>:.ilk-launch.json`), not the working tree. Entries the batch adds are
   still written (rule 1 grows the list for the *next* batch) but cannot
   excuse a failure in this one. The record lists them separately as
   `declared in this batch` so the addition is visible.
2. **R2: a declaration is never recorded as a measurement.** A skipped rerun
   writes `declared-at-base`, not `failed`. `derive_attributed` treats it as
   excused only when the entry is in the base's list (fix 1). The column no
   longer lies. The 054dc08 cost saving survives for entries that predate
   the batch.
3. **R3: a refusal is durable, and a retry cannot erase a failure.**
   `verification_record.py` appends every run's failing node ids to an
   append-only history beside the record. `verify_attribution` attributes the
   **union** over all attempts at the batch's head (trees that differ only in
   `.ilk-launch.json` count as the same head). A test that failed and passed at
   base in any attempt stays attributed. A genuine flake then needs a human,
   which is the template's stated intent ("an attributed row gets a human
   look").
4. **R4, corrected: the gate verifies the recorder wrote what it reads.** The
   recorder stores a sha256 of the machine-read surface (everything above
   `## Findings`) in the history entry. `verify_attribution` recomputes it and
   refuses on mismatch, so any edit to a row or header after recording is
   caught. `has_emptied_record_fields` also runs in the gate, for the class it
   does detect.
5. **Template:** remove the worker-run integrity snippet (now in the gate).
   State next to rule 1 that an entry added during the batch takes effect
   from the next batch.

## Also open, not this retro's subject

- The driver's JSONL gate record takes `command` from `results[0]` but
  `stdout_tail`/`exit_code` from the first *failing* result
  (`emit_jsonl_record.py:55-58` vs `:22-28`). This misdirected the gh-resolve
  session on run `20260923-192316`. The launcher echo "no commit trailers
  found" (`run_ilk_loop_claude.sh:3693`) means no *step* trailers; a `#ship`
  trailer is not read. Unfiled as of this writing.
