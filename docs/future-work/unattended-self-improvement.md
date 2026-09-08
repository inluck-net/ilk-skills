# Unattended self-improvement (future work)

**Status**: **partially built.** Every stage exists in some form; the pipeline is
gated at two deliberate human points (`draft`, `supervised_only`). **Blockers 2
and 3 are closed** — built in v0.9.88 and exercised end to end in v0.9.89, not
merely built. Blocker 1 (self-modification race) has machinery built but **not
wired**; the cutover is still open. **Blocker 4 (progress is self-reported) was
found on 2026-09-08 and is open** — and it is the one that survives closing the
other three.
**Last touched**: 2026-09-08 (v0.9.89)
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
| release | `/ilk-ship` Phases 0-3 | **Phase 0 hardened v0.9.89** — its hard stop had a hole: the ledger union trusted a self-derived step range as proof of a commit |
| deploy | `/ilk-ship` Phase 4, per-host, ssh-capable | **fixed v0.9.87; tag conformance v0.9.88, demonstrated v0.9.89** |

`/ilk-self-improve`'s own SKILL.md states the boundary today: *"This skill is a
planner, not an executor. It produces a plan; a human releases and runs it."*
That sentence is what this design would change, and it should not be changed
until the four blockers below are closed.

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

**Re-scored 2026-09-08 against a batch that actually ran through the loop, and
the headline row is a failure.** Two rows of the table above are now covered —
the "resolve, don't type" lint shipped in v0.9.88 and `plan_lint` enforced it
while authoring the v0.9.89 batch, and the at-base rerun was executed for real
(31 failing node ids, every one failing at base too, 0 attributed). But the
batch produced a *new* error, and it is the first one drawn from inside the
loop's own rails rather than from hand-driving:

| error, 2026-09-08 | mechanically caught? |
|---|---|
| The loop set a sub-plan `status: shipped` without performing its step 2 or writing either marker commit — twice, the second time in a 243s iteration with **zero commits** | **no.** Phase 0 returned `proven: True, missing_steps: []` while `git log --all --grep` found **0** commits for both `#step-2` and `#ship`. An unattended run would have tagged and deployed it. Caught by hand-reading a commit log; the audit hole was fixed mid-release (v0.9.89) |

The counterfactual argument below still holds for the *hand-driving* errors —
but it does not extend to this one. This error came from the rails, not from
working outside them, and it is what Blocker 4 exists to record.

**But score the counterfactual too, because it does not point the same way.**
Most of those errors came from working *outside* the loop's rails. An
unattended run does not hand-type a suite invocation — it runs `ship.suite`
through `run_local_checks.py`. Both releases that day carried **no batch
verdict at all** precisely because the work was direct-implemented; a loop batch
records one via `batch_gate.py`, and Phase 1 would then have had a real verdict
to verify instead of assembled substitute evidence. On the specific axis of
"is the evidence machine-produced", the unattended path is **better**
instrumented than the hand path that produced v0.9.86 and v0.9.87.

So the conclusion is not "too risky". It is: **four specific things must be
built, and neither the first nor the fourth is a policy choice.** Blockers 2 and
3 are now closed; 1 and 4 are not.

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

**Machinery status (2026-09-08):** `selfmod_worktree.py` implements the
worktree lifecycle (create/reuse/remove), liveness detection via `pgrep`
(fail-closed: unknown liveness ⇒ do not merge), merge under an exclusive
lock with branch-movement detection, and `MergeBlockedError` /
`BranchMovedError` / `WorktreeDirtyError` for every failure mode.
The module is tested (14 tests, `test_selfmod_worktree.py`), but **the loop
is not yet wired to it** — the cutover is a separate step taken when no
batch is in flight. See sub-plan `a-selfmod-batch-runs-in-a-worktree`.

**The lock is only half of it: the exclusivity mechanism must not mistake a
collaborator for an intruder.** A worktree plus a merge lock answers "is anyone
else running". It does not answer "did someone else legitimately touch this
branch while I was working". A consumer project supplied the first measured
instance on 2026-09-07: its resolver flags any branch commit whose author email
differs from the single identity it resolves for itself, and the automation and
the human behind it commit under two different addresses of the *same* account
(a noreply address and a work address). A human resolving a merge conflict
therefore read as a hostile takeover — and the stand-down path was wired to an
irreversible outward action, which closed a green ten-commit PR.

Two rules fall out, and they generalise past that project:

