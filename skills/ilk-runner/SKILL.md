---
name: ilk-runner
description: >-
  Orchestrate ilk-loop launch with watchdog for unattended runs, or show
  progress status. Delegates launching to ilk-launcher and watching to
  ilk-watchdog. Triggers: "start ilk with watchdog", "launch supervised
  ilk", "show ilk status", `/ilk-run`, `/ilk-status`, "跑 ilk 并守着",
  "看一下 ilk 进度", "auto-resume ilk", "babysit ilk".
---

# ilk-runner — orchestration skill for supervised ilk runs

A thin orchestration layer that sequences ilk-launcher and ilk-watchdog
to start a supervised ilk run, or shows progress status. Owns the
workflow guardrails; never reimplements launcher or watchdog logic.

## When to use

- User says: "start ilk with watchdog", "launch supervised ilk",
  "auto-resume ilk", "babysit ilk", "跑 ilk 并守着", `/ilk-run`.
- User says: "show ilk status", "ilk 进度", "ilk 跑到哪了",
  "where are we", `/ilk-status`.
- User wants ilk to run unattended overnight.

## Architecture

```
ilk-runner (this skill)
  ├── /ilk-run  → delegates to:
  │     ├── ilk-loop/scripts/loop_status.py   (queue check)
  │     ├── ilk-launcher/scripts/launch.sh    (spawn window)
  │     └── ilk-watchdog/scripts/watchdog.sh  (auto-restart)
  └── /ilk-status → delegates to:
        ├── ilk-loop/scripts/loop_status.py       (queue state)
        └── ilk-launcher/scripts/status_progress.py (rich dashboard)
```

## Workflows

### W1. Supervised launch (`/ilk-run`)

1. **Check queue**: run `loop_status.py`. If all shipped, report and stop.
2. **Read sub-plan**: understand remaining steps, risk signals.
3. **Read postmortems**: adjust launch params from history (see
   `ilk-launcher/SKILL.md` § "Agent decision guide").
4. **Pick params**: `MaxIterations` and `IterationTimeoutMin` based on
   remaining work + step character.
5. **Launch ilk**: invoke `ilk-launcher/scripts/launch.sh` (or
   `launch.ps1` on Windows) with resolved params.
6. **Start watchdog**: invoke `ilk-watchdog/scripts/watchdog.sh --detach`
   (or `watchdog.ps1 -Detach` on Windows) with default polling.
7. **Report**: window title, PID, params, watchdog PID, log paths
   (loop log from `last-launch.json`, JSONL summary, watchdog activity
   log, watchdog stdout/stderr log), and copy-ready tail commands.

### W2. Status check (`/ilk-status`)

1. **Current project**: run `loop_status.py` + `status_progress.py`.
   Print output, add agent judgment (health, ETA, anomalies).
2. **All projects** (if user asks): run `status_all.py`. Print table.

## Guardrails

- Always use `loop_status.py` — never inspect `docs/plans/` manually.
- Always use external-plan-aware scripts from `~/.ilk-data`.
- `/ilk-status` is read-only: no launching, stopping, or editing.
- Preserve existing launcher/watchdog defaults unless user specifies
  overrides.
- If all sub-plans shipped, say so — don't launch.

## Boundary

This skill owns the **sequence** and **guardrails**, not the
implementation. Actual launching is in `ilk-launcher`. Actual watching
is in `ilk-watchdog`. Actual loop execution is in `ilk-loop`. This
skill just orchestrates them in the right order with the right params.

## See also

- `ilk-launcher/SKILL.md` — spawning, status, stop
- `ilk-watchdog/SKILL.md` — auto-restart logic
- `ilk-loop/SKILL.md` — the loop itself
- `commands/ilk-run.md` — the command prompt for `/ilk-run`
- `commands/ilk-status.md` — the command prompt for `/ilk-status`
