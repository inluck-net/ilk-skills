# Retro 2026-10-09: a refused train read as a train never offered

## What happened

### 1. A high row diagnosed the wrong component

Row `ea9a959c8e5ff1e1` (filed 2026-10-09 07:24 UTC, `high`/`high`) said:

- a held draft master turns a fully shipped batch's exit into
  `blocked-no-runnable`;
- the project is then blacklisted;
- `train_candidates` only offers a train when `sentinel_all_shipped`, so no
  train starts.

It blamed this for the hand cuts of v0.9.177-180. Handoff-24 ranked it ahead of
Batch 2, and the retro of the same day
(`retro-2026-10-09-a-worker-ran-the-whole-batch-in-one-turn.md`, last bullet)
repeated it as an open design decision.

When the row was picked up for planning, three reads contradicted it.

- **The train-side check already passes this exit.**
  - `sentinel_all_shipped` (`skills/ilk-watchdog/scripts/release_train_dispatch.py:80-118`)
    accepts `blocked-no-runnable` when iterations >= 1, there is no `held_by`,
    and every *active* master is all-shipped. It skips draft masters.
  - Run on the real sentinel of run 20261009-143942 (4 iterations), it returns
    `True`.
  - Commit 23093133 added this behaviour, before the row was filed.
- **The train was offered on every pass, and refused for a permit.**
  `~/.ilk-data/logs/scheduler.log`, ilk-skills key, from 2026-10-08 12:00 to
  2026-10-09 22:20:
  - 277 `skip-permits: ... (consumed permit for host chad-mbp)`, every hour
    from 2026-10-08 14h onward;
  - 18 `skip-audit-failed (verdict=error)` (2026-10-08 12-14h);
  - 11 `skip-blacklist`;
  - 0 `release-train-started`. The last one was 2026-10-08 11:52.

  `maybe_start_release_train` checks permits after the sentinel and audit
  checks pass (`scheduler.sh:615-620`). So "no train started" meant "the train
  was offered and the only chad-mbp permit was spent". In release mode (a)
  that refusal is correct.
- **The blacklist was real, but it came from a different run.**
  - 15:04-15:35 was `skip-blacklist`. The newest postmortem on disk was the
    previous run's (`20261009-133401`, `local-checks-stuck`, generated
    14:39:33).
  - Run 143942 has no postmortem of its own: 0 of 335 files under
    `runtime/launcher/postmortems/`.
  - The backoff expired at 15:39. The first `skip-permits` came at 15:40.
  - `blocked-no-runnable` is not in `BLACKLIST_CLASSES`
    (`skills/ilk-watchdog/scripts/blacklist_status.py:42-49`). The morning run
    (094658, classified `blocked-no-runnable`) went straight to `skip-permits`
    at 09:50.

So the cause of the hand cuts was a missing permit. Nothing surfaced that: 277
refusals over 31 hours went into a log nobody read, while four releases were
cut by hand.

### 2. A worker shipped another master's quarantined sub-plan, hours after the rule against it

gh-resolve run 20261009-212756 is pinned to `ILK_MASTER=MASTER-2026-10-09b` and
runs on v0.9.180, which contains f1758ea9 ("never write `status:`"). In its
launcher log:

- 21:33:47: iteration 2 opened `Skill(skill=ilk-plan, args=continue)`.
  Iteration 1 had opened `Skill(skill=ilk, ...)` at 21:28:12.
- The worker went to the newest master (09d) and found the quarantined
  `an-unchanged-failure-waits` (`status: blocked`).
- 21:34-21:41: it ran that sub-plan's 132-file neighbour gate in the worker
  (4860 passed).
- It then Edit-ed `2026-10-09d-an-unchanged-failure-waits.md` (now
  `status: shipped`), `2026-10-09d-green-base-batch-verify.md` and
  `MASTER-2026-10-09d-execution-plan.md` (3 edits). It made 0
  `ship_transition` calls.
- A PreToolUse hook refused one Read. Edit was not refused.

gh-resolve-c6 confirmed with its own reads: ship-proof holds loop-executed rows
for steps 0-3, but there is no driver ship transition after the quarantine.

The same iteration spent 21:49-22:07 on step 1's `pins_only_lose_xfail.py`
gate, which cannot pass. Step 0 wrote two wrong pins, and the gate allows
only xfail removals. c6 owns that, and it is the second time today (09d
sub-plan 1, 17:42).

The iteration took 2524 s and cost 8.47 USD.

## Why

1. **The row was filed from code, not from the decision log.**
   - The filer read `classify_loop_status` and `train_candidates`, saw
     `blocked-no-runnable` in the sentinel, and inferred the rest: "blacklisted",
     "no train starts".
   - The scheduler writes one decision line per project per pass, so the claim
     could be checked with a single grep. Nobody ran it. The negative ("no
     train starts") had no denominator.
   - It was then copied twice (the retro, then handoff-24) without a re-check,
     and gained authority with each copy.
2. **A refusal that repeats forever is logged as a skip, the same as a routine
   skip.**
   - `skip-permits` looks exactly like `skip-busy`. Nothing counts how long the
     refusals have run or asks the owner for a permit.
   - The mechanism worked and the operator never heard about it, so a working
     gate was read as a broken one.
3. **A prose contract was broken within hours.**
   - f1758ea9 put "never write `status:`" in SKILL.md and the worker-gate
     notice.
   - The very next misrouted worker (`ilk-plan`, row a134fac5, still open)
     never read that text, and nothing enforced it.
   - The text was correct. It had no effect on a worker that never loaded it.

## The rule

1. **A row that claims something does not happen must quote its denominator
   from the decision log.**
   - Before filing "X never starts / is never offered / is blacklisted",
     tally the project's `scheduler.log` decisions over the window, and quote
     the count of the expected decision and of what happened instead.
   - Before ranking a row for planning, re-run that tally. A row copied from
     another doc is a claim about the past (CLAUDE.md rule 2).
2. **A refusal that repeats must reach the operator.**
   - If the same refusal repeats for longer than a release would take, it is a
     request for action, not a skip. Row `79299fe6151b6d6e` (below).
3. **A rule a worker can skip is enforced by the driver or a hook, not by
   text.**
   - Whatever the worker writes to plan frontmatter, the driver re-derives
     before acting on it. Row `d61afe74f26dd73d`.

## How to judge a future change

- For any row about dispatch or the train, grep the key's decision lines
  first. A diagnosis that the log contradicts goes back to `open` with the
  tally attached.
- For a worker-contract rule, find the mechanism that refuses the violation. If
  the only mechanism is the text, the rule is not yet in place.

## Filed / closed

- Closed `ea9a959c8e5ff1e1` as `wontfix`, misdiagnosed. The evidence above is
  attached to the row.
- `79299fe6151b6d6e` (high leverage, medium severity): a train refused for a missing permit is silent;
  277 refusals in 31 h reached nobody.
- `20b15e294c3c837d` (low): a run with no postmortem of its own inherits the
  previous run's blacklist class.
- `d61afe74f26dd73d` (high): a worker ignores the `ILK_MASTER` pin, opens
  another master via the wrong skill, and ships its quarantined sub-plan by
  editing plan files. Relates to `a134fac5e898099d` (misroute to `ilk-plan`)
  and `aba1019c3d9e5bfe` (the driver accepts a worker-edited pointer).
- gh-resolve's side (unsatisfiable pin gate, the 09d state, the `pipeline.py`
  documented-vs-live red) went to gh-resolve-c6, which confirmed it and holds
  it for Chad.
