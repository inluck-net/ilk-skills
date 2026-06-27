---
name: ilk-spec
description: >-
  Elaborate a thin, under-specified task into a detailed-enough design spec —
  for work where the missing detail is DOMAIN/INDUSTRY knowledge the user can't
  supply (not facts in their head). Researches conventions, drafts a design,
  runs an adversarial critique, gets human sign-off, and hands a
  /ilk-plan-ready spec (with verification-tier tags) to the loop. Triggers:
  "/ilk-spec", "help me spec this out", "I don't know the details",
  "flesh this out before planning". Works across Claude Code / Codex / Cursor /
  Kimi Code.
---

# ilk-spec — spec-elaboration front-stage

A planning *pre-stage* that runs BEFORE `/ilk-plan` for tasks that are too thin
to decompose, where the missing detail is **domain knowledge nobody can hand
you off the top of their head**. It researches how this class of thing is
normally built, drafts a design, attacks that design, gets your sign-off, and
hands a thick, tier-tagged task description to `/ilk-plan`.

This stage produces a **spec**, not code and not a plan. Decomposition is
`/ilk-plan`'s job; execution is the loop's.

## When to use (gate — read first)

Use ONLY when BOTH hold:

- The task is **thin / under-specified**, AND
- the missing information is **domain/industry knowledge** (how this class of
  thing is normally built), NOT details living in the user's head.

Skip this stage when:

- The user CAN answer clarifying questions (which repo, which auth, which
  deadline) → go straight to `/ilk-plan`, which already asks 1–3 clarifying
  questions for thin-but-answerable input.
- The task is already well-specified → go straight to `/ilk-plan`.

This stage costs extra model time (research + a critique pass). It is
**opt-in**, for the domain-gap case only — never a default pre-step on every
plan.

## The principle

`/ilk-plan` handles thin input by *asking the user*. That fails when the user
isn't the source of the missing knowledge. This stage fills the gap a different
way: **research the conventions, draft a design, then attack it** — and only
then hand a thick spec to planning.

The value-add over `/ilk-plan` and over plain brainstorming is two steps they
both lack: **step 3 (research industry conventions)** and **step 7 (adversarial
critique)**. Do not drop them — they are the reason this stage exists.

## Steps (in order — do not skip the approval gate)

1. **Capture the principles.** Record, verbatim, the user's thin input and any
   non-negotiables ("a tower-defense game", "must be fun first", "2D, original
   art"). These are the fixed constraints every later step must honor.

2. **Explore local context.** If a repo / docs already exist, read the parts
   relevant to the task. New project → note that and move on.

3. **Research the conventions** *(the step plain brainstorming lacks).*
   Search the web and draw on domain knowledge for *how this class of thing is
   built*. Produce a short **conventions brief**: the core systems it needs,
   2–3 common architectures, and what separates a good one from a boring one.
   For a tower-defense game that's: wave scheduler, tower taxonomy + targeting
   modes, upgrade/economy curve, enemy archetypes, pathfinding, difficulty
   balancing, save/load. **Cite sources.** Keep it tight — this informs the
   draft; it is not the deliverable.

4. **Draft the design.** From the principles + the brief, propose **2–3
   architectural approaches with trade-offs**, and recommend one. This is where
   the agent *proposes* concrete options instead of interrogating — the user
   reacts to alternatives rather than being asked questions they can't answer.

5. **Present in sections, approve per section.** Walk the design section by
   section; get a yes (or an edit) on each before moving on. Stop and ask only
   where a real fork needs the user's taste.

6. **Write the design doc** to a dated path under the project's spec dir
   (e.g. `docs/specs/YYYY-MM-DD-<topic>-design.md`). If the project has no repo
   yet, write it to a path the user designates and tell them where it landed.

7. **Adversarial critique** *(the second inserted step).*
   Dispatch a **critic subagent — a different model if the host has one** —
   tasked to *attack* the spec, not praise it: missing systems, vague
   mechanics, an un-fun or degenerate core loop, scope / feasibility risk, and
   any acceptance criterion no gate could check. Fold the survivable critiques
   back into the doc; note the ones you reject and why.
   - On a host that supports it, this critique MAY be dispatched in parallel
     with the draft's self-review by issuing **multiple dispatch calls in one
     turn**. Parallelism is an accelerator, never required — running the
     critique sequentially is an equally valid path.

8. **Self-review (mechanical).** Re-scan the doc for placeholders, internal
   contradictions, and ambiguity. Fix inline.

9. **Human final review (HARD GATE).** The user signs off on the whole spec.
   The spec is theirs to approve — this stage informs judgment, it does not
   replace it. Never auto-advance past this gate, exactly like `/ilk-plan`'s
   step-5 grouping approval.

10. **Hand off to planning.** Emit the elaborated spec as the task description
    for `/ilk-plan`. **Tag each major area by verification tier** so planning
    inherits it (tiers are defined in
    `ilk-loop/references/decomposition-principles.md` §12):
    - `loop-verified` — mechanically checkable (engine scaffold, wave spawner,
      damage calc vs spec, save/load, build + unit tests).
    - `compile-only` — compiles but has no runtime gate; needs a human pass.
    - `device-manual` — un-gateable (e.g. *is it fun?*). No `local_check`
      verifies fun; this stays a human / device pass.

## Boundary rules

- **Never skip step 9 (human approval).** It is a hard gate.
- **Never let the conventions brief (step 3) become false confidence** — a
  polished draft is still a guess; step 7 exists to puncture it.
- **This stage produces a SPEC, not code and not a plan.** Decomposition is
  `/ilk-plan`'s job; execution is the loop's.
- **Stay host-agnostic.** Phrase every runtime action as an action ("search the
  web", "dispatch a subagent", "write a file"), never as a host-specific tool.
  Do not hard-depend on any single host's orchestration tooling — parallel
  dispatch is an optional accelerator; the sequential path must always work.

## See also

- `/ilk-plan` — the planning core this stage feeds. Its step 3 points back here
  for the domain-gap case.
- `ilk-loop/references/decomposition-principles.md` §12 — verification-tier
  definitions used in step 10.
