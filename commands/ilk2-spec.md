Elaborate a thin, under-specified task into a detailed-enough design spec,
then hand it to `/ilk2-plan`. Anything the user typed after `/ilk2-spec` in
the chat is the thin task description.

This is a planning **pre-stage**, not the planner. Use it ONLY for the
domain-gap case: the task is thin AND the missing detail is domain/industry
knowledge nobody can supply off the top of their head (how this *class* of
thing is normally built). It researches conventions, drafts a design, attacks
that design, gets human sign-off, and emits a tier-tagged, `/ilk2-plan`-ready
task description.

This is the **ilk2-native** spec pre-stage, built beside `/ilk-spec` until
the switch (docs/07 §7.4.2). Spec elaboration and review are unchanged —
the 10-step recipe still lives in `<skill-root>/ilk-spec/SKILL.md`. What
changes is the hand-off and the lint: the approved spec is emitted as the
task description for `/ilk2-plan` (which drafts under `~/.ilk2/plans/` and
delivers with `ilk2 submit`), and the pillar lint runs the ilk2 checker when
that checker is installed.

> **When NOT to use:** if the user can answer clarifying questions (which repo,
> which auth, which deadline), or the task is already well-specified, skip this
> and go straight to `/ilk2-plan` — it already asks 1–3 clarifying questions for
> thin-but-answerable input. This stage costs extra model time; it is opt-in.

Follow these steps in order. Do NOT skip the human-approval gate.

## 1. Load the recipe

Read the authoritative steps and boundary rules:

- `<skill-root>/ilk-spec/SKILL.md` — the 10-step spec-elaboration recipe. Its
  steps 3 (research conventions) and 7 (adversarial critique) are the value-add
  over `/ilk2-plan` and plain brainstorming — do not drop them.
- `/ilk2-plan` (`commands/ilk2-plan.md`) — the planner this stage feeds. Read
  its spec-input validation (step 3) and its planning rules, so the spec you
  emit is one it can group.

## 2. Read the task description

Whatever the user typed after `/ilk2-spec` is the thin task. If empty:

- Stop and ask: "What's the under-specified task you'd like me to spec out?"

Confirm the gate before spending the research budget: is the missing
information **domain knowledge** (good fit) or **facts in the user's head**
(wrong tool — redirect to `/ilk2-plan`)? If it's the latter, say so and stop.

## 3. Run the recipe

Execute steps 1–10 from `ilk-spec/SKILL.md` in order:

- Capture principles → explore local context → **research the conventions
  (cite sources)** → draft 2–3 approaches with trade-offs → present per
  section → write the design doc → **adversarial critique (a different model if
  the host has one)** → mechanical self-review → **human final review** →
  hand off.

Phrase every runtime action as an action ("search the web", "dispatch a
subagent", "write a file"). Parallel dispatch (e.g. running the critique
alongside self-review by issuing multiple dispatch calls in one turn) is an
optional accelerator — the sequential path must always work.

## 4. Human-approval gate (HARD — do not auto-advance)

Step 9 of the recipe is a hard gate, exactly like `/ilk2-plan`'s step-5 grouping
approval. The user signs off on the whole spec before anything is handed to
planning. This stage informs judgment; it does not replace it. Never skip it.

## 5. Hand off to `/ilk2-plan`

Once the user approves, emit the elaborated spec as the task description for
`/ilk2-plan`, with **each major area tagged by verification tier**
(`loop-verified` / `compile-only` / `device-manual`) so planning inherits the
tiers. Then tell the user they can run `/ilk2-plan` with that description (or
offer to do it in the same turn).

`/ilk2-plan` then looks the project up in ilk2's registry, drafts a MASTER
plus sub-plans under `~/.ilk2/plans/<project>/<date>/`, runs its own grouping
approval and QC, and delivers with `ilk2 submit`. **This stage writes no plan
files and submits nothing** — its only output is the spec (recipe step 6's
design doc) and the task description handed to `/ilk2-plan`.

### Pillar → outcome-AC traceability (enforced by the spec lint)

Every pillar block in the spec MUST carry:

1. A **`verification_tier`** tag (or `tier:` shorthand) — one of
   `loop-verified`, `compile-only`, `device-manual`.
2. At least one **outcome-level AC** — a line starting with `- **AC` that
   asserts the player/user-facing outcome (e.g. "player can upgrade a tower
   through the inspector"), not just that the artifact compiles or unit-tests
   pass.

A pillar is NOT "done" when only its model layer is gated. The lint enforces
this structurally: it warns on pillars missing a tier tag or having only
compile-level ACs. Run it before handing off to `/ilk2-plan`:

```bash
python3 skills/ilk2-plan/scripts/plan_check.py --spec <spec-file>
```

- **If `skills/ilk2-plan/scripts/plan_check.py` is not installed**, skip the
  lint and say so in the report ("spec lint skipped: plan_check.py not
  installed"). Do not substitute V1's `plan_lint.py` — it is a V1 script and
  not this pre-stage's tool.
- Findings are warnings: fix the obvious ones inline, and surface the rest to
  the user before hand-off.

## Boundary rules

- **Never skip step 4 (human approval).**
- **Output is a spec, not a plan and not code.** Decomposition is `/ilk2-plan`'s
  job; execution is the loop's.
- **Don't let the conventions research become false confidence** — the
  adversarial critique exists to puncture a polished-but-wrong draft.
- **Stay host-agnostic** — no hard dependency on any single host's
  orchestration tooling.
- **Never write plan files from this stage** — drafts live under
  `~/.ilk2/plans/` and are written by `/ilk2-plan`, which delivers with
  `ilk2 submit`. Recipe step 6's design doc is a spec and stays where the
  recipe puts it.

## See also

- `/ilk2-plan` — the planning core this stage feeds.
- `<skill-root>/ilk-spec/SKILL.md` — the authoritative recipe.
