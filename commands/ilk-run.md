Start an ilk-loop run with its watchdog for the project above cwd.

This is a **supervised launch** — it resolves the project, checks the plan
queue, starts the loop in a detached window, then starts the watchdog to
auto-restart on clean exits. Use when the user says "start ilk with
watchdog", "launch supervised ilk", `/ilk-run`, "跑 ilk 并守着",
"auto-resume ilk", or wants ilk to keep running unattended.

Do NOT inspect `docs/plans/` manually as the source of truth. Always use
the external-plan-aware scripts.

> Orchestration scripts: `ilk-runner/scripts/ilk-run.ps1` (Windows),
> `ilk-runner/scripts/ilk-run.sh` (macOS/Linux). Other scripts live in
> `ilk-loop/scripts/`, `ilk-launcher/scripts/`, `ilk-watchdog/scripts/`.

---

## Platform routing — read this first

| Platform | What to run |
|----------|-------------|
| **Windows** | **Only** `ilk-run.ps1` (section W below). Do **not** run sections M1–M7 manually. |
| **macOS / Linux** | `ilk-run.sh` **or** sections M1–M8 manually. |

On Windows, Cursor's Shell tool often defaults to **Git Bash**. PowerShell
variables like `$env:USERPROFILE` **do not expand in Bash** — that is the
#1 cause of `/ilk-run` failures on Windows.

---

## W. Windows supervised launch (mandatory)

Run **exactly one** Shell command. Use the Git Bash form when the shell is
Bash (Cursor default on Windows); use the PowerShell form only when the
Shell tool is already PowerShell.

