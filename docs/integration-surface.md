# ilk integration surface — building tools on top of ilk

This document describes the **consumer-facing control & observability surface**
of ilk-skills: how an *external* tool (e.g. a dashboard, a chat bot, or a mobile
remote-controller) can **observe** and **control** the ilk loop **without
modifying ilk internals**.

It is written for someone building *on top of* ilk, not someone hacking *inside*
it. Scripts cited here are the source of truth — line numbers drift, so confirm
against the named file before depending on a detail.

> **TL;DR for tool builders:** ilk exposes **no HTTP API, no daemon socket, and
> no auth layer.** The integration surface is **(a) a handful of Python/PowerShell
> CLIs that emit JSON, (b) well-known JSON/JSONL files under `~/.ilk-data/`, and
> (c) the plan markdown files themselves.** You observe by running the status
> scripts / reading the files; you control by launching/stopping the runner and
> by **editing plan files between iterations**. There is **no way to steer a
> running iteration mid-flight** (see §7). A controller is therefore a thin shim
> that shells out to these CLIs and reads these files — plus whatever transport
> (HTTP/tunnel) and auth *you* add around them.

---

## 1. Mental model

```
  author plans            loop drives                observe / control
  ───────────             ───────────                ──────────────────
  /ilk-plan  ─►  MASTER-*.md + sub-plan .md  ─►  run_ilk_loop_claude.ps1
                 (under ~/.ilk-data/.../plans)      │  reads plans at the START
                                                    │  of each iteration only
                                                    ▼
                                          per-iteration: fresh agent session,
                                          fixed prompt, runs local_checks/gates,
                                          commits with [plan:<slug>#step-N]
                                                    │
                                                    ▼
                       writes ► last-exit.json (sentinel) + .ilk-loop.log (JSONL)
                                + running.pid / watchdog.pid
```

- **Plans are the program.** The unit of work is a `MASTER-*.md` plus its
  sub-plan `.md` files. Status lives in their YAML frontmatter.
- **The loop reads plans once per iteration**, at the top, via `loop_status.py`.
- **Everything observable is a file or a script that reads files.** No live API.
- **Control is coarse-grained:** launch, stop, and edit-plans-between-iterations.

---

## 2. Observability surface

### 2.1 `loop_status.py` — queue state (single project)
`skills/ilk-loop/scripts/loop_status.py`

```
python loop_status.py [--json]
```
Resolves the project from cwd / the external `~/.ilk-data` convention. **Exit
codes are the primary signal:**

| code | meaning |
|---|---|
| `0` | all sub-plans shipped |
| `1` | pending work exists (normal) — `next` identifies it |
| `2` | config error (no `MASTER-*.md` found / invalid project) |

`--json` shape (keys as emitted): `master`, `master_status`, `plans_dir`,
`subplans[]` (each: `fname`, `slug`, `status`, `current_step`, `estimated_steps`,
`repo`, `verification_tier`), `active`, `queued`, `shipped`, `queue_exit`,
`stalled`, `compile_only_summary`, `next` (`fname`/`status`/`cur`/`est`/`repo`)
or `null`.

### 2.2 `status_progress.py` — rich progress + health (single project)
`skills/ilk-launcher/scripts/status_progress.py`

```
python status_progress.py --project-path <abs-root> [--json]
```
`--json` shape (verified firsthand):

```jsonc
{
  "project":  { "name", "root" },
  "plans":    { "dir", "master" },
  "current":  { "slug", "status", "current_step", "estimated_steps" } | null,
  "summary":  { "shipped", "in_progress", "pending", "remaining_steps",
                "pace_min_per_step": float|null, "eta_minutes": float|null },
  "processes":{ "launcher_pid": int|null, "launcher_alive": bool|null,
                "watchdog_pid": int|null, "watchdog_alive": bool|null },
  "sentinel": { "state", "stale": bool, "pid": int|null, "last_exit_path" },
  "rows": [ { "slug", "status", "current_step", "estimated_steps",
              "verification_tier", "is_current": bool } ]
}
```
- **`processes` + `sentinel`** are how you tell *running* from *idle* from
  *stale-running* (sentinel says `running` but the PID is dead).
- **`pace`/`eta`** come from a rolling window of `[plan:...#step-N]` commit
  timestamps in `git log`; `null` until ≥2 step-commits exist.

### 2.3 `status_all.py` — all projects
`skills/ilk-launcher/scripts/status_all.py` — reads the launcher's
`projects.json` registry and prints a per-project table
(`project | state | plan-status | window-pid`), where `state` ∈
`running | stale-running | idle`. Always exits `0`.

