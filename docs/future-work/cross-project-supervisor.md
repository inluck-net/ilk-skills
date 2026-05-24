# Cross-project supervisor (future work)

**Status**: draft / future-work; not scheduled.
**Last touched**: 2026-05-24
**Origin**: design discussion in a planning session, after observing
that two MASTERs across two projects had no auto-chain mechanism.

## Problem

Today ilk's auto-resume is bounded by one project:

- `ilk-watchdog/scripts/watchdog.ps1` reads ONE project's
  `last-exit.json` and advances within that project.
- `promote_next_master.py` advances within a project's MASTER queue
  when the current one ships.
- `launch.ps1 --all` launches every registered project IN PARALLEL,
  not sequentially.

When you have multiple projects with queued MASTERs and want them to
run **sequentially** (to share API rate limits + CPU rather than
fight them), there is no built-in mechanism.

## Current workarounds (no code change)

- **Manual sequential**: launch project A, wait for its window to
  show all-shipped, manually launch project B. For 2-3 projects this
  is fine.
- A small one-off bash/ps1 wrapper around two `launch.*` calls if you
  want zero-touch tonight.

## Trigger conditions (when to actually build this)

Don't build until ONE of these is true:

- You have ≥4 projects with queued MASTERs at the same time.
- You hit a concrete "ilk was busy on a non-urgent project but I
  needed an urgent fix to start somewhere else" pain. Manual
  stop/launch dance painful enough to justify ~500 LOC of new code.
- mac-port has shipped and you find yourself wanting cross-project
  sequencing on macOS too — do v0 for both platforms in one batch to
  amortize the work.

If the only driver is "it would be nice to see all queued masters in
one view", build the DASHBOARD-only version first (read-only
enhancement to `status_all.py` showing each project's queue + global
priority order). It's cheap, much lower commitment, and tells you
whether you actually want execution chaining.

## Design — derived queue (no new state file)

**Key principle**: do NOT introduce a new registry file. Each
project's MASTER frontmatter already carries everything needed
(`priority`, `created`, `status`). The supervisor is a pure consumer
— it reads, it does not maintain its own cross-project state.

```python
def build_global_queue():
    queue = []
    for proj in read("~/.cursor/skills/ilk-launcher/projects.json")["projects"]:
        for master in glob(f"{proj.ilk_data_dir}/plans/MASTER-*.md"):
            if master.status != "shipped":
                queue.append({
                    "priority":     master.priority or 999,
                    "created":      master.created,
                    "project_path": proj.path,
                    "project_name": proj.name,
                    "master_path":  master.path,
                    "master_status": master.status,  # active | queued
                })
    return sorted(queue, key=lambda x: (x["priority"], x["created"]))
```

Source-of-truth stays in MASTER frontmatter. To re-order: edit the
MASTER's `priority:` field. To pause: set `pause_after_ship: true` on
a MASTER; supervisor stops after that one finishes (already a
defined frontmatter field per `master-template.md`).

## Supervisor process loop

```
loop:
  q = build_global_queue()
  if q is empty: exit 0

  target = q[0]   # highest priority, FIFO within priority

  if a PID is alive for target.project_path:
      poll <project>/.ilk-launcher/last-exit.json   # observe only

  elif target.master_status == "queued" and no active master in same project:
      promote it to active in-place (edit frontmatter)
      launch.* -ProjectPath target.project_path

  elif target.master_status == "active":
      launch.* -ProjectPath target.project_path

  poll <project>/.ilk-launcher/last-exit.json:
    state in {all-shipped, already-shipped}:
        # this master shipped; per-project watchdog promotes next
        # within the same project if any; otherwise project goes
        # idle. Re-loop, re-derive global queue, pick next.
        continue
    state in {merge-conflict, local-checks-stuck}:
        blacklist this project until next supervisor restart
        continue
    state in {no-progress, timeout, budget-exhausted}:
        # per-project watchdog handles restart; supervisor just polls.
        sleep poll_interval
```

