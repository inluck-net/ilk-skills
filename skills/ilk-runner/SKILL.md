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

> **`ilk-runner` scripts** live in `ilk-runner/scripts/` (`ilk-run.ps1`,
> `ilk-status.ps1`, `ilk-stop.ps1` on Windows; `ilk-run.sh` on macOS/Linux).
> Prefer these on Windows so agents do not mix Git Bash and PowerShell.
> Other scripts live in sibling skills: `ilk-loop/scripts/`,
> `ilk-launcher/scripts/`, `ilk-watchdog/scripts/`.

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
  │     ├── ilk-loop/scripts/loop_status.py        (queue check)
  │     ├── ilk-loop/scripts/promote_next_master.py (queue promotion)
  │     ├── ilk-launcher/scripts/launch.sh          (spawn window)
  │     └── ilk-watchdog/scripts/watchdog.sh        (auto-restart)
  └── /ilk-status → delegates to:
        ├── ilk-loop/scripts/loop_status.py       (queue state)
        └── ilk-launcher/scripts/status_progress.py (rich dashboard)
```

## Workflows

### W1. Supervised launch (`/ilk-run`)

**Windows:** run `ilk-runner/scripts/ilk-run.ps1` via
`powershell -NoProfile -ExecutionPolicy Bypass -File ...` (see
`commands/_windows-agent-shell.md`). Do not call `python3` or paste
PowerShell variables into Git Bash.

**macOS/Linux:** run `ilk-runner/scripts/ilk-run.sh`, or follow the steps
below manually.

1. **Check queue**: run `loop_status.py`. Apply the queue-state decision
   table before proceeding:
   - All shipped, no queued masters → report "nothing to run" and STOP.
   - Active master has pending/in-progress work → proceed to step 2.
   - Active master all shipped + queued master exists → promote (see
     below), then proceed.
   - No active master + queued master exists → promote (see below),
     then proceed.
   - Multiple active masters → report queue integrity issue and STOP.
2. **Promote if needed**: run
   `promote_next_master.py --project "$PROJECT_ROOT"` (or PowerShell
   equivalent). Inspect JSON output for `"promoted": true`. If promotion
   fails, treat it as a hard stop. After promotion, re-run
   `loop_status.py` to confirm the newly active master has pending work.
3. **Read sub-plan**: understand remaining steps, risk signals.
4. **Read postmortems**: adjust launch params from history (see
   `ilk-launcher/SKILL.md` § "Agent decision guide").
5. **Pick params**: `MaxIterations` and `IterationTimeoutMin` based on
   remaining work + step character.
6. **Launch ilk**: invoke `ilk-launcher/scripts/launch.sh` (or
   `launch.ps1` on Windows) with resolved params.
7. **Start watchdog**: invoke `ilk-watchdog/scripts/watchdog.sh --detach`
   (or `watchdog.ps1 -Detach` on Windows) with default polling.
8. **Report**: window title, PID, params, watchdog PID, log paths
   (loop log from `last-launch.json`, JSONL summary, watchdog activity
   log, watchdog stdout/stderr log), and copy-ready tail commands.

### W2. Status check (`/ilk-status`)

1. **Current project**: run `loop_status.py` + `status_progress.py`.
   Print output, add agent judgment (health, ETA, anomalies).
2. **All projects** (if user asks): run `status_all.py`. Print table.

## Guardrails

- Always use `loop_status.py` — never inspect `docs/plans/` manually.
- Always use external-plan-aware scripts from `~/.ilk-data`.
- Use `promote_next_master.py` for queue advancement — do not hand-edit
  master plan frontmatter.
- If the active master is fully shipped but queued masters exist, promote
  before launching. Never launch against a fully shipped active master.
- Multiple active masters are a hard stop — report the issue, do not
  launch.
- `/ilk-status` is read-only: no launching, stopping, or editing.
- Preserve existing launcher/watchdog defaults unless user specifies
  overrides.
- If all sub-plans shipped, say so — don't launch.
- If `status_progress.py` reports state=running but the PID is dead,
  this is a **stale sentinel**. Report: PID, sentinel path
  (`~/.ilk-data/projects/<key>/runtime/last-exit.json`), and log
  candidate paths. Do not treat as healthy. Suggest `/ilk-feedback`
  to classify, then clean the sentinel before relaunching.

## Self-hosting caution

When the project being run is the same repo that supplies the installed
`ilk-*` skills (self-hosting), a run can modify the very runner/skill
code it depends on. Risks:

- **Log path drift** — a commit during the run changes where logs are
  written; the post-run postmortem may not find the original path.
- **Stale sentinel** — `last-exit.json` reports `state=running` for a
  PID that already exited (runner code was replaced mid-run).
- **Lost evidence** — legacy skill-root logs deleted before the
  postmortem reads them.

Mitigations:
1. Run `preserve_active_run.py` before any log cleanup.
2. Use a stable runner snapshot (future work) for self-hosting projects.
3. After a self-hosting run, check for `self-hosting-drift` in the
   postmortem before relaunching.

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
