# Changelog

Releases are cut as annotated git tags. `git tag -l -n20 <tag>` shows the full
message for any release; `git log <old>..<new>` shows the commits between two.

This file records highlights. It is **not** exhaustive per-tag — the tags
themselves are the authoritative record.

## Recent releases

| Version | Date | Highlights |
|---|---|---|
| v0.9.56 | 2026-08-12 | The scheduler stops re-dispatching a drained-but-blocked master (96 run dirs in 8h, 90 empty — a `blocked` sub-plan is outstanding work but not *runnable*), and the runner stops reporting such a batch as "all shipped"; an iteration's own command file no longer tells the worker to background long gates that can never resume across the iteration boundary; the auto-quarantine path no longer dies on an unbound `$SKILL_ROOT`, a crash that also erased the JSONL record the postmortem needs; `/ilk-plan --yes` skips the grouping wait for one invocation — a flag, not a timer |
| v0.9.55 | 2026-08-10 | A recycled PID no longer wedges the queue: the sentinel, the scheduler's own lock and the launcher all verify the process behind a pidfile instead of asking `kill -0`. A 20-day-stale `running.pid` whose PID had been reused by an unrelated shell held one project at `skip-busy` on every poll. `ilk_pid_alive` is now one shared helper rather than a per-script copy |
| v0.9.54 | 2026-08-10 | A watchdog can no longer outlive the loop it supervises: `loop_status` was invoked with an unsupported `--project` flag so `advance` was unreachable and every drained queue span forever; both keep-alive paths are now liveness-aware and bounded |
| v0.9.53 | 2026-08-10 | Loop fidelity: worker slot homes get `commands/` so slots above 0 can run at all; a never-ran or throttled run no longer parks the project as `stuck-no-progress`; a timed-out iteration preserves its dirty tree and reports what consumed the budget; the dashboard resolves the real repo so pace/ETA work; `plan_lint` flags gates whose selector can silently select nothing |
| v0.9.52 | 2026-08-04 | Public-release readiness: internal IDs scrubbed, stale tests fixed, first CI, README as an OSS landing page |
| v0.9.51 | 2026-08-03 | `local_checks` gate actually runs on a shared remote (trailer-independent target discovery, verified end-to-end); one `run_id` per run; MASTER registry rows reconciled |
| v0.9.50 | 2026-08-03 | Scheduler skips a resolved-but-absent repo path instead of re-dispatching it forever; test fixture + runner hang fixes |
| v0.9.49 | 2026-07-30 | Dispatch planner verification when a master drains (closes the last manual join); AC-6 escalation guard |
| v0.9.48 | 2026-07-28 | Same-day plan slugs (`YYYY-MM-DD<letter>-`) parse across all eight call sites via shared `plan_slug.py` |
| v0.9.47 | 2026-07-26 | `supervised_only` scope guard: decoupled from autonomy tiers, narrow trigger enforced in `plan_lint` |
| v0.9.46 | 2026-07-04 | Native-IO hygiene + watchdog stale-non-success race fix |
| v0.9.45 | 2026-07-02 | macOS detached-runner verified PASS-with-caveat |
| v0.9.44 | 2026-07-01 | Real detached-runner steer-hook smoke test |
| v0.9.42 | 2026-07-01 | Hermetic `ilk-loop` test suite (path-length / pytest-timeout / powershell guards) |
| v0.9.41 | 2026-07-01 | Steer-hook bash parity (`steer_hook.sh` + `.sh` runner wiring) |
| v0.9.40 | 2026-07-01 | Operator steer-hook + vision-as-a-tool + vision-stall classification |

## Earlier milestones

The notes below were previously kept in `README.md`. They describe the shape of
the toolkit as it grew, and are preserved here rather than at the top of the
README.

### v0.8.13 — empty-repo resilience, `/ilk-schedule`, macOS monitoring

- **Empty-repo / stale-state resilience.** A run pointed at a freshly
  `git init`'d repo (branch but zero commits) no longer cascades into a hard
  stop: the runner degrades a missing `HEAD` to `(unknown)` instead of leaking a
  fatal, `collect.py` classifies a run that died before iter 1 as `interrupted`
  (instead of failing to produce a postmortem), the watchdog ignores a
  stale/contradicted sentinel and cross-checks `loop_status` before declaring
  "queue drained", `promote_next_master` treats `pending` as a `queued` alias,
  and the launcher's active-guard ignores a finished-but-lingering loop window.
- **`/ilk-schedule`** *(new slash command)* — launches the single cross-project
  scheduler detached, the way `/ilk-run` launches the per-project loop+watchdog.
  Use it to drain **all** projects' queues from one supervisor instead of one
  watchdog per project. `scheduler.{ps1,sh}` gained `-Detach` / `--detach`.
- **Monitoring surfaces** (esp. for macOS, where detached runs are headless
  `screen` sessions with no visible window):
  - `loop_status.py --json` + `status_all.py` — machine-readable per-project and
    all-projects status.
  - `/ilk-status --watch` (bash `--watch` / PowerShell `-Watch`) — a
    self-refreshing all-projects + slots cockpit.
  - **Desktop notifications** on watchdog/scheduler events (ship, blocked,
    restart, postmortem-failed, queue-drained). macOS `osascript`, Windows
    toast, Linux `notify-send`. Gated by `ILK_NOTIFY` (`0` disables).
  - **tmux slot sessions** — set `ILK_MULTIPLEXER=tmux` so the scheduler runs
    each slot in a named `ilk` session; `screen` stays the fallback.
  - **xbar / SwiftBar menu-bar plugin** (`tools/xbar/`) — glanceable macOS
    menu-bar status.

### v0.8.10 — planner/worker cost split + cross-project scheduling

- **`claude-worker` launcher engine.** Route the detached loop under the cheap
  worker home (`~/.claude-worker`) instead of the planner's official provider,
  for unattended / cost-sensitive runs (`--engine claude-worker`, or
  `{ "worker_engine": "claude-worker" }` in `.ilk-launch.json`). See
  [`skills/ilk-launcher/references/worker-engine.md`](skills/ilk-launcher/references/worker-engine.md).
- **Machine-wide default opt-in.** Set `ILK_DEFAULT_ENGINE=claude-worker` to
  default a whole machine to the worker without editing every project.
  Precedence: CLI `--engine` > `.ilk-launch.json` > `ILK_DEFAULT_ENGINE` >
  `claude`. The shipped default stays `claude`; a real `claude-worker` launch
  fails closed if the worker home isn't bootstrapped.
- **Cross-project scheduler (V1).** A single daemon
  (`skills/ilk-watchdog/scripts/scheduler.{ps1,sh}`) drains every project's
  queue FIFO, one at a time, routed through the worker engine — per-project
  sentinel mutex, poll/idle/auto-wake, pool cap 1, global budget ceiling,
  non-starving blacklist skip.
- Earlier: a one-command `claude-worker` PATH installer (v0.8.7) and the dual
  planner/worker Claude homes design.

### v0.5 — Codex parity

All skills and slash commands install under `~/.codex/skills/` and
`~/.codex/commands/` alongside Cursor and Claude Code; hardcoded `~/.cursor`
paths replaced with a skill-root resolver, command prompts made host-neutral,
and the launcher gained the `worker_engine` config + `--engine` override. New
`ilk-runner` skill plus `/ilk-run` and `/ilk-status` slash commands sequence
launcher + watchdog for supervised unattended runs.
