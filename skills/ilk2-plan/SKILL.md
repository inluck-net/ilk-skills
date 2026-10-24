---
name: ilk2-plan
description: >-
  Plan a new batch of work for an ilk2 project from a free-text task
  description: group the work into sub-plans, get the grouping approved,
  run the authoring QC, and deliver with `ilk2 submit`. The ilk2-native
  planner, built beside V1's /ilk-plan. Triggers: "/ilk2-plan",
  "plan this as an ilk2 batch", "draft an ilk2 plan for this and submit
  it". Works across Claude Code / Codex / Cursor.
---

# ilk2-plan — the ilk2-native planner

A thin wrapper. The instructions are not in this file.

Read `../../commands/ilk2-plan.md` — relative to this skill folder's real
location, so it resolves to the `commands/` directory beside the `skills/`
directory this folder lives in — and **follow it**. That file owns the
arguments (`--yes`, `--draft`), the grouping approval gate, the authoring
QC and delivery through `ilk2 submit`. None of that is repeated here.

`<skill-root>` is the **parent of this skill's folder**: the `skills/`
directory of the clone this folder resolves to, which holds the scripts
this planner runs. It is never V1's `~/.ilk/current/skills`.
