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

| Tier | Trigger | Attribution scope |
|---|---|---|
| 0 | docs/changelog only, no code (.py/.sh/.ps1) | content assertions only |
| 1 | changed symbol has zero resolved consumers | that module's own tests |
| 2 | N resolved consumers | tests covering those consumers, one hop out |
| 3 | contract-governed file OR a shared path/schema OR oracle failed | widest attribution scope (collection-floor + baseline-diff) |

Tier selection determines the **attribution scope** for the collection floor and baseline-diff — it does not prescribe a suite to run.

The three tier-3 triggers are checked **first and against the whole diff**: one
matching file among many forces the widest gate for the entire batch. There is no
per-file scoping.

**This tool's own artifacts are exempt** (`gate_scope.TOOL_ARTIFACT_DIRS`).
Anything under `.ilk-baselines/` is skipped by `_is_path_or_schema_change`,
because `store_baseline` writes a `.json` there on every release and `.json` is a
path/schema extension — so without the exemption last release's artifact forced
the next release to tier 3 regardless of what changed. See "Measured behaviour and
known limits" below.

Tier selection is a pure function of `(changed_paths, consumer_result,
contract_governed_set)`. Resolution happens in
`gate_scope.resolve_consumers`.

**Note the shape mismatch when wiring these together:** `select_tier` takes a
*list* of changed paths, but `resolve_consumers(module_name, project_root)`
resolves *one* module. Nothing in the API says which module to resolve for a
multi-file diff, and passing the path list where a module name is expected makes
the oracle report `FAILED` — which correctly degrades to tier 3, so the mistake is
silent and looks like a legitimate decision. Resolve per changed module and
combine, or state explicitly which module governs.

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
  across 1846 collected tests vs v0.9.66" (1846 = this repo's whole-suite
  collection measured 2026-08-19; `skills/ilk-loop/tests/` alone is 761).
- **Where baselines live:** `<project_root>/.ilk-baselines/<tag>__<hash>.json`
  (`baseline_diff.baseline_dir`, named by `gate_scope.BASELINE_DIR_NAME`). The
  directory is **gitignored on purpose** — a baseline is host-specific (the same
  v0.9.66 tag measured 746 passed/15 skipped on chad-mbp and 745/16 on rezmac),
  so it is not shared state, and committing it poisoned tier selection.
- Baselines are keyed on **(tag, suite_invocation)**. A scoped invocation will
  not compare against a whole-suite baseline — it returns `could_not_compare`
  rather than a misleading zero. That is correct, but it means a scoped Phase 1
  yields **no regression attribution at all**.

**Batch verdict verification.** Phase 1 does **not** run the test suite.
It **verifies** the recorded batch verdict — the record persisted by
the batch gate at the end of the loop run. The verification reads the
record, checks `head_sha` against the current HEAD, runs the collection
floor, and baseline-diffs. Phase 1 **refuses to release** on a
**missing**, **failed**, or **stale** verdict. A verify step that
proceeds anyway is the rubber-stamp failure mode.

**The refusal contract.** Phase 1 **refuses rather than substitutes**. A
missing, stale or failed verdict, or a `could_not_compare` baseline, **halts
and files**. It does not ship, and it does not accept an assembled
alternative.

This is deliberately stricter than what a human release does today. A human
may knowingly proceed on substitute evidence and label it; that judgment is
not available to an unattended run. The design doc
(`docs/future-work/unattended-self-improvement.md`, blocker 2) states this
explicitly.

**Implementation:** `phase1_verify.py` implements the refusal check. It
validates the batch-gate record (absent / stale_head / stale_invocation /
fail / error → refuse) and then checks the baseline report (could_not_compare
→ refuse). On any refusal, it writes a `phase1-refusal.json` artifact to
the runtime dir so the unattended pipeline has a file to act on.

**Why this is not caution — the dated reason.** Both v0.9.86 and v0.9.87
shipped on substitute evidence assembled and labelled by the same agent doing
the release:

- **v0.9.86** — batch verdict `stale_head`: `batch-gate.json` held the tip of
  a batch released five tags earlier. Baseline `could_not_compare`: no
  baseline for the previous tag on that host (newest was v0.9.81 against a
  v0.9.86 HEAD).
- **v0.9.87** — same condition on both engines. Each release proceeded on
  evidence the agent assembled for itself, which is the exact shape of the
  defect the batch-gate exists to prevent.

**The `regression_count` trap.** When `could_not_compare` is true,
`baseline_diff`'s payload still carries `regression_count: 0`. That field
is **meaningless** in that state — it is exactly the number an unattended
caller would read as success. Phase 1 checks `could_not_compare` first,
never reads `regression_count` in that state.

