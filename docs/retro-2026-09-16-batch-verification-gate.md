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

## 7. The rule this is evidence for

A fix to a gate should ask, before it is called done: **what does this gate
output when it is broken, and is that distinguishable from what it outputs when
the thing it checks is fine?** Eight of the nine entries in §1 would have been
caught by asking it once.
