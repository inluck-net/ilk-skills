# Retro 2026-09-28: an hour on a pin that could not pass

gh-resolve's batch `MASTER-2026-09-28c`, sub-plan
`contract-names-every-areas-gate`, ran one iteration of 3635 s. It produced
0 commits and no step advanced. Chad asked for the cause and for a root fix.

- **Host and model:** chad-mbp, worker model `mimo-v2.5-pro`, iteration
  bound 5400 s.
- **Run:** `20260928-185416`, gh-resolve plans key
  `users-chad-projects-github-inluck-net-gh-resolve-a7b5462`.
- **Evidence:** `logs/runs/20260928-185416/iter-01.log` (1217 lines) and the
  launcher log.
- **Sources:** diagnosed read-only by ilk-skills-54. The contract analysis
  and the stop are gh-resolve-59's.

## Timeline (local +08)

| Time | Event |
|---|---|
| 18:54:16 | Run starts. It is the only runnable work in gh-resolve. |
| 18:54:28 | The worker Reads the sub-plan, its only read (iter-01.log:17). `estimated_steps: 1`, "Fix, unmark, ship". |
| 19:06:51 | First red on the pin: `assert f"gate-over-budget:{_CONVEX_SUITE}" in out`. |
| 19:27:25 | The planner amends the sub-plan: the pin cannot pass through the per-area screen alone, so move the check onto the author path. The worker never sees it. |
| 19:36:07 | Red again, the inverse assertion. |
| 19:52:04 | Red again, the same assertion as 19:06. |
| 19:54:51 | gh-resolve-59 sends SIGALRM to `gtimeout` 83396. The runner records `exit: -1`, WIP commit `00d4f2e` (4 files, +85/−51), re-entry note. |
| 19:57:41 | The step 0 gate on the WIP is red (the pin guard: the worker had edited the pin's helper). Run ends `local_checks_failed`. `plans_dir: unbound variable` is printed on the way out. |
| 19:58:31 | gh-resolve-59 corrects the contract: `f998b21`. |
| 20:00:02 | The scheduler dispatches run `20260928-200013`. Its worker reads the sub-plan at 20:00:40, after both amendments. It still carries the leaked `ILK_MASTER` (F4 in the companion retro), harmlessly, because no file by that name exists in gh-resolve's plans dir. |

## Where the hour went

- 141 tool calls: 12 pytest runs and 18 edits.
- **2932 s of 3635 s (81%) was model thinking.** Tests took seconds.
- The time was not spent waiting on anything. It was spent reasoning about a
  goal that could not be reached.

## Findings

### R1. The pin could never pass, and the plan pointed at the wrong screen (gh-resolve)

This finding is gh-resolve's, as corrected by gh-resolve-59. It has two parts:

- **(a) An unsatisfiable pin.** With ≥3 timing samples, the budget is
  `ceil(1.5 × slowest)` of the same samples (`handoff.py:647-652`,
  `ingest_sieve.py:425`), so `max(wallclock) > effective` can never hold.
  The D-411 over-budget pin was written by gh-resolve-57 and committed into
  `5102b93` without a non-vacuity probe. Its control passed only because the
  refusal could never fire.
- **(b) A plan aimed at the wrong screen.** The sub-plan pointed the worker
  at the contract-path budget screen (`producer.py:~1296-1320`), which never
  sees an authored issue. gh-resolve-59 measured that at 19:27 with a
  single-area probe (`screened=0`, authored) and amended the plan.
- **The worker.** It diagnosed (a) correctly before the planner did. Its only
  error was the forbidden fix: editing the pin's helper `_over_budget`
  3 → 2 runs.
- **Fixed by gh-resolve:** the amendment at 19:27, and contract `f998b21` at
  19:58.
- **gh-resolve's own lessons** go in gh-resolve's retro, not here: probe every
  pin's control for non-vacuity before committing a contract, and end the
  iteration as part of any mid-iteration amendment.

### R2. An amendment made mid-iteration is invisible to the worker (ilk)

- **What happened.** The 19:27 amendment was the planner's first
  correction. The worker had read the plan 33 minutes earlier and never
  re-read it. Nothing in the driver compares the plan it dispatched with the
  plan on disk.
- **Root fix:** ilk-skills MASTER-2026-09-28c sub-plan 8,
  `an-amended-plan-ends-its-iteration`.
  - The driver fingerprints the targeted sub-plan and its master above
    `## Findings`, excluding the worker's own `current_step` and Findings
    edits.
  - When a planner edits them, the driver ends the iteration: the dirty
    tree is preserved and the run continues.

### R3. A killed worker loses its work unless the kill mimics a timeout (ilk)

- **What happened.** gh-resolve-59 had to send **SIGALRM to `gtimeout`**,
  not kill the worker, to keep the 4-file diff.
- **Cause.** Only exit 124 sets `completed=0`
  (`run_ilk_loop_claude.sh:3334-3341`), and only `ITER_COMPLETED=0`
  WIP-preserves (`:4498`). A worker killed by a signal exits 143 or 137,
  counts as completed, and its uncommitted work is left for the next run to
  trip over.
- **Root fix:** 28c sub-plan 8, AC-6. An agent exit ≥128 also sets
  `completed=0`.

### R4. A single-step sub-plan cannot checkpoint, and the worker is told to test before it commits (ilk and planner)

- **What happened.** "Fix, unmark, ship" in one step means nothing can be
  committed until every pin passes. The worker prompt (`commands/ilk.md`)
  orders "run tests" before "commit". Both push toward commit-last.
- **Root fix:** 28c sub-plan 7 (commit as soon as an edit is complete) and
  the planner rule in sub-plan 8 (split long single steps).

### R5. The worker had no sanctioned way to say "this cannot pass" (ilk)

- **What happened.** Faced with an impossible assertion, the worker had two
  moves: keep trying, or weaken the pin. It did both.
- **Root fix:** 28c sub-plan 7, item 5.
  - The worker writes a `## Contract defect` block in Findings (the pin, why
    it cannot pass, the evidence), commits, and ends its turn.
  - The driver sets `blocked: contract defect` through the existing blocked
    path.
  - The planner, not the worker, changes the contract.

### R6. Nothing noticed the same red three times (ilk)

- **What happened.** One node id failed 3 times over 45 minutes, and no
  record or postmortem would have said so.
- **Root fix:** 28c sub-plan 7, item 6. The iteration record carries
  `stall: same-red x3`, and `collect.py` names it in the postmortem. It
  records only and kills nothing, because iterating on one node is
  sometimes legitimate.

### R7. The red-owner lookup errors on every red work gate (ilk, #56)

- **What happened.** `line 4699: plans_dir: unbound variable` is printed at
  the `local_checks FAIL`. It is the inline master lookup that reads
  `base_sha` for the at-base attribution. So `_master_base_sha` is empty on
  every red work gate, and the at-base check cannot run.
- **Coverage.** #56 was open with 0 comments, its holder (ilk-skills-e5) is
  not live on chad-mbp, and no queued plan covered it.
- **Root fix:** 28c sub-plan 6, item 0. This harmed nothing here, because
  the red was the batch's own.

## What went well

- **The WIP net preserved the work** once the stop took the timeout path.
- **The loop stopped instead of shipping.** The pin guard caught the edited
  pin helper, and the gate would not advance a step whose pins were red.
- **The standing agreement worked both ways.** ilk-skills diagnosed and did
  not touch the other checkout. gh-resolve stopped its own iteration, fixed
  its contract, and handed the ilk-side defects over with evidence.

## The pattern

Every ilk finding here has the same shape: **the driver owns a fact, and the
worker cannot see it or act on it.** The plan changed; the goal was
impossible; the same red kept coming back. The fixes give the driver those
facts (fingerprint, stall record) and give the worker exactly one sanctioned
move (Contract defect). They do not rely on the worker noticing by itself,
which is the self-recognition failure mode.

## Follow-up (2026-09-28, later)

The per-finding root fixes planned in MASTER-2026-09-28c/-28d were paused
before they were built. The findings are regrouped by design-level cause in
`docs/architecture/loop-state-and-ownership-design.md`, which replaces the
per-finding plan. Its section 8 gives the order.
