# Unattended self-improvement (future work)

**Status**: **not built.** Every stage exists in some form; the pipeline is
gated at two deliberate human points (`draft`, `supervised_only`) and blocked
at one structural point (the self-modification race, below).
**Last touched**: 2026-09-07
**Origin**: asked directly after a session that carried an ilk-skills defect
from discovery through two releases and a two-host deploy, entirely by hand —
v0.9.86 (verification attribution) and v0.9.87 (Phase 4 ssh). The question was
whether that day could run without a human in it.

## The question

Can the improvement loop run end to end unattended — a defect filed by any
session, planned, implemented, verified, released and deployed — with no human
in the path?

## What already exists

Most of it. The stages are not the gap.

| stage | mechanism | state |
|---|---|---|
| capture | `/ilk-feedback` writes candidates to `~/.ilk-data/ilk-skills-improvements/candidates.json` | exists |
| adapt | `/ilk-self-improve` reads the backlog, formats a task | exists |
| plan | `/ilk-plan`, auto-gating self-modifying masters `draft` + `supervised_only` | exists |
| execute | the loop, step-recoverable, `run_local_checks.py` gates | exists |
| verify | batch-verification sub-plan; at-base rerun + non-defeasible gate | **hardened v0.9.86** |
| release | `/ilk-ship` Phases 0-3 | exists |
| deploy | `/ilk-ship` Phase 4, per-host, ssh-capable | **fixed v0.9.87** |

`/ilk-self-improve`'s own SKILL.md states the boundary today: *"This skill is a
planner, not an executor. It produces a plan; a human releases and runs it."*
That sentence is what this design would change, and it should not be changed
until the three blockers below are closed.

## The readiness test

Not "does the machinery exist" — it mostly does. The test is:

> **Would the machinery have caught the mistakes a human caught?**

The 2026-09-07 session is a usable dataset because the operator intervened four
times and the assistant erred about eight. Scored honestly:

| error that day | mechanically caught? |
|---|---|
| Verification sub-plan recorded "Attributed regressions: 0" over 2 real regressions | **yes, as of v0.9.86** (`lint_verification_attribution_unmeasured`) |
| Full suite run with another repo's `-n 8 --dist loadfile`, twice | no — the "resolve, don't type" lint is proposed, not built |
| The invocation mismatch was written down, then repeated on the next run | no — self-recognition; `~/.claude/CLAUDE.md` records that failing five times |
| `[exit 0]` read from `tail` through a pipe | no |
| A background task's "completed (exit code 0)" that was a trailing `echo`'s status | no |
| An edit script that failed to parse and silently applied nothing | no |
| A 2h idle-gated wait armed without checking a 30-iteration batch had just started | no |
| A baseline nearly stored under an invocation it was not measured with | no |

**1 of 8.** That ratio, not the stage inventory, is the argument for waiting.

**But score the counterfactual too, because it does not point the same way.**
Most of those errors came from working *outside* the loop's rails. An
unattended run does not hand-type a suite invocation — it runs `ship.suite`
through `run_local_checks.py`. Both releases that day carried **no batch
verdict at all** precisely because the work was direct-implemented; a loop batch
records one via `batch_gate.py`, and Phase 1 would then have had a real verdict
to verify instead of assembled substitute evidence. On the specific axis of
"is the evidence machine-produced", the unattended path is **better**
instrumented than the hand path that produced v0.9.86 and v0.9.87.

So the conclusion is not "too risky". It is: **three specific things must be
built, and the first is not a policy choice.**

## Blocker 1 — the self-modification race (structural)

Installed skills are **directory symlinks into the toolkit clone**. Measured
2026-09-07: `install.sh` dry-run reported **0 would-change lines** on the
machine holding the clone, because the working tree *is* the installed code.
Meanwhile 4-5 `run_ilk_loop_claude.sh` processes belonging to another project
were executing scripts out of that same clone for the whole session.

An unattended self-improvement batch therefore edits files that other live
loops are part-way through executing. This is the hazard already recorded as
`cross-project-toolkit-selfmod-hazard`; it is a race, not a preference, and no
amount of gating policy removes it.

**Design that closes it:** the improvement batch runs in a **git worktree**, not
the clone. Consumer loops keep executing the stable clone throughout. The
merge back into the clone is the only moment requiring exclusivity, and it is
short enough to hold a lock across — the same per-project sentinel mutex the
scheduler already uses for its FIFO drain would serve. Merge only when no
consumer loop is live; otherwise queue the merge.

