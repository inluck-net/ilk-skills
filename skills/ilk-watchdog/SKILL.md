---
name: ilk-watchdog
description: >-
  Auto-restart ilk-loop when it stops for whitelist-classified reasons
  (timeout-bound, max-iter-bound, api-flaky, interrupted) and BLOCK with a
  loud banner for blacklist reasons (stuck-no-progress, api-blocked,
  budget-exhausted, local-checks-stuck). Runs in a detached desktop window
  independent of the host agent and ilk itself, polls the ilk PID file
  every N minutes, and uses ilk-feedback's classification to decide
  whether to relaunch. Works across Cursor, Claude Code, and Codex.
  Use when the user says "watchdog ilk", "守着 ilk", "auto-resume ilk",
  "/ilk-watch", "babysit ilk", "monitor ilk", "restart ilk if it dies",
  "看着 ilk", "ilk 自动续跑", or wants ilk to keep running unattended
  overnight.
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

`<skill-root>` below means the installed skills base directory —
`~/.cursor/skills/` (Cursor), `~/.claude/skills/` (Claude Code), or
`~/.codex/skills/` (Codex) — depending on the host agent.

```
<skill-root>/ilk-watchdog/
  SKILL.md                  ← this file
  scripts/
    watchdog.ps1            ← per-project polling loop; -Detach flag spawns its own desktop window
    watchdog.sh             ← macOS/Linux per-project polling loop; --detach flag starts a screen session
    stop_watchdog.ps1       ← reads PID, tree-kills (Windows)
    stop_watchdog.sh        ← reads PID, kills (macOS/Linux)
    scheduler.ps1           ← cross-project scheduler (V1): drains ALL projects' queues FIFO
    scheduler.sh            ← macOS/Linux cross-project scheduler
    scheduler_scan.py       ← enumerates projects with queued work, FIFO-ordered (honors $ILK_DATA_HOME)

~/.ilk-data/projects/<key>/runtime/watchdog/
  watchdog.pid              ← PID of the watchdog process (deleted on clean exit)
  activity.log              ← append-only structured event log: poll, dead, classify, relaunch, ...
  watchdog.log              ← stdout/stderr capture when using --detach / -Detach
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

**Detached (unattended — recommended for overnight runs):**

```powershell
# Windows: spawns a new desktop window
& "$HOME\.cursor\skills\ilk-watchdog\scripts\watchdog.ps1" -ProjectName myproj -Detach
```

```bash
# macOS / Linux: starts a detached screen session
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/watchdog.sh" --project-name myproj --detach
```

On Windows, `-Detach` spawns a new PowerShell desktop window
(`Start-Process powershell -NoExit ...`) and exits immediately.
On macOS/Linux, `--detach` starts a `screen` session and exits; the
polling loop continues inside screen. Both write stdout/stderr to
`watchdog.log` in the watchdog state dir.

**Foreground (debugging only):**

```bash
# macOS / Linux — runs in current shell, blocks until watchdog exits
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/watchdog.sh" --project-name myproj
```

```powershell
# Windows — same, without -Detach
& "$HOME\.cursor\skills\ilk-watchdog\scripts\watchdog.ps1" -ProjectName myproj
```

Use foreground mode to see live output during troubleshooting. Do not
recommend plain `&` shell backgrounding for unattended use — prefer
`--detach` / `-Detach`.

### W2. Tune the polling cadence

```powershell
& "$HOME\.cursor\skills\ilk-watchdog\scripts\watchdog.ps1" `
    -ProjectName myproj -PollMin 3 -MaxRestarts 3 -Detach
```

