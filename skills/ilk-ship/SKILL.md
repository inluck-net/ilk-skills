---
name: ilk-ship
description: >-
  Run the verify → fix → release → deploy sequence for an ilk-loop batch.
  Phase 0 audits that every shipped sub-plan is proven; Phase 1 selects
  the gate tier and runs baseline-diff; Phase 2 fixes only attributed
  regressions; Phase 3 tags the release; Phase 4 installs per host.
  Use when the user says "/ilk-ship", "ship the batch", "release this
  batch", or "deploy".
---

# ilk-ship — the five phases

A release is a sequence, not a single action. Each phase has a hard
entry/exit condition. Phase 0 is non-skippable — a batch whose own
sub-plans are unproven must not be released.

**`shipped` is commit-only and local.** It is not pushed, not
CI-verified, not deployed. A sub-plan marked `shipped` has a commit
for every step and a green local gate on this machine. Nothing more.
This is the misreading the whole `ilk-ship` batch exists to correct.

## When to use

- The user says `/ilk-ship`, "ship the batch", "release this batch",
  or "deploy".
- After a batch's sub-plans are all `shipped` and the operator wants
  to cut a release tag and install on declared hosts.

## Prerequisites

Read these before executing any phase:

- `skills/ilk-ship/scripts/gate_scope.py` — tier selection and
  complement subtraction (Phase 1 engine).
- `skills/ilk-ship/scripts/baseline_diff.py` — node-id diff against
  a named tag baseline (Phase 1 engine).
- `skills/ilk-loop/scripts/ship_audit.py` — step-commit presence +
  gate-outcome predicate (Phase 0 engine).

## The five phases

| # | Phase | Engine | Blocking? | Loops to |
|---|---|---|---|---|
| 0 | **Audit** — every `shipped` sub-plan has a commit per step and a green final gate | `ship_audit.py` | **yes** — hard stop | — |
| 1 | **Verify** — select the tier, run it, apply both floors | `gate_scope.py`, `baseline_diff.py` | **yes** on attributed regressions | Phase 2 |
| 2 | **Fix** — resolve only what Phase 1 attributed to this change | — | — | Phase 1 |
| 3 | **Release** — CHANGELOG row + tag | — | **yes** | — |
| 4 | **Deploy** — install on each declared host, then the post-deploy hook | `ship:` `hosts` | reports per host | — |

### Phase 0 — Audit

**Entry:** a batch exists with `shipped` sub-plans.

**Exit:** every `shipped` sub-plan is **proven** — it has a commit for
every `### Step N` heading in its body, AND its final recorded gate
outcome is not `fail`.

**Engine:** `ship_audit.py`. It composes two checks:

1. **Step-commit presence** — searches `git log --format=%s%n%b`
   (full message, not subject only — body-placed trailers count)
   for `[plan:<slug>#step-N]` for every authored step.
2. **Gate outcome** — via `ship_integrity.evaluate_ship()` on the
   declared `local_checks`.

**Hard stop.** If ANY `shipped` sub-plan is unproven, Phase 0
refuses to advance to Phase 3. This is asserted by a test — a
documented hard stop that is not asserted is the 2026-08-14 failure
with better prose.

### Phase 1 — Verify

**Entry:** Phase 0 passed (all sub-plans proven).

**Exit:** the gate ran; attributed regressions are zero, OR Phase 2
is entered.

**Tier selection** (`gate_scope.py:select_tier`):

| Tier | Trigger | Gate scope |
|---|---|---|
| 0 | docs/changelog only, no code (.py/.sh/.ps1) | content assertions, no suite |
| 1 | changed symbol has zero resolved consumers | that module's own tests |
| 2 | N resolved consumers | tests covering those consumers, one hop out |
| 3 | contract-governed file OR a shared path/schema OR oracle failed | whole suite |

Tier selection is a pure function of `(changed_paths, consumer_result,
contract_governed_set)`. Resolution happens in
`gate_scope.resolve_consumers`.

