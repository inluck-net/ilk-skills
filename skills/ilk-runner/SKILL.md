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

**Windows (mandatory):** run **only** `ilk-runner/scripts/ilk-run.ps1` via
Shell — do not call `python`/`ilk_paths`/`loop_status` manually.

Git Bash (Cursor default on Windows):

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME/.cursor/skills/ilk-runner/scripts/ilk-run.ps1"
```

Never use `$env:USERPROFILE` in Git Bash. See `commands/ilk-run.md` section W.

**macOS/Linux:** run `ilk-runner/scripts/ilk-run.sh`, or follow manual
sections M1–M8 in `commands/ilk-run.md`:

1. **Check queue**: run `loop_status.py`. Apply the queue-state decision
   table before proceeding.
2. **Promote if needed**: run
   `promote_next_master.py --project "$PROJECT_ROOT"`. Inspect JSON for
   `"promoted": true`. Re-run `loop_status.py` after promotion.
3. **Read sub-plan**: remaining steps, risk signals.
4. **Read postmortems**: adjust launch params from history.
5. **Pick params**: `MaxIterations` and `IterationTimeoutMin`.
6. **Launch ilk**: `launch.sh` with resolved params.
7. **Start watchdog**: `watchdog.sh --detach`.
8. **Report**: PID, params, log paths, tail commands.

### W2. Status check (`/ilk-status`)

1. **Current project**: run `loop_status.py` + `status_progress.py`.
   Print output, add agent judgment (health, ETA, anomalies).
2. **All projects** (if user asks): run `status_all.py`. Print table.

## Guardrails

- **Windows `/ilk-run`:** use `ilk-run.ps1` only — never manual python steps in Bash.
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
