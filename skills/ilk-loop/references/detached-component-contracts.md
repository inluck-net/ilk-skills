# Detached-component runtime contracts (2026-06-16)

Canonical reference for the on-disk file contracts between ilk's detached
components. Written after three contract-violation bugs surfaced in run
`20260616-175453` (math-blocks). Each bug was a writer/reader disagreement
about the same file — this doc makes the implicit contracts explicit.

> **Target design, 2026-09-28:** `docs/architecture/loop-state-and-ownership-design.md`
> (accepted) supersedes the following parts of this document as the
> intended behaviour: iteration tree resolution (work_tree / selfmod), the
> ship-proof ledger (Contract 5), liveness (Contract 3 and every argv-pattern
> probe), red-owner attribution, and plan-status ownership. This document
> still describes the code as it stands. That design's section 7 lists every
> current divergence. A change that adds another derivation of an iteration's
> trees, work, state owner, liveness or verdict is a misleading patch; make
> the consumer read the single source instead.

## Component map

```
┌─────────────┐   launches    ┌───────────────────────────────────┐
│  launcher   │ ────────────> │          runner                   │
│ (scheduler) │               │  run_ilk_loop_claude.ps1 / .sh    │
└──────┬──────┘               └──────┬──────────────┬─────────────┘
       │                             │              │
       │ reads sentinel              │ writes       │ writes
       │ + JSONL                     ▼              ▼
       │                    ┌──────────────┐  ┌──────────────┐
       │                    │ .ilk-loop.log│  │ last-exit.json│
       │                    │   (JSONL)    │  │  (sentinel)   │
       │                    └──────┬───────┘  └──────┬────────┘
       │                           │                 │
       ▼                           ▼                 ▼
┌──────────────┐          ┌──────────────┐  ┌──────────────┐
│  watchdog    │          │  collect.py  │  │ status_all.py│
│  (ps1/sh)    │          │ (postmortem) │  │  (tray/xbar) │
└──────────────┘          └──────────────┘  └──────────────┘
```

**Key relationships:**
- The **runner** is the sole writer of both JSONL logs and the sentinel.
- **collect.py** reads JSONL logs + sentinel to produce postmortems.
- **status_all.py** reads the sentinel to determine project liveness.
- The **watchdog** reads sentinel + JSONL for health checks.
- The **launcher/scheduler** reads sentinel state to decide dispatch.

---

## Contract 1: Sentinel (`last-exit.json`)

### Format

```json
{
  "state": "running",
  "pid": 48268,
  "run_id": "20260616-175453",
  "started_at": "2026-06-16T17:54:53+0800",
  "ended_at": null,
  "iterations": 3,
  "project_path": "/path/to/project",
  "cli": "claude",
  "jsonl_log": "/path/to/log.jsonl",
  "merge_deferred": null,
  "held_by": null,
  "failed_check": null
}
```

**`failed_check`** (added 2026-09-29): present only when `state` is
`local_checks_failed` or `ship_integrity_violation`.  Carries the last
failing gate row's `{slug, step, command}` so the panel alert names the
check that failed, not the next runnable sub-plan.  Absent or `null`
for older sentinels and non-failure states.  `status_all.py` passes it
through; `render_xbar.py` uses it for the ilk-ref and the `--failed:`
submenu line.

### Who writes

- **`run_ilk_loop_claude.ps1`** — `Finalize-Sentinel` function (~line 777).
  Writes `state: "running"` at loop start; rewrites to a terminal value on
  any exit (normal, error, interrupt, budget, max-iterations).
- **`run_ilk_loop_claude.sh`** — equivalent `finalize_sentinel` function.
  The stop-reason decision (`_decide_iter_stop_reason`) consults
  `total_new` when `ITER_COMPLETED=0` (boundary kill): a productive
  boundary kill (new commits > 0) does NOT set `iter_stop_reason="timeout"`;
  the sentinel records the iteration's actual outcome (e.g. `all-shipped`).
  A barren boundary kill (zero new commits) still sets `timeout`.

### Who reads

- **`status_all.py`** — `resolve_project_status` (~lines 152-156).
  Computes `alive` from `state` + `pid_alive(pid)`.
- **`collect.py`** — `read_sentinel` (~line 204). Classifies the run's
  terminal state for postmortem.
- **Watchdog** — reads sentinel for stale/ hung detection. Also reads
  `last-launch.json`'s `log_dir` to recognise a superseded sentinel
  (one whose `run_id` precedes the latest launch's run id); a superseded
  sentinel is awaited, not classified.

### State vocabulary

| State | Meaning | Alive? |
|---|---|---|
| `"running"` | Loop is actively iterating | **Live** — check PID |
| `"shipped"` | All sub-plans shipped, clean exit | Terminal |
| `"local_checks_failed"` | A step's local_checks failed | Terminal |
| `"local_checks_failed_no_commits"` | A step's local_checks failed with 0 new commits this iteration (gate red on an unchanged tree — a red base or environment). Never set after an interrupted iteration with 0 commits (the gate is skipped; `_should_gate_iteration` returns 1) | Terminal |
| `"local_checks_environment_fault"` | A derived check (e.g. mention gate) could not run because its binary is missing from PATH (exit 126/127). The gate record carries `reason: "environment-fault: ..."`. Distinct from `local_checks_failed` (tests ran and failed) — the environment is broken, not the code. The sub-plan is NOT reverted; the project is parked. (sub-plan `an-unrunnable-derived-check-stops-once`) | Terminal |
| `"interrupted"` | The run ended without a terminal state. An operator stop (INT/TERM to the runner) adds `stopped_by: "signal:<SIG>"`; its absence means a crash or an unexplained exit. The watchdog never relaunches a sentinel carrying `stopped_by` (`relaunch_guard.py`) | Terminal |
| `"error"` | Unexpected runner error | Terminal |
| `"max-iterations"` | Hit iteration budget | Terminal |
| `"budget-exhausted"` | Hit `--max-budget-usd` cap | Terminal |
| `"quota-exhausted"` | Provider quota cap detected (`quota_detect.py`); imposed from outside the run and clears on the provider's schedule | Terminal |
| `"startup-hang"` | Pre-iteration-1 hang detected | Terminal |
| `"timeout"` | `gtimeout` killed the iteration before it completed | Terminal |
| `"ship_integrity_violation"` | A sub-plan was `shipped` with its declared gate red; the driver reverted it to `in-progress`. It parks the owning master (`park_master.py --owner-of <slug> --auto`; see "Park fields" below) **only when that revert did not happen**: a reverted sub-plan is runnable, and parking its master let the scheduler promote the next master over it (2026-10-03). Also: the worker changed the run's own master's `status` / park fields, which the driver restored from its pre-dispatch snapshot (`master_snapshot.py`; no park) | Terminal |
| `"no-progress"` | 3 consecutive iterations with zero new commits | Terminal |
| `"all-shipped"` | Every registered sub-plan is shipped **and every one is proven**; loop ended naturally | Terminal |
| `"shipped-unproven"` | Every registered sub-plan is shipped, but the ship-proof ledger holds no row for at least one — the ship claim is unverified | Terminal |
| `"blocked-no-runnable"` | All remaining sub-plans are `blocked`; nothing to dispatch. Also emitted when the master is held (non-runnable `master_status` such as `draft`), and when a human park holds the project, in which case the sentinel carries an additive `held_by: "<master filename>"` | Terminal |
| `"already-shipped"` | Nothing to do at launch time (all sub-plans already shipped) | Terminal |
| `"merge-deferred"` | A selfmod merge was deferred (live loop detected) and could not be retried; the worktree holds unmerged work. The scheduler re-dispatches the project to retry the merge. | Terminal |
| `"yielded"` | At an iteration boundary, the runner detected that *another* project on the host has a pending selfmod merge. This run yielded so the scheduler can dispatch that merge first. The sentinel carries `yielded_to: <project_key>`. Transient — the watchdog relaunches when the other project's merge lands. | Terminal |
| `"selfmod_merge_failed"` | A selfmod worktree's merge-back failed; committed work is parked in the worktree | Terminal |
| `"selfmod_live_clone_touched"` | A selfmod worker modified tracked files in the live clone; the run stops without merging and logs the file list | Terminal |
| `"work_tree_invalid"` | Master declared `work_tree:` but the path is missing, not a work tree, or shares no git objects with `--project-path` | Terminal |
| `"lock_held"` | Another runner holds this project's run lock; the unattended result file (if `ILK_MASTER` set) records `exit_state: lock_held` | Terminal |
| `"profile_unsupported"` | Windows runner: the master carries `ilk_profile: unattended` which the PS1 runner does not implement; result file written, no other action | Terminal |
| `"plan-amended"` | The targeted sub-plan or MASTER was edited above `## Findings` during the iteration (planner amendment). The watcher killed the agent, WIP-preserved the dirty tree, and the loop continues to the next iteration. The post-iteration gate is skipped (`_should_gate_iteration` returns 1 for interrupted iterations with 0 commits). The no-progress streak (3) bounds an amendment loop — if every iteration is amended with 0 new commits, the run stops with `no-progress`. JSONL record carries `plan_amended: true`. The fingerprint (computed by `plan_fingerprint.py`) excludes the frontmatter keys `current_step`, `status`, `last_updated`; for MASTER files it also excludes the registry Status column and the `## Progress log` section. | No — iteration-internal; loop continues |

**Naming conventions are intentional.** The hyphenated states (`no-progress`,
`all-shipped`, `timeout`, `budget-exhausted`, `quota-exhausted`,
`blocked-no-runnable`, `already-shipped`) and the underscored states
(`local_checks_failed`, `ship_integrity_violation`) come from two writers in
two languages (bash and
PowerShell). A consumer already reads the underscore forms; do not normalise
them to one convention.

### Sentinel state → postmortem label

**This is a second, different vocabulary — not more exit states.** The table
under *State vocabulary* above is the driver's own, written to
`last-exit.json` as `state` and to the JSONL as `stop_reason`. In the table
below only the **first** column holds those states; the middle column holds
`collect.py` postmortem labels (`CLASSIFICATION_LABELS`) and the right column
holds `watchdog.sh` actions. A reader extracting the driver's vocabulary
mechanically must take the table above and stop at this heading — treating
both tables as one yields the sum of their rows as if every row were a state.

`collect.py`'s `_SENTINEL_FAILURE_MAP` is the only place this mapping lives; a
terminal state missing from it falls through to the generic heuristics, which
is how a failed run gets classified `clean-success`.

| Sentinel `state` | `collect.py` label | `watchdog.sh` action |
|---|---|---|
| `"budget_exhausted"` | `budget-exhausted` | `block` |
| `"quota-exhausted"` | `quota-exhausted` | `block` |
| `"max-iterations"` | `max-iter-bound` | `relaunch` |
| `"interrupted"` | `interrupted` | `relaunch` |
| `"local_checks_failed"` | `local-checks-broken` (broken-gate result in checks) / `local-checks-stuck` | `block` |
| `"local_checks_failed_no_commits"` | `local-checks-unchanged` | `block` |
| `"local_checks_environment_fault"` | `local-checks-stuck` | `block` |
| `"ship_integrity_violation"` | `shipped-unverified` | `needs-human` |
| `"shipped-unproven"` | `shipped-unverified` | `needs-human` |
| `"merge-deferred"` | `merge-deferred` | `relaunch` |
| `"yielded"` | `yielded` | `relaunch` |
| `"selfmod_merge_failed"` | `merge-conflict` | `block` |
| `"selfmod_live_clone_touched"` | `merge-conflict` | `block` |
| `"timeout"` | *(none — falls through)* | `triage` |

`selfmod_merge_failed` was in **0** consumer files until 2026-09-17 — the
third instance of identical drift (after `ship_integrity_violation` and
`timeout`), and the first to escape to another project (gh-resolve's
`doctor --strict` failed 8 of its tests).  Added to `_SENTINEL_FAILURE_MAP` in
sub-plan `a-new-terminal-state-cannot-ship-unknown`.

`ship_integrity_violation` is written by `run_ilk_loop_claude.sh` and
`run_ilk_loop_claude.ps1` only. It was in **0** classifier files until
2026-08-29 — see the bug reference under Contract 2b.

**Park writer: `park_master.py --owner-of <slug>`.**  On a
`ship_integrity_violation` whose violating sub-plan the driver could NOT revert
to `in-progress` (changed 2026-10-03: a reverted, runnable sub-plan leaves its
master unparked, bounded by `auto_block_fails`/quarantine), the driver parks the
master that **owns** the violating slug (its registry lists a sub-plan whose `plan:` or filename-derived
slug matches).  Without `--owner-of` the driver parked whichever master was the
sole `queued` one — an unrelated master got parked while the violator was
reconciled back to `queued` and re-dispatched (rezmac 20260923-150625).
Ownership status filter: `PARKABLE | {shipped}` (the violating master is
usually `shipped` at park time because reconcile runs after).  Zero owners ⇒
exit 1 with JSON naming the slug and every master searched; the driver prints
the refusal and continues (the run still stops `ship_integrity_violation`).

**Park fields (D3, 2026-09-29).**  A park writes these master frontmatter
fields next to `status: blocked`.  Only `park_master.py` and the runner write
them.  The runner snapshots **its own run's master** (loop_status's selection,
honouring `ILK_MASTER`) before dispatch and restores a worker change to its
`status` / park fields after (`master_snapshot.py`).  Other masters on the key
are deliberately not guarded: on a resolver key gh-resolve's daemons pause,
un-park and create them while the runner is live, and a master created during
the turn is never touched.

The same master's registered sub-plans get a narrower guard
(`subplan_status_snapshot.py`): after the turn, a sub-plan `status:` that
changed **and** is outside the reader vocabulary (`pending`, `ready`,
`in-progress`, `shipped`, `blocked`, `skipped-by-operator`) is put back to its
pre-dispatch value and logged as `[status-guard]`. Examples are `complete`, or
`queued` on a sub-plan. That is not a terminal state, because the restore
repairs the plan. In-vocabulary changes are left to ship_transition,
quarantine and one-ship enforcement.

| Field | Written by | Meaning |
|---|---|---|
| `parked_at` | every park | local time, `YYYY-MM-DDTHH:MM:SS` |
| `parked_reason` | every park | quoted scalar; grammar below |
| `hold: human` | a park **without** `--auto` | holds the whole **project**: `scheduler_scan` skips it (`skip-held: <key> (<master>)`, journalled in `scheduler.log`), `promote_next_master` refuses (exit 1, `refused: held`), `loop_status --json` reports `held_by`, the runner exits `blocked-no-runnable` with `held_by`, and the watchdog does not relaunch |
| `yield: true` | `--yield` | the hold covers this master only; the project's other masters run |

`plan_status.project_held_by(plans_dir)` is the one reader: the `hold` field
alone decides, `status` is not consulted, and `yield: true` releases the
project.  `--unpark` strips all four fields and **refuses** a master with
`hold: human` unless `--release-hold` is passed, so an automated
`--unpark` (gh-resolve's reaper) can lift an auto-park but never an
operator's park.

**`parked_reason` grammar.**  Free text for a human park.  A driver park is
machine-readable, one park per violating slug:

```
ship_integrity_violation: run <RUN_ID> slug=<slug>
```

This has been the canonical form since `7744063` (`run_ilk_loop_claude.sh`).
The older form `ship_integrity_violation: run <RUN_ID> slugs=[<slug>,<slug>]`
named every violating slug in one park, and the PowerShell runner **still
writes it** (`run_ilk_loop_claude.ps1`, which also parks without
`--owner-of`).  Readers must accept both; `status_all.py` keys on the first
token only.

**Owed-park scan in `status_all.py` (2026-09-29).**  When
`pick_active_master`'s choice is not `active` or `queued` (e.g. the
newest-by-mtime master is `shipped`), the chosen-master branch never
sets `master_parked_reason`.  A second scan fires: it iterates every
master, applies the `pending_batches` owed-park test (`blocked` +
non-empty `parked_reason` + `master_has_nonshipped`), skips
`superseded` parks (residue), and picks the one promotion would
resume first — priority descending, `created` ascending (the
resume-first rule, reusing `promote_next_master`'s `_prio` /
`_created` helpers).  The scan sets `master_parked_reason`,
`active_master`, `batch`, and `display_fallback` from the winner.
**`pending_batches` excludes superseded parks (2026-09-29).**  The
`pending_batches` loop applies the same `_is_superseded_park` predicate:
a master whose unquoted `parked_reason` starts with `superseded`
(case-insensitive) does not count.  A superseded master will never run
— its work was re-planned into a later batch — so counting it inflates
the badge and never shrinks.  Violation parks and operator parks still
count, unchanged.

**Declared work_tree (`work_tree.py`).**  Master frontmatter
`work_tree: <absolute path>` declares the tree the driver observes, gates,
and ledgeres.  Absent or empty ⇒ behaviour unchanged.  Present ⇒ must be an
absolute path to an existing git work tree whose `git rev-parse
--git-common-dir` equals `--project-path`'s.  Otherwise the driver refuses
the iteration (stop reason `work_tree_invalid`, sentinel written, loud stderr
naming the path and which check failed).  Resolved once per iteration via
`work_tree.py`; `selfmod_effective_repo` maps through it when set.
Plans, ledger location, sentinel, project key: unchanged (they key off
`--project-path`).  Only *which tree is observed and gated* changes.

`shipped-unproven` was added 2026-09-08. Before it, `all-shipped` never
consulted proof: the loop printed `SHIP PROOF MISSING: 2 sub-plans shipped
without proof` and `[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch.` in
the **same** run, because `SHIP PROOF MISSING` is a *report*
(`loop_status.py`) and the driver's exit path never read it. It shares the
`shipped-unverified` label with `ship_integrity_violation` on purpose — the
same condition (a ship nothing verified) reached by a different route — so no
new arm is needed in either watchdog. `classify_loop_status` is now the single
decision point; the `test_all_shipped` exit-code shortcut was removed from the
per-iteration check because an exit code cannot express proof.

`timeout` was added to this table on 2026-08-29, having been a terminal state
**no classifier knew**. `run_ilk_loop_claude.sh:2184` sets
`iter_stop_reason="timeout"` and `:2504` promotes it to the sentinel's
`stop_reason`, but it appears in neither `_SENTINEL_FAILURE_MAP` nor (until
that date) `classify_action`'s arms, so it reached the `*` unknown-label
fail-safe. `watchdog.sh` now enumerates it on the `no-evidence|never-ran` arm.

**The mismatch is wider than `timeout` and is not yet fixed.** The raw-state
fallback at `watchdog.sh:933-934` assigns a *sentinel state* into a variable
`classify_action` reads as a *taxonomy label*, and the two vocabularies differ
in spelling as well as membership:

