# Auto-plan routing heuristic

When `conventions/config.yml` sets `auto_use_ilk_plan: true`, every host
agent session (Claude Code, Cursor, Codex) SHOULD route implementation
work through `/ilk-plan` instead of implementing directly, unless one of
the direct-implement conditions below applies.

## Route to /ilk-plan when ANY of these is true

- The work will not finish in this session (scope too large, many files).
- The work should run unattended (background, overnight, scheduled).
- The work needs to survive interruption (resumable across chat sessions).
- The work benefits from gated verification between steps (tests, CI,
  manual review checkpoints).
- The work spans multiple modules or layers (e.g. API + UI + migration).
- The user explicitly says "plan this" or "use ilk-plan".

## Direct-implement (skip /ilk-plan)

The route-to-/ilk-plan conditions above take precedence: if ANY of them
holds, plan. Otherwise, direct-implement when ANY of these holds:

- The task is single-shot and completable right now in one session.
- The scope is exploratory (spike, prototype, investigation).
- The deliverable is prose, docs, or config (no code logic changes).
- The user explicitly says "just do it" or "skip planning".

## How to announce and proceed

When the routing decision is clear:

1. Print ONE line: `Routing to /ilk-plan: <reason>.` or
   `Direct-implement: <reason>.`
2. Proceed automatically — do NOT pause for confirmation.

When the routing decision is ambiguous (could go either way):

1. Print: `Ambiguous routing — should I plan this or implement directly?`
2. Briefly list the factors and ask the user to choose.

## Source of truth

This file is the single source of truth for the routing heuristic.
Host-agent blocks installed by `install.sh` / `install.ps1` reference
this file; edit HERE, not in the installed copies.
