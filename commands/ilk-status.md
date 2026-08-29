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

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending
on the host agent.

## Windows: agent shell (read first)

**Preferred:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cursor\skills\ilk-runner\scripts\ilk-status.ps1"
```

See `commands/_windows-agent-shell.md`. On Windows use `python`, not `python3`.

## 1. Current-project status (default)

When the user asks about the current project (default). Run these steps
**sequentially** — each depends on the previous result. Do NOT run them in
parallel.

### 1a. Queue state

```bash
# macOS / Linux — queue state
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
# Exit codes: 0 = all shipped, 1 = pending work exists (normal), 2 = no plans / invalid project
```

```powershell
# Windows
python "<skill-root>\ilk-loop\scripts\loop_status.py"
# Exit codes: 0 = all shipped, 1 = pending work exists (normal), 2 = no plans / invalid project
```

### 1b. Interpret the exit code before proceeding

- **Exit code 0** — all sub-plans shipped. Report this to the user and stop;
  no further status checks are needed.
- **Exit code 1** — active or pending work exists. This is a **normal status
  outcome, not a tool failure.** Do NOT present it as an error or wrap it in
  "command failed" language. Continue to step 1c.
- **Exit code 2** — no plans directory or invalid project context. Report
  this to the user; suggest they `cd` into a project with plans or run
  `/ilk-plan` to create one. Do NOT continue to step 1c.

> **Important:** Some shells print "Exit code 1" in the tool result footer.
> This is informational, not an error. Agents must not re-raise it or skip
> downstream checks because of it.

### 1c. Rich progress dashboard

Only run this step if step 1b resolved to exit code 1. Pass the project
root explicitly:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-launcher/scripts/status_progress.py" --project-path <project_root>
```

```powershell
# Windows
python "<skill-root>\ilk-launcher\scripts\status_progress.py" -ProjectPath <project_root>
```

Replace `<project_root>` with the resolved project path (the directory
containing `.git` or `.ilk-meta.json`).

Add `--json` when you need structured data for rendering or further
processing (e.g. extracting ETA, checking launcher PID, comparing
remaining steps programmatically):

```bash
python3 "<skill-root>/ilk-launcher/scripts/status_progress.py" --project-path <project_root> --json
```

For human display, omit `--json` and print the output verbatim or in a
markdown box. Then add agent judgment:

- Time since last query and steps completed (from chat timestamps)
- Loop health / anomaly assessment
- Recommended action

## 2. All-projects live dashboard (--watch)

When the user says "ilk watch", "live dashboard", or asks for a self-refreshing
view of all projects, run the dashboard:

```bash
# macOS / Linux
bash "<skill-root>/ilk-runner/scripts/ilk-status.sh" --watch
# Optional cadence: -n 10 (seconds, default 5)
```

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-root>\ilk-runner\scripts\ilk-status.ps1" -Watch
# Optional cadence: -N 10 (seconds, default 5)
```

The dashboard refreshes every *N* seconds showing all projects + slot status.
Press Ctrl+C to exit.  Uses `ilk_dashboard.py` which reads `status_all.py --json`
on each frame.

## 3. All-projects status (when user asks globally)

When the user asks for "all projects", "global status", or `/ilk-status-all`:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-launcher/scripts/status_all.py"
```

```powershell
# Windows
python "<skill-root>\ilk-launcher\scripts\status_all.py"
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
If running with pending work: report progress and stale-progress guidance:

| Duration on same step | Assessment | Recommended action (read-only) |
|---|---|---|
| Under 30 minutes | Usually healthy | No action needed. |
| 30–60 minutes | Watching | Consider checking the log tail for activity. |
| Over 60 minutes, no recent commits or log movement | Likely stuck | Suggest `/ilk-feedback` for a postmortem or log inspection. |
| PID dead with pending work | Loop stopped | Suggest `/ilk-run` to restart. |

### Stale sentinel detection

If `status_progress.py` reports `state=running` but the PID is dead
(check with `ps -p <PID>` on macOS/Linux or `Get-Process -Id <PID>`
on Windows), the sentinel is stale. This typically happens when:

- The loop exited but `last-exit.json` was not updated (crash, SIGKILL).
- A self-hosting run replaced the runner code mid-flight.

Report these details to the user:
- **PID** and the fact it is dead
- **Sentinel path:** `~/.ilk-data/projects/<key>/runtime/launcher/last-exit.json`
  (resolve with `python3 ilk_paths.py --start <project> --sentinel-path`)
- **Log candidates:** paths that `collect.py` would search
  (external logs dir, legacy skill-root logs)

Suggest `/ilk-feedback` to classify the run, then clean the stale
sentinel before relaunching. Do not treat a stale sentinel as healthy.

These are recommendations only — never mutate state from this command.

### PID inspection (only if needed)

Avoid `ps ... | tail` pipelines — they break on short-lived processes and
are fragile across shells. Use explicit no-pipeline commands:

```bash
# macOS / Linux — check if PID is alive with elapsed time
ps -p "$PID" -o pid=,etime=,command=
```

```powershell
# Windows
Get-Process -Id <pid> -ErrorAction SilentlyContinue | Select-Object Id, StartTime, ProcessName
```

Only inspect PIDs when the script output is ambiguous (e.g. state shows
`running` but no recent progress). Prefer the script output over manual
process checks.

## 4. Boundary rules

This command is **read-only**. It must NOT:

- Launch, stop, or restart ilk
- Edit or view plan files
- Modify any state files
- Use `docs/plans/` as the source of truth

If the user asks to launch or stop, redirect to `/ilk-run` or `/ilk-stop`.
