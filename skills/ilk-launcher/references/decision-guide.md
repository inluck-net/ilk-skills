# Agent Decision Guide: Choosing MaxIterations and IterationTimeoutMin

> When launched via the agent, the agent should pick these values **based
> on the next pending sub-plan's content** AND **the recent run history
> for this project**, not on a static per-project config. The
> `.ilk-launch.json` mechanism still works as a fallback for humans
> running `launch.ps1` directly without agent help.

## Step 1.5 — Read recent postmortems (history-aware)

Before reasoning about the next sub-plan, the agent should glance at the
last few postmortems for this project (produced by the `ilk-feedback`
skill):

```
~/.ilk-data/projects/<key>/runtime/launcher/postmortems/*.md
```

Each postmortem has a YAML front-matter block with:

```yaml
classification: timeout-bound | max-iter-bound | api-flaky | api-blocked
                | stuck-no-progress | budget-exhausted | interrupted | clean-success
recommended_max_iterations: <int>
recommended_iteration_timeout_min: <int>
```

Read **only the front-matter** of the **3 newest** files (they're tiny;
this is cheap). Apply these soft rules:

| Pattern in last 3 postmortems | Adjustment to your sub-plan-based estimate |
|---|---|
| >=2 of 3 are `timeout-bound` | Use `max(your_estimate, max(recommended_iteration_timeout_min))` |
| >=2 of 3 are `max-iter-bound` | Use `max(your_estimate, max(recommended_max_iterations))` |
| >=2 of 3 are `api-flaky` or `api-blocked` | Tell the user before launching: "endpoint has been unstable; want to switch model or proceed?" |
| >=2 of 3 are `stuck-no-progress` | Tell the user the sub-plan colortile may need restructuring; ask before launching |
| Mostly `clean-success` | No adjustment; trust your sub-plan-based estimate |
| No postmortems exist | First run for this project — proceed with sub-plan-based estimate only |

This is **history as a prior**, not a hard override. The sub-plan reading
in Steps 1–4 still drives the baseline; postmortems shift defaults when
real evidence suggests we underestimated.

## Step 1 — Read what's about to run

Before calling `launch.ps1`, the agent should:

1. Run `loop_status.py` to find the next pending sub-plan (or read
   `status_all.py` output if already at hand).
2. Read that sub-plan's front-matter: `estimated_steps`, `current_step`,
   `priority`, `tickets`.
3. Skim its **Steps** section + **Manual user verification** section
   (if any). Notice signals like:
   - mentions of `pytest` / `vitest` / `npm run build` / `tsc` (compile-bound)
   - mentions of `playwright` / `e2e` / `chrome-devtools` smoke test
     (browser-bound; slow)
   - mentions of `wait_ci` / "wait for CI green" / "push and wait"
     (CI-bound; can hit 15–60 min per step)
   - data-heavy work: scraping, indexing, large file processing
   - pure refactors / small fixes / docs (fast)

## Step 2 — Pick MaxIterations

Baseline: `remaining_steps = estimated_steps - current_step`.

| Situation | Suggested MaxIterations |
|---|---|
| Single sub-plan, <=8 remaining steps, no risky steps | `max(remaining_steps x 2, 10)` |
| Multi sub-plan queue (many pending in master) | `max(total_remaining_steps x 1.5, 20)` |
| Steps include CI-wait or e2e — agent may need retries on red | bump above by +10 |
| Project unfamiliar / first run after big plan change | round up generously, cap at 60 |

Hard floor: **10**. Hard ceiling: **60** unless user explicitly asked for more.

## Step 3 — Pick IterationTimeoutMin

| Step character | Suggested IterationTimeoutMin |
|---|---|
| Pure code edits + unit tests only | **20** |
| Includes `npm run build` / `tsc` / `pytest` over a real suite | **30** |
| Includes browser smoke (chrome-devtools / playwright e2e) | **45** |
| Includes "push and wait for CI" with project CI >= 15 min | **60** |
| Includes "push and wait for CI" with project CI >= 30 min | **90** |

Hard floor: **15**. Hard ceiling: **120**.

## Step 4 — Apply project-specific common sense

The agent should also weight known project facts (from `current-stack.md`
and chad's prior signals — agent can ask if unsure):

- `myproj`: has playwright e2e + Gitee CI 15–30min → default toward
  60 timeout.
- `es_api`: backend API, mostly pytest → 30 timeout is plenty.
- `crawler`: data scraping iterations can each be slow → 60 timeout,
  fewer iterations needed.
- New / unknown project: **ask the user**, don't guess wildly.

## Step 5 — Always tell the user what you picked and why

When reporting back after launch, include a one-liner like:

> "Picked `-MaxIterations 30 -IterationTimeoutMin 60` because next
> sub-plan has 12 remaining steps and 3 of them include playwright e2e
> + push-and-wait-CI."

This lets chad correct you cheaply (`stop.ps1 -ProjectName foo` then
relaunch with adjusted flags).

## When NOT to apply heuristics

- User explicitly passes `-MaxIterations N` / `-IterationTimeoutMin M` →
  obey verbatim, don't second-guess.
- `.ilk-launch.json` exists in `<project>/docs/plans/` AND user is
  invoking the launcher directly (not via agent prompt) → the launcher
  already handles fallback resolution, agent doesn't need to override.
- User says "用默认" / "use defaults" → don't compute; let the launcher's
  built-in fallback (30 / 30) apply.
