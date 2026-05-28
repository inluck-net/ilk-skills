# Execution Plans (Master + Sub-Plans, ilk-Loop)

This folder hosts execution plans for this project, organised as a
**master plan** that indexes several **sub-plans**. Each sub-plan is a
sequence of small, atomic steps. The whole system is designed so that any
fresh chat session can resume the work from disk, without re-deriving
context — see `<skill-root>/ilk-loop/SKILL.md` (where `<skill-root>` is `~/.cursor/skills`, `~/.claude/skills`, or `~/.codex/skills` depending on the host) for the full
convention.

## Convention

```
docs/plans/
├── README.md                                  ← this file
├── MASTER-YYYY-MM-DD-execution-plan.md        ← strategic index for one batch
└── YYYY-MM-DD-<short-slug>.md                 ← one sub-plan per workstream
```

- **Master plan** per triage batch. Groups tickets into workstreams,
  records dependencies, defines execution order, registers every sub-plan.
- **Sub-plan** per workstream. Owns a list of tickets and a sequenced list
  of implementation steps with one commit per step.
- Sub-plans are **idempotent and resumable** — agents (or humans) can stop
  mid-flight and another loop iteration picks up from `current_step`.

## Sub-plan front-matter

```yaml
---
plan: <short-slug>
status: pending | in-progress | shipped | blocked
current_step: 0
tickets:
  - T-YYYY-NNNN
priority: P0 | P1 | P2 | P3
estimated_steps: <N>
last_updated: YYYY-MM-DD
---
```

## Driving the loop

From inside this project (or any sub-directory):

```powershell
# Quick status (no execution)
python "$HOME\.cursor\skills\ilk-loop\scripts\loop_status.py"
```

Or in a fresh Cursor chat, type `/ilk` — that will:
1. Run the status check
2. If everything is shipped → tell you and stop
3. Otherwise → load the master + next pending sub-plan and execute the
   next step

## Authoring rules

1. **One commit per step.** Step commits include `[plan:<slug>#step-N]`
   in the message so we can grep history by plan.
2. **Plans are derived artefacts.** Don't edit them outside the loop —
   instead update the source ticket and re-derive.
3. **Backlinks.** Each ticket in the upstream tracker should point back to
   its sub-plan; each sub-plan lists its tickets in front-matter.
4. **No new untracked side-quests.** If a step uncovers a new bug, file a
   new ticket and add a line under the sub-plan's "Out of scope" section —
   do not silently expand the plan.

See `<skill-root>/ilk-loop/SKILL.md` for full details, the state
machine, integration with the lark-tickets skill, and commit-message
conventions.
