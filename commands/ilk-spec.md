Elaborate a thin, under-specified task into a detailed-enough design spec,
then hand it to `/ilk-plan`. Anything the user typed after `/ilk-spec` in the
chat is the thin task description.

This is a planning **pre-stage**, not the planner. Use it ONLY for the
domain-gap case: the task is thin AND the missing detail is domain/industry
knowledge nobody can supply off the top of their head (how this *class* of
thing is normally built). It researches conventions, drafts a design, attacks
that design, gets human sign-off, and emits a tier-tagged, `/ilk-plan`-ready
task description.

> **When NOT to use:** if the user can answer clarifying questions (which repo,
> which auth, which deadline), or the task is already well-specified, skip this
> and go straight to `/ilk-plan` — it already asks 1–3 clarifying questions for
> thin-but-answerable input. This stage costs extra model time; it is opt-in.

Follow these steps in order. Do NOT skip the human-approval gate.

## 1. Load the recipe

Read the authoritative steps and boundary rules:

- `<skill-root>/ilk-spec/SKILL.md` — the 10-step spec-elaboration recipe. Its
  steps 3 (research conventions) and 7 (adversarial critique) are the value-add
  over `/ilk-plan` and plain brainstorming — do not drop them.

## 2. Read the task description

Whatever the user typed after `/ilk-spec` is the thin task. If empty:

- Stop and ask: "What's the under-specified task you'd like me to spec out?"

Confirm the gate before spending the research budget: is the missing
information **domain knowledge** (good fit) or **facts in the user's head**
(wrong tool — redirect to `/ilk-plan`)? If it's the latter, say so and stop.

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

Step 9 of the recipe is a hard gate, exactly like `/ilk-plan`'s step-5 grouping
approval. The user signs off on the whole spec before anything is handed to
planning. This stage informs judgment; it does not replace it. Never skip it.

## 5. Hand off to `/ilk-plan`

Once the user approves, emit the elaborated spec as the task description for
`/ilk-plan`, with **each major area tagged by verification tier**
(`loop-verified` / `compile-only` / `device-manual`) so planning inherits the
tiers. Then tell the user they can run `/ilk-plan` with that description (or
offer to do it in the same turn).

### Pillar → outcome-AC traceability (enforced by `plan_lint.py --spec`)

Every pillar block in the spec MUST carry:

1. A **`verification_tier`** tag (or `tier:` shorthand) — one of
   `loop-verified`, `compile-only`, `device-manual`.
2. At least one **outcome-level AC** — a line starting with `- **AC` that
   asserts the player/user-facing outcome (e.g. "player can upgrade a tower
   through the inspector"), not just that the artifact compiles or unit-tests
   pass.

A pillar is NOT "done" when only its model layer is gated. The linter
(`plan_lint.py --spec <spec-file>`) enforces this structurally: it warns on
pillars missing a tier tag or having only compile-level ACs. Run it before
handing off to `/ilk-plan`.

## Boundary rules

- **Never skip step 4 (human approval).**
- **Output is a spec, not a plan and not code.** Decomposition is `/ilk-plan`'s
  job; execution is the loop's.
- **Don't let the conventions research become false confidence** — the
  adversarial critique exists to puncture a polished-but-wrong draft.
- **Stay host-agnostic** — no hard dependency on any single host's
  orchestration tooling.

## See also

- `/ilk-plan` — the planning core this stage feeds.
- `<skill-root>/ilk-spec/SKILL.md` — the authoritative recipe.
