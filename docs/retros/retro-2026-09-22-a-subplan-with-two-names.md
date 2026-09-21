# Retro 2026-09-22 — a sub-plan with two names, and a ledger that only knew one

The `pv-5611` batch (kira-cloudflare, patient verification) did all seven
sub-plans' work, produced a PR that **merged at 2026-09-21T01:43:33Z**, and
then sat `blocked` for about nineteen hours because a sub-plan can be
identified two ways and the component that convicted it used the other one.

Sibling retro, same family, previous day:
[`retro-2026-09-21-the-gate-cannot-pass-from-the-worktree.md`](retro-2026-09-21-the-gate-cannot-pass-from-the-worktree.md)
— there a guard failed a session on its own cache; here a check failed a
batch on its own naming. Both convict correct work, and both were found by
measuring the instrument rather than the code under test.

## What happened

| Time | Event |
|---|---|
| 2026-09-21 01:43 | PR #5611 merges — the batch's output is already in `main` |
| 2026-09-21 03:30 | run `20260921-033056` starts, 7 iterations |
| 2026-09-21 05:52 | `pv5-verify` step-1 gate **passes**; ship-integrity reverts it anyway; master parked |
| 2026-09-22 ~01:00 | operator notices the row still on the SwiftBar panel |

The violation text:

```
[ship-integrity VIOLATION] pv5-round5-verify: missing commit for steps 0, 1
  — `shipped` is not backed by the work (0 of 2 authored steps committed)
[ship-integrity] reverted pv5-round5-verify to in-progress; no gate record
  for this slug; pointer left untouched
```

## Root cause: two derivable identities, one lookup

A sub-plan file carries a name in two places, and nothing reconciles them:

```
_slug_from_filename('2026-09-21-pv5-verify.md')  -> 'pv5-verify'          (plan_status.py:311)
frontmatter  plan:                               -> 'pv5-round5-verify'
```

Three facts then compose into the failure:

1. **The repo's `.ilk-remote-type` is `shared`**, so the worker omits
   `[plan:<slug>#step-N]` trailers *by design* (`SKILL.md:449-470`). On a
   shared remote the ship-proof ledger is the **only** evidence a step was
   committed.
2. **The ledger held 7 rows, one per sub-plan** — the relevant one keyed
   `pv5-verify`, the filename-derived name.
3. **`ship_integrity._missing_step_reason` reads the slug from the
   frontmatter only** (`ship_integrity.py:186-192`) and looked up
   `pv5-round5-verify`.

So the trailerless union — `test_ship_integrity_trailerless_repo.py` AC-1,
"ledger row covering the steps ⇒ ships" — could not match, and execution
fell through to AC-2, "no ledger ⇒ still a violation (fail-closed)". AC-2 is
correct policy applied to a lookup that asked the wrong question.

**Why the other six shipped.** Their two identities agree
(`2026-09-21-pv5-fix2-mint-gate.md` ↔ `plan: pv5-fix2-mint-gate`), so the
union found their rows. Exactly one sub-plan in the batch had a frontmatter
slug that differed from its filename, and exactly that one was reverted.

## The hypothesis that was wrong, and why it is worth recording

A parallel session diagnosed it as *"`pv5-verify` has `batch_verification:
true` AND `local_checks: []`; a verification sub-plan with no gate cannot
prove anything, so the loop parks"*. It is a plausible reading of the
frontmatter and it is wrong three ways:

- The sub-plan **has** gates — three per-step `local_checks` commands at
  lines 131, 133 and 163. Only the *frontmatter* list is empty.
- One of them **ran and passed in the failing run**:
  `[local_checks OK] pv5-verify step 1 -> pass`.
- An empty declared-gate list **cannot** produce this violation anyway:
  `evaluate_ship` returns `ok=True, reason="no gate declared — nothing to
  enforce"`. The violation text names *step commits*, not gates.

The related claim that `plan_lint.py:3612` hard-fails
`batch_verification: true` + `local_checks: []` is also not what that branch
says: it fires on *an empty `## At-base rerun` table AND no suite gate
anywhere in the sub-plan*, and `_extract_all_local_checks_commands` reads
per-step blocks too.