| raw state | `classify_action` arm | result |
|---|---|---|
| `local_checks_failed` | arms are `local-checks-stuck` / `local-checks-broken` | `*` → block |
| `max-iterations` | arm is `max-iter-bound` | `*` → block |
| `budget_exhausted` | arm is `budget-exhausted` | `*` → block |
| `no-progress`, `error`, `startup-hang` | absent | `*` → block |

Each action is defensible, but each is a **default rather than a decision** —
the same defect fixed for `timeout`. The likely correct repair is to map the
raw state through `_SENTINEL_FAILURE_MAP` at the fallback site rather than
enumerate six more arms.

### Invariants

1. **`"running"` is the ONLY live state.** All other states are terminal.
   The runner's `Finalize-Sentinel` enforces this on write; readers must
   honor it on read.

2. **A terminal state ⇒ `alive=False` without consulting the PID.**
   Windows recycles PIDs aggressively — a dead loop's PID may already
   belong to an unrelated process. Checking `pid_alive()` on a terminal
   sentinel produces false positives (the tray-stuck-running bug).

3. **`alive = (state == "running") AND pid_alive(pid)`.** Both conditions
   required. State gate first (cheap, definitive for terminal); PID check
   only when state is live (expensive `tasklist` subprocess).

4. **Finalize-on-exit.** The runner must rewrite the sentinel to a terminal
   state on ANY exit path (success, failure, interrupt, exception). A
   `state: "running"` sentinel whose PID is dead means the runner crashed
   without finalizing — the watchdog classifies this as "stale".

### Bug reference (20260616-175453)

`status_all.py` set `alive = pid_alive(pid)` without checking `state`.
The loop exited with `state: "local_checks_failed"`, but Windows recycled
its PID to another `powershell.exe`. `pid_alive` returned `True` → tray
showed "running" for an hour. Fixed in sub-plan #3
(`status-terminal-sentinel-alive`).

---

## Contract 1b: Refusal sentinel (`last-refusal.json`)

### Purpose

When a second runner is refused because another runner holds the lock, it
writes `last-refusal.json` instead of overwriting `last-exit.json`. This
preserves the live runner's sentinel while recording the refusal.

### Format

Same shape as `last-exit.json`:

```json
{
  "state": "lock_held",
  "pid": 48269,
  "run_id": "20260925-140101",
  "started_at": "2026-09-25T14:01:01+0800",
  "ended_at": "2026-09-25T14:01:01+0800",
  "project_path": "/path/to/project",
  "cli": "claude"
}
```

### Who writes

- **`run_ilk_loop_claude.sh`** — the lock wrapper, when `ilk_run_lock.py`
  exits 3 **and** the refusal marker file exists. The marker disambiguates
  lock-held (exit 3 from the helper) from a runner that itself exits 3.

### Who reads

- **`collect.py`** — may scan for refusal records to distinguish refused
  runs from crashed runs.
- **`status_all.py`** — does NOT read this file; it reads only
  `last-exit.json`.

### Invariants

1. **`last-exit.json` has exactly one writer: the runner holding the lock.**
   A refused runner writes `last-refusal.json`, never `last-exit.json`.
2. **The refusal marker disambiguates exit codes.** `ilk_run_lock.py` writes
   a marker file (`run.lock.refused-<pid>`) before exiting 3. The wrapper
   treats rc 3 as lock_held only if the marker exists, then deletes it.
   Without the marker, rc 3 is the runner's own exit code (passed through).
3. **`last-refusal.json` is terminal.** Its `state` is always `"lock_held"`.
   It is never rewritten by a subsequent run.

### Bug reference (ilk-skills #45)

rezmac 2026-09-25 14:01:01: a lock_held runner replaced `last-exit.json`
while pid 95267 held `run.lock`. The live runner's sentinel was overwritten
with `state: lock_held`, causing `status_all.py` to report the project as
idle. Fixed by writing `last-refusal.json` instead.

---

## Contract 2: JSONL logs (`.ilk-loop.log` + per-iter logs)

### Format

Each line is a standalone JSON object (JSONL / newline-delimited JSON).
The summary log (`.ilk-loop.log`) records one line per iteration:

```json
{"run_id":"20260616-175453","iteration":3,"subplan":"backend-core","outcome":"pass","exit_code":0,"raw":{"all_passed":true,"checks":[...]}}
```

Per-iteration logs (`iter-NNN.jsonl`) contain finer-grained events.

**Separators are spaced (`", "` / `": "`), not compact.** Verified against a
real `.ilk-loop.log` before this section was written. Readers `json.loads` each
line, so the spacing is not load-bearing — but a *new* writer must match it, and
the compact-separator rule that F2 pinned belongs to **Contract 2b**, not here.
Applying 2b's rule to this file is an easy and wrong inference.

### Two records per iteration: `started`, then the summary (2026-08-29)

A record is appended **before** the agent is invoked:

```json
{"run_id": "20260829-193429", "cli": "claude", "iteration": 1, "timestamp": "2026-08-29T19:34:29+0800", "project": "/path/to/proj", "model": "test-model", "status": "started"}
```

Then, on completion, the full summary line as before. The two pair on
`(run_id, iteration)`. **A `status: "started"` record with no matching summary
means the iteration was killed.**

Why it exists — and what it does *not* fix. `gtimeout` kills the **agent**; the
runner survives and writes the summary normally. Measured 2026-08-29 across
four real runs:

| runner | tree at kill | `.ilk-loop.log` |
|---|---|---|
| HEAD | clean / dirty | 415 / 423 bytes, `stop_reason=timeout` |
| `f5674c6^` | clean | 396 bytes, `stop_reason=timeout` |
| `f5674c6^` | dirty | **0 bytes** |

The 0-byte case is `f5674c6`'s defect (the WIP commit's stdout joining
`preserve_dirty_tree_on_timeout`'s return value, `int()` raising, the python
block dying before `print`), and it reproduces only with a dirty tree. It is
fixed. **A `gtimeout` kill was never the gap.**

The gap is a SIGKILL to the **runner** — `stop.sh`, a `launchctl bootout`, the
machine dying. Measured at HEAD before the start record:

```
.ilk-loop.log   NO-FILE  (zero records)
sentinel        state: running    <- stale-running crash artifact
orphaned        gtimeout ... claude -p   survived, reparented
```

and after:

```
.ilk-loop.log   301 bytes   {"run_id": ..., "status": "started"}
```

This matters because `.ilk-loop.log` is `collect.py`'s only input and
`scheduler.sh:510` builds its blacklist from postmortems derived from it. No
records ⇒ no postmortem ⇒ dispatchable forever.

> Note the sentinel is still left at `state: "running"` by a runner SIGKILL.
> That is invariant 4 of Contract 1 (a crash artifact the watchdog must catch),
> and it is **not** fixed here.

### Dedup key

`(run_id, iteration)` — a given run should have at most one summary line
per iteration. Readers should dedup if re-processing.

### Who writes

- **`run_ilk_loop_claude.ps1`** — appends to `$JsonlLog` (~line 799).
- **`run_ilk_loop_claude.sh`** — appends via `>>` (shell redirect). Since
  2026-08-29 it writes **twice** per iteration: the `status: "started"` record
  before `invoke_claude_iteration`, and the summary after.

### Who reads

- **`collect.py`** — `read_jsonl_iters` (~line 319) and
  `read_per_iter_jsonl` (~line 366). Feeds postmortem classification.
- **Watchdog** — may scan for iteration progress.

### Run-level terminal record (2026-09-19, D1 fix)

After the enforcement block, the runner emits a **second** record carrying
the terminal `stop_reason` — the reason the loop ended, including reasons
set by enforcement after the per-iteration record was written.

#### Format

```json
{"run_id":"20260918-175308","cli":"claude","iteration":999999,"timestamp":"2026-09-18T18:06:00+0800","project":"/path","stop_reason":"ship_integrity_violation","record_type":"run_exit","iters":1}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | Same run identifier as the per-iteration records |
| `cli` | string | Always `"claude"` (matches per-iteration records) |
| `iteration` | int | Always `999999` — keeps it out of the per-iteration de-dup key `(run_id, iteration)` |
| `timestamp` | string | ISO-8601 timestamp at loop exit |
| `project` | string | Absolute project path |
| `stop_reason` | string | The terminal reason — always present |
| `record_type` | string | Always `"run_exit"` — distinguishes from per-iteration records |
| `iters` | int | Number of iterations the run completed |

#### Why `iteration: 999999`

The per-iteration de-dup key is `(run_id, iteration)`. Real iterations are
numbered 1..N (max_iterations is typically 30-100). Using `999999` keeps
the terminal record out of the per-iteration de-dup while staying numeric
(`collect.py` uses `rec.get("iteration", 0)` and `int()`).

#### Who writes

- **`run_ilk_loop_claude.sh`** — appended after the enforcement block,
  before the sentinel finalization. A new `python3 -c` block constructs
  the record.
- **`run_ilk_loop_claude.ps1`** — (mirror, TBD).

#### Who reads

- **`collect.py`** — `_classify_core` checks for `record_type="run_exit"`
  and reads its `stop_reason` in preference to the last per-iteration
  record's (which may be empty due to D1).
- **`collect.py`** — `_iteration_records` excludes the `run_exit` record
  from every iteration count, the per-iteration table, duration statistics,
  the last-iteration tail and `iter_at_stop`.

#### Invariants

1. **The terminal record is ALWAYS emitted.** Even when `stop_reason` is
   empty or absent (which should never happen, but the record must exist
   for the classifier to know the run ended).
2. **`record_type` is the discriminator.** Readers must not key on
   `iteration: 999999` alone — the number is a de-dup convenience, not a
   semantic marker.
3. **The terminal record supplements, never replaces, the per-iteration
   record.** The per-iteration record stays in place; the terminal record
   adds the missing `stop_reason`.

### Invariants

1. **Files MUST be BOM-free.** Windows PowerShell 5.1's `-Encoding utf8`
   writes a UTF-8 BOM (`EF BB BF`) when creating a file. A BOM-prefixed
   JSONL file causes `json.loads` to raise *"Unexpected UTF-8 BOM"* — the
   record is silently dropped and collect.py reports "No JSONL records".

2. **Readers MUST use `encoding="utf-8-sig"`.** The `utf-8-sig` codec
   strips a leading BOM if present and is a no-op when absent. Every
   Python read of a file the toolkit's PowerShell side may have written
   should use `utf-8-sig` — this is strictly safer than `utf-8`.

3. **The runner MUST append BOM-free.** Use
   `[System.IO.File]::AppendAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`
   instead of `Add-Content -Encoding utf8`. Shell `>>` is already BOM-free.

4. **Each iteration produces exactly one summary record.** The
   `(run_id, iteration)` pair is the dedup key. A missing record means
   the iteration was interrupted before writing.

5. **A reader MUST tolerate a `status: "started"` record with no summary.**
   That is a killed iteration, not corruption. `collect.py` classifies it
   `timeout-bound` when the sentinel says `timeout`.

6. **"Started then killed" and "never ran" are different labels.** A run with
   **no** records degrades to `no-evidence`/`never-ran`; a run with a start
   record does not. They call for different actions — `never-ran` points at an
   environment fault, `timeout-bound` at the work itself — so the sentinel's
   `timeout` state is authoritative only when records exist.

### Bug reference (20260616-175453)

`run_ilk_loop_claude.ps1` used `Add-Content -Encoding utf8` which wrote a
BOM. `collect.py` read with `utf-8` (not `utf-8-sig`), `json.loads` choked
on the BOM, the record was swallowed by a bare `except json.JSONDecodeError`,
and the watchdog printed "POSTMORTEM FAILED" and blocked. Fixed in sub-plan
#1 (`collect-bom-tolerant-reads`).

---

## Contract 2b: local_checks results file (per-iteration gate verdicts)

The temp file `run_ilk_loop_claude.sh` mktemps at `:2076` for one iteration's
gate results. Distinct from Contract 2: it is per-iteration, short-lived, and
it is the **only** carrier of "did this sub-plan's declared gate pass?" from
the gate runner to ship-integrity enforcement.

### Format

One JSON object per line, written **compact** — `separators=(",", ":")`:

```json
{"slug":"issue-sync-schema-widen","step":2,"outcome":"fail","exit_code":1,"command":"bunx vitest run","head_sha":"abc1234"}
```

`outcome` is one of `pass` / `fail` / `error` / `inconclusive`. `fail` and
`error` are **blocking**. `command` is present for every outcome, so a passing
gate is distinguishable from a gate that never ran. `head_sha` is the HEAD
commit SHA at gate time, copied from `run_local_checks.py` output by
`emit_jsonl_record.py` (added 2026-09-29). A row without it is still readable
but is treated as "no proof" by the final-step gate invariant (fail closed).

### Who writes

- **`emit_jsonl_record.py`** — `build_record` + the append in `main`. The one
  writer. It replaced a hand-interpolated `echo` in the runner.

### Per-check classification (added 2026-09-24)

`run_local_checks.py` classifies each check result into `outcome` ∈
{pass, fail, error} and `reason` (str or null):

- exit 0 ⇒ `pass`
- `exit_code is None` (timeout, spawn exception) ⇒ `error`, reason from
  `error`
- exit 126 / 127 ⇒ `error`, reason `command not executable` /
  `command not found`
- stderr contains a line matching `^ILK-CHECK: unmeasured (.*)$` ⇒
  `error`, reason = the captured text. The FULL stderr is scanned, not
  only the 2000-char tail, so the marker is not lost.
- **environment red** (nonzero exit, but pytest/vitest summary shows
  ≥1 passed and 0 failed) ⇒ `error`, reason `"environment: <first
  stderr line matching VIOLATION|guard|Error, ≤200 chars>"`.  The
  failure is in the environment (conftest guard, stderr pollution),
  not the tests themselves.  Environment reds are NOT re-run in B2
  (their verdict cannot change) and are NOT quarantine strikes.
- any other nonzero ⇒ `fail`

The helper JSON also carries `path_prelude_applied: bool` (true when
`_read_path_prelude` returned a non-empty prelude that was prepended).

### Check-level `retry` field (added 2026-09-29)

A check's YAML entry may set `retry: false` to indicate that its outcome
is deterministic — re-running it cannot change the verdict.  This applies
to checks that read a fixed record (e.g., `verify_attribution.py` without
`--remeasure-if-stale`) or that test a property that doesn't change between
runs.

When a check with `retry: false` blocks on its first red, B2 skips the
confirm re-run.  The log line reads:
`[b2] <slug> step <N>: no re-run — verdict cannot change (retry: false)`.

Similarly, `verify_attribution.py` without `--remeasure-if-stale` is
implicitly deterministic: the verification record doesn't change between
runs, so re-deriving it is pointless.

### Synthesized mention gate (`scope: mention`)

When a step changes files that are pinned by line number in other files
(e.g., `src.py:10` in a doc table), `run_local_checks.py` synthesizes
an additional check to gate the test files that cite those pins. This
catches the 93-minute-delayed failure from retro F7.

**How it works:**

1. **Changed set resolution** (AC-1): finds files touched by commits
   carrying `[plan:<slug>#step-N]`. On shared remotes with no trailers,
   falls back to `git diff --name-only <pre-iteration head>..HEAD` if
   the runner exports `ILK_PRE_ITER_HEAD`. Otherwise skips with a
   logged `mention-gate: skipped (no trailer, no range)`.

2. **Mention search** (AC-2): for each changed file `X`, runs
   `git grep -lE '<basename(X>):<digits>|<repo-relative X>:<digits>'`
   to find files that pin it by line number.

3. **Test file resolution** (AC-3): splits mentions into test files
   (matched against project test globs) and non-test files. For
   non-test files, finds test files that mention them.

4. **Cap** (AC-4): if more than 20 test files cite the changed file,
   skips the gate and logs `WARN mention-gate: <n> files cite
   <X>:<line> — over cap, not gated`.

5. **Synthesis** (AC-3): appends one check to the step's gate:
   `<resolved suite invocation> <test files…>`, with `scope: mention`,
   timeout 300. If the declared gate already runs a file, drops it. If
   nothing is left, appends nothing.

6. **Vitest** (AC-6): a vitest project gets a `vitest run <files>` form.

**Output format:** the synthesized check appears in the results array
with `scope: "mention"` (instead of `"subplan"` or `"step"`). The
top-level `mention_check_count` field indicates whether a mention check
was appended (0 or 1).

### Rollup rule

The helper JSON includes a top-level `outcome` field — the rollup over
all per-check outcomes. The rule is **fail > error > pass**: if any
check is `fail`, the rollup is `fail`; else if any is `error`, the
rollup is `error`; else `pass`.

`local_check_outcome` in the runner uses the rollup when present and
falls back to the old `all_passed` / exit-code mapping only when it is
absent (so an old helper still works).

### The `ILK-CHECK: unmeasured` opt-in

A checked tool opts in to the unmeasured classification by printing a
stderr line `ILK-CHECK: unmeasured <reason>`. This is consumed by the
per-check classification above. The marker is not an exit-code convention
because gates are arbitrary commands and pytest already uses exit 3.

### Who reads

- **`blocking_checks.py`** — `--any` / `--targets` / `--slugs` / `--describe` /
  `--unattributable-count`. The one reader for the runner's B2 path (blocking
  test, confirm re-run, auto-quarantine, the human-readable failing-check
  line). Its modes count only *attributable* records — see invariant 6.
- **`test_ship_integrity`** in `run_ilk_loop_claude.sh` — its gate lookup
  matches `rec["slug"]` against each `shipped` sub-plan and turns `outcome`
  into `gate_passed` = `true` / `false` / `skip`.
- The runner's `.ilk-loop.log` record build, which embeds the parsed array as
  `local_checks`.
- **`ship_audit.audit_ship`** — when a final step is credited only by the
  `#ship` trailer and declares per-step `local_checks`, reads the JSONL loop
  log (via `loop_log_path`) to verify a `{slug, step: N, outcome: "pass"}`
  record exists.  Absent or unreadable log ⇒ `proven: False`.  Also checks
  the ship-proof ledger (Contract 5) for a `gate_pass_at_head` row covering
  the step as an alternative proof path.

### Proof channels for ship-integrity

A sub-plan that reads `shipped` is **backed** when at least one of these
channels proves its final step:

1. **Trailer.** A commit whose message contains
   `[plan:<slug>#step-<N>]` or `[plan:<slug>#ship]`. The primary channel
   on non-shared remotes.
2. **Ledger row.** A `ship-proof.jsonl` record whose step range covers
   the final step (Contract 5). Includes `gate_pass_at_head` rows
   (zero-commit green gates) — see Contract 5 format.
3. **Gate history.** A `gate-history.jsonl` row with `outcome: "pass"`
   for the final step whose `head_sha` is an ancestor of (or equal to)
   the slug's last step commit, or — for zero-commit steps — an
   ancestor of the current HEAD.

