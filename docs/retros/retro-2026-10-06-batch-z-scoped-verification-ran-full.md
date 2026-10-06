# Retro 2026-10-06 — Batch Z's scoped verification ran the full suite

Batch Z (`MASTER-2026-10-06z-remote-bounce-argv-execution-plan.md`), runs
`20261006-082402` and `20261006-084410`, on chad-mbp. Evidence comes from the
external plan and launcher state under `~/.ilk-data`, the Batch Z verification
record and ledger, the driver gate history, and ilk-skills commits
`e8b8968b..24cefe0f`.

**Headline.** Batch Z was supposed to run a cheap, change-scoped batch verify.
The record said `suite_scope: scoped`, `selection_size: 8`, but the command
actually ran the unfiltered 5,142-test suite. The gate took 951 seconds. This
was not a design choice to put a full suite in every RSI batch: D9 says
**scoped per batch, full per release**. It was a verifier bug in the
`ledger=require` cache-miss path, compounded by missing configuration that was
proposed with D9 but never added to this project's `.ilk-launch.json`.

The full run passed attribution, but the runner then reverted the implementation
sub-plan because its final-step proof was absent from the driver-owned gate
history. A planned gate-only recovery took 24 seconds, attribution re-derivation
took 13 seconds, and the authentic scheduler shipped both sub-plans without a
duplicate launch.

## What happened (chad-mbp, CST)

| time | event | evidence |
|---|---|---|
| 08:24:09 | The scheduler starts run `20261006-082402` on verification step 0. | loop JSONL |
| 08:39:54 | The canonical gate passes at `49cdc6c3` after 951 seconds. The record claims 8 selected files but reports 5,142 tests: 5,070 passed, 30 failed, 3 errors, 32 skipped. | gate history; Batch Z record |
| 08:41:02 | Ship integrity reverts `remote-bounce-argv` from shipped to in-progress at `site=final-gate`: there is no passing driver-owned gate row for its final implementation step. | `ship-reverts.jsonl` |
| 08:41:04 | The run exits `ship_integrity_violation`. | loop JSONL |
| 08:44:10 | After a planned amendment adding an explicit gate-only final step, the scheduler—not this operator session—starts run `20261006-084410`. | launcher state |
| 08:44:32 | The implementation's focused two-file gate passes in 24 seconds. | gate history |
| 08:45:44 | Attribution re-derivation passes in 13 seconds with 0 attributed failures; a signed Batch Z verdict is written. | gate history; `runtime/batch-gates/batch-2026-10-06z.json` |
| 08:46:47 | Both sub-plans are shipped and the scheduler stops `blocked-no-runnable` on the held master. | launcher sentinel and log |

No fixture runner was stopped. The long-lived processes observed under
`/private/var/.../pytest-of-chad/...` were test fixtures, not duplicate Batch Z
runs.

## What the design actually says

The repository does require a final sub-plan marked `batch_verification: true`.
That is the owner of batch-wide attribution; it does **not** mean that every
batch should run the full suite. `decomposition-principles.md` says that since
v0.9.100 this sub-plan is scoped to the batch diff plus importer tests, with a
full-suite fallback only when the scope cannot be computed or the batch touches
global test/build infrastructure.

D9 in `docs/architecture/unattended-unblocking-design.md` is more explicit:

1. per batch, run a cheap change-scoped verify;
2. per release, run one full suite with attribution before tag, push, or
   second-host deploy;
3. make the cadence project configuration, proposed as
   `ship.verify_cadence` and `ship.full_suite_gate`.

Batch Z's `.ilk-launch.json` has neither proposed field. Its verification
command did use `--scope auto`, and the oracle successfully selected eight
files, so this incident was not a legitimate full-suite fallback.

## Root causes

### R1 — the ledger-required cache-miss path drops the computed selection

`run_suite` accepts `selection` and documents that omitting it runs the full
suite (`verification_record.py:701-715`). The ordinary scoped path passes that
argument. The `ledger_mode == "require"` cache-miss fallback instead calls:

```python
results = run_suite(project, invocation, suite_budget)
```