## Interaction with per-project watchdog

Two clean modes, mutually exclusive at runtime:

| Mode | Per-project watchdog | Cross-project supervisor |
|---|---|---|
| **Standalone (today)** | one per running project; auto-advances within project | not running |
| **Supervisor mode** | still runs per project; advances within the active project's MASTER queue | runs at user scope; picks which project's loop should be alive at each moment |

Recommendation for v0: **supervisor on top of per-project watchdog**.
Supervisor only picks "which project". Intra-project advance stays
with the existing watchdog. Cleaner separation, lower risk.

Boundary behavior:

- Supervisor calls `launch.*` on project A. Per-project watchdog of A
  is started by `launch.*` as usual; advances within A.
- A's last queued MASTER ships, A's watchdog exits cleanly (no more
  work in A).
- Supervisor sees A's `last-exit.json` = `all-shipped` AND A's PID
  dead → rescans global queue → picks project B → launches it.

## Anti-goals

- **No web UI.** CLI only.
- **No system-service daemon.** User-space supervisor process; user
  opens a terminal and runs it (with detach option).
- **No mid-iteration preemption** ("pause A on step 3, switch to B
  urgently"). Too complex; preemption only at iteration boundaries —
  already what happens naturally if you stop A and start B manually.
- **No persistent supervisor state file.** Restart-safe by being
  purely derived from MASTER frontmatter.

## Rough scope

If/when scheduled, looks like:

| Sub-plan | LOC | Notes |
|---|---|---|
| `derive_global_queue.py` | ~80 | Pure function: read projects.json + each project's MASTERs, return sorted candidates |
| `cross_project_supervisor.ps1` | ~150 | Main loop, polling, blacklist tracking, detached-window option |
| `cross_project_supervisor.sh` | ~150 | macOS / Linux counterpart (after mac-port lands) |
| `stop_supervisor.ps1` + `.sh` | ~80 total | Kill the supervisor process |
| `status_all.py` enhancement | ~50 | Add "global queue position" + priority columns |
| Docs sweep | ~30 | New `ilk-supervisor` skill OR a section under `ilk-watchdog/SKILL.md` |

**Total: ~540 LOC**. One master plan, ~5-6 sub-plans, 2-3 days of
loop time on top of a working mac-port.

## Open questions for the implementer

1. **Detached vs foreground process**? Detached (like the launcher
   windows) = "set and forget". Foreground = explicit "I see it
   running". Default: detached, with a PID file at
   `~/.ilk-launcher/supervisor.pid` (user-scope, not per-project).
2. **Separate log file** at `~/.ilk-launcher/supervisor.log`, or
   prefix entries into each project's existing log? Default: separate
   — easier to grep "what did supervisor do across the night".
3. **`--priority N` CLI flag on `/ilk-plan`**? Today priority
   defaults to `null` (=999). For supervisor mode to be useful, users
   need an easy way to say "this batch jumps the queue". Default
   behavior would be FIFO-by-`created`.
4. **Single global lock vs configurable concurrency budget**? V0:
   single lock (exactly 1 project running). `--max-concurrent N` is a
   v1 follow-up.
5. **What about meta-projects?** Each meta-umbrella has its own
   `project_key` — supervisor treats it as one entry in the queue.
   No special handling needed beyond what derive_global_queue.py
   already gets from the resolver.

## Reference reading (when picking this up)

- `skills/ilk-loop/scripts/promote_next_master.py` — the
  intra-project promoter; same `(priority, created)` comparator goes
  into the derived global queue.
- `skills/ilk-watchdog/scripts/watchdog.ps1` — existing per-project
  watchdog; supervisor coexists with it.
- `skills/ilk-launcher/scripts/status_all.py` — the dashboard
  candidate for the read-only enhancement (much cheaper first step).
- `skills/ilk-loop/templates/master-template.md` — frontmatter spec
  (`priority`, `pause_after_ship`, `status`); these are supervisor's
  inputs.
