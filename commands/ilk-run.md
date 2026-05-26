Start an ilk-loop run with its watchdog for the project above cwd.

This is a **supervised launch** — it resolves the project, checks the plan
queue, starts the loop in a detached window, then starts the watchdog to
auto-restart on clean exits. Use when the user says "start ilk with
watchdog", "launch supervised ilk", `/ilk-run`, "跑 ilk 并守着",
"auto-resume ilk", or wants ilk to keep running unattended.

Do NOT inspect `docs/plans/` manually as the source of truth. Always use
the external-plan-aware scripts.

## 1. Resolve project context

Resolve the project once with `ilk_paths.py` and reuse the result for every
later step. Do not rely on `$(pwd)` / `(Get-Location)` as the launch project
— always use the `project_root` returned here.

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
```

```powershell
# Windows
python3 "<skill-root>\ilk-loop\scripts\ilk_paths.py" --start .
```

The script prints a JSON object. Extract at minimum:

- `project_root` — absolute path to use for `--project-path` / `-ProjectPath`.
- `project_key` — stable key for locating runtime/postmortem artifacts.
- `external_launcher_dir` — where `last-launch.json` and launcher logs live.
- `external_watchdog_dir` — where watchdog `activity.log` and `watchdog.log`
  live.

If `project_root` is `null` (no `.git` ancestor and no `.ilk-meta.json`),
tell the user to `cd` into a project root and STOP. Do not fall back to
the current directory.

Bind these to shell variables you can reuse below; the examples in later
sections refer to `$PROJECT_ROOT` (bash) or `$ProjectRoot` (PowerShell).

## 2. Check queue

Run `loop_status.py` to confirm there is pending work:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
```

```powershell
# Windows
python3 "<skill-root>\ilk-loop\scripts\loop_status.py"
```

`loop_status.py` exit codes are queue-state signals, not error codes —
non-zero is normal in the no-work and no-context cases:

- **Exit 0** — every sub-plan is `shipped`. There is nothing to launch.
  Tell the user "All sub-plans shipped — nothing to run." and STOP. Do
  not start the loop or watchdog.
- **Exit 1** — work remains (a `pending` or `in-progress` sub-plan).
  This is the normal success path for `/ilk-run`. The script printed the
  next sub-plan filename and full path; continue with section 3.
- **Exit 2** — invalid context (no plans dir resolved for `project_root`).
  Tell the user to `cd` into a project that has external plans under
  `~/.ilk-data/projects/<project_key>/plans/` (or legacy `docs/plans/`)
  and STOP.

## 3. Read the next pending sub-plan

Read the sub-plan file path printed by `loop_status.py` to understand:

- `estimated_steps` and `current_step` (remaining work)
- `priority` and `tickets`
- Step contents — look for signals:
  - `pytest` / `vitest` / `npm run build` → compile-bound
  - `playwright` / `e2e` / `chrome-devtools` → browser-bound
  - `wait_ci` / "push and wait" → CI-bound (15–60 min/step)
  - Pure refactors / docs → fast

## 4. Read recent postmortems (history-aware)

Check the last 3 postmortems under
`<external_launcher_dir>/postmortems/*.md` (resolved in section 1).

Apply these soft rules to adjust launch params:

| Pattern in last 3 | Adjustment |
|---|---|
| ≥2 `timeout-bound` | Bump `IterationTimeoutMin` to max(recommended) |
| ≥2 `max-iter-bound` | Bump `MaxIterations` to max(recommended) |
| ≥2 `api-flaky` / `api-blocked` | Warn user: endpoint unstable; ask before launching |
| ≥2 `stuck-no-progress` | Warn user: sub-plan may need restructuring |
| Mostly `clean-success` | No adjustment; trust estimate |
| No postmortems | First run — proceed with estimate only |

## 5. Pick launch parameters

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

## 6. Launch ilk

Pass `project_root` (resolved in section 1) explicitly so the launcher
never has to walk up from cwd or look up `--project-name` in
`projects.json`:

```bash
# macOS / Linux
bash "<skill-root>/ilk-launcher/scripts/launch.sh" \
    --project-path "$PROJECT_ROOT" \
    --max-iterations <N> --iteration-timeout-min <M>
```

```powershell
# Windows
& "<skill-root>\ilk-launcher\scripts\launch.ps1" `
    -ProjectPath $ProjectRoot `
    -MaxIterations <N> -IterationTimeoutMin <M>
```

Report: window title, PID, resolved params, rationale.

After launch, read `<external-launcher-dir>/last-launch.json` to get the
loop log path. The authoritative field is `log_file`.

## 7. Start watchdog

Start the watchdog with the same resolved `project_root` so it watches the
exact loop you just launched. Do not use `--project-name` / `-ProjectName`
here — name lookups depend on `projects.json` being current, and the
supervised flow already knows the path:

```bash
# macOS / Linux
bash "<skill-root>/ilk-watchdog/scripts/watchdog.sh" \
    --project-path "$PROJECT_ROOT" \
    --poll-interval-sec 300 --max-restarts 5 --detach
```

```powershell
# Windows
& "<skill-root>\ilk-watchdog\scripts\watchdog.ps1" `
    -ProjectPath $ProjectRoot `
    -PollMin 5 -MaxRestarts 5 -Detach
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

## 8. Summary

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
