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
| `"budget_exhausted"` | Hit token budget | Terminal |
| `"startup-hang"` | Pre-iteration-1 hang detected | Terminal |
| `"timeout"` | `gtimeout` killed the iteration before it completed | Terminal |
| `"ship_integrity_violation"` | A sub-plan was `shipped` with its declared gate red; the driver reverted it to `in-progress` | Terminal |

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
| `"timeout"` | *(none — falls through)* | `triage` |

`ship_integrity_violation` is written by `run_ilk_loop_claude.sh` and
`run_ilk_loop_claude.ps1` only. It was in **0** classifier files until
2026-08-29 — see the bug reference under Contract 2b.

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

- **`blocking_checks.py`** — `--any` / `--targets` / `--slugs` / `--describe`.
  The one reader for the runner's B2 path (blocking test, confirm re-run,
  auto-quarantine, the human-readable failing-check line).
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
| `step_from` | int | The step the iteration **started** on (from `PRE_ITER_TARGET`) |
| `step_to` | int | The step the iteration **reached** (the sub-plan's `current_step` after the agent ran) |
| `commits` | list[str] | SHAs in the iteration's `before..after` range |

The step range is **half-open**: `[step_from, step_to)`.  A record with
`step_from=0, step_to=3` proves steps 0, 1, and 2.

### Who writes

- **`run_ilk_loop_claude.sh`** — `write_ship_proof_records`, called after
  the post-iteration head capture and new-commit count.  Only writes when
  `total_new > 0` (an unproductive iteration claims no steps).

### Who reads

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

### General

- [ ] **Read with `utf-8-sig` for any file the PowerShell side may write.**
      This includes sentinels, JSONL, launch metadata, and registry files.
- [ ] **Test the BOM case explicitly.** Don't assume "works on my machine"
      (macOS/Linux never write BOMs; Windows PS 5.1 always does).
- [ ] **Run the existing regression tests** after your change:
      - `python -m pytest skills/ilk-feedback/tests/test_collect_bom_tolerant.py -q`
      - `python -m pytest skills/ilk-loop/tests/test_status_terminal_sentinel.py -q`
      - `powershell -File skills/ilk-loop/tests/test_runner_outcome_allpassed.ps1`

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