```bash
# macOS / Linux equivalent (detached):
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/watchdog.sh" \
    --project-name myproj --poll-interval-sec 180 --max-restarts 3 --detach
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

## Cross-project scheduler (V1.1 — slot pool)

The per-project watchdog above babysits **one** project. The
**scheduler** (`scheduler.ps1` / `scheduler.sh`) is its cross-project
sibling: a single long-lived daemon that drains **every** project's
queue, routed through the cheap worker provider.

### Slot pool — cross-project parallel, per-project serial

The scheduler dispatches up to **`-MaxConcurrent`** (default 5) ready
projects per scan cycle, each routed to a **distinct slot home**
(`~/.claude-worker` for slot 1, `~/.claude-worker-<i>` for slot i≥2).
Per-project serialism is still guaranteed by the per-project sentinel
(`running.pid`) — a busy project is skipped, never double-dispatched.
Set `-MaxConcurrent 1` for strict sequential (V1-equivalent) behavior.

How a scan cycle decides:

1. `scheduler_scan.py` enumerates all projects under
   `~/.ilk-data/projects/*` (honoring `$ILK_DATA_HOME`) that have a
   **runnable master** — an `active` master with ≥1 non-shipped
   sub-plan, or a `queued` master that promotion can activate. Projects
   where every master is `shipped` are excluded. Results are ordered
   **FIFO** by oldest-queued timestamp (active masters first, else
   the next-to-promote queued master).
2. The scheduler iterates the FIFO list and collects ready projects
   (free sentinel + `repo_path`-resolvable + not blacklisted) until
   live-count reaches `MaxConcurrent` or the list is exhausted. Each
   collected project is dispatched via `ilk-launcher`'s `launch.*` with
   **`-Engine claude-worker -WorkerHome <slot-home>`** so each dispatch
   uses an isolated worker home. Busy projects → `skip-busy`.
3. **Promote-before-dispatch:** if a selected project has no `active`
   master but HAS a `queued` master, the scheduler promotes it
   (`queued→active`) via `promote_next_master.py` before dispatching.
   This ensures multi-master projects always advance — no redispatch
   churn when the active master ships and the next master is still
   `queued`.
4. A project whose most recent postmortem is **blacklist**-classified
   (reuses `ilk-feedback`'s taxonomy) → `skip-blacklist`, and the scan
   moves on. One stuck project never starves the others.
5. If nothing is dispatchable → `idle`. The daemon polls again rather
   than exiting, so newly-queued work auto-wakes it on a later cycle.

**Guardrails.** `-MaxConcurrent` caps the number of live loops at any
time (default 5); `-MaxConcurrent 1` reproduces strict-sequential
behavior. A global dispatch / budget ceiling caps spend: hitting it
reports `idle: budget ceiling` rather than crashing. The per-project
sentinel mutex still guarantees ≤1 loop per project.

| Flag (PowerShell / bash) | Default | Meaning |
|---|---|---|
| `-PollMin` / `--poll-min N` | 5 | Minutes between scan cycles. |
| `-MaxConcurrent` / `--max-concurrent N` | 5 | Maximum concurrent live loops across all projects. Set to 1 for strict sequential. |
| `-MaxDispatches` / `--max-dispatches N` | -1 (unlimited) | Global dispatch ceiling; `0` = plan no dispatches. |
| `-MaxBudgetUsd` / `--max-budget-usd N` | 0 (unlimited) | Global budget ceiling. |
| `-DryRun` / `--dry-run` | off | Print the planned decision (JSON) without launching. |
| `-Once` / `--once` | off | Run a single scan cycle and exit (used by tests). |
| `-Detach` / `--detach` | off | Spawn the scheduler detached (Windows: new window; macOS/Linux: `screen` session) and return. |

```powershell
& "$HOME\.cursor\skills\ilk-watchdog\scripts\scheduler.ps1" -PollMin 5 -MaxConcurrent 5
```
```bash
bash "$HOME/.cursor/skills/ilk-watchdog/scripts/scheduler.sh" --poll-min 5 --max-concurrent 5
```

Prefer the **`/ilk-schedule`** slash command (wraps `scheduler … -Detach` and
resolves the skill root for you) over invoking `scheduler.*` directly — it's the
cross-project analogue of `/ilk-run`. The scheduler does **not** replace the
per-project watchdog — use the watchdog to babysit a single supervised run; use
the scheduler to drain a backlog across many projects unattended. (Tests
exercise it via `--dry-run` so no provider call is made.)

**Observability (v0.8.13+).** On macOS the scheduler/loops run as headless
`screen` sessions with no visible window, so use these instead of guessing:

- **`/ilk-status --watch`** (bash `--watch`, PowerShell `-Watch`) — a
  self-refreshing all-projects + slots cockpit over `status_all.py --json`.
- **`ILK_MULTIPLEXER=tmux`** — dispatch each slot into a named `ilk` tmux session
  (`tmux attach -t ilk` to see one pane per slot); `auto` (default) uses tmux if
  present, else `screen`.
- **`ILK_NOTIFY`** — desktop notifications on ship / blocked / restart /
  postmortem-failed / queue-drained (`0` disables). macOS `osascript`, Windows
  toast, Linux `notify-send`; fire-and-forget, never alters control flow.
- **xbar / SwiftBar** menu-bar plugin — see `tools/xbar/README.md`.

### V2 migration: best-of-N (worktrees + model-diverse)

The slot abstraction is deliberately shaped so V2 is additive. A **slot**
is `{ id, home, worktree?, model? }`:

- **V1.1 uses** `{ id, home }` — one project per slot, cwd = the project
  repo, each slot on its own isolated home.
- **V2 best-of-N adds** `worktree` (N worktrees of ONE repo) + a fan-out
  stage (N attempts of one task) + an evaluate/merge-the-winner stage.
- **Model-diverse best-of-N (target):** because each slot home has its own
  `settings.json` `env` block, slots can pin **different models/providers**
  (`ANTHROPIC_MODEL` / `ANTHROPIC_BASE_URL`). So V2 can run the same task
  across N *different models* in N worktrees and pick the winner. The
  slot-home bootstrap already accepts a per-slot model hook (from the
  `per-slot-worker-homes` sub-plan); V2 wires it through and adds the
  evaluation stage.

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

## Repo-tree invariant

Watchdog state lives **only** under
`~/.ilk-data/projects/<key>/runtime/watchdog/`. Never write anything
into the project tree, and **never modify any repo-tracked file**
(including `.gitignore`, `.gitattributes`, `README.md`, or any other
versioned file) to accommodate watchdog artifacts.

If you find a legacy in-project `.ilk-watchdog/` directory from an
older skill version, the **only** valid actions are:

```bash
# Option A — direct removal:
rm -rf <project>/.ilk-watchdog

# Option B — run the migrator (moves any salvageable state to ~/.ilk-data/):
python3 <skill-root>/../tools/migration/migrate_project_runtime_dirs.py \
    --project . --apply
```

Adding `.ilk-watchdog/` to the project's `.gitignore` is **wrong** — it
bakes the existence of skill state into the project repo. The correct
invariant is "skill state does not exist in the project at all." If
`.gitignore` already mentions these paths from a previous mistake,
that's a separate cleanup; do not add new entries.

## Known limitations

- **macOS/Linux `--detach` requires `screen`**: the `--detach` flag
  uses GNU `screen` to create a detached session. If `screen` is not
  installed, watchdog prints an error and refuses to detach. Install
  with `brew install screen` (macOS) or `apt install screen` (Linux),
  or run in foreground mode without `--detach`.
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
- **V2 best-of-N scheduler** — the V1.1 scheduler (above) already runs
  projects in parallel via the slot pool. V2 adds worktree-per-slot
  (N worktrees of one repo) + fan-out/evaluate-merge for model-diverse
  best-of-N; see the V2 migration section above and
  `docs/future-work/best-of-N.md`.

## See also

- `<skill-root>/ilk-feedback/SKILL.md` — the classifier this skill
  consumes.
- `<skill-root>/ilk-launcher/SKILL.md` — the launcher this skill
  invokes on whitelist hits.
