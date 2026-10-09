# Operating principles

Standing rules from the owner for every agent that plans, runs, fixes or
releases ilk-skills work, on any host, in any harness. These rules live in
the repo on purpose, so an agent with no Claude Code memory, or on another
machine, applies them too.

## 1. A smooth pipeline comes first

> "The smooth pipeline is always most important; whenever it becomes not
> smooth from a smooth state, this is the highest priority to fix, same for
> ilk-skills as gh-resolve."
> — Chad, 2026-10-10

**What counts as "not smooth".** Anything that stops a pipeline that was
working from moving work to `shipped` and released without a human:

- a stall (repeated `no-progress`, a run that re-gates the same green or red
  step without shipping);
- a relaunch onto the same red with nothing changed;
- a ship with a red step, or a ship nobody proved;
- a worker acting outside its step (wrong skill, another master's plan
  files);
- a train or release that cannot start for a reason that is not the owner's
  decision.

**How to apply it.**

- A smoothness regression goes ahead of every feature batch, RSI backlog
  row and planned work in the queue. Fix it, release it, then go back to the
  queue.
- Pick the fix with evidence from the run that broke: the launcher log line,
  the scheduler decision, the run id. The fix carries a test that fails on the
  old code.
- Tell the affected consumer's owner (for example the gh-resolve session) the
  tag that carries the fix, because their runs pick it up only from that
  release on.
- The same rule holds on the consumer side: gh-resolve treats its own
  regressions the same way.

**Examples (2026-10-09/10).** Each one was a smoothness regression, fixed
ahead of feature work:

| Symptom | Cause | Fix |
|---|---|---|
| A verify re-ran a green gate for 1 h and never shipped | a worker's `### Step N` note under Findings was counted as a step | v0.9.184 |
| A sub-plan shipped with its step-1 check red | a worker's pointer bump survived a red gate | v0.9.185 |
| A worker shipped another master's sub-plan by editing it | the plan-edit hook resolved no plans dir when installed | v0.9.181 |

## 2. ilk-skills upgrades unless its contract changes

> "ilk-skills is more an external, independent, basic component. So as long as
> no outward-facing contract or API change, the ilk-skills should always [be
> able to] upgrade."
> — Chad, 2026-10-10

Consumers read keys and the data-dir layout, status and sentinel files, CLI
flags, worker env vars and plan frontmatter, so those are the contract. A
release that changes none of them goes to every host.

*Proposed, not yet decided:* a release that does change the contract ships
behind a switch whose default keeps the old behaviour, so consumers opt in
instead of holding a host back (the case that held rezmac on v0.9.179).