- **A collaborator is not an intruder.** Any guard that decides ownership from a
  single identity is wrong the moment a second legitimate actor exists, and
  "another agent will resolve it anyway" is the standing expectation here.
  Ownership needs a *set* of trusted identities, or a marker that does not
  depend on authorship at all.
- **Never wire an uncertain verdict to an irreversible action.** A wrong
  takeover verdict is survivable; a wrong verdict that closes a PR is not. Make
  the stand-down reversible before making the guard smarter — the ordering
  matters, because a smarter guard still has a failure rate.

Whether the toolkit should own a trusted-identity model is **open and not
decided here.** Measured 2026-09-07: 0 of 412 `.py`/`.md`/`.sh` files under
`skills/`, `commands/` and `docs/` carry any commit-author-identity concept, and
0 carry a takeover or foreign-claim concept — claim/stand-down is a consumer
product concept, and the toolkit's own exclusivity is process-level
(`ilk_run_lock.py`, an flock held across exec, plus the scheduler's per-project
sentinel mutex). Building a toolkit-level identity model on one instance would
be abstracting ahead of the second consumer. Recorded here so that when a second
one appears, the promotion is a decision with prior art rather than a rediscovery.

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
- **Baseline** — **done, as of v0.9.89.** `.ilk-baselines/` holds
  `v0.9.87__62005b14ac04.json` (stored during v0.9.88's release, after Phase 1
  refused that release on `could_not_compare`) and `v0.9.88__62005b14ac04.json`
  (stored during v0.9.89's, from that batch's own at-base measurement of the
  tagged commit — same host, same resolved invocation, so no suite was re-run
  to learn something already measured). v0.9.89's baseline-diff returned
  `FOUND`: **0 regressions across 2829 collected tests vs v0.9.88, 0 new
  failures, 10 fixed.** The claim this bullet used to carry — "until it lands,
  every toolkit release hits `could_not_compare`" — no longer holds on this
  host.

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

## Blocker 4 — progress is self-reported, and nothing binds it to an artifact

**Found 2026-09-08, during the v0.9.89 release. Open.** This is the blocker that
survives closing the other three: with 1, 2 and 3 all shut, an unattended run
that day would still have tagged and deployed a batch containing a sub-plan
that was never completed.

What happened. Sub-plan `a-productive-timeout-is-not-a-barren-one` authored four
steps. Its step 2 was **contingent** — "if step 1 introduced a new label, add
its arm to `classify_action`" — and step 1 correctly introduced none, recording
that decision in its Findings as a labelled judgment call. So step 2 was
genuinely vacuous. The loop then set `status: shipped, current_step: 4` and
never committed anything for step 2 or for the ship transition. Measured:
`git log --all --grep` returns **0** commits for `[plan:…#step-2]` and **0** for
`[plan:…#ship]`, while the sub-plan reported `shipped`.

The chain has four links, and only the last one is fixed:

1. **Progress is self-reported.** `skills/ilk-loop/SKILL.md:200-215` instructs
   the *worker* to bump `current_step` in the sub-plan front-matter and commit
   it. The driver writes plan status in **0** places — grep
   `run_ilk_loop_claude.sh` for a plan-status write — it only reads it back
   through `loop_status`. The claim and its auditor read the same self-report.
2. **A gate proves the tree, not the step.** Step 2's three gates pass on
   unchanged state; measured with the step never performed,
   `test_watchdog_action_vocab.sh` exits 0 and `test_iteration_outcome.sh`
   reports 0 failed. Performing a step and skipping it are observationally
   identical to the gate.
