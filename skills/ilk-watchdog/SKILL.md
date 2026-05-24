---
name: ilk-watchdog
description: >-
  Auto-restart ilk-loop when it stops for whitelist-classified reasons
  (timeout-bound, max-iter-bound, api-flaky, interrupted) and BLOCK with a
  loud banner for blacklist reasons (stuck-no-progress, api-blocked,
  budget-exhausted, local-checks-stuck). Runs in a detached desktop PowerShell window
  independent of Cursor and ilk itself, polls the ilk PID file every
  N minutes, and uses ilk-feedback's classification to decide whether to
  relaunch. Use when the user says "watchdog ilk", "守着 ilk",
  "auto-resume ilk", "/ilk-watch", "babysit ilk", "monitor ilk",
  "restart ilk if it dies", "看着 ilk", "ilk 自动续跑", or wants ilk
  to keep running unattended overnight.
---

# ilk-watchdog — auto-resume layer for ilk-loop

A passive babysitter for a ilk-loop run. Detached desktop window, polls
the launcher's PID file every few minutes, and when ilk stops:

1. Asks `loop_status.py` whether all sub-plans are shipped.
2. If not, asks `ilk-feedback`'s `collect.py` to classify the run.
3. If classification is whitelist → relaunches via `ilk-launcher`'s
   `launch.ps1` with the postmortem's recommended params.
4. If classification is blacklist → prints a loud BLOCKED banner and exits
   so the human is forced to triage.

Watchdog never modifies plans, never kills ilk, never edits SKILL.md.
It only watches PID files, reads postmortems, and shells out to the
launcher.

## Architecture

```
~/.cursor/skills/ilk-watchdog/
  SKILL.md                  ← this file
  scripts/
    watchdog.ps1            ← polling loop; -Detach flag spawns its own window
    stop_watchdog.ps1       ← reads ~/.ilk-data/projects/<key>/runtime/watchdog/watchdog.pid, tree-kills

~/.ilk-data/projects/<key>/runtime/watchdog/
  watchdog.pid              ← PID of the watchdog window (deleted on clean exit)
  activity.log              ← append-only event log: poll, dead, classify, relaunch, ...
```

Three independent processes when fully running:

```
Window 1: ilk: <project>            (run_ilk_loop_claude.ps1)
Window 2: watchdog: <project>         (this skill)
Window 3 (optional): you, in Cursor   (can close anytime)

         writes ↓                       ↑ reads PID file
  ~/.ilk-data/projects/<key>/runtime/launcher/running.pid
```

The only inter-process channel is the file system. No IPC, no ports.

## Whitelist / blacklist (the core decision)

Reuses `ilk-feedback`'s 9-class taxonomy. Hard-coded in this skill:

| Classification | Action |
|---|---|
| `clean-success` (or sentinel state `all-shipped`/`shipped`) | Mark current master as `shipped`, promote next queued master to `active`, relaunch via ilk-launcher and keep polling. If the queue is empty, exit cleanly with the "queue drained" banner. |
| `timeout-bound` | ✅ Relaunch with postmortem's recommended params |
| `max-iter-bound` | ✅ Relaunch with postmortem's recommended params |
| `api-flaky` | ✅ Relaunch (params usually unchanged) |
| `interrupted` | ✅ Relaunch (user accidentally closed window? continue) |
| `stuck-no-progress` | ❌ BLOCKED — agent stalled, restart won't help |
| `api-blocked` | ❌ BLOCKED — endpoint truly down, restart won't help |
| `budget-exhausted` | ❌ BLOCKED — `--max-budget-usd` cap hit, raising it is a human decision |
| `local-checks-stuck` | ❌ BLOCKED — `local_checks` failed repeatedly while agent kept committing; AC may be wrong or step too coarse, restart won't help |
| (unknown) | ❌ BLOCKED — fail safe |

Hard cap `MaxRestarts` (default 5). Even with whitelist hits, watchdog
exits after this many restarts to force a human review pass.

## Standard workflows

### W1. Start watchdog for an already-running ilk

```powershell
& "$HOME\.cursor\skills\ilk-watchdog\scripts\watchdog.ps1" -ProjectName myproj -Detach
```

```bash
# macOS / Linux equivalent (runs in foreground; background with &):
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/watchdog.sh" --project-name myproj
```