Note this also changes what "deploy" means for the toolkit: the merge to the
clone *is* the deploy on the host holding it, which is why Blocker 3 matters.

## Blocker 2 — Phase 1 must actually run

Both Phase 1 engines were unavailable for **both** releases that day:

- batch verdict `stale_head` — `batch-gate.json` held the tip of a batch
  released five tags earlier;
- baseline-diff `could_not_compare` — no baseline for the previous tag on that
  host (newest was v0.9.81 against a v0.9.86 HEAD).

Each release then proceeded on substitute evidence chosen and labelled by a
human-readable judgment call. Unattended, nothing audits that judgment, and an
agent assembling its own substitute evidence for its own batch is the exact
shape of the defect v0.9.86 exists to prevent.

Two halves, one already free:

- **Batch verdict** — solved by construction if the work runs *as a loop batch*
  rather than direct-implement. The loop records a verdict at the batch tip.
- **Baseline** — parked as inbox item `2026-09-07 — ilk-skills-serial-suite-baseline`.
  Until it lands, every toolkit release hits `could_not_compare`.

Unattended release should **refuse** rather than substitute: a missing or stale
verdict, or a `could_not_compare` baseline, halts and files rather than ships.

## Blocker 3 — deploy cannot verify itself

Phase 4's `ok` means "this host's daemon matches **this host's own HEAD**". It
does **not** mean "this host is on the release". Measured 2026-09-07: rezmac sat
at v0.9.86 while v0.9.87 existed and reported `ok (ssh)`; only after it pulled
did it correctly read `stale-daemon`.

So a host that never pulls reports success forever. This is the third member of
a family — v0.9.74 (failed `bootstrap` read as `ok`) and v0.9.87 (a host never
contacted read as `ok`) are the other two. Same shape each time: **a state
meaning "checked and current" returned on a path that did not check the thing
that matters.**

Unattended deploy needs a state that asserts *"host is at tag X, with a daemon
running tag X's code"*, and a report where a host that is merely reachable
cannot be confused with a host that is deployed.

## What should stay gated even when all three close

Not a blanket human gate — a **staged deploy with a canary**, which the toolkit
already has the shape for (two hosts, and prior art in the gh-resolve batch's
"re-enable the canary after the batch ships and is verified"):

1. merge + deploy to the canary host,
2. watch one full dispatch cycle there,
3. deploy to the second host only if the cycle is clean,
4. roll back to the previous tag automatically if it is not.

Pushing a tag remains outward-facing, but for a private toolkit repo it is the
low-risk half. **Deploying to the two hosts that run everything else is the
high-risk half**, and that is what the canary stage is for.

## Trigger conditions (when to actually build this)

Following the convention of `cross-project-supervisor.md` — do not build until
ONE of these is true:

- The improvement backlog regularly holds candidates that sit unstarted for
  more than a few days because no one had a session to spend on them.
- The same class of toolkit defect is found and hand-fixed **three times**,
  indicating the loop would have caught it cheaper.
- A measured majority of a session's hand-driven toolkit fixes would have been
  caught by existing lints — i.e. re-score the readiness table above and get
  something better than 1 of 8.

Until then the honest ordering is: **build the lints that would have caught the
last set of mistakes, then remove the human.** Each lint built raises the
readiness score by a countable amount, which makes the decision measurable
rather than a matter of confidence.

## Nearest next steps, in dependency order

1. `plan_lint` rule + template bullet: a verification sub-plan must **resolve**
   the suite command (`ship_audit._resolve_expected_invocation`), never
   hard-code or restate one. Directly scores one row of the table.
2. The serial baseline (inbox item), which unblocks Phase 1's second engine.
3. `suite_timing.py` — serial vs `-n N` with **outcome-set equality gating
   speed**, writing a dated artifact. Settles whether the declared invocations
   on both projects are correct at all.
4. Worktree isolation + merge lock for self-modifying batches (Blocker 1).
5. A deploy state that names the tag, not just daemon freshness (Blocker 3).

## See also

- `docs/future-work/cross-project-supervisor.md` — the scheduler this would
  ride on; V1 shipped, V2 (parallel workers) still open.
- `docs/ship-gate-design.md` — the ship gate's own defect history.
- `skills/ilk-self-improve/SKILL.md` — the planner/executor boundary this
  design would move.
- `skills/ilk-loop/templates/batch-verification-subplan.md` — the at-base rerun
  contract that makes an unattended verdict trustworthy.
