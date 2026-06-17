# Orchestration collaboration — how the ilk components drain queues (2026-06-17)

Companion to [detached-component-contracts.md](./detached-component-contracts.md).
That doc specifies the **on-disk file contracts** (sentinel format, state
vocabulary, JSONL, liveness). This doc specifies the **goal** the components
collaborate to achieve, each component's role in service of it, and the two
**translation layers** that must be total and lossless — the layers where the
2026-06-16 orchestration stall lived.

## The goal

> The scheduler + per-project watchdog should be able to take **every**
> queued/running ilk-loop through **all** of its masters and sub-plans —
> autonomously and safely — across **all** registered projects, without a
> human in the steady state.

"Autonomously and safely" means: a clean run advances the queue on its own; a
recoverable stop auto-restarts; a genuine failure **parks the project with a
clear, correct reason** and moves on to other projects — it never silently
ships broken work, and never hard-stalls the whole system on a status nobody
mapped.

The toolkit's job is **orchestration**, not making a project's own gates pass.
If a project's sub-plan has a genuinely failing `local_checks` (its own plan
content), the correct outcome is *park-with-reason*, not *pretend success* and
not *mysterious block*.

## Component roles (in service of the goal)

| Component | Role | Files |
|---|---|---|
| **scheduler** | Cross-project pump. Scans all projects, dispatches runnable masters into slots, promotes the next queued master when one ships, applies blacklist/backoff/`supervised_only` gates. Drains the *fleet*. | `scheduler.ps1` / `.sh` |
| **launcher** | Spawns one detached run per project + attaches its watchdog. | `launch.ps1`, `run_ilk_loop_claude.*` |
| **runner (loop)** | Executes steps; sole writer of JSONL + sentinel; enforces `local_checks` (B2). Drains one project's *sub-plans*. | `run_ilk_loop_claude.*` |
| **feedback (classifier)** | Reads sentinel + JSONL after a run stops, emits one **classification label** + a postmortem. The *judgement* layer. | `collect.py` |
| **watchdog** | Per-run supervisor. On stop, runs the classifier and maps the label to an **action**: relaunch / block / graceful-stop / needs-human. The *act-on-judgement* layer. | `watchdog.ps1` / `.sh` |
| **tray / status_all** | Read-only mirror of fleet state for the human. Never decides anything. | `status_all.py`, `tools/tray/*` |

## The drain lifecycle (one project, one master)

```
scheduler dispatch ─▶ runner runs steps ─▶ runner finalizes sentinel (terminal state)
                                                      │
                                       watchdog observes terminal sentinel
                                                      │
                                       collect.py: sentinel + JSONL ─▶ LABEL
                                                      │
                                       watchdog: LABEL ─▶ ACTION
        ┌──────────────────────┬──────────────────────┬─────────────────────┐
   whitelist                blacklist              success                needs-human
   (relaunch, capped)       (BLOCK + park)         (stop cleanly)         (stop, notify)
        │                        │                      │                      │
        └─ same master ──────────┴─ scheduler next cycle: promote next master / next project
```

The fleet keeps draining as long as every stop resolves to exactly one action
and the project either advances (success → promote next) or parks cleanly
(blacklist) so the scheduler can spend its slots on other projects.

## Translation layer 1 — sentinel state → classification label (collect.py)

The runner's **terminal sentinel `state` is authoritative** about *what
happened*. The agent's narrative in the log ("all sub-plans shipped!") is
*not* — an agent can claim success in the same iteration the B2 gate fails it.

**Invariant L1 — honor the sentinel.** When the sentinel's terminal `state` is
a failure state, the classification MUST reflect that failure, regardless of
the agent narrative or how many iterations failed:

| Sentinel terminal `state` | Required label |
|---|---|
| `local_checks_failed` | `local-checks-stuck` |
| `budget_exhausted` | `budget-exhausted` |
| `max-iterations` | `max-iter-bound` |
| `interrupted` | `interrupted` |
| `startup-hang` | `no-evidence` (pre-iter-1) |
| `shipped` | `clean-success` (or `shipped-unverified` if a shipped sub-plan is a non-`loop-verified` tier) |

A failure sentinel must never be laundered into `clean-success`.

**Bug (2026-06-16, run 20260616-231713).** The sentinel said
`state=local_checks_failed (iters=3)`, but `classify()`'s `local-checks-stuck`
branch only fired at `fail_iters >= 3`; only the last of 3 iters failed, so it
fell through to the `clean-success` default and trusted the agent's "all
shipped" text. A single-iter gate failure was reported as success — violating
L1. Fix: the sentinel failure state overrides the iter-count heuristic.

## Translation layer 2 — classification label → watchdog action (watchdog.ps1)

**Invariant L2 — the mapping is TOTAL.** Every label `collect.py` can emit has
exactly one explicit watchdog action. No label may fall through to
"BLOCKED — UNKNOWN STATUS …". A new label in `collect.py` without a watchdog
branch is a bug, and a coverage test must fail when one exists.

