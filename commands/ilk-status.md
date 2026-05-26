Show ilk-loop progress for the current project (or all projects).

This is a **read-only** status command. It resolves the project, runs
`loop_status.py` for queue state, and `status_progress.py` for a rich
progress dashboard. Use when the user says "ilk status", "show ilk
progress", `/ilk-status`, "ilk 跑到哪了", "where are we", "ilk 进度",
or wants to check loop state without launching or stopping anything.

Do NOT inspect `docs/plans/` manually as the source of truth. Always use
the external-plan-aware scripts.

Do NOT launch, stop, edit plans, or mutate any state. This command is
strictly read-only.

## 1. Current-project status (default)

When the user asks about the current project (default):

```bash
# macOS / Linux — queue state
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
```

```powershell
# Windows
python3 "<skill-root>\ilk-loop\scripts\loop_status.py"
```

Then run the rich progress dashboard:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-launcher/scripts/status_progress.py"
```

```powershell
# Windows
python3 "<skill-root>\ilk-launcher\scripts\status_progress.py"
```

Print the output verbatim or in a markdown box. Then add agent judgment:

- Time since last query and steps completed (from chat timestamps)
- Loop health / anomaly assessment
- Recommended action

## 2. All-projects status (when user asks globally)

When the user asks for "all projects", "global status", or `/ilk-status-all`:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-launcher/scripts/status_all.py"
```

```powershell
# Windows
python3 "<skill-root>\ilk-launcher\scripts\status_all.py"
```

Shows a table:

```
project       state    plan-status                              window-pid
es_api        running  next: 2026-05-22-cleanup step 3 of 7     54321
myproj       idle     all sub-plans shipped                    -
crawler       idle     next: 2026-05-22-zara-source step 0 of 9 -
```

## 3. Interpret results

For each project, add agent judgment:

| State | Meaning |
|---|---|
| `running` | PID file exists AND PID alive — loop is actively working |
| `idle` | No PID file, OR PID references dead process |

If idle with pending work: "ilk stopped; run `/ilk-run` to restart."
If running with no pending work: "all shipped; watchdog will exit cleanly."
If running with pending work: report progress and ETA.

## 4. Boundary rules

This command is **read-only**. It must NOT:

- Launch, stop, or restart ilk
- Edit or view plan files
- Modify any state files
- Use `docs/plans/` as the source of truth

If the user asks to launch or stop, redirect to `/ilk-run` or `/ilk-stop`.