### 2.4 Sentinel — `last-exit.json`
`~/.ilk-data/projects/<key>/runtime/last-exit.json` — the terminal record of the
**last** run. Keys: `state`, `pid`, `run_id`, `started_at`, `ended_at`,
`iterations`, `project_path`, `cli`, `jsonl_log`. `state` is the stop reason
(`all-shipped`, `max-iterations`, `no-progress`, `timeout`, `budget-exhausted`,
`interrupted`, …). **A `running` state with a dead PID is a stale sentinel —
treat as crashed, not healthy.**

### 2.5 Per-iteration log — `.ilk-loop.log` (JSONL)
`~/.ilk-data/projects/<key>/logs/.ilk-loop.log` — one JSON object per iteration:
`run_id`, `cli`, `iteration` (0 = pre-loop check), `timestamp`, `project`,
`model`, `duration_sec`, `exit_code`, `completed`, `budget_exhausted`,
`new_commits_total`, `new_commits` (per-repo map), `log` (path to `iter-NN.log`),
`stop_reason`, and `local_checks[]` (each: `slug`, `step`, `outcome`,
`exit_code`, `raw`). This is your event stream for a live feed.

---

## 3. Control surface

### 3.1 Launch — `launch.ps1`
`skills/ilk-launcher/scripts/launch.ps1`
```
.\launch.ps1 -ProjectPath <path> [-MaxIterations n] [-IterationTimeoutMin n]
             [-Force] [-DryRun] [-WorkerEngine claude|codex] [-EnableMcp ...] [-DisableMcp ...]
```
Spawns a **detached** window running `run_ilk_loop_claude.ps1`, writes
`runtime/launcher/running.pid`, and refuses to start if a live PID already
exists (`-Force` overrides). (`launch.sh` is the macOS/Linux twin.)

### 3.2 Stop — `stop.ps1`
`skills/ilk-launcher/scripts/stop.ps1` — reads the PID file, tree-kills
(`taskkill /T /F`), removes the PID file. This is your **graceful stop**; the
run records a sentinel with `state: interrupted`.

### 3.3 Cross-project scheduler
`skills/ilk-watchdog/scripts/scheduler.ps1` (`/ilk-schedule`) — one daemon drains
every registered project's queue FIFO, pool cap 1, with a per-project sentinel
mutex and a global budget ceiling.

### 3.4 Steering between iterations — **edit the plan files**
This is the *only* programmatic steering. Edits to plan files under
`~/.ilk-data/projects/<key>/plans/` are picked up at the **start of the next
iteration**:
- Flip a sub-plan `status:` to `blocked` to pause it; to `pending`/`queued` to
  (re)enable.
- Adjust `current_step:` to rewind/advance.
- Drop a **new** sub-plan file + register it in the MASTER to queue more work.
- Create a new `MASTER-*.md` with `status: queued`; the watchdog promotes it
  after the active master ships.

See §7 for what this explicitly does **not** let you do.

---

## 4. Data-home layout

Root: `~/.ilk-data/` (override via `$ILK_DATA_HOME` / `$ILK_DATA_DIR`).
Resolver + key derivation: `skills/ilk-loop/scripts/ilk_paths.py`.

```
~/.ilk-data/projects/<project-key>/
  ├── plans/                 MASTER-*.md + YYYY-MM-DD-slug.md
  ├── runtime/
  │   ├── last-exit.json     terminal sentinel (§2.4)
  │   ├── launcher/running.pid
  │   └── watchdog/watchdog.pid
  └── logs/
      ├── .ilk-loop.log      JSONL summary, all runs (§2.5)
      └── runs/<run-id>/     per-iteration logs + HEAD snapshots
```

**`<project-key>`** = `ilk_paths.py:project_key()`: lowercase the absolute root,
replace non-alphanumerics with `-`, hash-suffix if >80 chars. e.g.
`C:\mywork\github\inluck-net\ilk-skills` →
`c--mywork-github-inluck-net-ilk-skills`. The key is **stable and derivable** —
a controller can compute the data-home path for any project from its root.
`find_plans_dir()` prefers the external `~/.ilk-data/.../plans/` over an in-tree
`docs/plans/`.

---

## 5. Plan file format (frontmatter is the contract)

Templates: `skills/ilk-loop/templates/master-template.md` and
`subplan-template.md`. The fields a controller reads/writes most:

- **MASTER:** `status` (`draft|queued|active|paused|shipped`), `created` (FIFO
  sort key), `priority`, `supervised_only` (never auto-promoted), `goal`.
- **Sub-plan:** `plan` (slug), `status` (`pending|in-progress|blocked|shipped`),
  `current_step`, `estimated_steps`, `depends_on[]` (prior slugs that must be
  `shipped`), `verification_tier` (`loop-verified|compile-only|device-manual`),
  `local_checks[]` (`command`+`timeout`), `scope_paths[]`.

Status vocabulary and tier meanings are detailed in
`skills/ilk-loop/references/decomposition-principles.md`.