The lesson is not that the other session was careless — the frontmatter
really does say `local_checks: []`. It is that **the violation message named
its own reason and the reason was not read**. "Missing commit for steps 0, 1"
is a claim about git history and the ledger; nothing in it is about gates.

## Why it stayed invisible for nineteen hours

The SwiftBar row rendered `pv5-round5-verify` — the frontmatter name. Anyone
grepping the plans dir for that string finds **no file**, because the file is
`2026-09-21-pv5-verify.md`. The same split that caused the bug also hid it.

## The fix

Folded into the running `provider-switching-tier12` batch as sub-plan
`one-subplan-one-slug` (order #9, ahead of batch verification), with two
halves:

1. **Runtime** — the trailerless union resolves a sub-plan's ledger rows
   under *either* identity: the frontmatter `plan:` slug and
   `_slug_from_filename`. The fail-closed default is unchanged: neither
   present ⇒ violation.
2. **Plan time** — `plan_lint` gains a HARD finding when the two disagree,
   so new plans cannot introduce the split. Existing files are rescued by
   the union rather than renamed.

`regression_for` is set on that sub-plan, which obliges it to carry a
reproducing gate — the pv-5611 shape, as a test.

## Scale, and why this is not a one-off

`test_ship_integrity_trailerless_repo.py`'s own docstring records the
previous instance of the same class: **26 of 149 `last-exit.json` records**
on rezmac in `ship_integrity_violation`, 17 of them on or after 2026-09-16,
when a root-resolution fix made the check fire instead of silently skip. That
round was fixed by teaching the check to read the ledger at all. This round
is the same check reading the ledger under the wrong key.

Both rounds share a shape worth naming: **a fail-closed check whose input
resolution is weaker than its verdict.** Fail-closed is right when the
question was asked correctly; when the lookup is wrong, fail-closed converts
a naming inconsistency into a parked batch.

## A sibling found by the same re-measurement — `--scope auto`

The parallel session, re-checking its own diagnosis with a corrected
instrument, turned up a second member of the family in gh-resolve's
2026-09-21 batch. Its verification sub-plan is titled *batch verification —
full suite* and its gate passes `--scope auto`:

```
verification_record.py --project . --batch batch-2026-09-21 \
  --base-sha 67fcf359... --run-suite --scope auto --suite-timeout 1800
```

Two occurrences of `--scope auto` in that file, zero of `--scope full`.
`auto` let the diff choose; it chose **9 files / 228 tests against 5608
collected**, and that selection was written to `runtime/batch-gate.json` as
`verdict: pass` while the tree carried **11 real failures**. The template
already says to pass `--scope full` when the plan mandates a full suite —
nothing enforced it.

Same shape as the slug defect: **a plan whose two halves disagree about what
it is**, with no mechanical check that they agree. Folded into
`one-subplan-one-slug` as AC6, because one lint pass can cover both.

It also changed this batch. `provider-switching-tier12-verify` was written
with `--scope auto`, internally consistent with a body that said "scoped to
the changed area" — so the proposed lint would NOT have fired on it, and it
would still have been wrong: this batch modifies
`run_ilk_loop_claude.sh`, a **bash** runner whose consumers no Python import
oracle can resolve. A scope computed from importers systematically
under-counts a shell change. Switched to `--scope full`, with the basis
recorded in the sub-plan (430.0s / 3486 collected on chad-mbp, paid once per
batch) and the falsifier stated.

The general lesson: *internal consistency is not correctness*. A plan can
agree with itself and still describe the wrong gate.

## What would have caught it sooner

Reading the violation string. It names step commits; the repo is `shared`;
therefore the ledger is the only evidence; therefore the next question is
"what key is the row under?" — which is one `jq`/`collections.Counter` over
`ship-proof.jsonl` and took under a minute once asked. The nineteen hours
were not spent failing to find it; they were spent not looking, because the
panel said something that matched no file on disk.