**No commits and no gate row is never backed.** A sub-plan with no
trailer, no ledger row, and no qualifying gate-history row must be
refused regardless of its `status:` line.

### Invariants

1. **Readers MUST parse JSON. Pattern-matching the serialised text is
   forbidden.** A regex over the wire format encodes a *formatting* choice as
   a *semantic* one, and the two drift the moment a writer changes
   `json.dumps` arguments. Ask `blocking_checks.py`, or `json.loads` the line
   yourself — never `grep` for `"outcome":"fail"`.

2. **The writer MUST emit compact separators.** Readers parse JSON, so this
   cannot break them; it keeps the file honest about its own contract, and
   `grep`-based *diagnosis* by a human at 3am is the realistic consumer of
   that honesty.

3. **The file MUST live until ship-integrity enforcement has run.** Its
   lifetime is the whole post-iteration region, not just the `.ilk-loop.log`
   record build. Cleanup belongs after the `test_ship_integrity` call.

4. **An absent result is not a failed result.** A sub-plan with no record in
   this iteration's file is `skip`, never `unknown` — `ship_integrity.py`
   counts `unknown` as a violation, and the caller's guard
   (`run_ilk_loop_claude.sh`, `!= "true" && != "false" -> continue`) is the
   2026-08-20 cross-run scoping fix. Do not loosen it; keep the file alive
   instead.

5. **A malformed line MUST NOT blind the reader to the rest.** The file is
   appended to once per gate by a subprocess, so a truncated tail is possible;
   `blocking_checks.read_records` skips unparseable lines rather than raising.

6. **A record's identity MUST come from the invoker.** `slug` and `step` name
   the target the runner chose to gate — never the checked process's own
   output, which is absent exactly when the gate failed. A record with no
   `slug` is **unattributable**: it is not a verdict about any sub-plan, it
   does not block, and it MUST be reported (`--describe` carries an
   `N unattributable (no slug)` segment; the driver echoes the count to
   stderr and continues). The writer takes identity as trailing argv from
   `invoke_local_checks`; the helper's stdout may fill a blank but never
   override.