**Complement subtraction** (`gate_scope.subtract_complement`): the
selected gate commands are compared against already-recorded commands
from the JSONL log. Commands already run are subtracted. The two
**floors** are never subtracted:

1. **baseline-compare** — node-id diff against the last tag.
2. **collection** — `--collect-only` catches the class that voids
   every other result.

**Baseline-diff** (`baseline_diff.run_baseline_diff`):

- Comparison by **node id**, not count. Same count, different node
  ids must NOT report zero regressions.
- The ref is resolved via `git describe --tags --abbrev=0`, never
  assumed.
- Missing baseline is "could not compare", distinct from "zero
  regressions".
- A collection error voids every other result from that run.
- The denominator statement carries the search space: "0 regressions
  across 698 collected tests vs v0.9.63".

### Phase 2 — Fix

**Entry:** Phase 1 attributed regressions to this change.

**Exit:** fixes applied; loops back to Phase 1 for re-verification.

Phase 2 fixes **only** what Phase 1 attributed. Fixing inherited
failures inside a release is how a release grows unbounded, and it is
why baseline-diff's node-id attribution is a hard input rather than a
nicety.

### Phase 3 — Release

**Entry:** Phase 1 verified (zero attributed regressions).

**Exit:** CHANGELOG row written, tag created.

Pushing the tag is an outward-facing action and stays an explicit
operator step — `/ilk-ship` prepares the release; it does not push.

### Phase 4 — Deploy

**Entry:** Phase 3 tagged.

**Exit:** install attempted on each declared host.

**Per-host reporting.** Phase 4 reports per host and never reports
success for a host it did not reach. An unreachable host is
`unreachable`, not `ok`. The `hosts` field in the `ship:` block is
declarative data — Phase 4 acts on it.

**Post-deploy hook.** After installing on each host, the post-deploy
hook runs (if configured). The `ILK_ALLOW_FULL_SUITE=1` environment
variable is the sanctioned way to run a whole suite past the
`no-full-suite.sh` hook (`no-full-suite.sh:75-76`). It exists because
the hook blocks bare `pytest` invocations to prevent accidental
full-suite runs; the escape lets a deliberate full-suite gate proceed.

## Missing `ship:` block

A missing `ship:` block degrades to a documented default — it never
hard-blocks. The default is:

- `suite.command`: `python3 -m pytest` (the standard runner)
- `suite.flags`: `[]` (no extra flags)
- `suite.timeout`: 300 (5 minutes)
- `baseline_red`: `[]` (no exclusions)
- `hosts`: `[<current-host>]` (deploy to this machine only)
- `path_prelude`: `""` (no PATH setup)

This default is stated here and enforced in `ship_config.py`.

## No phase claims a skipped step

Each phase's output states what it ran and what it did not. This is
the same discipline Phase 0 enforces on sub-plans, applied to the
skill itself.

## `install.sh` discovery

`commands/ilk-ship.md` and `skills/ilk-ship/` are discovered by
`install.sh` via glob (`install.sh:379-387`):

- Skills: `find ... -name 'ilk-*'` → finds `ilk-ship/`
- Commands: `find ... -name 'ilk*'` → finds `ilk-ship.md`

There is **no registration list to edit**. A host that has not re-run
`install.sh --apply` does not have the command symlink — the command
is invisible there. The AC is "discovered by the glob and present in
the dry-run plan", not "registered".

## CLI

```bash
# Run Phase 0 on the current project
python3 skills/ilk-ship/scripts/ship_audit.py --subplan path/to/subplan.md

# Select the gate tier
python3 skills/ilk-ship/scripts/gate_scope.py  # (imported, not CLI)

# Run baseline-diff
python3 skills/ilk-ship/scripts/baseline_diff.py \
  --failures-json '["test_foo", "test_bar"]' \
  --search-space 698 \
  --suite-invocation "python3 -m pytest --timeout-method=signal"

# Validate ship config
python3 skills/ilk-ship/scripts/ship_config.py --validate --project .
```
