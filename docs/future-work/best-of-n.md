# Best-of-N (V2) — readiness assessment & when to use it

**Status**: future-work / not yet built. The V1.1 batch
(`MASTER-2026-06-06-scheduler-v1-1`) lays the *substrate*; V2 is a separate,
design-heavy effort. **Last touched**: 2026-06-06.

**Origin**: discussion after building the cross-project scheduler (V1) and its
slot-pool successor (V1.1). The slot abstraction was deliberately shaped so
best-of-N is an additive next step rather than a rewrite. This doc records how
ready we are, what's still missing, and — importantly — **which kinds of plans
actually warrant best-of-N**, because most do not.

---

## What "best-of-N" means here

Run the **same task N times in parallel**, each attempt isolated in its own git
**worktree** + its own **worker home** (optionally a **different model**), then
**evaluate** the N candidate results and **merge/cherry-pick the winner**,
discarding the rest.

Two flavors:

- **Run-diverse**: same model, N attempts — exploits run-to-run variance.
- **Model-diverse**: N *different* models/providers on the same task — exploits
  model strengths, and doubles as a way to benchmark a cheap provider against a
  strong one on real work. *(This is the flavor we specifically want to try.)*

---

## Readiness after V1.1

### ✅ The substrate V1.1 provides
- **Per-slot isolated worker homes** (`~/.claude-worker-<i>`, cloned from base)
  — the N-way isolation best-of-N requires.
- **`-WorkerHome` / `CLAUDE_WORKER_HOME` launcher override** — point any run at
  any slot home.
- **Slot pool + concurrency accounting** (`-MaxConcurrent`, live-slot counting)
  — the parallel execution engine.
- **Per-slot model hook** in the slot-home bootstrap (accepted, currently
  inert) — the seam for model-diverse.
- **Master-queue promotion correctness** — multi-master draining works.

### ❌ Net-new for V2 (NOT in V1.1)
1. **Git worktree lifecycle** — create N worktrees/branches of *one* repo, route
   each slot's cwd to its worktree, tear them down after.
2. **A fan-out driver** — V1.1's scheduler unit is *"a project with queued
   work"*; V2's unit is *"N attempts of one task."* That's a different
   orchestration mode (1 task → N parallel runs), a new driver, not a tweak.
3. **Wire the per-slot model** — V1.1 only *accepts* the hook; V2 writes per-slot
   `settings.json` with distinct `ANTHROPIC_MODEL`/`ANTHROPIC_BASE_URL` and
   verifies routing.
4. **The evaluation / winner-selection stage** — *the real V2 problem, and the
   least specified.* After N attempts produce N candidate branches, how is the
   winner chosen? Test pass-rate? A judge agent? A metric? Everything downstream
   depends on this.
5. **Merge-back + git concurrency** — "ship" changes meaning (pick one branch,
   merge to main, discard the rest); plus verifying concurrent commits across
   worktrees sharing one `.git` are safe.
6. **Runtime keying for worktrees** — each worktree path resolves to a *different*
   `project_key`, so plan/runtime placement for "N attempts of the same plan" is
   a design decision (shared plan, N runtimes).

### Verdict
After V1.1 we are **ready to *design* V2, not to *implement* it blind.** The
infrastructure delta (worktrees + fan-out + model wiring) is mostly mechanical
and builds cleanly on V1.1; the **evaluation + merge** stage is novel and should
be settled first.

---

## Suggested V2 sequencing

1. **Pre-req (cheap insurance):** actually run V1.1's slot pool on two real
   concurrent projects for a while — validates isolated-home + shared-disk
   behavior under real load before V2 leans harder on it.
2. **Design spike:** nail the **evaluation strategy** (#4 above). It drives the
   worktree/merge model. Decide the oracle(s): test suite, build/typecheck,
   benchmark, lint, or a reviewer/judge agent (or a combination + tie-breakers).
3. **V2a — mechanics:** worktree lifecycle + fan-out driver + per-slot model
   wiring. Builds directly on V1.1 slots.
4. **V2b — selection:** evaluation + merge-the-winner + cleanup. The design-heavy
   part.

---

## When to use best-of-N — and when NOT to

**Best-of-N costs N× tokens and adds orchestration + merge overhead. It is a
quality lever for a minority of plans, not a default.** Reach for it only when
*all three* hold:

1. **High outcome variance** — the task is open-ended / hard enough that
   independent attempts genuinely differ in quality.
2. **A cheap, objective oracle exists** — you can pick the winner mechanically
   (tests, benchmark, build, type/lint, a measurable metric). Without this,
   best-of-N just multiplies human review by N.
3. **The task is high-value enough to justify N× spend.**

### Good use cases (DO use best-of-N)
- **Hard bugfixes where the first fix often misses** — generate N candidate
  patches, keep the one that turns the failing test green. Strong oracle (the
  test), high variance.
- **Open-ended design / refactor of a gnarly module** — no single right answer;
  attempts vary a lot. Oracle = full test suite + build, tie-broken by a
  reviewer agent or human eyeball at ship.
- **Algorithmic / tricky-logic tasks** — correctness varies run-to-run; the unit
  tests are the oracle.
- **Performance optimization** — N approaches, pick the fastest that still passes
  correctness. The benchmark is the oracle.
- **Model-diverse "which model is best for this task?"** — run the same task on
  Opus vs the cheap worker vs another provider; compare on the oracle. Also the
  honest way to validate that a cheap provider is "good enough" for a class of
  work before defaulting to it.
- **Flaky / under-specified surfaces** where one shot frequently stalls —
  parallel attempts raise the odds one lands.

### Poor use cases (do NOT — single-shot instead)
- **Mechanical / deterministic changes** — renames, dependency bumps, formatting,
  codemods, config edits. One correct outcome; N attempts waste N× cost.
- **Well-specified CRUD / boilerplate with tight ACs** — the normal loop gets it
  first try.
- **No cheap objective evaluator** — if telling the winner apart needs expensive
  human judgment, best-of-N multiplies that burden instead of saving it.
- **Sequential / stateful work that can't be cleanly isolated** per worktree
  (e.g. steps that mutate shared external state).
- **Routine, cost-sensitive throughput** — best-of-N is the opposite of cheap;
  reserve it for the high-variance, high-value minority. For everyday volume, the
  cheap single worker (default `claude-worker`) is the right tool.

### One-line heuristic
> Use best-of-N when **variance is high AND a cheap oracle can pick the winner
> AND the task is worth N× the cost.** Otherwise single-shot on the worker.

Practically, this means best-of-N should be **opt-in per plan/sub-plan** (e.g. a
frontmatter flag like `best_of_n: 3` with an `oracle:` spec), never a global
default — most sub-plans in a normal batch should remain single-shot.

---

## See also
- `docs/future-work/cross-project-supervisor.md` — V1 (shipped) / V2 lineage.
- `~/.ilk-data/.../plans/MASTER-2026-06-06-scheduler-v1-1-execution-plan.md` —
  the slot-pool substrate + the "V2 migration target" section.
- `skills/ilk-launcher/references/worker-engine.md` — engine routing + `-WorkerHome`.
