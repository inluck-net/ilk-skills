# Retro 2026-10-09: a worker ran the whole batch in one turn, and every counter read 0

## What happened

Two MiMo-worker batches ran on chad-mbp on 2026-10-09: ilk-skills
`MASTER-2026-10-09c` and gh-resolve `MASTER-2026-10-09b`. For more than an hour,
every sub-plan of both showed `pending 0/N`. Chad asked twice what was wrong.
Work was in fact landing; the step counters were not moving.

- **ilk-skills run 20261009-133401** had one iteration, 13:34-14:35.
  - Its first tool call was `Skill(skill=ilk-plan, args=continue)`. That is the
    planning skill; the loop skill is `ilk`.
  - It made 9 commits across all three sub-plans: steps 0-2 of
    `a-repeated-failure-files-its-own-row`, steps 0-2 of
    `rezmac-rows-reach-the-rsi-backlog`, and an empty
    `fix(verify): no attributed regressions [plan:repeat-failure-backlog-verify#step-1]`
    (15e6bbcd). The batch verification had never run.
  - At the end of the turn the driver gated:
    - sub-plan 1 step 2: pass at 14:14
    - sub-plan 2 step 2: pass at 14:24
    - verify step 1: `verify_attribution.py --remeasure-if-stale`. It went
      `[gate-stalled] idle 9.5 min (0.2 s CPU)` and ended
      `ILK-CHECK: unmeasured timeout`.
  - The run stopped `local_checks_failed`.
- **ilk-skills run 20261009-143942** opened the correct skill.
  - It found all three steps already committed and set `current_step: 3` in the
    frontmatter itself.
  - The driver logged
    `no commit trailers found; gating the active sub-plan instead (... 0)`.
    It ran step 0's gate, a single test file, then `[driver-ship] shipped after
    a green gate` at 14:53.
  - The code had passed its full step-2 gate at 14:14, so this ship is not
    wrong. But the driver accepted a counter the worker had edited by hand.
- **gh-resolve run 20261009-135408** finished step 0 at 14:09 and logged "Now
  proceeding to **step 1**" in the same turn. It also printed the host-agent
  routing line `Direct-implement: step 1 of sub-plan 1.` At 14:53 it was still in
  iteration 1, with 1 commit and every sub-plan at 0/N.
  - Its 90-min iteration bound would have hit at 15:24, so it was stopped and
    relaunched by hand (Chad, ~15:00).

- **gh-resolve run 20261009-133350** did the most damage of the three.
  - It did all 3 steps of `the-suite-has-no-expiry-date` (step-2 gate pass at
    59106fae).
  - The worker then set the sub-plan's `status: complete`. `complete` is not an
    ilk state.
  - The dependent sub-plan never became runnable: `blocked-no-runnable`, and the
    master went to `blocked`. The scheduler then dispatched the next master
    (09b) over the operator's first priority (D-513, in 09a).
  - gh-resolve-a4 repaired it: `ship_transition.py --ship` (8892a2a6), un-parked
    09a and re-queued 09b.
  - A4 stopped run 135408 at ~15:00. Step 0 was kept (15059547); a 1-line step-1
    edit was saved as a patch and reverted.

## Why

The step contract contradicts itself, and the worker follows the looser text.

- `commands/ilk.md:66`: "Execute exactly one step from the sub-plan."
- `skills/ilk-loop/SKILL.md:199`: "Execute exactly the next step (or a few
  consecutive ones if context allows)."
- `commands/ilk.md:66-68` adds "You MAY execute several consecutive steps in
  the same chat IF you have clear context capacity".
- `SKILL.md:213-214` tells the worker to bump `current_step`, but not by how
  much, and nothing says `status:` is off limits.
- The runner's worker-gate notice ends "End your turn ONLY when all are green".
  It says when the turn may end, not that it must.
- The driver advances steps only at the end of an iteration. A multi-step turn
  is therefore invisible for up to the full 90-min bound, and gates for all its
  steps run at once, if they run at all.

Misrouting to `ilk-plan` makes it worse, because that skill has no one-step rule.
Tally over ilk-skills launcher logs `*202610*`: `Skill(skill=ilk)` 57,
`ilk-plan` 5 (runs 20261002-002715, 20261002-222156, 20261003-054714,
20261008-085055, 20261009-133401), `ilk-status` 14. gh-resolve run
20261009-133350 also opened `ilk-plan`.

## Filed

- `a134fac5e898099d`: the worker routes `/ilk please continue` to `ilk-plan`
  (run_ilk_loop_claude.sh:46).
- `aba1019c3d9e5bfe`: SKILL.md licenses multi-step turns and worker-side
  `current_step` bumps (SKILL.md:199, :213-214).
- Still unexplained: verify step 1's re-measure sat idle for 9.5 min at 0.2 s
  CPU. What it waited on was not measured. The next verify of 2026-10-09c is the
  test. If it repeats, it is a wait the 2026-10-07 rule forbids
  (`retro-2026-10-07-one-suite-per-batch.md`).

## The rule

**One turn, one step.** The worker does the step at `current_step`, commits it
with its trailer, makes that step's gate green, bumps `current_step` by exactly
one (N -> N+1), and ends the turn. It never writes `status:`. The +1 bump stays:
for steps that are not gate-first, the driver does not advance the pointer
itself, and the ship-proof rows read it (run_ilk_loop_claude.sh:2350-2358,
detached-component-contracts.md `step_to`).

A status panel that reads 0 while commits land is a contract failure. It is not
"slow progress".

## How to judge a future change

Count the step trailers a single iteration produced. The answer must be at most
one step (plus `test-infra:` fixes for that step). Anything a worker writes into
plan frontmatter is a claim, not evidence; the driver must re-derive it from
trailers and gate history.

## Fixed (owner, 2026-10-09)

- `SKILL.md` "The loop": exactly ONE step per turn; bump by exactly one; never
  write `status:`; end the turn.
- `commands/ilk.md` section 5: the "several consecutive steps" licence is removed.
- The runner's worker-gate notice now ends: bump `current_step` from N (+1),
  commit, END YOUR TURN; do not start the next step; never write `status:`.
- Pin: `skills/ilk-loop/tests/test_one_turn_one_step.py`. 4 tests; all 4 fail on
  the pre-fix text.
- Not fixed: the driver still accepts a worker-edited pointer as the step to gate.
  The run-2 ship gated step 0 after the worker jumped to 3. That is a driver
  change (row aba1019c3d9e5bfe).
- Not fixed: a held draft master turns a shipped batch's exit into
  `blocked-no-runnable`, which blacklists the project and starts no train (row
  ea9a959c8e5ff1e1). It is pinned on purpose by
  `test_a_draft_master_is_never_acted_on.py` AC-1 (#55), so it needs a design
  decision, not a quick flip.
