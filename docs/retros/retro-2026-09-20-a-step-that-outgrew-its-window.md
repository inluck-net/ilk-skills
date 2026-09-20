# Retro 2026-09-20 — a step that outgrew its window

The G2 `g2-lark-init-project-tests` step 1 thrashed across three consecutive
30-minute iterations on 2026-09-20 evening, netting one real commit in 90
minutes, because the step's size exceeded the runner's iteration window and
every fresh session re-paid a fixed re-orientation cost before editing. The
operator asked for a retrospective with a standing guarantee: **no project
planned with `/ilk-plan` and run by the scheduler should hit this again.**

## Timeline (all measured this session)

| Time | Event |
|---|---|
| 21:27 | step 0 committed — `4371fb7` "20 failures classified — all mock-target drift on `get_tenant_access_token`" |
| ~21:55 | run 212505's iteration killed at its 30-min bound (exit 137); WIP preserve `d245520` |
| ~22:00 | scheduler redispatch (run 220025) |
| ~22:30 | run 220025's iteration killed at its bound after **15 Edit/Write tool calls** (banked in WIP `3bc4559`); next dispatch (run 223054) |
| 22:46 | run 223054's iteration 16 minutes in, **zero edits yet** — still re-orienting |

Net: 3 iterations, 1 real commit, ~50% of each window spent re-deriving state
that was already on disk (the classification commit, the failing-test list,
the prior edits inside the WIP commits).

## Mechanism

Three parts, each verified in-session:

1. **The window is a launch flag, not a plan property.** The runner takes
   `--iteration-timeout-min` (default 30 — `run_ilk_loop_claude.sh:27`), the
   launcher reads the project's `.ilk-launch.json` `iteration_timeout_min`
   (`launch.sh:289`) and passes it through (`launch.sh:676`). The planner's
   per-sub-plan `recommended_iteration_timeout_min` is read by **lint only**
   (`plan_lint.py:1548`) — it justifies gate-timeout sums; nothing at launch
   time ever sees it. Two vocabularies for one concept, never wired.
2. **The re-orientation tax is per-iteration.** Each iteration is a fresh
   session (the design that keeps context small); its only memory is the plan
   file plus the tree. With no re-entry note, it re-reads the plan, re-runs
   the failing tests, and re-derives the diagnosis — the 22:30→22:46 stretch
   above is that tax measured.
3. **The system converged anyway — by accident of design.** The WIP-preserve
   on timeout (`Preserved by ilk-runner on timeout`) banked each cycle's
   partial edits, and timeout-bound exits are relaunch-whitelisted
   (`run_ilk_loop_claude.sh:2799`), so the loop made forward progress at
   ~half efficiency rather than stalling. This is why the batch eventually
   finishes — and also why nothing flagged the inefficiency: commits existed,
   so no no-progress classifier fired.

The un-wired declaration is the defect. A step that cannot fit a window is a
PLANNING failure (it should have been split), and a window that ignores the
plan's own size declaration is a RUNTIME failure. Both halves exist today.

## Prevention (the guarantee)

- **P1 — wire the declaration.** The launcher resolves the effective window
  as `max(project iteration_timeout_min, max recommended_iteration_timeout_min
  across the active master's sub-plans)` (capped by a scheduler maximum).
  After P1, a sub-plan that declares `recommended_iteration_timeout_min: 90`
  (as `g2-verify` already does) gets a 90-minute window without anyone
  editing config. Interim mitigation applied 2026-09-20 22:55: this project's
  `.ilk-launch.json` now sets `iteration_timeout_min: 60` — judgment call,
  basis: the measured half-window tax and the lark step's measured edit rate;
  falsifier: a genuinely hung agent now takes 60 min to bound (the watchdog's
  liveness is heartbeat-based, so hangs are still caught independently).
- **P2 — plan-time window-fit lint.** `/ilk-plan` (and `plan_lint`) gains a
  one-window test alongside the existing fresh-session test
  (decomposition-principles §5): a step whose edit surface (files × expected
  edits) plausibly exceeds half the effective window must either be split or
  carry an explicit `recommended_iteration_timeout_min` with its basis. HARD
  finding at plan time — the cheap interception point, exactly like
  `lint_verification_subplan_hardcodes_suite`.
- **P3 — the WIP preserve stamps re-entry state.** The timeout-preserve path
  appends a Findings note (or extends its commit message) naming what the
  iteration completed and what remains, so the next session's first act is
  continuing, not re-deriving. Tonight this was a hand-written planner patch
  (`g2-lark-init-project-tests` Findings, 22:50); it should be structural.
  The commit message already carries `[wip:timeout] files=N` — grow it into
  a step-scoped remainder.

## Adjacent findings observed the same evening (not this retro's scope)

- The ship-integrity audit fired mid-run 73s after the verify push commit and
  parked kira's pv6 with a verdict that read stale the moment it was written
  (two manual unparks tonight). The audit outruns the driver's own ledger.
- `scheduler.log` prints `promote: <key> -> None` on every kira dispatch —
  the promote line's target-name lookup returns empty while the dispatch
  itself succeeds.
- Panel residue: a running project whose master shipped mid-run shows an
  empty batch name (status_all.py:479 accepts active/queued only — same line
  the parked-batch gap lives on; SP8 covers the parked half).

## Follow-through

P1-P3 to be planned as the next toolkit batch (`/ilk-plan`, queued behind
G2). The retro and the interim config mitigation committed together.