---

## 6. Verification & gates

- **`run_local_checks.py`** (`skills/ilk-loop/scripts/`) —
  `python run_local_checks.py --project <path> --slug <slug> [--step N]`. Emits
  JSON (`commands[]` with per-command `exit_code`/`stdout`/`stderr`/`timed_out`,
  `overall_outcome`). Exit `0` pass / `1` fail / `2` config error. **This is how
  you re-verify a gate independently** (e.g. to confirm a "blocked" run is a real
  failure vs a transient false-stop).
- **Ship report** — `generate_ship_report.py` produces a GREEN/YELLOW/RED
  verdict doc as the final gate.
- **Tiers** — `loop-verified` (runtime gate proves it), `compile-only`
  (build/type-check only; human must verify behaviour), `device-manual` (needs a
  physical device / external app). A controller surfacing "shipped" should also
  surface the tier — `shipped` + `compile-only`/`device-manual` means *human
  verification still pending*.

---

## 7. ⚠️ No mid-iteration steering (load-bearing constraint)

**ilk-loop is strictly plan-file-driven and reads the plan once per iteration.**
A controller **cannot** inject an instruction into an iteration already in
flight. Concretely:

- The runner's prompt is a **static** value fixed at launch (default
  `/ilk please continue the active plan`); it is **not** re-read from disk
  between iterations.
- Each iteration runs a fresh agent session for up to `IterationTimeoutMin`
  (default 30 min) as a **black box** to the loop. There is no command file,
  pipe, or socket to nudge it.
- Plan-file edits take effect **only at the next iteration boundary**, not
  mid-iteration.
- The only mid-iteration intervention is a hard **stop** (kill the PID), which
  ends the run (sentinel `interrupted`); the watchdog/scheduler then relaunches.

**Implication for an "append instruction to a running agent" feature** (the
Cursor-iOS-style steering): it does **not** exist today and would require new
ilk work — e.g. a per-iteration prompt re-read from a controller-written file, or
a shorter iteration cadence so edits land sooner. Design around between-iteration
steering first; treat live steering as a new capability to spec, not a given.

---

## 8. PR / CI integration

PR operations are **not** in ilk-skills core. The `gitee-api` skill provides
open/merge/status/diff against gitee.com; sub-plan frontmatter only carries CI
metadata (`ci_status_endpoint: gitee`, `ci_required`, timeouts). A controller
that wants "review & merge a PR from the UI" should drive `gitee-api` (or the
gitee REST API) directly, alongside ilk's status — they are complementary, not
unified.

---

## 9. What ilk does NOT provide (so you know what to build)

| You want | ilk gives you | You must add |
|---|---|---|
| Remote access | local CLIs + files | transport (HTTP server / tunnel / relay) |
| Auth / multi-user | none | your own auth layer — this grants agent control, treat as privileged |
| Live progress push | JSONL file you can tail | a watcher → websocket/SSE bridge |
| Steer a running agent | between-iteration plan edits only | new ilk capability (§7) |
| PR review/merge | nothing (see §8) | `gitee-api` integration |

A minimal controller backend is therefore a thin service that: resolves a
project's `<key>`, shells out to the status CLIs / tails the JSONL, exposes
launch/stop, writes plan-file edits for between-iteration steering, and adds the
transport + auth ilk deliberately leaves out.

---

## 10. Installed hooks (Claude Code only)

`install.sh --apply` symlinks `hooks/*.sh` into `~/.claude/hooks/` and
reconciles `~/.claude/settings.json` to register them under
`PreToolUse` → `Bash`.  Foreign entries in `settings.json` are preserved
(idempotent, dry-run safe).  Currently one hook:

| Hook | File | Behaviour |
|---|---|---|
| Full-suite guardrail | `hooks/no-full-suite.sh` | Denies unscoped `pytest`/`npm test`/`cargo test`/`go test` runs. Escape hatch: `ILK_ALLOW_FULL_SUITE=1` (inline or exported). See the file's header comment for rationale. |

Cursor and Codex have no equivalent hook mechanism.

---

## Source-of-truth pointers

- `skills/ilk-loop/scripts/loop_status.py` — queue state + exit codes
- `skills/ilk-launcher/scripts/status_progress.py` / `status_all.py` — dashboards
- `skills/ilk-loop/scripts/run_ilk_loop_claude.ps1` — the loop; sentinel + JSONL writers; iteration structure (§7)
- `skills/ilk-loop/scripts/ilk_paths.py` — data-home + `project_key()`
- `skills/ilk-loop/scripts/run_local_checks.py` — gate runner
- `skills/ilk-loop/templates/{master,subplan}-template.md` — frontmatter contract
- `skills/ilk-loop/references/decomposition-principles.md` — status/tier semantics
