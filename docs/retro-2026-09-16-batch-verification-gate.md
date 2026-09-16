# Retrospective — the batch-verification step-1 gate, 2026-09-14 to 2026-09-16

**Status:** open — two follow-ups tracked in the improvement backlog (see §6).

## 1. The measurement that prompted this

Seven of the eight releases v0.9.98–v0.9.105 are fixes to one mechanism: the
batch-verification sub-plan and the proof its step-1 gate produces — **nine
distinct fixes**, six of them one-per-release and three shipped together in
v0.9.105. All landed inside three days.

| Version | Date | What was wrong with the step-1 gate / its proof |
|---|---|---|
| v0.9.98 | 2026-09-15 | Step 0 discarded its own progress at the iteration bound — no commit, no record |
| v0.9.100 | 2026-09-15 | The step ran the whole suite and did not record which scope it used |
| v0.9.101 | 2026-09-15 | The gate invoked `verify_attribution.py`, **which did not exist** |
| v0.9.102 | 2026-09-15 | Five docs still described the superseded whole-suite behaviour |
| v0.9.103 | 2026-09-15 | An unparseable `local_checks` block read as green |
| v0.9.104 | 2026-09-15 | The verification record and the proof artifact were different files; nothing bridged them |
| v0.9.105 | 2026-09-16 | The gate command carried an **unsubstituted `<record path>`** and could not run |
| v0.9.105 | 2026-09-16 | A gate re-run stamped a proof over a tree its suite never ran on |
| v0.9.105 | 2026-09-16 | No check existed for the *class*: any future template token would repeat it |

(v0.9.99 is the exception: a PATH fix, adjacent but not this mechanism.)

Nine fixes to one gate in three days is not nine unrelated bugs. It is one
under-specified component being repaired an instance at a time.

## 2. What the instances have in common

Every one of the nine is the same shape:

> **The gate reported a state it had not measured.**

- v0.9.101 — a gate that cannot run reports whatever the shell's exit code
  happens to mean.
- v0.9.103 — a block that cannot be parsed yields zero checks, and zero checks
  all pass.
- v0.9.104 — a batch verified green and read `SHIP PROOF MISSING`, because the
  artifact written and the artifact read were different files.
- v0.9.105 (placeholder) — a literal `<record path>` fails as *record not
  found*, which is indistinguishable from *step 0 never wrote one*.
- v0.9.105 (tree guard) — a proof stamped with the tree at write time asserts
  that a suite ran on code it never saw.

In each case the **failure mode and a legitimate state produce identical
output**. That is the defect class, and it is the same one the house rules name:
*an empty answer must be unconstructible without looking.*

## 3. Why it kept recurring

Three reasons, in increasing order of how much they cost:

1. **The template is prose, and prose is not checked.** `<record path>` sat in
   `templates/batch-verification-subplan.md` from the day it was written. Every
   fix to the *script* left the *instruction* untouched, and the instruction is
   what the planner renders.

2. **The remedy each time was an instance fix.** v0.9.101 made the script exist;
   the placeholder fix below fixed the one token. Neither asked *what else has
   this shape* — which is why the born-stale-proof defect was found by reading
   output while verifying the placeholder fix, not by a check. Had the class
   question been asked at v0.9.101, the remaining eight would have been one
   piece of work.

3. **The diagnosis the failure invites is the wrong one.** `record not found`
   points at step 0. An operator or agent following that pointer re-runs the
   suite, which cannot fix a malformed command, and the loop spends another
   iteration. On 2026-09-15 this consumed two runs — `20260915-190958` and
   `20260915-231529` — and reverted two sub-plans from `shipped` to
   `in-progress` **after each had run its suite green** (5262 passed / 0 failed
   for batch c; `suite_failed: 0` over a 5319-test selection for batch d).

## 4. What was done about the class, not the instance

Per the standing preference that a visible artifact beats self-recognition —
self-recognition has failed repeatedly and is recorded as having done so — each
of these is a check that fails loudly, not a note asking a future reader to
remember:

- **`lint_gate_placeholder_unresolved`** (`plan_lint.py`). Any `local_checks`
  command in a *rendered* sub-plan that still carries a template token is a HARD
  finding at plan time, before a run is spent. This kills the whole class, not
  the one token. Calibrated against the corpus rather than guessed: all 4
  placeholders appearing inside gate commands across the toolkit's templates are
  multi-word (`<skill-root>`, `<batch-slug>`, `<record path>`, `<command that
  proves this step's outcome>`), and all 4 false positives in a sweep of **678
  sub-plans across 11 projects** were bare single words genuinely belonging to
  their command (`<key>PATH</key>`, `<integer>5</integer>`, `update <id>`).
  Requiring a space or hyphen separates the two sets exactly: **0 of 678** flagged
  after tightening, with the historical defect still caught.

- **`verify_attribution.reject_placeholder`** — the runtime half. If a
  placeholder reaches the gate anyway, it says so and names the plan file as the
  thing to fix, rather than failing as a missing record.

