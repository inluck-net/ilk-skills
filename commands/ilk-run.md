Start an ilk-loop run with its watchdog for the project above cwd.

This is a **supervised launch** — it resolves the project, checks the plan
queue, starts the loop in a detached window, then starts the watchdog to
auto-restart on clean exits. Use when the user says "start ilk with
watchdog", "launch supervised ilk", `/ilk-run`, "跑 ilk 并守着",
"auto-resume ilk", or wants ilk to keep running unattended.

Do NOT inspect `docs/plans/` manually as the source of truth. Always use
the external-plan-aware scripts.

## 1. Resolve project and check queue

Run `loop_status.py` to confirm there is pending work:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
```

```powershell
# Windows
python3 "<skill-root>\ilk-loop\scripts\loop_status.py"
```

- Exit 0 → all sub-plans shipped. Tell the user "All sub-plans shipped —
  nothing to run." and STOP.
- Exit 1 → pending work exists. The script printed the next sub-plan path.
  Continue.
- Exit 2 → no plans dir found. Tell the user to `cd` into a project with
  plans. STOP.

## 2. Read the next pending sub-plan

Read the sub-plan file path printed by `loop_status.py` to understand:

- `estimated_steps` and `current_step` (remaining work)
- `priority` and `tickets`
- Step contents — look for signals:
  - `pytest` / `vitest` / `npm run build` → compile-bound
  - `playwright` / `e2e` / `chrome-devtools` → browser-bound
  - `wait_ci` / "push and wait" → CI-bound (15–60 min/step)
  - Pure refactors / docs → fast

## 3. Read recent postmortems (history-aware)

Check the last 3 postmortems in:

```
~/.ilk-data/projects/<key>/runtime/launcher/postmortems/*.md
```

Apply these soft rules to adjust launch params:

| Pattern in last 3 | Adjustment |
|---|---|
| ≥2 `timeout-bound` | Bump `IterationTimeoutMin` to max(recommended) |
| ≥2 `max-iter-bound` | Bump `MaxIterations` to max(recommended) |
| ≥2 `api-flaky` / `api-blocked` | Warn user: endpoint unstable; ask before launching |
| ≥2 `stuck-no-progress` | Warn user: sub-plan may need restructuring |
| Mostly `clean-success` | No adjustment; trust estimate |
| No postmortems | First run — proceed with estimate only |

## 4. Pick launch parameters

**MaxIterations** — baseline = `estimated_steps - current_step`:

| Situation | MaxIterations |
|---|---|
| Single sub-plan, ≤8 remaining, no risky steps | `max(remaining × 2, 10)` |
| Multi sub-plan queue | `max(total_remaining × 1.5, 20)` |
| CI-wait or e2e steps | +10 above |
| Unknown project | Ask user |

Floor: 10. Ceiling: 60 unless user overrides.

**IterationTimeoutMin** — based on step character:

| Character | Timeout |
|---|---|
| Code edits + unit tests | 20 |
| `npm run build` / `tsc` / `pytest` | 30 |
| Browser smoke | 45 |
| Push-and-wait CI (≥15 min) | 60 |
| Push-and-wait CI (≥30 min) | 90 |

Floor: 15. Ceiling: 120.

## 5. Launch ilk

```bash
# macOS / Linux
bash "<skill-root>/ilk-launcher/scripts/launch.sh" \
    --project-path "$(pwd)" \
    --max-iterations <N> --iteration-timeout-min <M>
```

```powershell
# Windows
& "<skill-root>\ilk-launcher\scripts\launch.ps1" `
    -ProjectPath (Get-Location) `
    -MaxIterations <N> -IterationTimeoutMin <M>
```

Report: window title, PID, resolved params, rationale.

After launch, read `<external-launcher-dir>/last-launch.json` to get the
loop log path. The authoritative field is `log_file`.

## 6. Start watchdog

```bash
# macOS / Linux
bash "<skill-root>/ilk-watchdog/scripts/watchdog.sh" \
    --project-name <project-name> --poll-interval-sec 300 --max-restarts 5 --detach
```

```powershell
# Windows
& "<skill-root>\ilk-watchdog\scripts\watchdog.ps1" `
    -ProjectName <project-name> -PollMin 5 -MaxRestarts 5 -Detach
```

After launch, resolve the watchdog dir to get log paths:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start "$(pwd)" --where
```

The watchdog writes two logs:
- `<external-watchdog-dir>/activity.log` — structured activity log
- `<external-watchdog-dir>/watchdog.log` — stdout/stderr when using `--detach`

Report: watchdog PID, polling interval, max restarts, both log paths.

## 7. Summary

Read `last-launch.json` and resolve watchdog dir, then print a single
summary block:

```
ilk launched: <project-name>
  Window:     ilk: <project-name>
  PID:        <pid>
  Iterations: <max-iterations>
  Timeout:    <timeout-min> min
  Watchdog:   PID <watchdog-pid>, poll every <poll-min> min, max <max-restarts> restarts
  Logs:
    Loop log:      <last-launch.json .log_file>
    Loop JSONL:    <skill-root>/ilk-loop/logs/.ilk-loop.log
    Watchdog act:  <external-watchdog-dir>/activity.log
    Watchdog out:  <external-watchdog-dir>/watchdog.log
  Tail (macOS/Linux):
    tail -f "<loop-log>"
    tail -f "<watchdog-activity-log>"
  Tail (Windows):
    Get-Content "<loop-log>" -Wait
    Get-Content "<watchdog-activity-log>" -Wait
  Plan:       <next-sub-plan> (step <current>/<total>)
```

Tell the user they can check progress with `/ilk-status` and stop with
`/ilk-stop`.
