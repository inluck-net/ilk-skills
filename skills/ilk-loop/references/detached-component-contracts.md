# Detached-component runtime contracts (2026-06-16)

Canonical reference for the on-disk file contracts between ilk's detached
components. Written after three contract-violation bugs surfaced in run
`20260616-175453` (math-blocks). Each bug was a writer/reader disagreement
about the same file — this doc makes the implicit contracts explicit.

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
  "iteration": 3,
  "exit_code": null,
  "generated_at": "2026-06-16T17:55:00+08:00"
}
```

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
- **Watchdog** — reads sentinel for stale/ hung detection.

### State vocabulary

| State | Meaning | Alive? |
|---|---|---|
| `"running"` | Loop is actively iterating | **Live** — check PID |
| `"shipped"` | All sub-plans shipped, clean exit | Terminal |
| `"local_checks_failed"` | A step's local_checks failed | Terminal |
| `"interrupted"` | User or watchdog killed the run | Terminal |
| `"error"` | Unexpected runner error | Terminal |
| `"max-iterations"` | Hit iteration budget | Terminal |
| `"budget-exhausted"` | Hit `--max-budget-usd` cap | Terminal |
| `"startup-hang"` | Pre-iteration-1 hang detected | Terminal |
| `"timeout"` | `gtimeout` killed the iteration before it completed | Terminal |
| `"ship_integrity_violation"` | A sub-plan was `shipped` with its declared gate red; the driver reverted it to `in-progress` | Terminal |
| `"no-progress"` | 3 consecutive iterations with zero new commits | Terminal |
| `"all-shipped"` | Every registered sub-plan is shipped **and every one is proven**; loop ended naturally | Terminal |
| `"shipped-unproven"` | Every registered sub-plan is shipped, but the ship-proof ledger holds no row for at least one — the ship claim is unverified | Terminal |
| `"blocked-no-runnable"` | All remaining sub-plans are `blocked`; nothing to dispatch | Terminal |
| `"already-shipped"` | Nothing to do at launch time (all sub-plans already shipped) | Terminal |

**Naming conventions are intentional.** The hyphenated states (`no-progress`,
`all-shipped`, `timeout`, `budget-exhausted`, `blocked-no-runnable`,
`already-shipped`) and the underscored states (`local_checks_failed`,
`ship_integrity_violation`) come from two writers in two languages (bash and
PowerShell). A consumer already reads the underscore forms; do not normalise
them to one convention.

**Sentinel state → postmortem label.** `collect.py`'s `_SENTINEL_FAILURE_MAP`
is the only place this mapping lives; a terminal state missing from it falls
through to the generic heuristics, which is how a failed run gets classified
`clean-success`.

| Sentinel `state` | `collect.py` label | `watchdog.sh` action |
|---|---|---|
| `"budget_exhausted"` | `budget-exhausted` | `block` |
| `"max-iterations"` | `max-iter-bound` | `relaunch` |
| `"interrupted"` | `interrupted` | `relaunch` |
| `"local_checks_failed"` | `local-checks-broken` (<3 iters) / `local-checks-stuck` | `block` |
| `"ship_integrity_violation"` | `shipped-unverified` | `needs-human` |
| `"shipped-unproven"` | `shipped-unverified` | `needs-human` |
| `"timeout"` | *(none — falls through)* | `triage` |

`ship_integrity_violation` is written by `run_ilk_loop_claude.sh` and
`run_ilk_loop_claude.ps1` only. It was in **0** classifier files until
2026-08-29 — see the bug reference under Contract 2b.

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
{"slug":"issue-sync-schema-widen","step":2,"outcome":"fail","exit_code":1,"command":"bunx vitest run"}
```

`outcome` is one of `pass` / `fail` / `error` / `inconclusive`. `fail` and
`error` are **blocking**. `command` is present for every outcome, so a passing
gate is distinguishable from a gate that never ran.

### Who writes

- **`emit_jsonl_record.py`** — `build_record` + the append in `main`. The one
  writer. It replaced a hand-interpolated `echo` in the runner.

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

### Gate-result isolation fields (added 2026-08-30)

`run_local_checks.py` emits four fields in its output JSON that describe
the isolation state of the working tree when the gate ran:

| Field | Type | Meaning |
|---|---|---|
| `head_sha` | `string \| null` | HEAD commit SHA at gate time; `null` if not a git repo |
| `dirty_paths` | `int` | Count of uncommitted + untracked paths before isolation |
| `isolated` | `bool` | `true` iff the tree was successfully pinned to HEAD |
| `restore_error` | `string \| null` | Error from stash pop; `null` on success |

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
  dispatch is idempotent (marker file) and skips `supervised_only` masters and
  blacklisted projects. A human pass remains valid and is the fallback when
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
| `step_to` | int | The step the iteration **reached** (the sub-plan's `current_step` after the agent ran) |
| `commits` | list[str] | SHAs in the iteration's `before..after` range |

The step range is **half-open**: `[step_from, step_to)`.  A record with
`step_from=0, step_to=3` proves steps 0, 1, and 2.

### Who writes

- **`run_ilk_loop_claude.sh`** — `write_ship_proof_records`, called after
  the post-iteration head capture and new-commit count.  Only writes when
  `total_new > 0` (an unproductive iteration claims no steps).

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

**Not provided, by design:** per-commit attribution between slugs. When an
iteration advances two sub-plans, both rows carry the full `before..after`
range. Without trailers there is nothing to partition on, and no reader
consumes `commits` for attribution. A sub-plan created *during* an iteration
has no baseline and gets no row; batch-verification sub-plans exist at plan
time, so the case that matters is covered.

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
  matching is unchanged; the ledger only ever *adds* attribution.
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

## See also

- `docs/loop-runtime-hardening.md` — broader runtime hardening notes
- `docs/ship-gate-design.md` — the ship gate: why `shipped` never meant
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