- **`verify_attribution.check_verified_tree`** — a proof is written only for the
  tree the suite actually ran on. Compares **trees, not heads**, so the empty
  marker commit the step makes by design is correctly ignored.

- **The template now mandates `verified_head:`**, machine-readable, so the check
  above has something to read. A record naming no commit is refused: unknown is
  not unchanged.

## 5. What is still only prose

Recorded honestly, because an unfixed item presented as fixed is the same defect
one layer up:

- **The heredoc rule.** `templates/batch-verification-subplan.md` step 0 warns
  against writing the record with an unquoted heredoc, because backticks get
  command-substituted. It is still only a warning, and it was still violated:
  `batch-2026-09-15d-batch.md` lines 17-18 read `1.  — added  to schema expected
  set` — every backticked value emptied. The gate does not care, but the one
  artifact a human reads lost its evidence. Nothing checks this.

- **Nothing verifies that a template's rendered output is runnable.** The lint
  above catches placeholders specifically. A template whose command is malformed
  in some other way still ships.

## 6. Tracked follow-ups

Both are in the improvement backlog
(`~/.ilk-data/ilk-skills-improvements/candidates.json`), where
`/ilk-self-improve` reads them:

1. **A verification record with emptied fields should be rejected.** Detect the
   heredoc-mangling signature (a `**Field:**` whose value is empty where the
   template requires one) and fail step 0's gate.
2. **Rendered-template runnability check.** Assert that every command a template
   emits is executable as rendered, not merely placeholder-free.

## 6b. The cost half — 70 full suites in one day

The same mechanism was also, separately, **slow without bound**, and that is
recorded here so no future batch inherits it.

**Measured 2026-09-16 on ilk-skills: 70 whole-suite pytest invocations in one
day**, ~305s each — roughly six hours of test execution for one batch, which had
still not converged when it was stopped.

### Why it was unbounded

The verification step asks two questions with one instrument:

| question | needs | got |
|---|---|---|
| did this change break anything? | a broad run, **once** | a broad run |
| did my fix work? | the **failing selection** | a broad run, every time |

The fix loop re-answers the first question every time it should be answering the
second, with no convergence guarantee — each fix can break something else — and
no cap on suite runs. `quarantine_subplan.py` bounds *consecutive gate reds*;
nothing bounded the resource actually burned.

The template already contained the right principle and applied it to the wrong
half. Of the at-base rerun it says: *"This is cheap — it is the failing
selection, not the suite. Measured: 2 failing node ids, 0.14s at base."* That is
exactly the rule the fix loop needed, applied only to exoneration.

### Three compounding bugs, all now fixed

1. **One unmatched module name forced a full run.** `compute_suite_scope`
   returned `full` from *inside* its mapping loop on the first module with no
   test file, discarding every selection already computed. The module in
   question had tests — `test_verification_record_emission.py` and
   `test_record_is_measured.py` — and the mapper only looked for
   `test_verification_record.py`. **3102 tests / 305s → 172 tests / 18.6s**
   once it degraded per module and learned to match prefixes and importers.
   (`6aa001f`)

2. **`baseline_red` was read from the wrong place, in the wrong shape.** The
   list lives at `ship.baseline_red` and holds dicts; the reader checked the
   top level and assumed strings. Six correctly-declared entries covering 32
   known failures were invisible, so every record said `in baseline_red: no`
   for all 35 rows, and **24 of 24 at-base reruns were subprocesses
   rediscovering declared facts**. Declared entries now resolve from the
   declaration. (`054dc08`)

3. **A declared failure was re-measured anyway** — the same fix; `run_at_base`
   no longer spawns a subprocess for a node id the project has already
   exonerated.

Bug 2 is worth its own line: **I reported "baseline_red is empty" as a finding,
and it was not.** An empty answer from the wrong location is indistinguishable
from an empty answer from the right one — the exact defect this document is
about, committed inside the tool built to refuse it, and then published as
evidence.

### What a plan costs now, and what is still open

Per verification sub-plan, in the happy path: **one suite invocation**, scoped.
Step 1 parses the record and runs no tests. No other sub-plan may run a broad
gate (`lint_wholesuite_gate_outside_verification_subplan`).

**Still open, and the reason this section says "cost half" rather than "fixed":**
the fix loop is still N broad runs, because a red step-0 gate does not advance
and the next iteration re-runs step 0 in full. The three-pass design —
discover once, re-run only the failing selection while fixing, confirm once —
is specified in `docs/verification-record-design.md` and **not implemented**.
Until it is, the count is `1 + number of fix iterations`, unbounded.

## 7. The rule this is evidence for

A fix to a gate should ask, before it is called done: **what does this gate
output when it is broken, and is that distinguishable from what it outputs when
the thing it checks is fine?** Eight of the nine entries in §1 would have been
caught by asking it once.