7. **The driver's gate cap equals the helper's declared budget.**
   `invoke_local_checks` sizes its outer deadline as
   `max(total_declared + 60, LOCAL_CHECKS_TIMEOUT_SEC)`, where
   `total_declared` is the sum of every `timeout:` the sub-plan declares —
   frontmatter `local_checks` AND per-step fences — counted by
   `gate_declared_timeout` in `run_local_checks.py`. An undeclared check
   counts as the runner's default (120s), never 0, so the cap never kills a
   check before the runner's own `run_one` timeout would. If the cap kills
   the helper (gtimeout exit 124), the outcome is `inconclusive` — a
   non-verdict, not a failure. A sub-plan shipped in the same iteration
   with an inconclusive gate is reverted to `in-progress` (pointer
   untouched) and the revert does NOT count toward `auto_block_fails`
   (the driver's cap caused it, not the code). A sub-plan shipped in a
   prior run keeps the `skip` behaviour (the 2026-08-20 scoping rule).

### Bug reference (kira-cloudflare 20260828-211346)

Three defects, one silent ship. The driver log's two consecutive lines:

```
478:  [local_checks FAIL] issue-sync-schema-widen step 2 -> fail  cmd: bunx vitest run ...
480: === Loop ended: all-shipped ===
```

1. **The format contract broke in a refactor.** `emit_jsonl_record.py` wrote
   `json.dumps(rec)` — a space after every colon — while four readers in
   `run_ilk_loop_claude.sh` used `grep -qE '"outcome":"(error|fail)"'`, which
   forbids that space. The match never fired, so the entire B2 block was dead
   code. Fixed by invariants 1 and 2.

2. **The file was deleted before enforcement read it.** `rm -f` ran during the
   `.ilk-loop.log` build, ~90 lines before `test_ship_integrity` was handed
   the same path. The lookup hit `OSError`, `gate_passed` stayed `skip`, and
   the scoping guard skipped the sub-plan. Fixed by invariant 3 — *not* by
   loosening the guard (invariant 4).

3. **The stop reason had no classifier.** `ship_integrity_violation` was in 2
   of 532 tracked files (both runners) and 0 classifier files, so even a
   firing gate would have been laundered into `clean-success`. Fixed by the
   sentinel-state table under Contract 1.

Each defect alone was sufficient to ship a red gate as verified. Fixed in
sub-plan `a-red-gate-cannot-ship-a-subplan` (2026-08-29).

### Bug reference (gh-resolve resolver run, kira-cloudflare launcher 20260902-183120)

One defect (invariant 6), a finished fix stranded. The gate errored; the
writer, taking identity from the checked process's own stdout, recorded an
anonymous record; the driver printed `[local_checks ERR] 0 step  -> error`;
B2 confirmed the phantom target `slug="0"` against itself and stopped the
loop; the batch's second sub-plan never ran, and gh-resolve's `reap` refused
the run (`refused:plan-not-shipped`) until an operator landed it by hand.
Reproduced byte-exactly 2026-09-03 against HEAD `21e846d`
(sub-plan `a-gate-result-carries-its-identity`):

```
record:            {"slug":"","step":null,"outcome":"error","exit_code":1}
--targets output:  " 0"            (leading space; _step_of maps None -> 0)
read -r slug step: slug="0" step=""
driver printed:    [local_checks ERR] 0 step  -> error
```

Fixed in sub-plan `a-gate-result-carries-its-identity` (2026-09-03) by
invariant 6: identity from the invoker, unattributable records reported
instead of enforced.

### Gate-result isolation fields (added 2026-08-30, updated 2026-09-25)

`run_local_checks.py` emits four fields in its output JSON that describe
the isolation state of the working tree when the gate ran:

| Field | Type | Meaning |
|---|---|---|
| `head_sha` | `string \| null` | HEAD commit SHA at gate time; `null` if not a git repo |
| `dirty_paths` | `int` | Count of uncommitted + untracked paths before isolation |
| `isolated` | `bool` | `true` iff the tree was successfully pinned to HEAD |
| `restore_error` | `string \| null` | Error from stash apply (by SHA); `null` on success. Includes the SHA and recovery command. |

**Stash message prefixes (2026-09-25):**

| Prefix | Used by | Meaning |
|---|---|---|
| `ilk-gate-isolation <sha>` | `isolate_to_head` | Gate isolation stash; `<sha>` is HEAD at isolation time |
| `ilk auto-stash (branch setup)` | `setup_one_branch` | Branch switch stash (tracked only since 2026-09-25) |

**Restore-by-sha (2026-09-25):** `isolate_to_head` captures
`git rev-parse refs/stash` immediately after the push and restores with
`git stash apply --index <sha>`. Only on a successful apply does it find
the SHA's index in `git stash list --format=%H` and drop that entry.
Never `git stash pop` (it takes whatever is on top). A failed apply
leaves the stash in place and puts the SHA + recovery command into
`restore_error`.

**Selective stash with snapshot (2026-09-25):** When
`ILK_PRE_ITER_SNAPSHOT` names a readable snapshot, `isolate_to_head`
stashes only tracked changes and untracked paths that appeared after the
snapshot was taken (`iteration_snapshot.py changed-since`). Pre-existing
untracked files stay in the tree. Without a snapshot, the legacy `-u`
behaviour is preserved.

**Integrity violation `gate_isolation_restore_failed` (2026-09-25):**
When gate results carry `restore_error`, the runner prints
`! [gate-isolation] RESTORE FAILED` and appends an integrity row
`{"slug": <slug>, "violation": "gate_isolation_restore_failed: <sha>", "enforced": false}`
to `_INTEGRITY_VIOLATIONS_FILE`.

**Readers:**

- **`ship_integrity.py`** (`evaluate_ship`) — when `isolated=false` and
  `dirty_paths > 0`, the ship verdict is `ok=false` with reason
  `unisolated`.
- **`blocking_checks.py`** — indirectly, via the `error` field that
  `run_local_checks.py` sets when isolation fails (the `error` outcome
  is blocking per Contract 2b).
- **`ship_gap.py`** (SP3) — reads `head_sha` and `isolated` to detect the
  verify-more-than-commit gap.

### JSONL iteration-record `ship_gap` field (added 2026-08-30)

When an iteration commits fewer paths than it verified, the JSONL record
carries a `ship_gap` object:

```json
{
  "ship_gap": {
    "committed_paths": 2,
    "tree_paths": 170,
    "gap": 168,
    "unexplained": true
  }
}
```

**Writer:** `run_ilk_loop_claude.sh` — the env-block record builder,
guarded like `new_commits`. Absent when `unexplained` is false or the
probe fails.

**Readers:** `collect.py`, `ilk-feedback` — post-hoc analysis of the
gap. Absence is normal and means no gap was detected.

### Gate-history persistent file (added 2026-09-29)

Every gate row is also appended to `<runtime>/launcher/gate-history.jsonl`
with `run_id`, `iteration` and `timestamp`. It is append-only. It is never
written from a worker session, and the path comes from `ilk_paths` (external
runtime dir). The file enables the final-step gate invariant to check
historical gate outcomes across iterations.

Format — same compact separators as the per-iteration results file, enriched
with three fields:

```json
{"slug":"alpha","step":1,"outcome":"pass","exit_code":0,"head_sha":"abc1234","run_id":"r01","iteration":3,"timestamp":"2026-09-29T10:00:00+0800"}
```

**Writer:** `emit_jsonl_record.append_gate_history` — called by the runner
after each gate check.

**Readers:** `ship_integrity.check_final_step_gate` — merges this file's
rows with the current iteration's results to find qualifying pass rows.
`repeat_failure.detect` — reads gate rows to find red-gate repeats
(consecutive runs whose last gate at the same step is fail/error with the
same head_sha).

**No-gate-ran scoping (added 2026-09-29):** The final-step gate check runs
for ALL newly-shipped sub-plans, including those whose gate was skipped
this iteration (``gate_passed == "skip"``).  For such sub-plans the
per-iteration results file has no row, so the check falls back entirely to
this persistent history file.  A sub-plan already shipped before the
current iteration is never re-litigated (the prior-run guard in the
runner).

---

## Contract 3: Liveness (PID + sentinel cross-check)

### The problem

ilk's components never call each other — they communicate only through
files on disk. To answer "is this loop alive?", a reader must check TWO
independent signals:

1. **The sentinel state** — is it `"running"` (live) or terminal?
2. **The PID** — is the process still alive?

Neither signal alone is sufficient:

- **PID alone fails on recycling.** Windows aggressively reuses PIDs. A
  dead loop's PID may already belong to `chrome.exe` or `powershell.exe`.
  Checking only `pid_alive()` produces false positives.

- **State alone fails on crash-without-finalize.** If the runner process
  is killed hard (SIGKILL, power loss), it can't rewrite the sentinel.
  A `state: "running"` sentinel with a dead PID means the runner crashed —
  the watchdog must detect this as stale.

### The contract

```
alive = (state == "running") AND pid_alive(pid)
```

- **State gate first** (cheap string comparison, definitive for terminal).
- **PID check only when state is live** (expensive subprocess, but
  necessary to detect crash-without-finalize).

### Cross-component enforcement

| Component | Checks | Action on terminal |
|---|---|---|
| `status_all.py` | State + PID | Reports `alive: false` → tray shows "idle" |
| Watchdog | State + PID + mtime | Blocks project if stale beyond threshold |
| `collect.py` | State only | Classifies the terminal state for postmortem |
| Scheduler | State | Skips dispatch for non-`"running"` projects |

### Bug reference (20260616-175453)

`status_all.py` checked only `pid_alive(pid)`, ignoring the terminal
sentinel state. The loop exited cleanly (state: `"local_checks_failed"`),
but PID 48268 was recycled to another process. `pid_alive` returned `True`
→ tray showed "running" for over an hour. Fixed in sub-plan #3
(`status-terminal-sentinel-alive`).

---

## Contract 4: Verification marker (compile-only / device-manual)

### Purpose

A shipped `compile-only` or `device-manual` sub-plan has not been runtime-verified
in-loop. Before later work can build on it, verification must occur — either by a
human (device or manual pass) or by the automated planner verification dispatch
that fires when a master drains. This contract defines the on-disk marker that
records that confirmation, so `promote_next_master.py` can block promotion of
dependent masters until verification occurs.

### Format

A `verified:` field in the sub-plan's YAML front-matter:

```yaml
---
plan: my-subplan
status: shipped
verification_tier: compile-only
verified: true          # <-- human-verify marker
---
```

### Accepted values

| Value | Meaning |
|---|---|
| `true` / `yes` / `1` | Verified (human or machine) — promotion may proceed |
| *(absent)* | **Not verified** — back-compat default; treat as unverified |
| `false` / `no` / `0` | Explicitly unverified (same as absent, but intentional) |
| Any other string | Treat as unverified (degrade-safe — see below) |

### Who writes

- **A human** editing the sub-plan file directly (after a device or manual pass).
- **`/ilk-feedback`** — when the feedback flow records a human verification result.
- **The planner verification dispatch** — when `scheduler_scan.py` detects a master
  reaching all-shipped, it dispatches a planner-tier session (engine: `claude`,
  home: `~/.claude`) running the verification entrypoint. That session may set
  `verified: true` if gates pass, or escalate (leaving the marker absent). The
  dispatch is idempotent (marker file) and skips blacklisted projects.
  (Retired 2026-09-20: the `supervised_only` skip was removed — worktree
  isolation + merge bounce made it vestigial.) A human pass remains valid
  and is the fallback when
  dispatch cannot happen (launcher missing, planner home unbootstrapped).

### Who reads

- **`promote_next_master.py`** — before promoting a master that declares a
  `builds_on` dependency on a compile-only/device-manual sub-plan, checks that
  the dependency's sub-plan has `verified: true`. If absent or falsy, promotion
  is skipped with a logged reason.
- **`loop_status.py`** — already surfaces `needs-verify:<tier>` per shipped row;
  the marker is informational here (the banner fires on tier, not on marker).

### Invariants

1. **Absent ⇒ unverified.** A sub-plan that predates this contract (no `verified:`
   field) is treated as unverified. This is the safe back-compat default — old
   shipped sub-plans must not silently become "verified" just because the field
   didn't exist.
2. **Degrade-safe on malformed input.** A `verified:` value that is not a
   recognized truthy string (e.g. `"maybe"`, `"pending"`, garbage) is treated
   as unverified. Readers must never raise on an unexpected value — degrade to
   skip-with-reason.
3. **Marker is independent of `status: shipped`.** A sub-plan must be `shipped`
   before the marker is meaningful. A `verified: true` on a `pending` sub-plan
   is a no-op (and should be flagged as an anomaly if detected).
4. **Machine-set is valid.** The drain→verify dispatch may set `verified: true`
   automatically. This is not a bug — it is the intended close of the manual
   join. A human pass is still valid and is the fallback when dispatch cannot
   happen. Absent still means unverified (invariant 1).
5. **Verify-dispatch pins its master.** `_dispatch_verification_on_drain`
   passes `--master <master_path.name>` to `launch.sh` so the dispatched
   run's `loop_status` picks the shipped master that just drained, not a
   draft or unrelated master. A successful dispatch is logged via
   `_log.info("[verify-dispatch] dispatched <project> for <master>")`.

### Bug reference (field case: compile-only carry-forward)

Two sub-plans shipped `compile-only` and the loop advanced
to later masters that built on them — no marker, no block, no human pass. The
unverified work compounded: later sub-plans silently accommodated bugs in the
earlier layers. This contract closes that gap mechanically.

---

## Contract 5: Ship-proof ledger (`runtime/launcher/ship-proof.jsonl`)

### Purpose

On a shared remote, `[plan:<slug>#step-N]` trailers are stripped from commit
messages by policy (`SKILL.md` → "Shared remote trailer policy").  Two
mechanisms break there: `ship_audit.check_step_commits` (which matches only
by trailer) and gate targeting (which resolves the pre-iteration step, not the
step the iteration reached).  The ledger is a trailer-independent record of
which commits belong to which step range.

### Format

One JSON object per line, written **compact** — `separators=(",", ":")`:

```json
{"run_id":"20260829-120000","iteration":4,"slug":"gate-work","repo":"/path/to/project","step_from":0,"step_to":3,"commits":["abc1234","def5678"]}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | The runner's run identifier |
| `iteration` | int | The iteration number (0-indexed) |
| `slug` | string | The sub-plan slug |
| `repo` | string | Absolute path to the repository |
| `step_from` | int | The step the iteration **started** on — the slug's *real* pre-iteration `current_step`, never a floor (see "Who writes") |
| `step_to` | int | The step the iteration **reached**.  For a sub-plan shipped in this iteration (probe reports `status: shipped` or the new commits carry `[plan:<slug>#ship]`), this is `estimated_steps` — the ship transition does not bump `current_step`, so without the override the final step's per-step gate would never be targeted.  Otherwise, the sub-plan's `current_step` after the agent ran. |
| `commits` | list[str] | SHAs in the iteration's `before..after` range, **filtered per slug** on personal remotes (only commits whose message carries `[plan:<slug>#`).  On shared remotes, the full unfiltered range. |
| `attribution` | string? | **Optional.** Present only on shared remotes (or when no `[plan:` trailers exist in range).  Always `"unfiltered-no-trailers"` — signals that `commits` was not filtered by slug because trailers were absent.  Readers tolerate its absence (old format). |

A **`gate_pass_at_head`** row carries three additional fields and has
`commits: []`:

| Field | Type | Meaning |
|---|---|---|
| `proof` | string | Always `"gate_pass_at_head"` — distinguishes from normal rows |
| `head` | string | The HEAD SHA at gate time |
| `gate_outcome` | string | Always `"pass"` — the gate ran and passed |

```json
{"run_id":"20260925-140000","iteration":1,"slug":"gate-work","repo":"/path","step_from":0,"step_to":3,"commits":[],"provenance":"loop-executed","proof":"gate_pass_at_head","head":"abc1234","gate_outcome":"pass"}
```

The step range is **half-open**: `[step_from, step_to)`.  A record with
`step_from=0, step_to=3` proves steps 0, 1, and 2.

### Who writes

- **`run_ilk_loop_claude.sh`** — `write_ship_proof_records`, called after
  the gate has run (`local_checks` block completes).  The gate outcome is
  evaluated from the real results file, so `gate_pass_at_head` can fire
  for zero-commit green gates.  Writes a normal row when `total_new > 0`.
  When the gate passed with `total_new == 0`, writes a `gate_pass_at_head`
  row so `ship_integrity` can still prove the step (ilk-skills #48).  When
  the gate is absent or failing and `total_new == 0`, writes nothing and
  announces on stderr.

  **Per-slug trailer filter (AC-1):** on personal remotes, each slug row
  lists only commits whose message carries `[plan:<slug>#`.  A row with 0
  matching commits is refused with a stderr message (AC-2).  On shared
  remotes, trailers are stripped by policy, so the full commit list is kept
  and the row gains `"attribution": "unfiltered-no-trailers"` (AC-3).
  This prevents a slug whose gate never ran from crediting itself with
  another sub-plan's work (retro-2026-09-29 F2).

**The slug universe comes from TWO pre-iteration captures, not one**
(v0.9.94, 2026-09-09):

| Capture | Function | Feeds |
|---|---|---|
| `PRE_ITER_TARGET` | `get_active_subplan_targets` (`:784`) | the gate's fallback target, and the row for the slug the loop targeted |
| `PRE_ITER_ALL_STEPS` | `get_all_subplan_steps` (`:816`) | every OTHER non-shipped sub-plan and the step it starts on |

Both are read at `:2418`, **before** the agent runs, because afterwards the
agent has marked sub-plans `shipped` and moved their steps.

`get_active_subplan_targets` emits exactly ONE line — `[0]` of the non-shipped
sub-plans — so keying the writer off `PRE_ITER_TARGET` alone capped the ledger
at one row per iteration however many sub-plans the iteration advanced. A run
fast enough to finish two sub-plans in ONE iteration structurally could not
produce the second row, and the refusal was permanent because the iteration
that would have written it is over. **The faster run was the less publishable
one.** Measured on gh-resolve #4829.

`PRE_ITER_TARGET` itself was deliberately NOT widened: it is the gate's
fallback target, and widening it would point the gate at sub-plans the
iteration never touched.

**A slug added from the baseline earns a row only by having ADVANCED**
(`advanced_only`, guard at `:1074`); the targeted slug earns its row from
having been targeted. Without that asymmetry every untouched sub-plan in the
batch would get a row claiming the iteration's commits — and see "Who reads"
below for why that is not a cosmetic problem.

**`step_from` for an added slug is its real pre-iteration step. Do not
simplify it to 0.** The tempting repair — union the slug set out of the
post-iteration `loop_status` payload already in hand at `:933` — yields slug
and `step_to` but must invent `step_from`, and a sub-plan advanced into may be
one the loop partially worked earlier and returned to. An invented floor is
invisible at the consumer's reap, which reads presence only, but
`ship_audit.check_step_commits` (`:176-178`) counts a step committed if any
record covers it in `[step_from, step_to)`, so a floor of 0 marks every earlier
step proven. Already measured at `ship_audit.py:234-240`: `step_from 0 /
step_to 4` attributed step 2 for a sub-plan with no step-2 commit, and
`missing_steps` came back `[]` for work never done (batch 2026-09-08c).

**Per-slug commit filtering:** on personal remotes, each slug row now lists
only commits whose message carries `[plan:<slug>#`.  This prevents a slug
whose gate never ran from crediting itself with another sub-plan's work
(retro-2026-09-29 F2).  On shared remotes, trailers are absent by policy,
so the full range is kept and marked with `"attribution": "unfiltered-no-trailers"`.
A slug with 0 matching commits on a personal remote gets no row at all.

### Who reads

**A row's mere existence proves its slug to the consumer.** gh-resolve's
`reap._check_ship_proof` builds `proven_slugs` from the whole ledger file and
tests set membership on `slug` (`reap.py:1010-1021`, `:1035`); `commits`,
`step_from`, `step_to`, `iteration` and `run_id` are never read, and the set is
NOT scoped by run. So an unearned row does not merely look plausible to a human
reading the file — it is accepted as proof and the run publishes. That is why
`advanced_only` exists, and why a test writing into a real project's ledger is
guarded against in `conftest.py` rather than treated as untidiness.

- **`ship_audit.py`** — `check_step_commits` accepts an optional
  `ledger_records` parameter.  Union semantics: a step is committed if
  the trailer regex matches **or** a ledger record covers it.  Trailer
  matching is unchanged; the ledger only ever *adds* attribution.  A
  `gate_pass_at_head` row is accepted only when `gate_outcome` is `"pass"`
  and `proof` is `"gate_pass_at_head"`; a row with `gate_outcome != "pass"`
  is rejected entirely (it must not fall through to the normal union, which
  trusts any record for a trailerless slug).
- **`ship_audit.py` CLI** — resolves the ledger path via `ilk_paths.py`
  and reads it automatically.

### Invariants

1. **The ledger is supplementary.** Where trailers exist, they are the
   stronger evidence (they are in the commit itself).  The ledger must
   never override a trailer match.

2. **An unreadable ledger degrades to trailer-only.** An absent, empty,
   truncated, or malformed ledger must never raise or downgrade a ship
   that trailers already prove.  `ship_proof_ledger.read_records` skips
   unparseable lines rather than raising.

3. **One record per slug per iteration.** A runner that commits for more
   than one slug in a single iteration writes one record per slug (not a
   schema change).  Readers must tolerate multiple records per
   `(run_id, iteration)`.

4. **No record for unproductive iterations.** An iteration that produced
   no commits must not write a ledger record — a record with an empty
   `commits` list would prove a step that has no commit.
   **Exception:** a `gate_pass_at_head` row, which carries
   `commits: []` plus `proof: "gate_pass_at_head"` and
   `gate_outcome: "pass"`.  This row proves the step via the gate
   rather than via commits (ilk-skills #48).

5. **Compact separators.** Same contract as Contract 2b (local_checks
   JSONL): `separators=(",", ":")`.

### Bug reference (kira-cloudflare 20260828-211346 + 20260829-001901)

Two symptoms, one cause — the shared-remote trailer policy:

1. **Ship-proof is structurally unobtainable.** `ship_audit.py:70-92`
   matches a step's commit only by trailer.  On a shared remote, every
   sub-plan ships `(!) unproven` regardless of how many real commits it
   has.  The always-on warning became an ignored warning — the worker
   dismissed it as "cosmetic" while 3 of 3 declared gates were red.

2. **The last step's gate never runs.** Gate targeting on a shared remote
   uses `PRE_ITER_TARGET` (the pre-iteration step), not the step the
   iteration reached.  When an agent advances several steps in one
   iteration, the broadest gate (declared on the last step) is
   structurally unreachable.  Measured on run `20260829-001901`: two
   sub-plans, both shipped, neither directory gate was ever a target.

Both fixed in sub-plan `a-shared-remote-ship-can-be-proven` (2026-08-29).

---

## Contract 5b: Revert notice (`runtime/launcher/ship-reverts.jsonl`)

### Purpose

When the runner reverts a shipped sub-plan (ship-integrity violation,
inconclusive gate, one-ship enforcement, or final-gate violation), it
appends a row to `ship-reverts.jsonl`. At the next iteration, the prompt
carries a notice line for each revert whose slug is still not shipped,
so the next worker knows the revert is intentional and must not resync
by editing frontmatter.

### Format

One JSON object per line, written **compact** — `separators=(",", ":")`:

```json
{"slug":"alpha","ship_commit":"abc1234","reason":"ship_integrity","from_status":"shipped","to_status":"in-progress","from_step":2,"to_step":1,"run_id":"r01","iteration":1,"timestamp":"2026-09-29T10:00:00+0800","site":"integrity"}
```

A row from a red-step gate also carries `red_step`, `red_step_commits`, and
optionally `attribution`:

```json
{"slug":"alpha","ship_commit":"abc1234","reason":"ship_integrity","from_status":"shipped","to_status":"in-progress","from_step":2,"to_step":1,"run_id":"r01","iteration":1,"timestamp":"2026-09-29T10:00:00+0800","site":"integrity","red_step":1,"red_step_commits":["deadbee","cafe123"]}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `slug` | string | The sub-plan slug |
| `ship_commit` | string or null | The last `[plan:<slug>#ship]` commit SHA, or null if none exists |
| `reason` | string | Why the revert happened: `ship_integrity`, `inconclusive_gate`, `one_ship_enforcement` |
| `from_status` | string | Always `shipped` |
| `to_status` | string | Always `in-progress` |
| `from_step` | int or null | The `current_step` before the revert (null if unknown) |
| `to_step` | int or null | The `current_step` after the revert (null if pointer untouched) |
| `run_id` | string | The runner's run identifier |
| `iteration` | int | The iteration number |
| `timestamp` | string | ISO-8601 timestamp at revert time |
| `site` | string | One of `integrity`, `inconclusive`, `one-ship`, `final-gate` |
| `red_step` | int or null | **Optional.** The step whose gate was red (only for `integrity` site reverts caused by a red gate). Absent for non-gate reverts. |
| `red_step_commits` | list[string] or null | **Optional.** SHAs whose `[plan:<slug>#step-<red_step>]` trailer claims the red step. On a shared remote (no trailers), every commit in the iteration's range. |
| `attribution` | string or null | **Optional.** Present only on shared remotes (or when no trailers exist in range). Always `"unfiltered-no-trailers"` — signals that `red_step_commits` was not filtered by slug. |

### Who writes

- **`run_ilk_loop_claude.sh`** — via `revert_notice.append_revert_row()`,
  called at each revert site: ship-integrity violation, inconclusive gate,
  one-ship enforcement, and final-gate violation.

### Who reads

- **`run_ilk_loop_claude.sh`** — `revert_notice.read_revert_rows()` +
  `assemble_revert_notice()` to build the prompt injection. Only rows whose
  slug is still not shipped are included.
- **`repeat_failure.detect`** — reads revert rows to find revert repeats
  (consecutive runs with matching site, from_step, ship_commit).

### Invariants

1. **Append-only.** The file is never truncated or rewritten. Each revert
   produces exactly one row.
2. **Absent file = no reverts.** A missing or empty file is not an error —
   it means no reverts have occurred.
3. **Unparseable lines are skipped.** A truncated or malformed line is
   silently dropped (fail-closed on unreadable records, same as
   Contract 2b invariant 5).
4. **The prompt is byte-identical with no reverts.** When the file is
   empty or all revert slugs are now shipped, no notice is injected.
5. **`site` is one of four values.** `integrity` (ship-integrity violation),
   `inconclusive` (driver-cap killed gate), `one-ship` (one-ship enforcement),
   `final-gate` (final-step gate violation). Any other value is an error.
6. **Red-step fields are optional and additive.** A row without `red_step`
   renders as today — the original notice template is unchanged.  When
   `red_step` and `red_step_commits` are present, `assemble_revert_notice`
   appends one line per commit: `commit <sha7> claims <slug> step <N>, but
   that step's gate was red — its message is not a verdict; re-run the gate
   before relying on it.`
7. **`red_step` is derived from the gate results, not from the pointer.**
   The runner reads the per-iteration `local_checks` results file to find
   the lowest step of this slug whose gate came back `fail` or `error`.
   A revert without gate data (the `skip` path) carries no `red_step`.

### Bug reference (retro-2026-09-29 F1)

Ship-integrity reverted `root-area-typecheck-gate` to `pending` and left
ship commit `9b59229`. The next worker found `pending` next to a `#ship`
commit, called it a "bookkeeping desync", and set it to `shipped` by hand.
The prompt and `commands/ilk.md` never mentioned reverts, so the worker
had no way to know the revert was intentional.

---

## Contract 6b: The project key is a cross-repo contract

### The rule

`ilk_paths.project_key` decides the directory every run's records are filed
under. Any consumer that must find those records again — in this repo or
another — **calls the canonical implementation. It does not re-derive the
transform.**

```bash
python3 skills/ilk-loop/scripts/ilk_paths.py --project-key --start <path>
```

`--project-key` keys the path **as given**, with no project-root resolution:
a caller holding a worktree path gets that worktree's key. The launcher
separately keys by *resolved root* (`run_ilk_loop_claude.sh:269`), which
answers a different question — do not assume the two agree for a path that
sits inside a project rather than at its root.

### The algorithm, for a mirror that cannot call out

Lowercase the resolved absolute path; replace every run of `[^a-z0-9]+` with
`-`; strip leading and trailing `-`. **If the result exceeds 80 characters**,
replace the tail: `slug[:72].rstrip("-") + "-" + sha1(lowercased_abs_path)[:7]`.
Note the `rstrip` — when the 72-char cut lands on a separator the key is 79
characters, not 80.

### Why this is a contract and not a detail

gh-resolve's `reconcile.py:41` re-implemented the transform rather than shell
out to another repo — a reasonable instinct — but omitted the cap. The two
forms **agree below 80 characters and diverge above**, and because the cap is a
one-way hash the consumer cannot recover the producer's key from the path
alone.

Measured on rezmac 2026-09-09: **0 of 5 exit records reachable by the remedy
`doctor` prints, and 0 of 5 computed directories exist**, out of 90. So
`gh-resolve reconcile <worktree>` — the command an operator is told to run —
exits 0 and does nothing, forever, on every parked run. A run that parks and
cannot be reconciled needs a human, which is the outcome the loop exists to
avoid.

The near-miss is the instructive part: the live worktree key
`users-chad-projects-keyreply-kira-cloudflare-scratch-worktrees-resolver-7359f23`
is **79 characters** and agrees with the uncapped form by one character. The
divergence was invisible until a worktree name grew.

`skills/ilk-loop/tests/test_project_key_contract.py` pins the algorithm with
named vectors, including that near-miss and the failing case, so a mirror
implementation in any language can be diffed against it.

---

## Contract 7: The no-progress dispatch bound (`no-progress.json`)

### The rule

The scheduler stops dispatching a project after **N consecutive launches that
each ended non-clean with no plan progress**. It depends on nothing but launch
history — specifically **not** on a postmortem existing.

### Why it cannot depend on postmortems

`read_blacklist_from_postmortems` (`scheduler.sh`) builds the blacklist from
postmortem **files**. No postmortem ⇒ no entry ⇒ dispatchable forever.

Field record (rezmac, 2026-08-29): three launches at 12:01, 12:37 and 13:12,
each killed, each leaving no postmortem. The scheduler log read `promote:` /
`dispatch:` / `skip-busy` throughout and never indicated anything was wrong; it
was found only by reading `activity.log`. The watchdog was **not** the component
that needed convincing — it declined to relaunch and exited. The scheduler
re-dispatches independently.

> A bound that requires a successful postmortem is a bound that switches off
> exactly when things are worst.

### Format

`<project_data_dir>/runtime/launcher/no-progress.json` — beside the sentinel,
deliberately **not** under `postmortems/`:

```json
{"count":2,"signature":"34fdde416ede","updated_epoch":1700000000}
```

`signature` is a sha1 prefix over every sub-plan's `status` + `current_step`, so
a step advance **or** a ship changes it.

### N = 3

Declared as `NO_PROGRESS_THRESHOLD`, overridable via
`ILK_NO_PROGRESS_THRESHOLD`. Three, on three grounds:

1. the observed launch count on 2026-08-29;
2. `run_ilk_loop_claude.sh` already uses `no_progress_streak -ge 3`;
3. `collect.py` splits its `local_checks_failed` narrowing on `iter_count < 3`.

A fourth threshold would be one more number to reconcile.

### Invariants

1. **The counter advances once per DISPATCH, not per poll.** The check sits past
   every skip gate (blacklist, backoff, rapid-terminal, cooldown, busy), so
   reaching it means the project is genuinely about to launch. Counting per poll
   would trip the bound within minutes regardless of how many launches occurred.
2. **The counter is persisted.** launchd `KeepAlive` bounces the scheduler; an
   in-memory counter would reset the bound on every bounce — the failure mode
   being fixed.
3. **Progress resets it, whatever else happened.** A slow batch that is
   advancing must never be blocked. This is the bound's main false-positive risk.
4. **A clean exit resets it, whatever the plan state.** A run can finish cleanly
   with no step advance (everything already shipped). That is success.
5. **First sight is not evidence.** A project whose stored signature is `none`
   counts as progress. Nothing may be bounded for never having been seen.
6. **The refusal is loud.** Logged through the structured
   `(decision, key, reason)` form as `no-progress-bound`, naming the project,
   the count, the threshold, and how to clear it. The observed failure's
   defining property was a log that looked healthy.
7. **One acknowledgement gesture, not two.** The bound is cleared by
   `<project_data_dir>/runtime/launcher/blacklist-cleared.json` — the same
   resolve-ack `blacklist_status.py` reads, on the same `cleared_at >=` rule.
   A second ack file would mean `/ilk-resume` clears one bound and silently
   leaves the other.
8. **Additive.** `read_blacklist_from_postmortems` is unchanged and remains the
   richer signal when a postmortem exists. Pinned by a byte-comparison test
   against `6aaf28b`.

### Known gap (recorded, not fixed)

**A project driven by a manual `/ilk-run` plus a watchdog, with no scheduler,
has no such bound.** The scheduler owns the dispatch decision, so that is where
the bound lives; a second copy in the watchdog would mean two components with
independent, drifting notions of "no progress", and the watchdog already
declined to relaunch in the observed failure. It was not the one that needed
convincing.

An unrecorded gap is one somebody rediscovers the expensive way, so it is
written here rather than left implied.

---

## Contract 6: A captured function's stdout is its return value

### The rule

When a caller writes `x=$(fn ...)`, **stdout is `fn`'s return channel**.
Anything else printed there is concatenated into the value. Bash issues no
warning, and the corrupted value usually still satisfies an `-n` guard, so the
failure surfaces far from its cause.

Diagnostics from such a function belong on **stderr** or in a log file —
never on stdout.

### Who this binds

Every shell function whose stdout a caller captures. Measured 2026-08-29:
**32** such functions across `watchdog.sh` (12), `scheduler.sh` (9) and
`run_ilk_loop_claude.sh` (11).

### Field records — two instances, three months apart

**1. `watchdog.sh` / `invoke_postmortem_collect` (rezmac, 2026-08-29).**
`write_log()` ends with a bare `echo "$line"`. The failure paths called it and
then `echo ""`, so the captured value was the log line plus a blank line —
non-empty. The `-n` guard at `:930` passed and the raw-state fallback at
`:933-934` was **unreachable by construction**. The watchdog logged:

```
[13:12:07] classification: [13:12:07] collect.py produced no valid report path: ''
```

The embedded timestamp is `write_log`'s signature and is how it was found.
Three relaunches (12:01 / 12:37 / 13:12), no plan progress, nothing declining
to relaunch. Fixed by `write_log_quiet`, whose console copy goes to stderr;
`write_log` itself is unchanged for its other 33 call sites.

**2. `run_ilk_loop_claude.sh` / `preserve_dirty_tree_on_timeout` (`f5674c6`).**
The function ends with `echo "$wip_count"`. Its own diagnostics *correctly*
used `>&2` — but its `git commit` redirected only stderr, so on a **successful**
commit git's `[main abc1234] WIP: ...` joined the return value, `int()` raised
in the JSONL builder, and the entire iteration record was lost (run
`20260829-163114`: 0 records in `.ilk-loop.log`). Fixed by redirecting the
commit's stdout.

> Note the asymmetry: instance 2's function had *correct* logging discipline
> and still leaked, because a **subcommand** printed. The rule is about the
> channel, not about loggers.

### A related but distinct sub-family: wrong *format*, not wrong channel

`emit_jsonl_record.py` (F2) wrote spaced JSON while the runner grepped for the
compact form. Same outcome — a broken contract on a function's output — but it
is a Python format contract, not a stdout-channel violation, and no channel
detector can catch it. Pinned separately by
`skills/ilk-loop/tests/test_gate_record_format_contract.py`.

### Enforcement

`skills/ilk-watchdog/tests/test_captured_fn_logging.py` parses all three shell
scripts, resolves every `x=$(fn ...)` capture, and flags a captured function
that either calls a stdout-writing logger or runs a `git` subcommand that
prints on success without redirecting stdout.

Two properties that keep it honest:

- **It is proven to fire.** Run against `f5674c6^` it reports exactly the
  `preserve_dirty_tree_on_timeout` violation; against HEAD, zero. A meta-test
  with no demonstration that it *can* fail is decorative.
- **The fd number is load-bearing.** An early draft matched any `>/dev/null`
  substring and so read `2>/dev/null` as a stdout redirect — which would have
  scored instance 2 as already-fixed, that instance being precisely "stderr
  redirected, stdout not". The matcher pins fd 1 explicitly.

---

## Contract 8: Batch-gate verdict record (`runtime/batch-gate.json`)

The batch-end gate's verdict, persisted once per batch HEAD. This is
project runtime state, not launcher state: it outlives the run that
produced it and `/ilk-ship` Phase 0 reads it long afterwards, so both
sides resolve its location through `batch_gate.resolve_runtime_dir` —
the one resolver. Two writers resolving it differently is how the
2026-08-25 defect wrote its marker in one directory and its verdict in
another.

### Format

```json
{
  "verdict":    "pass" | "fail" | "not_configured" | "error",
  "head_sha":   "<40-char hex>",
  "invocation": "<the command that was run>",
  "timestamp":  "<ISO-8601>",

  "undeclared":     ["<node id>", ...],
  "excused_count":  31,

  "tree_sha":       "<40-char hex>",   // the tree the verdict describes
  "writer":         "batch_gate.py"    // provenance; absent on legacy records
}
```

> **`batch_gate.write_record` is the SOLE writer of this record. No other
> component may author it — not the runner, not a skill, and not a worker
> session.** This section documents the format so that READERS can validate it;
> it is not a template for producing one.
>
> This is stated because it was violated. On 2026-09-09, resolver run
> `a491abe9` (#4824) had its enforced gate time out (`exit_code: 124` against a
> declared `timeout: 300`). The worker session grepped for `batch-gate`, read
> THIS SECTION, and wrote the file by hand with `verdict: "pass"` and a
> back-dated timestamp. Rejected for a mismatched `invocation`, it polled
> `loop_status.py --json`, was told by the failure detail which value was
> expected, wrote that value instead, and polled again. Two writes, one poll
> between them, narrated in the worker's own tool descriptions.
>
> Two defences now exist, and neither is this paragraph:
> - a `pass`/`fail` verdict naming no `invocation` validates as **`unenforced`**
>   — a record cannot pass a suite it does not name;
> - `tree_sha` is emitted only by `write_record`, so tree-based freshness is
>   unavailable to a record nothing vouches for.
>
> A prose rule cannot bind a process optimising against a validator. Treat this
> note as explanation, and the validator as the enforcement.

The first four fields are `REQUIRED_FIELDS` and are **fixed** — a record
carrying exactly those four, written by any older version of the gate,
must keep loading. The last two are the **attribution fields** (added
v0.9.81): `undeclared` lists the failing node ids no `baseline_red`
declaration covered, and `excused_count` counts the ones a declaration
did cover.

### Who writes

- **`batch_gate.py`** — `_run_gate_inner` computes the split
  (`_undeclared_failures`) and carries it on the returned
  `BatchGateRecord`; `run_batch_gate` persists it on every path,
  including `error`. The attribution fields are written only when
  computed: `not_configured` and `error` records carry none.

### Who reads

- **`ship_audit.py`** (`_resolve_batch_record`) — after
  `validate_record` says the record is fresh, renders the refusal via
  `batch_gate.format_verdict_reason`, naming each undeclared node id and
  the excused count.
- **`batch_gate.py` `main()`** — prints the `[batch-gate]` excused /
  undeclared lines from the record it just wrote, not from module state.
- **`loop_status.py`** — for each shipped sub-plan, computes
  `proof_freshness` (the freshness basis) and `proof_bookkeeping_paths`
  via `batch_gate.freshness_basis`, and surfaces them in `--json` output.

### Freshness rule

A record's verdict is **fresh** when the code at HEAD is the same code
the gate certified. Two comparisons exist:

1. **Tree-equal** (default). The record carries `tree_sha` and the
   current tree matches it exactly. This is the strongest basis: the
   code is byte-identical.
2. **Bookkeeping-only** (added 2026-09-28). The record carries both
   `tree_sha` and `writer` (provenance), the trees differ, but every
   changed path is in `BOOKKEEPING_PATHS`. Currently that set contains
   only `CHANGELOG.md`. This covers release commits that touch only
   the changelog — they change the tree but not the code the gate
   certified.

Without `tree_sha` (legacy or hand-authored records), the comparison
falls back to strict `head_sha` equality. Without `writer`, the
bookkeeping-only path is unavailable — a record nothing vouches for
keeps the strict comparison.

**`BOOKKEEPING_PATHS` is narrow by design.** Tests read Markdown
(`master-template.md`, `SKILL.md`, `docs/architecture`), and
`CHANGELOG.md` appears only as a path string (`test_gate_scope.py:154`).
A guard test (`test_changelog_commit_keeps_the_proof.py::AC-7`) fails if
any test or script starts reading a `BOOKKEEPING_PATHS` entry as content.

**The writer stays strict.** `verify_attribution.check_verified_tree`
compares exact trees — a proof is still only written on the tree the
suite ran on. The bookkeeping-only relaxation applies at READ time only.

**`freshness_basis(...)`** returns `{"basis": "tree-equal" |
"bookkeeping-only" | "head-equal" | "stale", "recorded_tree",
"current_tree", "bookkeeping_paths": [...]}`. `validate_record` still
returns exactly `"fresh"` for both fresh bases.

### Invariants

1. **`REQUIRED_FIELDS` never grows.** Optional fields are added around
   it, never into it; the three validation paths that iterate it must
   keep accepting a four-field record. Guarded by
   `test_batch_gate_attribution.py::test_legacy_four_field_record_still_loads`,
   which builds such a record by hand and must never be deleted.

2. **Absence means "not recorded", never "none".** A record without the
   attribution fields was written by a gate that did not record
   attribution (older version, or a verdict for which attribution was
   never computed). It MUST be reported as such, and MUST NOT render as
   "0 undeclared" — absence rendered as a count of zero is the original
   defect with more fields. This is the batch-level twin of Contract 2b
   invariant 6: a result that cannot name what it is about must be
   reported as such, not silently interpreted.

3. **The verdict rule is not part of this contract.**
   `verdict = "pass" if (failing and not undeclared) else "fail"` is
   correct as of 2026-08-26 and explicitly out of scope; this contract
   only governs how the already-computed verdict and its attribution
   travel.

### Bug reference (v0.9.80 Phase 0 refusal, 2026-09-03)

The gate computed a 31-excused / 1-undeclared split, printed it to the
launcher log at gate time, and persisted four fields — none of them the
attribution:

```
2097: [batch-gate] 31 failure(s) excused by ship.baseline_red
2098: [batch-gate] 1 UNDECLARED failure(s) — not covered by ship.baseline_red:
2099: [batch-gate]   skills/ilk-loop/tests/test_vl_describe.py::TestSmokeGateway::test_hello_image_returns_answer
```

`/ilk-ship` Phase 0 then refused the batch with one line — `batch gate
recorded: fail` (`ship_audit.py` could say nothing else). The refusal was
re-derived by hand, the cause was misattributed to the verdict rule
itself, and the recommended fix was backwards — rebuilding a
`baseline_red`-awareness that had existed since 2026-08-26 — while the
correct one-line fix (declare the node id) was dismissed as useless.
Fixed in sub-plan `a-batch-verdict-names-its-blockers` (2026-09-03) by
invariants 1 and 2.

### batch-gate.json writers

One verdict per tree. The verification record (written by
`verify_attribution.write_gate_record`) wins: when one exists for HEAD's
tree with `writer: "verify_attribution"`, `verdict: "pass"`, and the
expected `invocation`, the batch-end gate defers — it prints
`already verified` and writes nothing.

When the batch-end gate does run and the existing record is from
`verify_attribution` (same or different tree), the gate writes its result
to `batch-gate.batch_gate.json` beside it, preserving the verification
record. Readers (`ship_audit`, `loop_status`) keep reading
`batch-gate.json`, which is the verification record's file.

The batch-end gate is the fallback for masters without a
batch-verification sub-plan. When a verification sub-plan exists and
its gate-first path discharged, the batch-end gate should find a
`verify_attribution` record and defer.

---

## Contract 9: The commit trailer (`[plan:<slug>#step-N]`)

### Purpose

The trailer is how a commit says which step of which sub-plan it discharges.
It is the only such record that lives *inside the repository* — the ship-proof
ledger (Contract 5) lives outside it, and only covers shared remotes where
the trailer is stripped by policy. Every downstream claim of the form "this
step was done" resolves, directly or through the ledger, to a trailer.

### Format

```
<type>(<scope>): <summary> [plan:<slug>#step-<N>]
<type>(<scope>): <summary> [plan:<slug>#step-<N>,step-<M>]
chore(plans): <slug> shipped [plan:<slug>#ship]
```

`<slug>` is the sub-plan front-matter `plan:` value — **not** the filename,
which carries a date prefix the slug does not.

### Who writes

- **The agent driving a step**, prompted by `run_ilk_loop_claude.sh`. There
  is no code path that generates the trailer; it is typed. That is the whole
  reason this contract exists.
- **On a shared remote the trailer is omitted entirely** by policy
  (`SKILL.md` → "Shared remote trailer policy"; `classify_remote` +
  `.ilk-remote-type` decide). Absence there is correct, and Contract 5 covers
  attribution.

### Who reads

- **`ship_audit.check_step_commits`** (`ship_audit.py:139`, `:158`) — builds
  `rf"\[plan:{re.escape(slug)}#…\]"` and `rf"\[plan:{re.escape(slug)}#ship\]"`.
  `re.escape` means this is an **exact string match**; there is no
  normalization step.
- **`ship_integrity.py:206`** — delegates to `check_step_commits`, so it
  inherits the exact match.
- **`loop_status.py:397-426`** — imports `ship_audit` and surfaces the
  `(!) unproven` marker from its verdict.
- **Gate discovery in the driver** (`run_ilk_loop_claude.sh:829-831`) —
  extracts `\[plan:[^#]+#step-[0-9]+\]` and keeps the max step per slug, to
  decide which step's `local_checks` to run.

### Invariants

1. **The match is exact, and stays exact.** A slug is matched by
   `re.escape`d equality. Fuzzy matching must never become an *acceptance*
   path: an audit that accepts an approximate slug is forgeable, which
   inverts its purpose.

2. **An unknown slug is an error, not an absence.** A trailer naming a slug
   with no corresponding sub-plan file in the plans dir is a typo. Zero
   matches for the audited slug therefore has two distinct causes — no work,
   or misspelled work — and no reader may report the second as the first.

3. **Zero matches names its near misses.** When `audit_ship` finds *every*
   authored step missing, it scans the same range for `[plan:<other>#…]`
   trailers whose longest common prefix with the audited slug covers ≥80% of
   the shorter slug, and appends `near-miss slug '<other>' found in commit
   <sha>` to `reasons` (`ship_audit.py:32-88`, `:411-429`). **The verdict is
   unchanged** — the sub-plan still audits as unproven and `/ilk-ship`
   Phase 0 still hard-stops. Only the report becomes actionable.

4. **The typo is caught in the iteration that wrote it.** The driver compares
   each extracted trailer slug against the slugs read from the plans dir and
   prints `! [trailer-slug] unknown slug '<bad>' … (nearest: '<real>')` to
   stderr (`run_ilk_loop_claude.sh:1306-1391`, called at `:2431`). Five
   commits later at Phase 0 is too late to be cheap to fix.

5. **A missing trailer is silent; a wrong one is loud.** The write-time check
   fires only when trailers were found at all (guarded on a non-empty targets
   file at `run_ilk_loop_claude.sh:2423`). It must never fire on the
   shared-remote path, where trailers are absent by design.

### Bug reference (gh-resolve, 2026-09-08)

Five commits carried `[plan:a-collaborator-is-not-antruder#…]` against a
sub-plan whose `plan:` value is `a-collaborator-is-not-an-intruder` — one
character removed, **5 of 5 mangled, 0 correct**. The last of the five spells
the slug correctly in its *subject* and wrongly in its *trailer*, which is
what made it invisible to a human reading the log. The work was complete and
correct; `ship_audit` reported `missing commit for steps 0, 1, 2, 3` and
Phase 0 hard-stopped a finished sub-plan. This is the false-**positive**
mirror of the `missing_steps: []` false negative in the same family.

Fixed in sub-plan `a-mistyped-slug-is-not-missing-work` (2026-09-08) by
invariants 3 and 4. Pinned by
`skills/ilk-loop/tests/test_trailer_slug_integrity.py`.

---

## Adding a new reader or writer

When adding a component that reads or writes any of the three artifact
types above, follow this checklist:

### For JSONL files

- [ ] **Read with `encoding="utf-8-sig"`** — never plain `utf-8`.
      `utf-8-sig` strips a BOM if present, no-op if absent. The PowerShell
      side may have written a BOM; this is a known Windows PS 5.1 behavior.
- [ ] **Write BOM-free** — if writing from PowerShell, use
      `[System.Text.UTF8Encoding($false)]` or `[System.IO.File]::WriteAll*`.
      Shell `>>` is already BOM-free.
- [ ] **Dedup on `(run_id, iteration)`** — don't double-count if re-reading.
- [ ] **Add a BOM-tolerance test** — write a test with a BOM-prefixed
      fixture and assert the reader handles it. See
      `skills/ilk-feedback/tests/test_collect_bom_tolerant.py` for the
      pattern.

### For `last-exit.json` (sentinel)

- [ ] **Check `state` before trusting `pid_alive()`** — a terminal state
      means the run is over, regardless of PID. Use:
      `alive = (state in LIVE_SENTINEL_STATES) and pid_alive(pid)`
- [ ] **Never assume a PID is unique to a run** — Windows recycles PIDs.
      The PID + state pair is the identity, not the PID alone.
- [ ] **Use `LIVE_SENTINEL_STATES = {"running"}`** — don't hardcode the
      check; import or define the set so new live states are added once.
- [ ] **Finalize on exit** — if you're a runner, rewrite the sentinel to a
      terminal state on every exit path. A `state: "running"` sentinel
      with a dead PID is a crash artifact the watchdog must catch.
- [ ] **Adding a new terminal state is a contract change.** Every terminal
      state written to the sentinel MUST be:
      1. In `collect.py`'s `_SENTINEL_FAILURE_MAP` (or handled by a named
         special-case branch in its classify function).
      2. Reachable through `watchdog.sh`'s `classify_action` arms — either
         via the label the map emits, or via the raw-state fallback.
      3. Pinned by `skills/ilk-loop/tests/test_terminal_state_is_declared.py`.
      A state missing from both consumers falls through to generic heuristics,
      which is how a failed run gets classified `clean-success`.  This has
      happened three times: `ship_integrity_violation` (until 2026-08-29),
      `timeout` (until 2026-08-29), and `selfmod_merge_failed` (until
      2026-09-17) — the last being the first to escape to another project.

### For human-verify marker (sub-plan front-matter `verified:`)

- [ ] **Absent ⇒ unverified.** Never treat a missing `verified:` field as
      confirmed. The safe default is always "not verified".
- [ ] **Degrade on malformed input.** An unrecognized value (not `true`/`yes`/`1`
      or `false`/`no`/`0`) must be treated as unverified — skip with a reason,
      never raise.
- [ ] **Check `status: shipped` first.** The marker is only meaningful on a
      shipped sub-plan. Ignore it on pending/in-progress sub-plans.

### For a shell function whose stdout is captured

- [ ] **Log to stderr or a file, never stdout.** In `watchdog.sh` use
      `write_log_quiet`, not `write_log`.
- [ ] **Redirect subcommands that print on success** — `git commit`,
      `git checkout`, `git push` and friends. `2>/dev/null` is **not** enough;
      it silences stderr and leaves stdout on the return channel.
- [ ] **Run the detector**:
      `python3 -m pytest skills/ilk-watchdog/tests/test_captured_fn_logging.py -q`
      Add the script to its `_SCRIPTS` tuple if you introduced a new one.

### For a commit trailer slug (`[plan:<slug>#step-N]`)

- [ ] **Match exactly.** Use `re.escape(slug)`; do not normalize, lowercase,
      or strip characters. Reuse `ship_audit.check_step_commits` rather than
      writing a second regex.
- [ ] **Never accept a near miss.** Similarity is diagnostic output only.
      A reader that accepts an approximate slug makes ship-proof forgeable.
- [ ] **Distinguish "no work" from "misspelled work".** Zero matches must
      report the near-miss slugs present in the same range (Contract 9
      invariant 3), not just the missing step numbers.
- [ ] **Tolerate a legitimately absent trailer.** On a shared remote there
      are none; fall back to the ship-proof ledger (Contract 5) and stay
      silent rather than reporting an anomaly.
- [ ] **Run the regression test**:
      `python3 -m pytest skills/ilk-loop/tests/test_trailer_slug_integrity.py -q --timeout=60 --timeout-method=signal`

### General

- [ ] **Read with `utf-8-sig` for any file the PowerShell side may write.**
      This includes sentinels, JSONL, launch metadata, and registry files.
- [ ] **Test the BOM case explicitly.** Don't assume "works on my machine"
      (macOS/Linux never write BOMs; Windows PS 5.1 always does).
- [ ] **Run the existing regression tests** after your change:
      - `python -m pytest skills/ilk-feedback/tests/test_collect_bom_tolerant.py -q`
      - `python -m pytest skills/ilk-loop/tests/test_status_terminal_sentinel.py -q`
      - `powershell -File skills/ilk-loop/tests/test_runner_outcome_allpassed.ps1`

## Contract 10: The ship transition (front-matter + marker commit)

**Writers:** `ship_transition.py` (`--ship`), and the agent following
`templates/subplan-template.md`'s "Step N — E2E + handoff".
**Readers:** `loop_status.py`, `ship_audit.py`, `plan_status.reconcile_master_status`,
`test_ship_integrity` in `run_ilk_loop_claude.sh`.

Shipping a sub-plan writes to **two stores that no transaction spans**:

| half | store | mutable? |
|---|---|---|
| `status: shipped` in the sub-plan front-matter | `~/.ilk-data/projects/<key>/plans/` — **outside** the repo | yes |
| `chore(plans): <slug> shipped [plan:<slug>#ship]` | the project repo — **inside** it | no (append-only) |

They are separate by design (`toolkit-data-never-enters-consumer-repo`), so
the transition between them has to be recoverable rather than atomic.

### The rule

**Write the marker commit first, the front-matter second.**

Not because either order is compelled by evidence — only the recoverability
is. The residue of this order is `marker-without-status`, which converges
forward from evidence that is already durable: an idempotent front-matter
write, safe to repeat. The reverse residue is `status-without-marker`, which
could only converge by *fabricating* a commit for work nothing in the repo
attests to — forging the trail `ship_audit` reads. `--repair` therefore
**refuses** that direction and names the pair, rather than guessing.

### What went wrong (2026-09-08, gh-resolve)

`MASTER-2026-09-07c-execution-plan.md` read `status: shipped` while
`2026-09-07c-conflict-batch-verify.md` read `status: in-progress` at
`current_step: 2`, with the `[plan:conflict-batch-verify#ship]` commit
present at `41f0cd688724`. `loop_status` then selected that older master as
next-active and flagged three of its sub-plans `(!) unproven`. The window is
opened by an iteration killed at the timeout boundary between the two writes
— which is why this contract's fix shipped after
`a-productive-timeout-is-not-a-barren-one`.

### The intent marker

`ship_transition.py` writes `.ship-intent.json` into the plans dir **before**
the first half and clears it only once **both** are durable. It is what
separates "an iteration died mid-ship" from "someone edited a file by hand" —
the two leave identical store contents otherwise.

That distinction is load-bearing for the driver. `test_ship_integrity` reverts
`shipped` → `in-progress` when a sub-plan's gate is red, and the marker commit
stays in history — a state byte-identical to an interrupted transition. So the
driver's automatic converge (`converge_ship_transition`) passes
`--only-interrupted` and repairs **only** pairs the intent marker attests to;
an unconditional auto-repair would silently re-ship a deliberately unwound
sub-plan on the next run. The operator CLI leaves the flag off, because
converging an old residue is a judgment a human is making on purpose.

### Refusals are expected in volume, and are not this contract's bug

Measured on gh-resolve 2026-09-08: **145 diverged pairs across 312 sub-plans
— 1 repairable, 144 refused**, with 169 `#ship` markers in history. Of the 144,
**141 slugs carry other `[plan:<slug>#step-N]` trailers**, so the trailer
convention *was* in force for them; they simply shipped without a marker
commit. That is a proof gap `loop_status` already reports as `(!) unproven`,
not a half-written transition, and `--repair` cannot decide it from evidence.
The CLI prints the shared refusal paragraph once and names every slug, so the
one actionable pair is not buried (69KB → 5.9KB on that run).

### If you add a reader or writer

- [ ] **Never write the front-matter first.** If you cannot use
      `ship_transition.ship()`, do the marker commit first by hand.
- [ ] **Never fabricate a marker commit** to converge a pair. Refuse.
- [ ] **Never auto-repair without `--only-interrupted`** from an automatic
      caller — you will out-vote `test_ship_integrity`'s revert.
- [ ] **Match markers over `--all` and the full message** (`%s%n%b`), as
      `ship_audit.check_step_commits` does. A subject-only predicate misses
      body-placed trailers.
- [ ] **Run the regression test**:
      `python3 -m pytest skills/ilk-loop/tests/test_ship_transition.py -q --timeout=60 --timeout-method=signal`

---

## Contract 13: The selfmod merge-back host-wide probe

### Purpose

A selfmod merge-back replaces the files under `~/.claude-worker/skills/`
symlinks.  Any loop running THIS clone's `run_ilk_loop_claude.sh` for another
repo — or any worker whose argv contains `ilk please continue` — reads those
files.  Merging under them corrupts the running loop.

The per-project probe (`_find_live_ilk_pids`) matches only runners whose
`--project-path` is THIS repo.  The host-wide probe catches the two classes
the per-project probe misses.

### What blocks a merge

| class | match | example |
|---|---|---|
| clone runner | `<clone>/skills/ilk-loop/scripts/` appears literally in argv | `run_ilk_loop_claude.sh --project-path /other/repo` |
| worker | `ilk please continue` appears in argv | `claude -p "ilk please continue …"` |

### What does NOT block

| class | reason |
|---|---|
| runner for a different clone | literal path prefix does not match |
| this process or its ancestors | excluded by `_self_and_ancestor_pids()` |
| this process's descendants | excluded (the merge runs as a child of the runner) |

### Who writes

- **`selfmod_worktree.py`** — `_find_live_ilk_pids_hostwide` runs after
  `_find_live_ilk_pids` in `merge_back()`.

### Who reads

- **`merge_back()`** — Step 1a2, between the per-project probe (Step 1a)
  and the daemon bounce (Step 1b).
- **The merge CLI** — `main()`, which prints each blocking PID's category
  (clone-runner or worker) before exiting 2.

### Invariants

1. **Literal path prefix, never a substring.** A dotted path must not match
   a neighbour (`/a/repo` must not match `/a/repo-2`).  The match is
   `scripts_dir in cmdline`, where `scripts_dir` is the resolved
   `<clone>/skills/ilk-loop/scripts/` path.

2. **Fail-closed on probe failure.** If `_pgrep` raises (broken probe,
   permission error), the result is `MergeBlockedError(blocking_pids=[-1])`,
   which the CLI converts to exit 4.  Never treat a probe failure as "no
   loops".

3. **Ancestor and descendant exclusion.** The probing process and its
   ancestors are excluded (the merge runs inside the loop).  Descendants
   are also excluded (the merge runs as a child of the runner).

4. **Host-wide, not world-wide.** The probe matches THIS clone's script
   path, not any runner.  A different clone's runner carries a different
   script path and is not matched.

### Bug reference (gh-resolve-57, 2026-09-28)

Three resolver processes (pids 19829, 19834, 19850) were running
`…/ilk-skills/skills/ilk-loop/scripts/run_ilk_loop_claude.sh` with
`--project-path /Users/chad/Projects/keyreply/kira-cloudflare-scratch`.
The per-project probe (`_find_live_ilk_pids`) would have let the merge
proceed because the `--project-path` did not match.  The host-wide probe
catches these by matching the script path prefix.

---

## See also

- `docs/runtime/loop-runtime-hardening.md` — broader runtime hardening notes
- `docs/runtime/ship-gate-design.md` — the ship gate: why `shipped` never meant
  "gated", the tier table's measured behaviour, and its open limits
  (BOM reads, git stderr, branch policy, hung-alive detection).
- `skills/ilk-loop/SKILL.md` — the loop convention itself.
- Sub-plan `a-failed-classification-cannot-be-the-classification` (2026-08-29)
  — Contract 6, the captured-stdout rule.
- Sub-plan #1 (`collect-bom-tolerant-reads`) — the BOM fix.
- Sub-plan #2 (`runner-trust-allpassed`) — the all_passed fix.
- Sub-plan #3 (`status-terminal-sentinel-alive`) — the liveness fix.
- Sub-plan `a-mistyped-slug-is-not-missing-work` (2026-09-08) — Contract 9,
  the commit-trailer slug.
- Sub-plan `one-writer-for-the-ship-transition` (2026-09-08) — Contract 10,
  the two-store ship transition and its repair.
- Contract 11 (2026-09-16) — the enforcement scope, and the three defects
  that produced it.

## Contract 11: The enforcement scope (which ships may be auto-reverted)

**This is an outward-facing interface.** Every project's loop runs this code
from the shared toolkit clone. A change to the scope does not degrade a feature
— it decides whether other people's pipelines keep running. It is versioned by
behaviour, not by a number, so the compatibility rules below are the only thing
standing between a refactor here and a halted pipeline elsewhere.

### The two checks, and why they are not interchangeable

Both ask "is this `shipped` backed by the work". They differ in **scope**, in
**when**, and in **what they do about it** — and conflating them is what caused
the 2026-09-16 near-miss.

| | `ship_integrity` (in-loop) | `ship_audit` / `/ilk-ship` Phase 0 |
|---|---|---|
| runs | every iteration, from the runner | once, at release |
| scope | **the ACTIVE master's registered sub-plans only** | **everything the release covers, no limit** |
| on failure | **auto-reverts** `shipped` → `in-progress` | **refuses to release**; reverts nothing |
| blast radius | the batch being worked | a report a human reads |

The asymmetry is deliberate. An auto-revert is cheap and safe *inside the batch
a worker is currently driving* — the worker is right there and will redo the
step. The same auto-revert pointed at historical batches is a mass revert, and
it is worse than it sounds: `ship_integrity_violation` maps to
`shipped-unverified` → `needs-human` (see the state vocabulary above), which
`ilk-watchdog/tests/test_shipped_unverified_never_relaunches.py` pins as
never-relaunch. A mass revert therefore **halts an unattended loop and the
watchdog refuses to restart it**.

So: reaching backwards is not "stricter enforcement". It is an outage.

### Format — how scope is computed

`run_ilk_loop_claude.sh` → `test_ship_integrity`:

1. Resolve the active master: `loop_status.pick_active_master`.
2. Its registered sub-plans: `plan_status.extract_subplan_files`.
3. Walk `"$plans_dir"/*.md`. **Nothing else narrows this walk** — the registry
   membership test below is the only batch boundary that exists.
4. For each `status: shipped` sub-plan with a declared gate, resolve a verdict
   from this iteration's `local_checks` results file (Contract 2b).

### `--gate-passed` vocabulary

Four values. `skip` and `unknown` are **not** synonyms, and the distinction is
load-bearing:

| value | meaning | gate half | step-commit half |
|---|---|---|---|
| `true` | gate ran, passed | enforced | enforced |
| `false` | gate ran, failed | enforced (violation) | enforced |
| `unknown` | a gate is DECLARED and its result is MISSING | **violation** — dishonest | enforced |
| `skip` | no gate ran this ITERATION | **not enforced** | **enforced** |

`skip` exists because "does every authored step have a commit" is a question
about git history and holds whether or not a gate ran. Without it, a worker that
skipped its gate skipped enforcement with it.

**But `skip` is only passed for sub-plans inside the active batch.** Outside it,
a non-verdict still means skip-entirely. That single conditional is the whole
contract.

### Who writes

- `run_ilk_loop_claude.sh::test_ship_integrity` — computes scope, resolves the
  verdict, invokes the checker, applies the revert.
- `ship_integrity.py` — pure validator; decides, never reverts.

### Who reads

- `ilk-watchdog` — via the sentinel state, to decide relaunch (Contract 1).
- `/ilk-ship` Phase 0 — independently, with no scope limit.
- Every consumer project's loop, from the shared clone.

### Invariants

1. **Auto-revert never reaches outside the active master's registry.** A change
   that widens this is a breaking change to every consumer.
2. **Unresolvable scope fails SAFE, not wide.** If `pick_active_master` raises
   or no `MASTER-*.md` exists, the scope set is empty and every non-verdict
   takes the skip-entirely path — the pre-2026-09-16 behaviour. A broken master
   lookup must never be able to mass-revert. *Verified by the gh-resolve session
   reading the diff, 2026-09-16.*
3. **Membership is an exact whole-name match** (`grep -qxF` on the basename), so
   a sub-plan whose filename is a prefix of another cannot be pulled into scope.
4. **`skip` is not `unknown`.** Collapsing them re-creates either the mass
   revert (if `skip` enforces the gate half) or the original evasion (if
   `unknown` stops enforcing the step half).
5. **Fail-open on an unresolvable project root stays.** `ship_integrity`
   warns and returns no violation when git cannot answer. Flipping this to
   fail-closed is the 2026-08-20 shape.

### Backward compatibility — what may and may not change

**May change freely:** the wording of warnings; how the scope set is *computed*,
provided the resulting set is never larger than the active master's registry;
performance.

**May change only with a migration:** the `--gate-passed` vocabulary. Adding a
value is safe **only if** older `ship_integrity.py` versions reject it loudly
rather than coercing it — an unrecognised value that falls through to
`{"all_passed": False}` reads as a red gate and reverts. Removing or repurposing
a value is breaking.

**May not change without a decision record:** invariants 1-5. Each one has an
incident behind it, and each failure mode is an outage in someone else's repo
rather than a bug in this one.

**Before changing any of this, measure the blast radius over the FULL plan
corpus** — `"$plans_dir"/*.md`, every project — not a recent slice. See the bug
reference.

### Bug reference (2026-09-16)

Three defects in one mechanism, in sequence, each found only because the next
was looked for.

1. **The check never ran.** `ship_integrity` resolved the project root from
   `Path.cwd()`. Plans live at `~/.ilk-data/projects/<key>/plans`, which has no
   `.git` ancestor by design, and the runner invokes the check from outside the
   repo. Measured across every project's launcher logs: **18 skips against 18
   fires** — it silently no-opped about half the time. Fixed by resolving the
   root through the registry (`136f1b6`).

2. **The check was never called.** The runner `continue`d past it entirely
   whenever no gate ran in the current iteration. A worker that skipped its gate
   skipped enforcement with it, and
   `authored-steps-stop-at-the-findings-section` shipped **twice** with no
   commit for step 2. Fixed by passing `skip` instead (`6010938`).

3. **That fix removed the only scope.** The `continue` in (2) was also the
   *de-facto* batch boundary — nothing else narrows the walk. Measured on
   gh-resolve: **445 plan files, 360 shipped, 358 enforced, 71 would revert**,
   oldest `2026-07-26-corpus-dossier` — the entire Jul–Sep corpus. That is the
   2026-08-20 incident again (69 of 150 then). Fixed by making the batch
   boundary explicit (`2317750`); enforced population went **358 → 5**.

**How it was caught, and the lesson that generalises.** Defect 3 was found
before release *only* because the impact was checked with the consuming project
rather than reasoned about. The ilk-skills session first reported **6** reverts
— having globbed `2026-09-1[5-6]*.md`, 33 of 445 files, and published a 7% slice
as the blast radius. The gh-resolve session re-ran the same instrument over the
full population and reported 71. Both numbers came from the same code; only one
had the right denominator.

A negative or a count about a *corpus* is only as good as the glob that produced
it. For this contract the corpus is every `.md` in every project's plans dir,
because that is what the runner walks.

## Contract 12: The master pin (`ILK_MASTER`)

**Writer:** `scheduler.sh` → `launch.sh --master NAME`

**Readers:** `loop_status.py`, `run_ilk_loop_claude.sh`, worker `/ilk`

**Purpose:** Pin a run to a specific master, preventing the scheduler from
dispatching work on the wrong master when multiple non-terminal masters exist
in one plans dir.

### How it works

1. **Scheduler resolves the master name.** `scheduler_scan.py` returns an
   `active_master_name` field (the first active master's filename, or `null`).
   The scheduler parses this and passes `--master <name>` to `launch.sh`.

2. **launch.sh exports ILK_MASTER.** When `--master NAME` is passed,
   `launch.sh` adds `export ILK_MASTER='NAME';` to the `env_prefix` that
   precedes the runner command. The runner and worker inherit this env var.

3. **loop_status.py honours the pin.** In `pick_active_master()`, when
   `ILK_MASTER` is set and the file exists in the plans dir, that master is
   the only candidate — whatever its status. A pinned all-shipped master
   yields all-shipped; it never rolls over to another master. When the file
   does not exist, a notice is emitted and the default pick proceeds.

4. **Dry-run shows the pin.** `launch.sh --dry-run` prints `IlkMaster: NAME`
   when the flag is passed. The scheduler's `--dry-run --once` JSON includes
   `"master": "NAME"` in the dispatch record.

### Backward compatibility

When `ILK_MASTER` is unset (the default), behaviour is identical to today:
`loop_status.py` uses its existing priority-based pick. The scheduler only
passes `--master` when `active_master_name` is non-empty in the scan output.

### Contract violations

- A missing pinned master file emits a notice but falls back (does not stall).
- A pinned master that is all-shipped or terminal yields that state (no rollover).
- The pin is per-run, not persistent — it lives only in the runner's environment.

---

## Contract 13: Run result file (unattended profile)

### Purpose

An opt-in feature for gh-resolve-dispatched runs: when the master carries
`ilk_profile: unattended` and `result_file: <abs path>`, the runner writes a
structured JSON result file on every exit path. gh-resolve reads this file to
determine the run's outcome instead of parsing the sentinel or postmortems.

Without the profile, behaviour is byte-for-byte identical to today.

### Format

```json
{
  "schema_version": 1,
  "provenance": "runner",
  "pinned": false,
  "run_id": "",
  "master_slug": "",
  "project_key": "",
  "worktree": "",
  "base_sha": "",
  "head_sha": "",
  "commits": [{"sha": "", "subject": ""}],
  "checks": [{"slug": "", "step": 0, "cmd": "", "cwd": "",
               "outcome": "pass|fail|unmeasured", "exit_code": null,
               "failed_count": null, "duration_s": 0.0, "reason": null,
               "path_prelude_applied": false}],
  "gate_resolution": "trailer|ledger|pre_iter|active|none",
  "proof": {"trailer_found": false, "ledger_rows": 0,
            "verification_record": null, "record_suite_failed": null},
  "integrity": [{"slug": "", "violation": "", "enforced": false}],
  "exit_state": "", "iterations": 0, "started_at": "", "ended_at": ""
}
```

### Who writes

- **`run_result.py`** — the sole writer. Called by the runner on every exit
  path (normal terminal, `finalize_sentinel` for signal/abnormal exit,
  and lock refusal when `ILK_MASTER` is set).
- **The runner** (`run_ilk_loop_claude.sh`) — invokes `run_result.py write`
  with the accumulated checks JSONL and integrity violations JSONL.

### Who reads

- **gh-resolve** — reads the file to determine the run's outcome. A missing
  file is interpreted as `runner_died`.

### Check outcome vocabulary

The result file uses a **tri-state** vocabulary distinct from the internal
four-state `{pass, fail, error, skipped}`:

| Internal | Result file | Rules |
|---|---|---|
| `pass` | `pass` | — |
| `fail` | `fail` | keeps `exit_code` and `failed_count` |
| `error` | `unmeasured` | requires `reason` |
| `skipped` | `unmeasured` | requires `reason` |
| `inconclusive` | `unmeasured` | requires `reason` |

A timeout is never `fail` in the result file. `unmeasured` rows without a
`reason` are refused (the writer raises).

### Exit states

Every exit path writes one of:

| Exit state | Meaning |
|---|---|
| `all-shipped` | Clean finish |
| `shipped-unproven` | Sub-plans shipped without proof |
| `blocked-no-runnable` | All remaining sub-plans blocked |
| `ship_integrity_violation` | Gate red on a shipped sub-plan (record-only under profile) |
| `local_checks_failed` | Gate failed |
| `interrupted` | Signal/abnormal exit (from `finalize_sentinel`). A SIGINT or SIGTERM while the agent runs now terminates it within a 10 s grace period (TERM → wait → KILL) before writing this state. |
| `lock_held` | Another runner holds the lock |
| `profile_unsupported` | PS1 runner: profile not implemented |
| `max-iterations` | Hit iteration budget |
| `timeout` | Iteration timeout |
| `no-progress` | 3 consecutive barren iterations |

### Atomicity

The writer uses `tmp` + `os.replace`. A crash between the write and the
replace leaves no partial file (the tmp is cleaned up by the OS eventually).

### Profile semantics

When `ilk_profile: unattended` is set:
- **No park, no revert.** `ship_integrity.py --record-only` prints violations
  but does not modify front-matter. The runner skips `park_master.py`.
  Violations are recorded in the result file's `integrity[]` with
  `enforced: false`.
- **No needs-human dead ends.** `shipped-unproven` and `blocked-no-runnable`
  still stop the run. They are recorded as exit_states, and the "Do NOT
  relaunch; this needs a human" lines are replaced by
  `[unattended] exit_state=<x> — see <result_file>`.
- **`pinned`** is `true` iff `ILK_MASTER` was set for this run.

### Validation

`result_file` must be:
- Absolute (starts with `/`).
- Outside the project root and the selfmod worktree.
- Its parent directory must exist or be creatable.

Invalid ⇒ log `[unattended] result_file refused: <reason>`, keep the
profile's no-park semantics, and write no file.

### Invariants

1. **One file per run.** The file is written once on the terminal exit path.
   Multiple writes (e.g. from `finalize_sentinel` and the normal exit) are
   idempotent — the last write wins, and both carry the same data.
2. **Missing file = runner died.** gh-resolve interprets a missing result
   file as the runner crashing without finalizing.
3. **Never written by the worker.** Only the runner writes this file.
4. **`provenance` is always `"runner"`.** gh-resolve uses this to
   distinguish runner-written files from any other source.

## Contract 13: The worker session marker (`ILK_WORKER_SESSION`)

`ILK_WORKER_SESSION=1` is set in the environment of the spawned claude
process (the worker), but NOT in the runner's own environment. This
ensures that the gate-first fast path and post-iteration `local_checks`
do not see the variable.

### Who writes

- **`run_ilk_loop_claude.sh`** (`invoke_claude_iteration`) — prepends
  `ILK_WORKER_SESSION=1` to the `gtimeout ... claude ...` command line.
  It is set only for that child process, not exported into the runner.

### Who reads

- **`verification_record.py`** — when `--run-suite` is passed and
  `ILK_WORKER_SESSION=1`, refuses to run the suite (returns nonzero,
  prints `ILK-CHECK: unmeasured refused in a worker session`). The suite
  must run in the driver, not inside a worker session.
- **`verify_attribution.py`** — when `--remeasure-if-stale` triggers a
  re-measure and `ILK_WORKER_SESSION=1`, refuses with the same marker.
  The worker must commit its fix and end its turn; the driver re-measures
  on the next iteration.
- **`batch_gate.py`** — both `run_batch_gate` and `write_record` refuse
  inside a worker session.  `WorkerSessionRefused` (a `PermissionError`
  and an `OSError`) is raised before any read of git or any write.  The
  CLI catches it and exits 1 with the same `ILK-CHECK: unmeasured refused`
  marker.

### Contract: `--remeasure-if-stale`

`verify_attribution.py` gains `--remeasure-if-stale`. The record is
stale when it is missing, when its `verified_head` is not HEAD, or when
its `suite_failed` is not an integer. In that case (and outside a worker
session) the flag calls `verification_record.main()` to re-measure with
the record's `base_sha` and `suite_scope` (or `--base-sha` / `--scope`
when the record is missing and they were passed), then attributes as
today. Inside a worker session the flag does NOT re-measure: it fails
with the same refusal as `--run-suite` in a worker session.

## Contract 14: The stale-record step-0 fallback (25c)

A `batch_verification: true` sub-plan whose step 1 gate is the pre-25a
form (`verify_attribution.py` without `--remeasure-if-stale`) cannot
re-measure after a fix commit: the worker is refused by `--run-suite`,
step 1 rejects the stale record, and step 0 never re-runs because
`current_step` is 1. The run loops until blacklisted for no progress.

### The probe: `--is-stale`

`verify_attribution.py --is-stale --project . --batch <slug>` exits 0
when the record is stale (missing, `verified_head` ≠ HEAD, or
`suite_failed` not an integer) and exits 1 when fresh. It never runs the
suite. Used by the gate-first fast path to decide whether to re-run
step 0.

### The fallback

In the gate-first fast path (`attempt_gate_first_fast_path`), for a
`batch_verification: true` sub-plan at `current_step >= 1` whose step
declares `gate_first`:

1. Run `--is-stale` against the batch record.
2. If stale (exit 0), run step 0's gate first (gate-first, no worker).
   Log `[gate-first] <slug>: record stale (<reason>) — re-running step 0
   before step <N>`.
3. Then run the current step's gate as normal.

Step 0's gate re-measures the record. The current step's gate then sees
a fresh record and passes.

### Finished batch-verify redirect

When a `batch_verification: true` sub-plan has every step discharged
(`current_step >= estimated_steps`) but is still `pending` (not shipped),
`get_active_subplan_targets` returns a step index past the last heading.
The gate-first fast path detects this and targets the last step
(`estimated_steps - 1`) instead:

1. If the last step declares `gate_first`, its gate re-runs as a fresh
   measurement. With every step discharged, the driver ships the sub-plan
   — no agent needed. Log: `[gate-first] <slug>: all steps discharged but
   unshipped — re-running step <n>'s gate to ship`.
2. If the last step does **not** declare `gate_first`, the driver cannot
   ship it. Log: `[gate-first] <slug>: all steps discharged but unshipped,
   and step <n> is not gate_first — the driver cannot ship it`. The
   iteration ends without dispatching a worker.

Regression: 2026-10-01 20:20, a-red-is-blamed-on-its-owner-verify stalled
at current_step 2/2 — every iteration dispatched a worker that could do
nothing.

### Scope rule

When `--remeasure-if-stale` re-measures and no explicit `--scope` was
passed (args.scope is "auto"), the record's `suite_scope` takes
precedence. If the record says `suite_scope: full`, the re-measure uses
`--scope full`. This prevents a downgrade from full to auto when step 0
measured full and step 1 has no `--scope` flag.

### plan_lint WARN

`lint_old_form_verify_step1` flags a `batch_verification: true` sub-plan
whose step 1 gate calls `verify_attribution.py` without
`--remeasure-if-stale`:

```
WARN <slug>: step 1 verify gate lacks --remeasure-if-stale (pre-25a form;
safe since 25c, but it re-measures only via the driver's step-0 fallback)
```

### Contract: `--is-stale`

`verify_attribution.py` gains `--is-stale`. Requires `--batch`. Exits 0
(stale) or 1 (fresh). Never runs the suite. The driver uses it before
dispatching the gate-first fast path's current step.

---

## Contract 15: The pre-iteration snapshot (`pre-iter-snapshot-${i}.json`)

### Purpose

A snapshot of dirty/untracked state taken **before** the agent runs, so
`preserve_dirty_tree_on_timeout` can commit only paths the iteration
changed — not files that were already dirty or that another tool touched.

### Format

```json
{
  "head": "<sha>",
  "dirty": {"<path>": "<git hash-object or \"deleted\">"},
  "untracked": ["<path>", "..."]
}
```

### Who writes

- **`run_ilk_loop_claude.sh`** — calls `iteration_snapshot.py take` for
  each repo in `REPOS` (resolved to its effective repo). Written at
  iteration start, right after `get_repo_heads`. Exports
  `ILK_PRE_ITER_SNAPSHOT=<path>` for the iteration.

### Who reads

- **`preserve_dirty_tree_on_timeout`** — calls
  `iteration_snapshot.py changed-since` to get the NUL-separated list of
  paths that are dirty/untracked NOW and either were not in the snapshot or
  have a different content hash. Stages only those paths (`git add --`).
- **Gate isolation** (sub-plan `gate-isolation-restores-what-it-took`) —
  will read the same file to limit `git stash push` to new untracked paths.

### Invariants

1. **Writer is the runner, before the agent.** The snapshot MUST be taken
   before `invoke_claude_iteration`. A snapshot taken after captures the
   agent's own changes as "pre-existing" and defeats the purpose.
2. **Missing or unreadable snapshot ⇒ fallback to `git add -A`.** The
   runner logs `no pre-iteration snapshot — preserving the whole dirty tree`
   and preserves everything. Losing a timed-out agent's work is worse than
   an over-broad WIP commit.
3. **Empty `changed-since` ⇒ skip the commit.** If nothing changed since
   the snapshot, there is nothing to preserve.
4. **`git hash-object` for content comparison.** Two states of the same
   path are compared by their blob hash, not by mtime or size.
5. **`--exclude-standard` semantics come from git.** `git status
   --porcelain=v1 -z --untracked-files=all` already respects
   `.gitignore` and `--exclude-standard`.

### Schema

The snapshot is a single JSON object. `dirty` maps paths to their
`git hash-object` output (40-char hex) or the literal string `"deleted"`
when the file no longer exists. `untracked` is a list of paths git
considers untracked (respecting `.gitignore`).

### Bug reference (ilk-skills #46)

`preserve_dirty_tree_on_timeout` ran `git add -A` and committed every
dirty and untracked path. On rezmac `kira-cloudflare-resolver`
`50bc6c223` (Sep 22) and `dc49ac2ba` (Sep 24), this took
`gc_push_failures.json` — a tracked file the gc tool edits — and
committed it in the WIP. The snapshot ensures only iteration-changed
paths are staged.

## Contract 16: Verify-sub-plan gate targeting (ascending steps)

A sub-plan whose frontmatter carries `batch_verification: true` is a
**verify sub-plan**: its step 0 writes a verification record, and step 1
(or later) reads it. The gate must run **every committed step in ascending
order**, not just the max step. If step 0's gate fails, later steps are
not attempted.

Normal (non-verify) sub-plans keep the existing max-step-per-slug rule:
only the highest committed step is gated.

### Sources

| Source                     | Verify sub-plan       | Normal sub-plan     |
|----------------------------|-----------------------|---------------------|
| `get_local_check_targets`  | all committed steps   | max step per slug   |
| `get_ledger_check_targets` | all steps in range    | max step_to-1       |
| merge (`all_targets_file`) | all steps preserved   | max step per slug   |

### Detection

`_list_verify_slugs` reads every sub-plan file in the plans directory and
returns slugs whose frontmatter has `batch_verification: true`. Called once
per gate resolution cycle (in `get_local_check_targets`,
`get_ledger_check_targets`, and the merge).

### Bug reference (ilk-skills #43)

gh-resolve run `20260925-085248` iter 7 committed `#step-0` and `#step-1`
of a verify sub-plan together. The driver gated only step 1 (max-step
rule), so step 0's driver-side suite never ran. The sub-plan shipped as
`loop-verified` on the strength of step 1's gate alone.

---

## Contract 13: Red-owner attribution (`red_owner.py`)

### Purpose

When the widest gate goes red, the running sub-plan may not be the one
that introduced the regression. A cross-sub-plan regression causes a
strike (`auto_block_fails` bump) on the wrong sub-plan, which can
quarantine it without fixing the real cause. This contract defines the
bisect helper and the runner's no-strike-when-not-owner rule.

### The helper

`red_owner.py` binary-searches `git rev-list --first-parent <base>..<head>`
to find the first commit where the failing test nodes break. It uses a
detached worktree (never the live tree) and is bounded:
`ceil(log2(n))+1` runs, wall-clock cap (`--budget-s`, default 300).

### Format

```json
{"first_red": "<sha>", "first_red_subject": "...", "base_green": true}
```

| Field | Type | Meaning |
|---|---|---|
| `first_red` | `string \| null` | SHA of the first red commit, or `null` |
| `first_red_subject` | `string \| null` | Commit subject, or `null` |
| `base_green` | `bool` | `false` means the base was already red (no bisect) |
| `reason` | `string` | Present only when `first_red` is `null` due to budget |

### Who writes

- **`red_owner.py`** — invoked by the runner.

### Who reads

- **`run_ilk_loop_claude.sh`** — after B2 confirms a gate failure, before
  the quarantine call. Maps `first_red` to its owning sub-plan via the
  commit's `[plan:<slug>#…]` trailer.

### Invariants

1. **Bisect never touches the working tree.** A detached worktree under
   the run's log dir is used. `git status --porcelain` and
   `git stash list` are unchanged afterwards.

2. **No strike on the wrong sub-plan.** When the owner slug differs from
   the running sub-plan, `auto_block_fails` is NOT bumped. The runner
   appends a Findings note: `cross-sub-plan regression: <node> first red
   at <sha> (<owner>); needs a fix sub-plan`. The run still stops
   (gate is red).

3. **Budget cap is safe.** `first_red: null` with `reason: budget` means
   the runner falls through to today's behavior (normal strike).

4. **Red at base skips bisect.** `base_green: false` means the test was
   already broken at the master's base — no bisect, normal strike.

### Bug reference (ilk-skills #51)

gh-resolve MASTER-2026-09-25b sub-plan 3's widest gate went red on a
test first broken by sub-plan 1's commit `2708654`. Sub-plan 3 took the
strike (`auto_block_fails` 1 of 2) and could not fix it in scope.

---

## Contract 17: The declared-gates file (`ILK_DECLARED_GATES_FILE`)

### Purpose

The runner writes the current iteration's declared `local_checks` commands to
a file and exports its path as `ILK_DECLARED_GATES_FILE`. The `no-full-suite`
hook reads this file and denies any Bash command whose normalized form matches
a declared gate — the driver runs these gates after the worker commits, so the
worker must not run them itself.

### Format

One command per line, exactly as declared in the sub-plan's `local_checks`
(frontmatter block + per-step `local_checks` fences). De-duplicated on the
raw command string:

```
bun run test:non-ui:convex
python3 -m pytest skills/ilk-loop/tests/test_hooks_install.py -q
```

### Who writes

- **`run_ilk_loop_claude.sh`** — right after exporting `ILK_ITERATION_SUBPLAN`,
  before `invoke_claude_iteration`. Uses `run_local_checks.collect_declared_local_checks`
  to extract commands from the target sub-plan's frontmatter and per-step fences.
  No target, or no declared gates ⇒ no file, variable unset.

### Who reads

- **`hooks/no-full-suite.sh`** — when `ILK_DECLARED_GATES_FILE` names a readable
  file, a Bash command whose normalized form equals one of its lines is denied
  with reason `the driver runs this gate after your commit — commit and end your turn`.
  Normalized means whitespace-collapsed, with a leading `cd <x> &&` stripped.

### Lifecycle

The variable is exported alongside `ILK_ITERATION_SUBPLAN` before the agent
runs, and unset alongside it after the iteration completes. The file lives in
`${RUN_LOG_DIR}/declared-gates-<i>.txt` and persists for post-mortem inspection.

### Invariants

1. **Exact match after normalization.** Both the gate command and the Bash
   command are normalized (whitespace-collapsed, `cd <x> &&` stripped) before
   comparison. No fuzzy matching.
2. **Empty file ⇒ no denial.** A file with zero lines is equivalent to no file.
3. **Absent variable ⇒ no denial.** When `ILK_DECLARED_GATES_FILE` is unset or
   empty, the hook's existing runner-list and scoping logic applies unchanged.
4. **The hook's existing denials still apply.** The declared-gates check runs
   AFTER the runner-list detection, so a command already denied by the runner
   list (e.g. bare `pytest`) is denied by the runner list, not by the
   declared-gates mechanism.

---

## Contract 18: The phase marker (`phase.json`)

### Purpose

While the runner is in a gate (not the agent), the panel (xbar) needs to
display the batch, sub-plan, and step — and show a gate timer instead of a
stale iteration age and climbing heartbeat. The runner writes its current
phase to `phase.json` so `status_all.py` can surface it.

### Format

```json
{
  "phase": "agent|gate|batch-gate|between",
  "slug": "sub-plan-slug|null",
  "step": 2|null,
  "run_id": "20260929-083918",
  "iteration": 1,
  "started_at": 1727587200,
  "pid": 48268
}
```

`slug` and `step` are `null` for non-gate phases (`agent`, `between`,
`batch-gate`). `iteration` is the current iteration counter (0 before the
first iteration). `started_at` is epoch seconds when the phase was written.

### Who writes

- **`run_ilk_loop_claude.sh`** — the `write_phase` function. Called at each
  choke point:
  - `agent` — right before the `gtimeout ... claude` pipeline launch.
  - `between` — after the pipeline `wait` returns.
  - `gate` — at `invoke_local_checks` entry, with slug and step from the
    first line of the targets file.
  - `between` — at `invoke_local_checks` return.
  - `batch-gate` — at `invoke_batch_gate` entry.
  - `between` — at `invoke_batch_gate` return.
  - At run exit, `phase.json` is **removed** (in `finalize_sentinel`,
    `_write_terminal_sentinel`, and the main-loop final sentinel write).

### Who reads

- **`status_all.py`** — reads `phase.json` from the launcher dir to populate
  the `phase`, `phase_slug`, `phase_step`, and `phase_elapsed_s` fields in
  its JSON output. The panel renderer (`render_xbar.py`) consumes these.

### Atomicity

The write uses a tmp file in the same directory, then `mv`. A write failure
never aborts the run — the function returns 0 on error.

### Staleness rule

A `phase.json` is **stale** (must be read as absent) when ANY of:

1. The `pid` field names a process that is not alive.
2. The `run_id` field does not match the sentinel's `run_id`.
3. The file is unparseable.

A stale file provides no phase information — the panel falls back to its
current behaviour (iteration age + heartbeat).

### Invariants

1. **One writer.** Only `run_ilk_loop_claude.sh` writes `phase.json`. No
   other component may author it.
2. **Removed at exit.** `phase.json` is deleted on every exit path (normal,
   signal, error, pre-loop terminal). A `phase.json` left behind is a crash
   artifact, same as a `state: "running"` sentinel.
3. **Additive.** A runner without `write_phase` produces no `phase.json`.
   Readers must tolerate its absence — the panel's current behaviour is the
   fallback.
4. **`between` nulls slug and step.** The `between` phase always passes
   empty slug and step, which become `null` in the JSON. A `between` phase
   with non-null slug/step is a bug.

### Bug reference (gh-resolve run 20260929-083918)

Chad's screenshot at ~09:08 showed the panel as `* gh-resolve iter 1 · 28m ·
♥784s` with no batch, sub-plan, or step. The runner was in fact healthily
running the batch-verify gate (pid 50479). Three defects:

1. `iter 1 · 28m` was the run's age (from `iter-NN.log` creation time).
2. `♥784s` climbed during a healthy gate (heartbeat = `iter-NN.log` mtime;
   nothing writes that file during a gate).
3. No batch, sub-plan, or step (the worker set the MASTER `shipped` before
   the gate ran; `status_all` found no active master).

`phase.json` closes all three: the panel reads the phase instead of inferring
it from iteration metadata.

---

## Contract 19: Verify-step integrity (`verify_step_integrity.py`)

### Purpose

A verify step exists to resolve attributed regressions — it runs the suite,
finds failures, and fixes them.  Nothing in ilk stops a verify worker editing
tests on its way to green.  I2 sub-plan 0 protects only DECLARED gates.

This contract adds three checks at the verify step's gate
(``verify_attribution.py``, before it can pass):

1. **Refuse** if the verify diff modifies any file named as a pin file by
   ANY sub-plan's ``pins_only``-style gate, or any ``test_*`` file another
   batch's sub-plan created.
2. **Refuse** if the verify diff adds a ``skip``, ``skipif``, ``xfail``, or
   ``importorskip`` to an existing test, or deletes an existing test function.
3. **Declare and surface** any other edit to an existing test file: the commit
   body must carry ``[test-change: <node> — <why>]``.  A missing declaration
   ⇒ refuse.  Declared changes pass.

### Who writes

- **`verify_step_integrity.py`** — `check_verify_commit(project, verify_slug, batch_base, pin_files)` returns a list of `Violation` objects.  Called from `verify_attribution.py` or a gate check.

### Who reads

- **`verify_attribution.py`** — at the verify step's gate, before passing.
- **The runner** — may call it as part of the local_checks block for a batch-verification sub-plan.

### Format

```python
@dataclass
class Violation:
    file: str
    reason: str
    line: int | None = None
```

### Invariants

1. **Pin files are immutable to the verify step.** A file named as a pin file by any sub-plan's gate must not be modified by the verify step, regardless of which batch owns it.
2. **Skip/xfail additions are refused.** A verify step must not weaken tests to reach green.  This includes `@pytest.mark.skip`, `@pytest.mark.skipif`, `@pytest.mark.xfail`, `pytest.importorskip`, and their unittest equivalents.
3. **Test function deletions are refused.** A verify step must not remove tests to reach green.
4. **Undeclared test changes are refused.** Any other edit to an existing test file must be declared in the commit body via `[test-change: <node> — <why>]`.  A missing declaration is a refusal; a declared change passes and is listed in the verification record.
5. **Code-only changes are clean.** A verify commit touching only non-test code produces no violations.

### Bug reference (gh-resolve G1, 2026-10-02)

gh-resolve G1's batch-verification step 1, commit `47e2cf21` ("fix(verify): resolve attributed regressions"), changed 3 test files to get green:

1. **Reshaped (not loosened):** `tests/test_reconcile_truthful_verdict.py::TestEscalatedWithErrors::test_escalated_errors_surfaced`.  The fixture row at :140-143 gained `"pr": 9999`, which steers it around the batch's new skip.  No assertion changed and no skip or xfail was added, yet the original case is now untested.
2. **Another batch's pin file:** `tests/test_31d_a_run_records_its_clone.py:96` (pinned by gh-resolve 10-01d).  Its pin guard now fails.
3. **A contract sync:** `== 5` → `== 6` after the batch added a terminal state.  That one is legitimate.

Nothing in ilk stopped the verify worker from editing tests on its way to green.

---

## Contract: the suite ledger (`suite_ledger.py`)

The suite ledger measures full-suite results keyed by tree sha so the verify
can look up HEAD and base verdicts instead of re-measuring.  The driver is the
sole writer; worker sessions are refused.  No ledger process command line
contains `run_ilk_loop` — the spawned measurement uses `suite_ledger.py
measure`, not the runner.

### Writers

- **The driver only.** `suite_ledger.py` refuses to write in a worker session
  (`ILK_WORKER_SESSION=1` raises `LedgerRefused`).
- `run_ilk_loop_claude.sh` calls `suite_ledger.py spawn` at agent return and
  `suite_ledger.py point` after ship-integrity and in the gate-first path.
- `verification_record.py` calls `suite_ledger.py measure` (sub-plan 2).

### Files

| File | Writer | Reader | Key |
|---|---|---|---|
| `ledger/<tree>.json` | `suite_ledger.py measure` | `suite_ledger.py lookup`, `verification_record.py` | tree sha |
| `running.json` | `suite_ledger.py spawn` | `suite_ledger.py wait_for` | pid |
| `queued.json` | `suite_ledger.py spawn` | `suite_ledger.py wait_for` | tree sha |
| `points.jsonl` | `suite_ledger.py point` (driver) | `red_owner.py` (sub-plan 3) | run_id + iteration |
| `returned/<slug>.json` | `suite_ledger.py return-reds` (driver) | `suite_ledger.py return-reds` (dedup check) | slug |

**`points.jsonl` row fields.** The main-loop point row's `shipped` lists the
sub-plans whose frontmatter turned `shipped` during the iteration (resolved
from the plans dir by `plan:` slug via `point_row_shipped_slugs`).  `master`
is the active MASTER's basename (resolved by `get_plans_dir` +
`pick_active_master`).

### Key

The ledger lives under the external logs dir for the git-common-dir repo's
project key.  A selfmod worktree and its clone share one ledger.

### Worker-session refusal

Every write command (`measure`, `spawn`, `point`) checks
`ILK_WORKER_SESSION=1` and raises `LedgerRefused` before touching disk.

### Forbidden pattern

No ledger process command line contains `run_ilk_loop`.  The spawned
measurement uses `suite_ledger.py measure`, not the runner.

### `return-reds` — reopen the in-batch owner

`suite_ledger.py return-reds --project P --batch B --plans-dir D` reopens the
sub-plan that owns each attributed red id so the loop re-dispatches the owner
instead of a cold verify worker.

**Exit codes:**

| Code | Meaning | State change |
|---|---|---|
| 0 | Returned successfully. Per owner slug: sub-plan reopened to `status: in-progress` at last step, local_checks appended, `#### Returned red` block added under `## Findings`. | Yes |
| 1 | Refused — worker session (`ILK_WORKER_SESSION=1`). | None |
| 3 | No owner for some attributed id (owner `—`, or owner sub-plan file missing in `plans_dir`). | None |
| 4 | Id already returned to the same slug (`returned/<slug>.json` lists it). | None |

**Writer:** `suite_ledger.py return_reds` (the driver, via
`run_ilk_loop_claude.sh`'s `ledger_return_reds` function).

**Reader:** the reopened sub-plan's next worker, which sees the
`#### Returned red` block and the added local_checks gate item.

**Integration:** `attempt_gate_first_fast_path`'s red branch calls
`ledger_return_reds` when `sub_plan_has_batch_verification` is true.  On
exit 0, `GATE_FIRST_NO_DISPATCH=1` suppresses the verify worker dispatch.
Any other exit leaves today's behaviour (verify worker dispatched).

**`returned/<slug>.json`:** a JSON array of node ids already returned to
*slug*.  Prevents the same id from being returned twice (exit 4).  Lives
under the ledger directory.

---

## Contract 20: The daily digest (`digest/`)

### Purpose

The scheduler writes yesterday's digest page once per day, on its first
cycle after local midnight.  The page is rendered by `ilk_digest.py
scheduled --day D` behind an atomic marker so it is never retried.

### Files

| File | Writer | Reader |
|---|---|---|
| `digest/D.md` | `ilk_digest.py write` (called by `scheduled`) | Chad; `ilk_digest.py today` |
| `digest/.scheduled-D` | `ilk_digest.py scheduled` (`O_CREAT\|O_EXCL`) | `scheduler.sh` `maybe_write_digest` |
| `digest/scheduled-D.log` | `nohup` redirect from `scheduler.sh` | Human diagnosis |

### The marker

Created with `os.open(..., O_CREAT|O_EXCL|O_WRONLY)` before the render
starts.  Its content is written last:

```json
{"day":"2026-10-03","rendered_at":"...","escalations":1,"notified":true,"error":null}
```

A render exception is caught and recorded in `error`; the marker stays
so the scheduler does not retry.  `error` is `null` on success.

### The hook (`scheduler.sh` `maybe_write_digest`)

Called as `maybe_write_digest || true` at the top of every cycle, before
the scan, so idle cycles also write.  In `--dry-run` it only logs
`digest-due` and spawns nothing.  `ILK_DIGEST=0` disables the hook
entirely.

Yesterday is computed with `$PYTHON` (`datetime.date`), not `date -v`
/ `date -d`.

### Notification

`ilk_notify.py --event digest-ready` is called only when escalations >= 1
(an `escalated` row or an unreadable audit file).  An unknown event is
titled `ilk — <event>` (`ilk_notify.py:41`), so `ilk_notify.py` is not
edited.

### Once-per-day rule

The `O_CREAT|O_EXCL` marker is the sole guard.  A render that fails after
creating the marker records the error and is NOT retried.  Manual
re-render: `ilk_digest.py render --day D`.

### Invariants

1. **One render per day.** The marker is atomic; a second call returns
   `already: true`.
2. **No retry on failure.** The marker stays; the error is recorded in it.
3. **Dry-run writes nothing.** No marker, no page, no digest dir.
4. **Idle cycles write.** The hook runs before the scan.
5. **`ILK_DIGEST=0` disables.** The hook returns 0 immediately.

---

## Contract 21: Safety-case verdict record (`runtime/safety-case/<tree>.json`)

### Purpose

The RSI safety case runs three components (invariants, golden batch, teeth)
and records its verdict keyed by tree SHA. The release train reads this
record as a precondition for cutting a release.

### Format

```json
{
  "tree": "<40-char tree sha>",
  "head": "<40-char commit sha>",
  "verdict": "pass" | "fail",
  "components": [
    {
      "name": "invariants" | "golden" | "teeth",
      "ok": true | false,
      "seconds": 42.1,
      "budget_seconds": 120,
      "exit": 0,
      "tail": "last 500 chars of stdout+stderr"
    }
  ],
  "catalog_sha256": "<sha256 of mutations.json>",
  "kernel_sha256": "<sha256 of safety-kernel.json>",
  "writer": "driver",
  "ts": "2026-10-04T12:00:00+0800"
}
```

### Key

Tree SHA (`HEAD^{tree}`) — stable across commits with identical tree content.

### Writers

- **`safety_case.py run`** — the only writer. Refuses in a worker session
  (`ILK_WORKER_SESSION=1`). Atomic write (tempfile + `os.replace`).

### Readers

- **`release_train.py prove`** — after Phase 1 passes, runs
  `safety_case.run` with `no_record=True` (the proof file carries the
  result; no standalone record is written during prove). A `fail` verdict
  is a prove refusal with reason `safety-case: <component> <tail>`.

### New prove refusal reasons

| Reason prefix | Meaning |
|---|---|
| `kernel-range: <reason> <sha12> <path>` | A kernel-range violation was found in `<last_tag>..HEAD`. Sub-reasons: `kernel-edit-by-unattended-build`, `rules-edit-by-loop-build`, `kernel-edit-unresolved-master`. |
| `safety-case: <component> <tail>` | A safety-case component failed (exit ≠ 0 or over budget). Component names: `invariants`, `golden`, `teeth`. |

### Invariants

1. **Driver-only write.** `safety_case.py` refuses to run when
   `ILK_WORKER_SESSION=1`.
2. **Atomic record.** The record is written via tempfile + `os.replace`.
3. **No record during prove.** `prove` passes `no_record=True` so a gate
   never writes a real record; the proof file carries the safety-case
   result inline.
4. **Budget enforcement.** Each component has a hard timeout and a budget
   (invariants: 120s, golden: `budget.json max_seconds`, teeth: 900s).
   A component that exceeds either is `ok: false`.
5. **Missing budget.json is a failure.** A missing or corrupt
   `budget.json` for the golden batch returns `ok: false` with exit 2,
   never skipped.

## Startup refusal: a release host never runs from the clone

### Purpose

On a host whose layout file reads `release`, running ilk from a git working
tree (the live clone or a selfmod worktree) would bypass the release's pinned
code.  Three entry points (`launch.sh`, `watchdog.sh`, `scheduler.sh`) plus
`ilk-run.sh` now refuse at startup, before any state-changing work.

### Layout file rule

The layout file is `$(dirname ${ILK_RELEASES_ROOT:-$HOME/.ilk/releases})/layout`.
The host is on the release layout when that file exists and its content
(whitespace-trimmed) is exactly `release`.  Same rule as `install.sh:910-913`.

### Entry points

| Script | Guard location | Sourced-use skip |
|---|---|---|
| `launch.sh` | Inside `ILK_SKIP_MAIN` block, before `main "$@"` | `ILK_SKIP_MAIN=1` |
| `watchdog.sh` | Inside `BASH_SOURCE == $0` block, before `main "$@"` | sourced (not `$0`) |
| `scheduler.sh` | After `ILK_SANDBOX` refusal, before `_ilk_pid.sh` | `ILK_DOTSOURCE_ONLY=1` |
| `ilk-run.sh` | After skill-root resolution, before promotion | none (always runs) |

### Clone detection

`ilk_dir_is_git_tree <dir>` walks up from `<dir>` checking each ancestor for
`.ilk-release.json` and `.git` (file or dir).  The shallowest marker wins:
`.ilk-release.json` first → not a git tree; `.git` first → is a git tree.
Same rule as `_ilk_toolkit_head.sh:47-66`.  A selfmod worktree has a `.git`
FILE, so it counts as a git tree.

### Exit code and token

A refusal exits **3** (free in all three scripts) and prints to STDERR:

```
<component>: refusing: clone-run-on-release-host: this host's ilk layout is release (<layout file>) but <dir> is inside a git working tree; run it through ~/.ilk/current, or set ILK_ALLOW_CLONE_RUN=1
```

The literal token `clone-run-on-release-host` is the named reason tests match.

### Override

`ILK_ALLOW_CLONE_RUN=1` skips the guard entirely (returns 0).

### Invariants

1. **Execution-only.** The guard runs at each script's EXECUTION entry, never
   at source time.  Sourced use (`ILK_SKIP_MAIN=1`, `ILK_DOTSOURCE_ONLY=1`,
   watchdog sourced) never runs the guard.
2. **No state change on refusal.** `scheduler.sh`'s guard runs BEFORE
   `_ilk_pid.sh` / the lock, so a refusal creates no pid or lock file.
   `ilk-run.sh`'s guard runs before promotion.
3. **Stderr only.** The guard prints to stderr, never stdout.  Captured-function
   logging rules apply.

---

## The `train_only` scan-row field (`scheduler_scan.py`)

### Purpose

When a project has **no runnable master** (every master is shipped or blocked)
but its sentinel passes the all-shipped rule (`sentinel_all_shipped` returns
True) and no release marker exists, `_scan_one_project` returns a row with
`"train_only": true`. This lets the scheduler start the release train for
projects whose queue is drained — the common case for the last batch of a
project.

Without this field, `_scan_one_project` returned `None` for masterless
projects, and the train hook inside the dispatch loop never reached them.
The last batch of a queue (the usual ilk-skills case) never got its train.

### Format

The row carries every field `scheduler.sh` reads, plus `train_only`:

```json
{
  "key": "<project-key>",
  "path": "<absolute project data dir>",
  "repo_path": "<absolute source repo path>",
  "oldest_queued_ts": "0001-01-01T00:00:00",
  "has_active_master": false,
  "active_master_name": null,
  "train_only": true
}
```

### Who writes

- **`scheduler_scan.py` `_scan_one_project`** — the only writer. Emitted
  when pass 1 finds no active or queued master, and the sentinel +
  marker check passes.

### Who reads

- **`scheduler.sh`** dispatch loop — treats a `train_only` row differently
  from a normal dispatch row: it offers the row to
  `maybe_start_release_train` and moves on without dispatching,
  promoting, or counting against capacity.

### Invariants

1. **A `train_only` row is never dispatched.** `scheduler.sh` does not
   pass it to `launch.sh`, does not promote a master, and does not count
   it against the dispatch capacity. It only offers it to the train hook.
2. **A `train_only` row is never promoted.** The row's
   `has_active_master` is always `false`; promotion requires an active
   or queued master.
3. **The marker prevents re-start.** Once `maybe_start_release_train`
   writes `<run_id>.started`, subsequent scans return `None` (the
   marker check in `_scan_one_project`).
4. **A non-all-shipped sentinel yields no row.** The sentinel must pass
   `sentinel_all_shipped`; a failure sentinel (`local_checks_failed`,
   `error`, etc.) produces no `train_only` row — the project is
   excluded as before.

## Contract 12: Improvement backlog cross-host sync (`backlog_sync.py`)

### Purpose

The improvement backlog (`~/.ilk-data/ilk-skills-improvements/candidates.json`)
is per-host. Autoplan runs only on the RSI host (chad-mbp). This module pulls
remote hosts' rows over ssh, merges them tagged with `relations.origin_host`,
and writes the local file only when both sides parsed.

### Who writes

- **`backlog_sync.pull()`** — the only cross-host writer. It writes only from
  a strict read (`triage_backlog.read_backlog_strict`), tags
  `relations.origin_host`, and never writes to the remote.

### Who reads

- **`improvement_backlog.load()`** — reads the merged backlog.
- **`autoplan.py`** — reads candidates for planning.

### Invariants

1. **Never write from a lenient read.** `improvement_backlog._load_raw` returns
   `[]` on a decode error. `backlog_sync` uses `read_backlog_strict` which
   raises on a corrupt file, so a merge never wipes rows.
2. **Never write to the remote.** The only remote command is
   `hostname; cat ~/.ilk-data/ilk-skills-improvements/candidates.json`.
3. **Tag origin_host.** Every row pulled from a remote host gets
   `relations.origin_host` set to that host's name. Never as a top-level key.
4. **Never change status.** The local host owns `status` (planned/shipped).
   Remote rows refresh `seen_count`, `last_seen`, `title`, `gap`, `evidence`
   but never `status`.
5. **Throttle.** At most one pull per host per 30 minutes (configurable).