**Git Bash (preferred on Windows — works in Cursor's default shell):**

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME/.cursor/skills/ilk-runner/scripts/ilk-run.ps1"
```

**PowerShell shell:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cursor\skills\ilk-runner\scripts\ilk-run.ps1"
```

Pass the project explicitly when cwd is not the project root:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME/.cursor/skills/ilk-runner/scripts/ilk-run.ps1" -Start "C:/path/to/project"
```

Optional overrides (after reading sub-plan / postmortems if you need to
adjust): `-MaxIterations 40 -IterationTimeoutMin 45`

Preview without launching: `-DryRun`

**What the script does:** resolve project → queue check → promote queued
master if needed → estimate launch params from sub-plan + last 3 postmortems
→ `launch.ps1` → `watchdog.ps1 -Detach`.

**Do not** call `python`, `ilk_paths.py`, or `loop_status.py` manually on
Windows unless `ilk-run.ps1` is missing or fails.

**Exit codes inside the script:** `loop_status.py` exit **1** means pending
work exists — that is the **normal launch path**, not an error. Only treat
the script's own non-zero exit as failure.

**After the script succeeds (exit 0):** read
`<external_launcher_dir>/last-launch.json` and print the section **Summary**
block below. Tell the user `/ilk-status` and `/ilk-stop`.

**Self-hosting:** if the project contains `skills/ilk-loop/`, warn the user
before launching (see section S below). The script prints a warning when it
detects this.

---

## M. macOS / Linux supervised launch

**One command (preferred):**

```bash
bash "$HOME/.cursor/skills/ilk-runner/scripts/ilk-run.sh" /path/to/project
```

Or follow sections M1–M8 manually.

`<skill-root>` below means `~/.cursor/skills/`, `~/.claude/skills/`, or
`~/.codex/skills/` depending on the host agent.

---

## M1. Resolve project context (macOS/Linux manual only)

Resolve the project once with `ilk_paths.py` and reuse the result for every
later step. Do not rely on `$(pwd)` as the launch project — always use the
`project_root` returned here.

```bash
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
```

Extract at minimum:

- `project_root` — absolute path for `--project-path` / `-ProjectPath`.
- `project_key` — stable key for runtime/postmortem artifacts.
- `external_launcher_dir` — `last-launch.json` and launcher logs.
- `external_watchdog_dir` — watchdog `activity.log` and `watchdog.log`.

If `project_root` is `null`, tell the user to `cd` into a project root
and STOP.

## M2. Check queue (macOS/Linux manual only)

```bash
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
```

Exit codes are queue-state signals, not errors:

- **Exit 0** — every sub-plan is `shipped`. Check queued masters (M2b) before stopping.
- **Exit 1** — work remains. **Normal success path for `/ilk-run`.** Continue.
- **Exit 2** — invalid context. STOP.

> **Agents:** exit code 1 is not a tool failure. Do not report it as an error.

### M2b. Queue-state decision table

| Queue state | Behavior |
|---|---|
| Active master has pending/in-progress work | Launch current active master. |
| Active master all shipped + queued master exists | Run `promote_next_master.py`, re-run `loop_status.py`, then launch. |
| No active master + queued master exists | Run `promote_next_master.py`, re-run `loop_status.py`, then launch. |
| All masters shipped + no queued | Report nothing to run. STOP. |
| Multiple active masters | Report queue integrity issue. STOP. |

```bash
python3 "<skill-root>/ilk-loop/scripts/promote_next_master.py" --project "$PROJECT_ROOT"
```

After promotion, re-run `loop_status.py`.

## M3. Read the next pending sub-plan (macOS/Linux manual only)

Read the sub-plan path printed by `loop_status.py` for `estimated_steps`,
`current_step`, priority, tickets, and step character (pytest, e2e, CI-wait).

## M4. Read recent postmortems (macOS/Linux manual only)

Check the last 3 postmortems under
`<external_launcher_dir>/postmortems/*.md`.

| Pattern in last 3 | Adjustment |
|---|---|
| ≥2 `timeout-bound` | Bump `IterationTimeoutMin` to max(recommended) |
| ≥2 `max-iter-bound` | Bump `MaxIterations` to max(recommended) |
| ≥2 `api-flaky` / `api-blocked` | Warn user; ask before launching |
| ≥2 `stuck-no-progress` | Warn user: sub-plan may need restructuring |
| Mostly `clean-success` | No adjustment |
| No postmortems | Proceed with estimate only |

## M5. Pick launch parameters (macOS/Linux manual only)

**MaxIterations** — floor 10, ceiling 60:

| Situation | MaxIterations |
|---|---|
| Single sub-plan, ≤8 remaining | `max(remaining × 2, 10)` |
| Multi sub-plan queue | `max(total_remaining × 1.5, 20)` |
| CI-wait or e2e steps | +10 above |

**IterationTimeoutMin** — floor 15, ceiling 120:

| Character | Timeout |
|---|---|
| Code edits + unit tests | 20 |
| build / tsc / pytest | 30 |
| Browser smoke | 45 |
| Push-and-wait CI (≥15 min) | 60 |
| Push-and-wait CI (≥30 min) | 90 |

## M6. Launch ilk (macOS/Linux manual only)

```bash
bash "<skill-root>/ilk-launcher/scripts/launch.sh" \
    --project-path "$PROJECT_ROOT" \
    --max-iterations <N> --iteration-timeout-min <M>
```

## M7. Start watchdog (macOS/Linux manual only)

```bash
bash "<skill-root>/ilk-watchdog/scripts/watchdog.sh" \
    --project-path "$PROJECT_ROOT" \
    --poll-interval-sec 300 --max-restarts 5 --detach
```

---

## Summary (all platforms)

After launch, read `<external_launcher_dir>/last-launch.json`:

```
ilk launched: <project_key>
  Window:     ilk: <project_key>
  PID:        <pid>
  Iterations: <max-iterations>
  Timeout:    <timeout-min> min
  Watchdog:   PID <watchdog-pid>, poll every <poll-min> min, max <max-restarts> restarts
  Logs:
    Loop log:      <last-launch.json .log_file>
    Loop JSONL:    <last-launch.json .jsonl_log>
    Watchdog act:  <external_watchdog_dir>/activity.log
    Watchdog out:  <external_watchdog_dir>/watchdog.log
  Tail (macOS/Linux):
    tail -f "<loop-log>"
    tail -f "<external_watchdog_dir>/activity.log"
  Tail (Windows):
    Get-Content "<loop-log>" -Wait
    Get-Content "<external_watchdog_dir>\activity.log" -Wait
  Plan:       <next-sub-plan> (step <current>/<total>)
```

Postmortems land under `<external_launcher_dir>/postmortems/`.

## Planner vs Worker Claude

By default `/ilk-run` uses the active Claude home (`~/.claude` — the **Planner
Claude** on its official provider). To run the loop as a cheaper **Worker
Claude** on a separate Anthropic-compatible provider, launch the runner through
the worker wrapper so it inherits the worker home and a fail-closed preflight:

```bash
# macOS / Linux
bash tools/claude-worker/claude-worker.sh /ilk-run
```

```powershell
# Windows
.\tools\claude-worker\claude-worker.ps1 /ilk-run
```

Bootstrap the worker home once (`tools/claude-worker/bootstrap.sh` /
`bootstrap.ps1`) with explicit provider values. Do **not** live-switch the
provider with CCSwitch during a worker run. See
`docs/dual-claude-homes-design.md` and `tools/claude-worker/README.md`.

## S. Self-hosting projects

If the project contains `skills/ilk-loop/`, `skills/ilk-launcher/`, etc., warn:

> **Self-hosting detected** — this project supplies the installed ilk skills.
> A run may modify runner code mid-flight. Consider `preserve_active_run.py`
> after the run and check for `self-hosting-drift` in the postmortem before
> relaunching.

Do not block the launch — advisory only.