### Measured behaviour and known limits

Measured 2026-08-19 by replaying `select_tier` over v0.9.57..v0.9.67 with a
**healthy** oracle (so this is the best case, not the degraded one):

| release range | files | tier | trigger |
|---|---|---|---|
| v0.9.57..58 | 3 | 1 | zero resolved consumers |
| v0.9.58..59 | 15 | 3 | `collect.py` |
| v0.9.59..60 | 5 | 1 | zero resolved consumers |
| v0.9.60..61 | 12 | 3 | `status_all.py` |
| v0.9.61..62 | 3 | 3 | `status_all.py` |
| v0.9.62..63 | 40 | 3 | `collect.py` |
| v0.9.63..64 | 8 | 3 | `status_all.py` |
| v0.9.64..65 | 4 | 3 | `collect.py` |
| v0.9.65..66 | 12 | 3 | `run_ilk_loop_claude.sh` |
| v0.9.66..67 | 24 | 3 | `.ilk-baselines/…json` (spurious — now fixed) |

**Distribution: tier 3 in 8 of 10; tier 0 and tier 2 never selected.** In
practice the four-tier ladder behaves as a binary that says "run everything",
because the dominant trigger is the contract-governed set — and `collect.py`,
`status_all.py`, `loop_status.py` and `run_ilk_loop_claude.sh` are exactly the
files toolkit batches tend to touch.

Two consequences worth knowing before relying on Phase 1:

1. **Tier 3 has no cost ceiling.** `TierDecision` carries `tier`, `reason` and
   `consumer_count` — there is **no cost field**. Nothing measures, records or
   bounds what the selected gate costs, so tier 3 on this repo means 1846 tests
   with no declared budget and no cheaper substitute. When a whole-suite run is
   disallowed, Phase 1 has nothing to fall back to and simply cannot complete —
   which is what happened on the v0.9.67 release: the tier-3 gate was not run,
   and the release shipped on scoped evidence plus the collection floor instead.
   A ceiling should compare the selected tier's measured cost against a budget in
   the `ship:` block and **state** any downgrade, since a silent narrowing is the
   failure this whole skill exists to prevent.
2. **The artifact self-poisoning is fixed, but did not move the historical rate.**
   Exempting `.ilk-baselines/` changes a docs-only-plus-baseline diff from tier 3
   to tier 0, and that is worth having. It does **not** reduce the 8-of-10 figure,
   because those selections were driven by contract-governed files, not by the
   artifact. Making the tier-3 triggers per-file rather than whole-diff is the
   change that would move it — and that interacts with the sentinel case behind
   AC-4 (a path change at 2 call sites broke 12 fixtures across 7 files), so it
   needs care rather than a quick narrowing.

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

**Per-host reporting.** Phase 4 reports per host with one of exactly
four states:

| State | Meaning |
|---|---|
| `ok` | Install succeeded, all daemons are current, **and** (when `--require-tag` is set) the daemon code resolves to the required release tag. |
| `tag-mismatch` | The daemon's recorded sha does not resolve to the required release tag (when `--require-tag` is set). A sha that resolves to no tag at all is not-conformant (fail closed). |
| `stale-daemon` | Install succeeded but at least one daemon holds stale code (and the tag matches, if checked). |
| `unreachable` | Could not probe the host (ssh failed, launchctl absent, script missing). |

**A stale daemon blocks `ok`.** Phase 4 never reports success for a
host it did not reach, and a host with new code installed and an old
daemon running is not deployed. Detection uses the resolver script:

```bash
# Single-host (detect-only, release-conformance check):
python3 skills/ilk-ship/scripts/host_deploy_status.py \
  --bouncer skills/ilk-watchdog/scripts/bounce_daemons.sh \
  --require-tag v0.9.87

# Multi-host. --local-host names the declared host that IS this machine;
# every other host is reached over ssh, so ITS --bouncer path is the path on
# that host (same order as --hosts).
python3 skills/ilk-ship/scripts/host_deploy_status.py \
  --bouncer skills/ilk-watchdog/scripts/bounce_daemons.sh \
  --bouncer /Users/chad/Projects/github/inluck-net/ilk-skills/skills/ilk-watchdog/scripts/bounce_daemons.sh \
  --hosts chad-mbp,rezmac --local-host chad-mbp \
  --require-tag v0.9.87
```

Add `--bounce-hosts` to permit actual bouncing (omit for detect-only).
Add `--require-tag <tag>` to enforce release conformance — a host whose
daemon code does not resolve to the given tag reports `tag-mismatch`
instead of `ok`. A sha that resolves to no tag is not-conformant (fail
closed). Omit for the default three-state check.

