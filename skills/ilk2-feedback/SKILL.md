---
name: ilk2-feedback
description: >-
  Generate a postmortem for an ilk2 batch — its labels, its numbers, and
  what to change next time — then optionally record those findings through
  `ilk2 improvement add`. The ilk2-native postmortem command, built beside
  V1's /ilk-feedback. Triggers: "/ilk2-feedback", "postmortem this batch",
  "what went wrong in that batch". Works across Claude Code / Codex /
  Cursor.
---

# ilk2-feedback — the ilk2-native postmortem

A thin wrapper. The instructions are not in this file.

Read `../../commands/ilk2-feedback.md` — relative to this skill folder's
real location, so it resolves to the `commands/` directory beside the
`skills/` directory this folder lives in — and **follow it**. That file
owns the batch resolution, the `postmortem.py --write` run, the judgement
the script cannot make, and the `--findings` offer. None of that is
repeated here.

`<skill-root>` is the **parent of this skill's folder**: the `skills/`
directory of the clone this folder resolves to, which holds the scripts
this postmortem runs. It is never V1's `~/.ilk/current/skills`.
