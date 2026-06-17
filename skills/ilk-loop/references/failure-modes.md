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
- **Guard (IMPLEMENTED):** `plan_lint.py` — `lint_brittle_exact_list_assertion` flags
  a sub-plan `local_checks` command with an exact-list-equality pattern
  (`== [...]`, `deepStrictEqual(...)`, `assertEqual(...)`) and recommends
  superset/contains. Also: `decomposition-principles.md` §8 documents the
  anti-pattern + the superset convention for project-side tests. Project-test
  linting (JS/etc.) is out of scope — the guard catches the shape at plan
  authoring time, language-agnostically.
- **Reinforces:** the same "test the contract, not tighter than the contract"
  discipline behind [[depends-on-yaml-not-json]] (parser hard-coded one format).

### FM-0003 — gate runner false-failed a passing suite (GBK decode → None stdout → swallowed crash)
- **Date:** 2026-06-17 · **Project:** ilk-skills (toolkit; affects all projects) · **Class:** C (noisy/erroneous signal treated as authoritative)
- **Symptom:** `cd server && npm test` was **green (210 pass / 0 fail)** when run directly, but the runner's gate reported `local_checks_failed`, **B2-confirmed**, and stalled math-blocks repeatedly. A `shipped` sub-plan (decimals-numberline) appeared to have a failing gate.
- **Root cause:** `run_local_checks.py` ran `subprocess.run(..., text=True)` **without an explicit encoding** → child output decoded via the locale codec (**cp936/GBK** on zh-CN Windows). For UTF-8 `npm` output, `cp.stdout` came back `None`; `_tail(None)` raised `TypeError`, which the caller's `except` swallowed — **discarding the real exit code (0)** and emitting a false "failed". Intermittent (depends on output), which is why some gates passed and others didn't.
- **Fix:** `85d0577` — pin `encoding="utf-8", errors="replace"` on capture + guard `_tail` against `None`/empty.
- **Guard (IMPLEMENTED):** `skills/ilk-loop/tests/test_run_local_checks_capture.py` — `_tail(None)` no-crash, UTF-8 output captured + passes, real non-zero still fails. **Also:** `skills/ilk-loop/scripts/lint_subprocess_encoding.py` (AST-based self-lint) + `tests/test_subprocess_encoding_lint.py` — flags any `subprocess.run`/`Popen` capture call without `encoding=`, scans entire toolkit for zero violations. Prevents re-introduction.
- **Secondary lesson:** the **B2 false-stop guard (re-run-to-confirm) cannot catch a *deterministic* gate-runner crash** — the crash reproduces on re-run and gets "confirmed" as a real fail. B2 filters *transient* flakiness only; a broken check command/runner must be fixed at the source.
- **Reinforces:** [[gbk-console-ascii-only-stdout]], [[inline-python-open-needs-utf8]] — the zh-CN/GBK default-encoding family (now also in subprocess *capture*, not just stdout/file reads).

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