at `verification_record.py:1941`. It therefore runs the bare configured suite
even though scope resolution already produced eight test files. The record is
then rendered from the earlier scope object, so it persists `suite_scope:
scoped` and `selection_size: 8` alongside the full-suite count.

This is a recurrence of the exact failure mode already described in
`run_suite`'s docstring: a scoped label without an applied selection.

### R2 — the ledger identity and record do not prove what command ran

The new ledger entry stores the base invocation, counts, and failures, but not
the applied selection or the final executed command. A full result can
therefore be cached under an identity that readers treat as authoritative for
a scoped record. There is no fail-closed consistency check between
`suite_scope`, `selection_size`, the executed argv, and the ledger entry.

The result is semantically misleading even though its attribution verdict is
valid: `scope=scoped` promises a cost boundary as well as a test-selection
claim.

### R3 — D9's cadence configuration remains prose

The design proposed `ship.verify_cadence: batch | release` and
`ship.full_suite_gate: batch | release | ci`. Batch Z's project configuration
contains only the suite command and baseline-red policy. The planner and
release train therefore cannot enforce the approved split from configuration;
they depend on `--scope auto` behaving correctly.

### R4 — implementation proof was exercised but not recorded at the final step

The worker ran focused tests while implementing the remote-bounce argv fix,
but ship integrity only accepts a driver-owned passing row for the sub-plan's
declared final step and tree. The original two-step plan shipped without that
row. When the verification sub-plan finished its first step, the global
integrity scan correctly found the stale proof and reverted the implementation
slug.

The safe recovery was a planned third, gate-only step. It was linted and
preflighted before release, then executed by the genuine scheduler. No gate row
was forged manually. This same recovery shape has appeared before; the planner
should produce it up front instead of relying on an after-the-fact amendment.

### R5 — feedback reports missing proof as a red gate

`ilk-feedback` classified run `20261006-082402` as `shipped-unverified`, which
is directionally correct, but explained that the sub-plan was shipped "while
its gate was red." No implementation gate was red. The failure was that a
qualifying final-step proof row did not exist. The gate-first iteration also
left no iteration transcript for feedback, so its postmortem displayed
`<no tail available>` despite useful driver evidence in gate history and
`ship-reverts.jsonl`.

That wording matters operationally: "fix a red test" and "record the required
proof" lead to different recoveries.

## What went right

- The scope oracle chose eight files. The selection failure is isolated to the
  ledger-required execution path, not the importer analysis.
- Attribution remained fail-closed. Fifteen failures passed at base and then
  failed 0/3 head reruns, so they were recorded as owed flakes; the other 15
  were declared-at-base. The signed verdict contains zero undeclared or
  attributed regressions.
- Ship integrity stopped a shipped implementation whose final proof was absent.
- The recovery stayed inside the planning and scheduler boundaries: planned
  amendment, clean lint/preflight, scheduler dispatch, driver-owned proof.
- Exact-process observation distinguished the one live project PGID from
  temporary pytest fixture processes, so no fixture or duplicate was disturbed.

## Corrective work

Plan the following as a gated ilk-skills batch before enabling autonomous RSI
landing:

1. Pass the computed `selection` into `run_suite` on every cache-miss path,
   including `ledger=require`.
2. Make ledger identity and evidence scope-aware: persist the selected files
   and exact executed command (or an equivalent canonical digest), and never
   reuse a full-suite result as proof that a scoped command ran.
3. Fail closed when a record claims `scoped` but cannot prove the selection was
   applied. Add regression tests for the exact Batch Z cache-miss path.
4. Implement D9's project-level cadence fields and make planner/release-train
   behavior enforce them: scoped attribution per batch, one full attributed
   suite per release for this self-hosting toolkit.
5. Ensure every implementation sub-plan ends with a driver-owned qualifying
   gate row before a verification sub-plan can expose it to ship-integrity
   enforcement. Teach the planner to add an explicit gate-only last step when
   needed.
6. Refine feedback evidence and taxonomy so `red final gate`, `missing final
   proof`, and `stale final proof` are reported separately. Preserve useful
   evidence for gate-first iterations even when no worker transcript exists.

Until those corrections ship, Batch Z proves the remote-bounce fix but does
not prove that accelerated RSI has the intended verification cost boundary.