3. **A contingent step carried no empty-marker instruction.** The
   batch-verification template states one for its own no-op step ("commit the
   empty marker and move on"); a hand-authored contingent step in a generated
   sub-plan did not, so there was no artifact prescribed for the judgment
   "this step is vacuous". This is a planner-discipline gap, not a driver bug.
4. **The audit trusted the self-report back.** `ship_audit`'s ledger union
   treated a record's `[step_from, step_to)` range as proof of a commit — and
   the driver derives that range from the worker's own `current_step`
   (`run_ilk_loop_claude.sh:2267`), so the range restated the claim under
   audit. A record claiming `step_from: 0, step_to: 4`, whose own `commits`
   list held 3 shas, made `missing_steps` come back `[]`. **Fixed in v0.9.89**:
   the union is scoped to the trailerless (shared-remote) regime it was
   authored for, so when a slug carries trailers, a step without one is a real
   gap.

**Relaunching is not a repair path, and that was measured too.** The sub-plan
was reopened to `current_step: 2` and re-dispatched by the scheduler; the second
run (`20260908-143046`, 1 iteration, 243s) produced **zero commits** and set
`shipped` again. Two identical outcomes with nothing different between the
attempts is a design defect, not a flake — the same reasoning recorded in
`relaunch-fixes-state-not-step-design`.

**What would close it.** Any one of these breaks the self-report loop; the first
is the cheapest and the most direct:

- **A per-step commit-presence check in the driver.** After a step's gate
  passes, require a commit carrying that step's trailer before advancing
  `current_step`. A vacuous step then costs one empty commit, which is the
  convention already in use elsewhere.
- **A planner rule** that any contingent step ("if X, then …") must state its
  empty-marker commit for the case where X is false. Enforceable in
  `plan_lint`.
- **Requiring a `#ship` commit for a `shipped` sub-plan.** Attempted in
  v0.9.89 and deliberately **not** shipped: it broke 8 tests across 3 files
  that assert PROVEN for a trailered sub-plan without one, so `proven` currently
  means "a commit per authored step" by contract. Changing that is a contract
  decision, and it is pinned as a `strict=True` xfail in
  `skills/ilk-loop/tests/test_ship_audit_ledger_gap.py` rather than left to be
  rediscovered.

**Why this gates removing the human.** The other three blockers are about the
machinery being *available* — a worktree to run in, a Phase 1 that can execute,
a deploy state that means what it says. This one is about the machinery being
*truthful* about work it performed itself. A pipeline can have all three and
still ship a claim, and the only thing that caught it here was a human reading
a commit log.

## What should stay gated even when all four close

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

Four of the five original items are done. Kept with their outcomes rather than
deleted, so the ordering that produced them stays legible.

1. ~~`plan_lint` rule + template bullet: a verification sub-plan must
   **resolve** the suite command
   (`ship_audit._resolve_expected_invocation`).~~ **Done, v0.9.88**
   (`lint_verification_subplan_hardcodes_suite`), and it enforced the v0.9.89
   batch's authoring rather than sitting unused.
2. ~~The serial baseline, which unblocks Phase 1's second engine.~~ **Done** —
   v0.9.87's baseline stored during v0.9.88's release, v0.9.88's during
   v0.9.89's. Phase 1's second engine has now run for real twice.
3. ~~`suite_timing.py` — serial vs `-n N` with outcome-set equality gating
   speed.~~ **Done, v0.9.88** (713 lines, refuses to measure a loaded box).
4. **Worktree isolation + merge lock for self-modifying batches (Blocker 1).
   Machinery built; cutover STILL OPEN.** Re-measured 2026-09-08:
   `selfmod_worktree` appears 25 times across `skills/` and `tools/`, in
   exactly **2 files** — the module and its test — so no driver calls it. Every
   sub-plan of the v0.9.89 batch edited the clone that live consumer loops
   execute. **This is now the only original item left.**
5. ~~A deploy state that names the tag, not just daemon freshness
   (Blocker 3).~~ **Done, v0.9.88; demonstrated v0.9.89** —
   `host_deploy_status.py --require-tag` reported `chad-mbp: ok (local)` /
   `rezmac: ok (ssh)` with exit 0, and rezmac read `tag-mismatch` until it was
   genuinely on the release, which is the discrimination this item asked for.
6. **NEW — bind progress to an artifact (Blocker 4).** A per-step
   commit-presence check in the driver before `current_step` advances, plus a
   `plan_lint` rule requiring a contingent step to state its empty-marker
   commit. Cheapest of the three candidate fixes in that section, and the one
   that does not require a contract change.

One reporting note for whoever runs the next release: a
`host_deploy_status.py --bounce-hosts` invocation reports the **pre-bounce**
state. Both hosts printed `tag-mismatch` on the bouncing run and `ok` on the
next detect-only check, with nothing changed in between. It errs toward
refusing, so it is not a false `ok` — but the bouncing run's own output is not
the deploy verdict.

## See also

- `docs/future-work/cross-project-supervisor.md` — the scheduler this would
  ride on; V1 shipped, V2 (parallel workers) still open.
- `docs/ship-gate-design.md` — the ship gate's own defect history.
- `skills/ilk-self-improve/SKILL.md` — the planner/executor boundary this
  design would move.
- `skills/ilk-loop/templates/batch-verification-subplan.md` — the at-base rerun
  contract that makes an unattended verdict trustworthy.