`-Detach` makes the script spawn a new desktop PowerShell window
(`Start-Process powershell -NoExit ...`) running the polling loop, then
exits immediately so the calling shell is free.

Without `-Detach`, the polling loop runs in the current shell — useful
for debugging, not for unattended use.

### W2. Tune the polling cadence

```powershell
& "$HOME\.cursor\skills\ilk-watchdog\scripts\watchdog.ps1" `
    -ProjectName myproj -PollMin 3 -MaxRestarts 3 -Detach
```

```bash
# macOS / Linux equivalent:
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/watchdog.sh" \
    --project-name myproj --poll-interval-sec 180 --max-restarts 3
```

`-PollMin` default 5 (good for 30–60 min iters). Set lower for faster
recovery on flaky endpoints; higher for very long iters where 5 min
poll is excessive.

### W3. Stop watchdog (without stopping ilk)

```powershell
& "$HOME\.cursor\skills\ilk-watchdog\scripts\stop_watchdog.ps1" -ProjectName myproj
```

```bash
# macOS / Linux equivalent:
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/stop_watchdog.sh" --project-name myproj
```

Kills only the watchdog window. The ilk window keeps running.

## When the agent invokes this skill

1. Confirm ilk is running for the target project (`status_all.py` shows
   `running`). If not, tell the user to start ilk first.
2. Check whether a watchdog is already running for this project (read
   `~/.ilk-data/projects/<key>/runtime/watchdog/watchdog.pid`). If alive,
   refuse with a helpful message — don't double-run.
3. Resolve project (cwd walk-up / `-ProjectName` / `-ProjectPath`, same as
   launcher).
4. Spawn watchdog with `-Detach`. Default `PollMin=5`, `MaxRestarts=5`
   unless the user said otherwise.
5. Report: window title, watchdog PID, polling interval, max restarts,
   activity log path.
6. Do NOT poll or `AwaitShell` — watchdog is independent.

## Boundary rules

- **Never modifies plans, sub-plans, or any SKILL.md.** Read-only on
  everything except its own state files.
- **Never kills ilk.** Only watches.
- **Never overrides blacklist.** Even if user says "auto-restart everything",
  blacklist is hard-coded to protect against runaway loops on real bugs.
- **Always uses postmortem's recommended params on restart.** That's the
  whole point — a timeout-bound restart with the SAME timeout would just
  re-timeout. The postmortem has already done the safe-bump math.
- **MaxRestarts is a hard ceiling**, no override flag. If a project keeps
  needing more restarts, the trend itself is the signal — ask a human.

## Known limitations

- **Windows session-bound**: `Start-Process` detaches from Cursor but not
  from the Windows interactive user session. Logging out / switching users
  kills both ilk and watchdog. Same caveat as ilk-launcher.
- **No support for multiple concurrent watchdogs per project.** The PID
  file mechanism prevents it.
- **PID rollover**: very theoretically a dead PID could be reused by an
  unrelated process; watchdog would think ilk is alive. Probability
  negligible in a 24h window. Failure direction is safe (no spurious
  restart).
- **Relaunch failure not specially handled in v0**: if the relaunched
  ilk dies during startup, next poll classifies it as `interrupted`
  and tries again, eating MaxRestarts. Acceptable — MaxRestarts caps
  the worst case.

## Relationship to other skills

| Concern | Owner |
|---|---|
| The actual ilk loop | `ilk-loop` |
| Spawning ilk window, status, manual stop | `ilk-launcher` |
| Single-run postmortem + classification | `ilk-feedback` |
| **Auto-decide whether to relaunch after stop** | **`ilk-watchdog`** (this skill) |
| Modifying heuristics / SKILL.md | Humans only |

## Future (out of v0 scope)

- `-WithWatchdog` flag on `ilk-launcher`'s `launch.ps1` to start ilk
  + watchdog in one command.
- Notification on BLOCKED (Windows toast / Telegram bot / email).
- Cross-project watchdog dashboard (one watchdog window monitoring all
  projects in `projects.json`).

## See also

- `~/.cursor/skills/ilk-feedback/SKILL.md` — the classifier this skill
  consumes.
- `~/.cursor/skills/ilk-launcher/SKILL.md` — the launcher this skill
  invokes on whitelist hits.