| Label | Action |
|---|---|
| `timeout-bound`, `max-iter-bound`, `api-flaky`, `interrupted` | **relaunch** (whitelist, capped by `MaxRestarts`) |
| `stuck-no-progress`, `api-blocked`, `budget-exhausted`, `local-checks-stuck`, `dependency-unreachable` | **BLOCK + park** (blacklist; needs human / `/ilk-resume`) |
| `clean-success` | **stop cleanly** — job done, no relaunch, no red banner; scheduler promotes the next master next cycle |
| `shipped-unverified` | **stop + notify** needs-verification (no relaunch) |
| `self-hosting-drift` | **stop + notify** (toolkit self-edit drift; human review) |
| `no-evidence` | **stop + triage** (run left no usable records) |

**Bug (2026-06-16).** `clean-success` (and `self-hosting-drift`) were in
neither whitelist, blacklist, nor the special-cases, so they hit
`BLOCKED — UNKNOWN STATUS 'clean-success'` and fail-safe exited — turning a
"job done" into a hard stall. Fix: complete the mapping and assert totality
with a coverage test enumerating `collect.py`'s label constants.

> Note: a `clean-success` reaching the watchdog is legitimate (e.g. an
> `already-shipped` stop). It is distinct from the **success sentinel states**
> (`all-shipped`/`already-shipped`/`shipped`) the watchdog short-circuits
> *before* classifying — both must resolve to "stop cleanly".

## Translation layer 3 — fleet state → human (tray / status_all)

The tray is a **read-only mirror**; it must never disagree with itself. Its
two surfaces are derived from the same `status_all --json`:

**Invariant L3 — tooltip and panel agree.** The tooltip's running/attention
count MUST equal the number of corresponding rows in the right-click panel. A
tooltip that says "1 running" with an empty panel is a contract violation
(observed 2026-06-16). The pure data contract lives in `render_tray.py`
(loop-testable); the GUI paint in `ilk-tray.ps1` is device-manual.

## Translation layer 4 — drain PAST blocked work (queue advancement)

The fleet must not let one blocked/un-runnable unit wedge everything behind it.
This is the capability that was missing when math-blocks stalled: 10 masters /
24 sub-plans, but the active master (`curriculum-rescope`) had a stuck
`redeploy-vps13` sub-plan, so the other 9 masters (21 sub-plans) never ran.

**Definitions.**
- A sub-plan is **runnable** iff `status ∈ {pending, in-progress}` AND every
  `depends_on` sub-plan is `shipped`. A `blocked` sub-plan, and any sub-plan
  whose dependency is `blocked`/non-shipped, is **not** runnable.
- A master is **drainable** iff it has ≥1 runnable sub-plan. A master with
  non-shipped sub-plans but **zero runnable** ones is **stalled** (its only
  remaining work is blocked or blocked-dependent).

**Invariant L4 — bypass, don't wedge.**
1. **Within a master:** `loop_status` picks the next *runnable* sub-plan,
   skipping `blocked` ones and ones whose `depends_on` is unmet. (It already
   prefers actionable over blocked; it must also honor `depends_on`.)
2. **Across masters:** when the active master is **stalled** (non-shipped but no
   runnable sub-plan), it must **yield its active slot** so the next queued
   master is promoted and run — even though it is not all-shipped. The stalled
   master is NOT marked `shipped` (that would lie); it is parked
   (`status: blocked` at the master level, or left active-but-skipped) and
   surfaced for human resolution, while the rest of the queue drains.
3. **Across projects:** a project whose active master is stalled but which has
   other queued masters with runnable work stays **dispatchable** — it is not
   blacklisted/parked at the project level just because one master is stuck.
4. **No silent ship.** Bypassing blocked work must never flip the blocked
   sub-plan or its master to `shipped`. The blocked unit stays visible
   (postmortem / status) so a human can `/ilk-resume` it later; only the
   *runnable* remainder advances.

**Auto-quarantine (optional policy).** A sub-plan that repeatedly fails its
`local_checks` (e.g. an unreachable deploy target) can be auto-marked `blocked`
after N consecutive failures so L4 bypass kicks in without a human — instead of
the loop re-failing the same gate every iteration. This turns a hard stall into
"park the bad sub-plan, drain the rest." Threshold and opt-out are policy knobs.

**Verification.** L4 is verified end-to-end by a **mock-master drain harness**:
a generator builds a synthetic `ILK_DATA_HOME` with several masters where some
sub-plans are deliberately `blocked`, then drives `loop_status` /
`promote_next_master` / the scheduler scan and asserts every *non-blocked*
sub-plan/master drains while the blocked ones are bypassed (and never falsely
shipped).

## Why this matters for "drain all queues"

The fleet only drains if **every** stop resolves cleanly:
- L1 broken → a failure looks like success → the watchdog tries to "finish"
  and the queue's real state is wrong.
- L2 broken → a valid outcome hits UNKNOWN STATUS → the watchdog hard-stalls
  instead of parking, and the scheduler can't reason about the project.
- L3 broken → the human can't see what's actually running.

All three are **totality / authority** bugs: honor the authoritative signal
(L1), make the mapping total (L2), keep the mirror self-consistent (L3).

## See also

- [detached-component-contracts.md](./detached-component-contracts.md) — the on-disk file formats these layers read/write.
- [decomposition-principles.md](./decomposition-principles.md) §11 (shipped ≠ verified), §13 (bias toward autonomy), §15 (autonomy tiers).
