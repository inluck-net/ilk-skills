# Changelog

Releases are cut as annotated git tags. `git tag -l -n20 <tag>` shows the full
message for any release; `git log <old>..<new>` shows the commits between two.

This file records highlights. It is **not** exhaustive per-tag — the tags
themselves are the authoritative record.

## Recent releases

| Version | Date | Highlights |
|---|---|---|
| v0.9.62 | 2026-08-14 | The tray stops naming a **blocked** sub-plan as the one being worked. `status_all._resolve_next_subplan` skipped only `shipped`, so the first non-shipped sub-plan won — and `blocked` is non-shipped. Observed on gh-resolve: the SwiftBar panel read `2/4 shadow-mode-provisioning running on mimo-v2.5-pro` while pid 57457 (run `20260814-013546`) was in fact working `first-party-means-our-repo` at 1/5; `shadow-mode-provisioning` had been `blocked` at 2/4 for hours. `loop_status.py` had it right, so the two disagreed about the same question — and `plan_status.master_has_runnable` already carried the rule in its docstring (`plan_status.py:177-181`): *"a blocked sub-plan is outstanding work, but it is NOT runnable — nothing the loop does will advance it until a human unblocks it."* Both call sites were wrong for the same reason: the active-master branch feeds the tray, and the queued-master branch feeds `manually_runnable`, so a blocked-only queued master advertised work the loop could never pick up. Fixed by **importing** `plan_status._RUNNABLE_SUBPLAN_STATUSES` rather than re-declaring the set — a second literal is precisely how these two drifted — with a test asserting the import is the source, by value plus a source check rather than object identity (identity fails spuriously in a full-suite run, where the two modules load via different `sys.path` entries). Note this is a *different* cause from v0.9.61's phantom: there the PID had been recycled onto an unrelated process; here the PID is a genuine live runner and the sub-plan label alone was wrong. A third cause remains open (`8b73476e` / `d1426c98`): tests that spawn a real runner against a pytest tmp project register it in the production data root, where it is correctly counted as a live loop because it *is* one — 55 of 75 project dirs are now such junk |
| v0.9.61 | 2026-08-13 | The menu bar stops counting loops that ended weeks ago. v0.9.55 taught the *bash* side to verify the process behind a pidfile (`_ilk_pid.sh:ilk_pid_alive`) rather than ask `kill -0`, but the Python status readers were missed — and `_ilk_pid.sh`'s own comment asserted otherwise ("Mirrors pid_health.pid_command_alive … already used by status_progress/status_all"), while `pid_command_alive` was in fact called from **0 of 4** sentinel-liveness sites. State-gating cannot close this gap on its own: it only covers runs that reached `Finalize-Sentinel`, and a run killed before that keeps `state="running"` forever, leaving the PID as the only remaining evidence. A gh-triage sentinel written 2026-07-08 named PID 18920, which on 2026-08-13 at 11:14:49 the OS handed to an unrelated `zsh … pytest` shell: the SwiftBar title read `ilk 2*` against one live loop, and because the phantom's master was empty its row showed no step and no sub-plan — so the panel appeared to contradict its own icon. `pid_health` gains `pid_cmdline` (full argv via `ps -ww` / `/proc/<pid>/cmdline` / CIM — the *base* name is `bash` for every runner and so cannot tell one from any other shell, which is why the existing `pid_command_alive` was the wrong instrument) and `ilk_pid_alive`, now used by `status_all` (tray/xbar/dashboard), `ilk_watch`, the launcher's `status_all`, and `status_progress`. An unreadable command line still falls back to bare liveness — over-reporting a run as live is the safe direction, since the stale-running path is what parks a project. A stale sentinel now surfaces as `!` stale-running instead of as a running loop. Five status tests asserted liveness on `os.getpid()` and went red on the new semantics, correctly: the pytest process is exactly the sort of unrelated command a recycled PID lands on. They and six neighbours take a new `live_ilk_pid` fixture that spawns a genuinely runner-shaped process, and a test pins the pattern list to `_ilk_pid.sh`'s, since a divergence between the two is a status display that contradicts the scheduler |
| v0.9.60 | 2026-08-12 | `plan_lint` stops treating a directory argument as a scope. `_is_whole_suite_command` counted *any* positional arg as scoping, so `pytest tests/ -q` escaped `lint_wholesuite_gate_baseline` entirely while a bare `pytest -q` was caught — it judged the gate form almost nobody writes and skipped the two most common gates in the corpus (`python3 -m pytest tests/ -q`, 33 occurrences; `pytest tests/ -x -q`, 17). A directory scopes nothing the lint cares about: pytest collects the whole tree, so one collection error under it fails the gate — and 28 of the 47 hidden sub-plans declare that gate in **frontmatter**, which re-runs at *every* step, so a red tree false-blocks every step and can drive the project to `stuck-no-progress`. The old rule was also accidentally inconsistent — `"tests"` sits in `_NON_PATH_TOKENS`, so `pytest tests -q` was whole-suite while `pytest tests/ -q` was not; a trailing slash decided it. Now a directory tree is whole-suite, a single file and a `::node_id` stay scoped, `-k`/`--deselect` still scopes, and value-taking flags no longer leak their argument into the path list. Over 333 real sub-plans: effectively-whole-suite gates judged **84 → 123**, findings **208 → 306**. Still open (`b37ce4609f15cd21`): the `baseline-green` escape is accepted on the presence of the phrase and never verified, which silences 26 of those 123 |
| v0.9.59 | 2026-08-12 | Diagnosability: one command now answers *"why is nothing running for this project?"*. New read-only `/ilk-doctor` walks 8 gates in order and stops at the first blocker, printing the artifact each gate consulted — and its primary signal is progress across **two** samples, because a 15-minute foreground gate is byte-identical to a stall in any single one. `plan_lint`'s gate lints can finally see per-step `local_checks`: 12 of 13 call sites read frontmatter only, so ~11 gate lints inspected roughly a fifth of the gates they claimed to cover (the same `pytest -q` gate scored 1 finding in frontmatter and **0** in a per-step block — which is why a gh-resolve step gated on a baseline-red 2152-test suite passed lint clean, then failed three times across ~90 min). Re-linting 333 sub-plans surfaces 123 newly-visible findings, 85% of them whole-suite gates with no verified baseline. A new `lint_redundant_gate` stops the defect being authored at all: a step body must not instruct a command its own `local_checks` already runs, since the driver runs gates *after* the commit. `stop.sh` no longer kills itself, a bystander shell whose argv merely contains the project path, or the launchd-supervised scheduler — killing that one made launchd restart it and re-dispatch the still-runnable master, so "stop" caused a relaunch; it now verifies the tree is gone before reporting success. Postmortems regain their tail under the `runs/` layout, and a run that left no JSONL records says so instead of raising `KeyError` |
| v0.9.58 | 2026-08-12 | A project's declared `iteration_timeout_min` is finally honoured: `read_project_config` never looked at `<project_root>/.ilk-launch.json` — the location `/ilk-plan` step 8a already documents — so gh-resolve declared 60, silently got the 30-minute default, and had its last step killed mid-suite twice |
| v0.9.57 | 2026-08-12 | One runner per project, enforced by the kernel: the runner now acquires a per-project `flock` and re-execs under it, so a second runner is refused (exit 3) before it writes a sentinel, a run dir or `running.pid` — closing the fan-out that put 10 runner processes and 4 concurrent pytest interpreters in one working tree. Dispatch-side busy checks additionally ask the process table rather than trusting a `running.pid` that tracked 1 of 10 live runners. Portable via `fcntl.flock` + cleared `FD_CLOEXEC` (`flock(1)` does not exist on macOS), and the kernel releases it even on `SIGKILL` |
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
