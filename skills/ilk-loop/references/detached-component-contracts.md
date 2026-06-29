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

### Dedup key

`(run_id, iteration)` — a given run should have at most one summary line
per iteration. Readers should dedup if re-processing.

### Who writes

- **`run_ilk_loop_claude.ps1`** — appends to `$JsonlLog` (~line 799).
- **`run_ilk_loop_claude.sh`** — appends via `>>` (shell redirect).

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

### Bug reference (20260616-175453)

`run_ilk_loop_claude.ps1` used `Add-Content -Encoding utf8` which wrote a
BOM. `collect.py` read with `utf-8` (not `utf-8-sig`), `json.loads` choked
on the BOM, the record was swallowed by a bare `except json.JSONDecodeError`,
and the watchdog printed "POSTMORTEM FAILED" and blocked. Fixed in sub-plan
#1 (`collect-bom-tolerant-reads`).

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

## Contract 4: Human-verify marker (compile-only / device-manual)

### Purpose

A shipped `compile-only` or `device-manual` sub-plan has not been runtime-verified
in-loop. Before later work can build on it, a human must confirm it actually works.
This contract defines the on-disk marker that records that confirmation, so
`promote_next_master.py` can block promotion of dependent masters until verification
occurs.

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
| `true` / `yes` / `1` | Human has verified — promotion may proceed |
| *(absent)* | **Not verified** — back-compat default; treat as unverified |
| `false` / `no` / `0` | Explicitly unverified (same as absent, but intentional) |
| Any other string | Treat as unverified (degrade-safe — see below) |

### Who writes

- **A human** editing the sub-plan file directly (after a device or manual pass).
- **`/ilk-feedback`** — when the feedback flow records a human verification result.
- Never written by the autonomous loop, the runner, or the scheduler.

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

### Bug reference (field case: compile-only carry-forward)

Two sub-plans shipped `compile-only` and the loop advanced
to later masters that built on them — no marker, no block, no human pass. The
unverified work compounded: later sub-plans silently accommodated bugs in the
earlier layers. This contract closes that gap mechanically.

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
  (BOM reads, git stderr, branch policy, hung-alive detection).
- `skills/ilk-loop/SKILL.md` — the loop convention itself.
- Sub-plan #1 (`collect-bom-tolerant-reads`) — the BOM fix.
- Sub-plan #2 (`runner-trust-allpassed`) — the all_passed fix.
- Sub-plan #3 (`status-terminal-sentinel-alive`) — the liveness fix.
