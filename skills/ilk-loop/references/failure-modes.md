# Failure-mode ledger

A curated, **shape-keyed** record of orchestration failures (scheduler / watchdog /
loop / runner / feedback / tray) and — crucially — **the standing guard that now
prevents each from recurring**. This is the distillation layer: per-run
postmortems (`~/.ilk-data/.../postmortems/`) are the raw input; the principle
docs ([decomposition-principles.md](./decomposition-principles.md),
[orchestration-collaboration.md](./orchestration-collaboration.md),
[detached-component-contracts.md](./detached-component-contracts.md)) are the
generalized rules. This ledger sits between them: it groups incidents by
*failure shape* so the recurring pattern is visible.

## Rules of use

- **An entry is not "done" until the `Guard` field names a concrete check** (a
  test, a lint, a launch assertion). A row without a guard is an open risk, not
  a closed incident. Mark guard status `implemented` / `proposed`.
- **Append-only, one row per incident.** Trigger to add a row: any
  *blacklist-class* postmortem (`stuck-no-progress`, `local-checks-stuck`,
  `api-blocked`, `budget-exhausted`, `dependency-unreachable`) or any production
  stall / wrong-decision. Not every minor bug.
- **Keep it terse.** Link out to the commit/version and the principle it
  reinforces; don't re-narrate. Deep narratives live in `case-studies/`.

## Failure-shape taxonomy

Boundary modes (component A trusts/handles a signal from component B wrongly):

- **A — divergent derivation:** two components derive the "same" fact differently.
- **B — unhandled value:** a producer emits a value the consumer doesn't handle.
- **C — noisy-claim-as-fact:** a non-authoritative signal treated as authoritative.
- **D — cross-process state ownership:** state with no single owner / in-memory
  state outliving a cycle.

Process/quality classes (not boundary bugs, but recurring):

- **V — verification-not-enforced (§11):** work `shipped` without its gate
  actually running → self-reported, not verified.
- **O — over-specified contract:** an assertion/check pinned tighter than the
  contract (exact-equality on a set designed to grow; one input format hard-coded).
- **T — trust-tier mislabel (§12):** `verification_tier` claims more trust than
  the sub-plan can actually deliver.

The taxonomy is not exhaustive — add a class when a genuinely new shape appears.

---

## Ledger

### FM-0001 — backend sub-plan shipped while the full server suite was red
- **Date:** 2026-06-17 · **Project:** math-blocks · **Class:** V (verification-not-enforced, §11)
- **Symptom:** a `curriculum-rescope` backend sub-plan reached `status: shipped`
  while `cd server && npm test` had 2 failing tests (caught only during the
  manual redeploy verification).
- **Root cause:** the gate *design* is correct — every backend sub-plan declares
  `cd server && npm test` (the **full** suite, not a per-file gate, so NOT the §8
  anti-pattern). So a launch advanced on self-report: either it ran **without
  `-RunLocalChecks`** (manual `/ilk-run` omits the flag; the scheduler defaults
  it ON), or the breakage was transient between ships. Unconfirmed which — no
  `gates ON/OFF` banner was found in the project's launcher logs.
- **Fix / resolution:** suite is green again (`fail 0`); systemic fix is to
  guarantee gates fire on every launch path.
- **Guard (PROPOSED):** (1) postmortem/collect flags a run that reached `shipped`
  on a sub-plan whose `local_checks` were never executed (gates off) — surface it
  as `shipped-unverified`, not `clean-success`; (2) a launch preflight assertion
  that the `gates ON (-RunLocalChecks)` banner is present whenever any pending
  sub-plan declares `local_checks`. Neither implemented yet.
- **Reinforces:** decomposition-principles §11 (shipped ≠ verified).

### FM-0002 — exact-equality assertion on a growing shared list
- **Date:** 2026-06-17 · **Project:** math-blocks · **Class:** O (over-specified contract)
- **Symptom:** `listActiveProblemTypes('math.pep.g3.s2')` test asserted the active
  type set **`== [area, perimeter]`**; adding `divisionSharing` (a later unit)
  correctly grew the set and broke that test (+ a sibling in `problems.test.js`).
  Functional code was right; the assertion was over-specified.
- **Root cause:** exact-equality (`==` / frozen list) pinned against a set that is
  *designed to grow* one entry per curriculum unit (b5/b7/b8/b9 will each add
  more). Every growth re-breaks it — a brittle assertion, not a real regression.
- **Fix / resolution:** change to **containment / superset** assertion (assert the
  set ⊇ {area, perimeter}, not == ). Confirm the green-making fix was the
  superset form, not "append divisionSharing to the exact list" (which just
  re-breaks next unit).
- **Guard (PROPOSED):** a test-smell lint — flag an exact-equality / frozen-list
  assertion against a known registry / active-set accessor (e.g.
  `listActiveProblemTypes`, label/type registries); require superset/contains.
  Mechanically lintable → candidate for the `contract-discipline-qc` batch. Not
  implemented yet.
- **Reinforces:** the same "test the contract, not tighter than the contract"
  discipline behind [[depends-on-yaml-not-json]] (parser hard-coded one format).

---

## Backfill candidates (this week's ilk-skills incidents, not yet entered)

These are already documented inline in the principle/design docs + memory; add as
rows if/when the ledger becomes the canonical index:

- scheduler rapid-terminal wedge (D + C) — stale-sentinel false positive +
  counter-never-resets; guard: `test_scheduler.ps1 staleexit` + arm-once/decay tests.
- L1 collect sentinel authority (C) — guard: `test_sentinel_authoritative.py`.
- L2 watchdog label→action totality (B) — guard: total-mapping test; hardening in
  `contract-discipline-qc` (`label-action-totality-lint`).
- B2 runner false-stop (C) — guard: `test_b2_confirm_retry.py` (confirm-on-re-run).
- L4 depends_on YAML-not-JSON parse (O) — guard: `TestParseDependsOn` unquoted-form
  cases ([[depends-on-yaml-not-json]]).
- L3 tray tooltip↔rows (A) — guard: `test_render_tray_contract.py`.