In single-host mode the script prints one line (`ok` / `stale-daemon` /
`tag-mismatch` / `unreachable`) and exits 0 / 1 / 1 / 2 respectively.
In multi-host mode it prints one `<host>: <state> (<transport>)` line per
declared host and exits 0 only if every host is `ok`.

**`--local-host` is not optional in practice.** A deploy label rarely matches
a hostname — on this Mac the declared hosts are `chad-mbp` and `rezmac` while
`hostname` returns `Chads-MacBook-Pro.local`, and `ssh chad-mbp` does not
resolve. An undeclared host is therefore ssh'd and reports `unreachable`. That
is the fail-closed direction and is deliberate: the alternative, which shipped
until 2026-09-07, was that **every** declared host ran the local bouncer, so
`--hosts chad-mbp,rezmac --bounce-hosts` bounced this machine twice and printed
`rezmac: ok` for a host it never contacted. Nothing in the output distinguished
that from a real deploy; it was caught only because rezmac's scheduler pid was
unchanged from two days earlier. Each line now names its transport so the
report can be audited.

This is the third false-`ok` in this resolver — the family:

| release | what returned `ok` | what it had not checked |
|---|---|---|
| v0.9.74 | a host whose `bootstrap` had failed | that the daemon came back |
| v0.9.87 | a host that was never contacted | that ssh happened at all |
| this one | a host at an older tag | that the host is on the release |

Same shape each time: a state meaning "checked and current" returned on a
path that did not check the thing that matters. The regression test
(`test_false_ok_family.py`) names the family, not the instance — the next
false-`ok` should be recognised as a fourth member, not a novelty.

The `hosts` field in the `ship:` block is declarative data — Phase 4
acts on it. Every declared host appears in the summary; a host missing
from the report is indistinguishable from a passing one.

**Post-deploy hook.** After installing on each host, the post-deploy
hook runs (if configured). The `ILK_ALLOW_FULL_SUITE=1` environment
variable is the sanctioned way to run a whole suite past the
`no-full-suite.sh` hook (`no-full-suite.sh:75-76`). It exists because
the hook blocks bare `pytest` invocations to prevent accidental
full-suite runs; the escape lets a deliberate full-suite gate proceed.

## Direct releases do not run the full suite

A **direct release** is one whose commits did not go through the loop — a
hand-made fix, tagged without a batch. It has no batch-verification sub-plan,
therefore no batch-gate record, therefore nothing for Phase 1's verdict engine
to verify.

**Do not manufacture one by running the suite by hand.** Invoking
`batch_gate --run` at release time produces a record the releasing agent
created for itself, moments before reading it back as proof. That is the exact
shape the refusal contract exists to prevent, and it is what v0.9.86, v0.9.87,
v0.9.90 and v0.9.91 each did — the last two while quoting the refusal contract
in their own tag bodies.

**The full suite belongs to the loop, once per batch**, in the sub-plan marked
`batch_verification: true`. It does not belong here.

### What a direct release runs instead

1. **A change-scoped gate.** Every test file referencing the changed modules
   AND their resolved production importers. Name the file count and the result.
2. **Nothing else is claimed.** The evidence covers the change and its readers.
   It does not cover the rest of the tree, and the tag body must say so.

Measured 2026-09-09 on this repo: the change-scoped gate for a `batch_gate` +
`ship_audit` change was 63 files / 606 tests / 55s, against a full suite of
2877 tests / ~264s that found **0** attributed regressions on three consecutive
direct releases. The scoped gate is what actually caught the defects, both
times.

### The tag body must state the narrower claim

A direct release's tag records **what was run and what was not** — not a
sentence that reads like a full verification. State the scoped gate, its file
and test counts, and the fact that no whole-suite run backs this release. A
reader who cannot tell a scoped release from a batch release will read the
first as the second, which is the same misreading the whole skill exists to
correct.

### If whole-tree evidence is actually wanted

Route the work through the loop so the batch-verification sub-plan runs the
suite once, under the gate that persists a real record. That is cheaper than a
hand-run suite and it produces evidence the releasing agent did not author.

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

**What reads `path_prelude`:** `run_local_checks.py` prepends the prelude to
each gate command's shell invocation (`run_local_checks.py:372-374`). The
prelude is typically `export PATH="/some/dir:$PATH"` to add toolchain
directories that the driver's environment lacks (e.g. `~/.bun/bin` for
`bunx`). `plan_lint.py`'s `lint_gate_executable_on_driver_path` also reads it
to verify that gate executables resolve on the effective PATH.

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
  --search-space 1846 \
  --suite-invocation "python3 -m pytest --timeout-method=signal"

# Validate ship config
python3 skills/ilk-ship/scripts/ship_config.py --validate --project .
```
