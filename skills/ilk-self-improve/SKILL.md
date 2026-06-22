---
name: ilk-self-improve
description: >-
  Plan toolkit improvements from the shared improvement backlog. Reads open
  candidates emitted by /ilk-feedback, formats a task description, and
  delegates to /ilk-plan on the ilk-skills repo. The resulting master is
  auto-gated draft+supervised_only (self-modifying). Triggers:
  "/ilk-self-improve", "improve ilk", "self-improve ilk", "ilk 自我改进".
---

# ilk-self-improve — backlog → plan adapter

A source adapter for the ilk loop, analogous to `/ilk-lark`. It
pulls from the **improvement backlog** (populated by `/ilk-feedback` when a
postmortem finding is a toolkit/process gap) and hands a formatted task
description to `/ilk-plan`.

## When to use

- The user says "/ilk-self-improve", "improve ilk", "self-improve ilk",
  or "ilk 自我改进".
- After a `/ilk-feedback` postmortem surfaced toolkit gaps and the user
  wants to plan fixes.

## Boundary

This skill is a **planner**, not an executor. It produces a plan; a human
releases and runs it (the master is `draft` + `supervised_only` because it
edits the toolkit itself). It does NOT auto-apply changes.

## Workflow

1. Run `build_task.py` to read open candidates from the improvement backlog.
2. If the backlog is empty, report "nothing to improve" and stop.
3. Otherwise, hand the task description to `/ilk-plan` on the ilk-skills repo.
4. `/ilk-plan` auto-gates the resulting master as `draft` + `supervised_only`
   (self-modifying batch) and auto-registers the project.

## Key files

- `scripts/build_task.py` — reads backlog, emits task description.
- Backlog store: `~/.ilk-data/ilk-skills-improvements/candidates.json`
  (managed by `ilk-feedback/scripts/improvement_backlog.py`).

## See also

- `/ilk-feedback` — emits upstream candidates into the backlog.
- `/ilk-plan` — the planning core this adapter delegates to.
- `skills/ilk-feedback/scripts/improvement_backlog.py` — backlog schema + API.
