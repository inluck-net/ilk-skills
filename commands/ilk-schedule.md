Launch the cross-project scheduler that drains ALL projects' queues.

This is the **cross-project** supervision model — a single scheduler daemon
scans every project's plan queue and dispatches ready work into
`-MaxConcurrent` slot homes. Use when the user says "start the scheduler",
"launch scheduler", `/ilk-schedule`, "run all projects", "one supervisor
for everything", or wants to stop launching per-project watchdogs.

Contrast with `/ilk-run` which supervises **one** project with its own
watchdog window. Both models coexist by design (ilk-watchdog/SKILL.md).

> Orchestration scripts: `ilk-runner/scripts/ilk-schedule.ps1` (Windows),
> `ilk-runner/scripts/ilk-schedule.sh` (macOS/Linux).

---

## Platform routing — read this first

| Platform | What to run |
|----------|-------------|
| **Windows** | **Only** `ilk-schedule.ps1` (section W below). |
| **macOS / Linux** | `ilk-schedule.sh` (section M below). |

---

## W. Windows — run exactly one command

**Git Bash (preferred on Windows — works in Cursor's default shell):**

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME/.claude/skills/ilk-runner/scripts/ilk-schedule.ps1"
```

**PowerShell shell:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\ilk-runner\scripts\ilk-schedule.ps1"
```

Optional overrides: `-MaxConcurrent N -PollMin M`

Preview without launching: `-DryRun`

---

## M. macOS / Linux — run exactly one command

```bash
bash "$HOME/.claude/skills/ilk-runner/scripts/ilk-schedule.sh"
```

Optional overrides: `--max-concurrent N --poll-min M`

Preview without launching: `--dry-run`

---

## /ilk-run vs /ilk-schedule

| | `/ilk-run` | `/ilk-schedule` |
|---|---|---|
| **Scope** | ONE project | ALL projects |
| **How** | `launch.ps1` + `watchdog.ps1 -Detach` | `scheduler.ps1 -Detach` |
| **Windows** | One watchdog window per project | One scheduler window total |
| **Concurrency** | N/A (one project) | `-MaxConcurrent` slot homes |
| **When to use** | Single-project focus | Multi-project fleet |

Both coexist — use whichever fits the operator's needs. The scheduler is
the "one supervisor for everything" model; `/ilk-run` is the "dedicated
watchdog per project" model.

---

## Summary

After launch, the scheduler window shows real-time dispatch logs. Each
project is routed to a distinct worker slot home (`~/.claude-worker`,
`~/.claude-worker-2`, etc.).

```
ilk-scheduler launched:
  Poll:           every <N> min
  Max concurrent: <M> projects
  Slots:          ~/.claude-worker, ~/.claude-worker-2, ...
  Window:         <detached window title>
```
