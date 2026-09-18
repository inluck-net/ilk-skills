# The verification record (2026-09-16)

**Status (2026-09-16):** the measure/derive split, the signed record, and the
single-command step 0 are **implemented and shipped**. The three-pass fix loop
(§"What stays open") is **specified and not implemented** — that is the
remaining cost work. Supersedes the implicit design that
`templates/batch-verification-subplan.md` described in prose.

Written after six consecutive stuck verification steps across three projects.
The enforcement half is a separate outward-facing contract:
`skills/ilk-loop/references/detached-component-contracts.md` → **Contract 11**.

## The defect it exists for

The batch-verification step has one job: decide whether this batch broke
anything, and leave behind evidence a human or a release procedure can audit.

It does that through a **record** — a markdown file under
`<ext logs>/verification/<batch-slug>-batch.md` — written by the loop worker in
step 0 and re-read by a strict parser (`verify_attribution.py`) in step 1.

That record is a **data structure an LLM is asked to serialize into markdown by
following prose instructions**, and a **grammar-sensitive parser** then
validates it. Nothing connects the instructions to the grammar.

Measured 2026-09-16 on `verify_attribution.py` against
`verification_record.py`:

| What the gate parses out of the record | Who authors it |
|---|---|
| failure count (`suite_failed: N` or `Suite: N passed, F failed, …`) | worker prose |
| `## At-base rerun` section heading | worker prose |
| one table row per reported failure | worker prose |
| each row's final verdict cell | worker prose |
| the `_(no failures)_` marker | worker prose |
| emptied-field signature (heredoc mangling) | worker prose |
| `verified_head:` | **tool** |

**One of seven.** And `suite_scope:`, which the tool does emit, the gate never
reads.

### The six stuck runs, and which surface each hit

Two prose-authored surfaces, not one. The gate **command** is also written by a
planner from prose, and can be wrong before the record is even reached.

| run | surface | what happened |
|---|---|---|
| gh-resolve 2026-09-15c | A — command | literal `<record path>` never substituted → "record not found" → reverted |
| gh-resolve 2026-09-15d | A — command | identical |
| kira-cloudflare issue-5445 | A — command | the whole gate was `bunx tsc --noEmit`; no record was ever written |
| gh-resolve layer-3 | B — content | verdict cells read `no (fixed)` and `N/A`; the gate read anything-not-`YES` as a pass |
| ilk-skills 20260916-112605 | (not this step) | a work sub-plan's own test; listed only to keep the denominator honest |
| ilk-skills 20260916-123117 | B — content | failure count written as `\| failed \| 24 \|` instead of a line → refused |

Five of six are this design. They are not unrelated bugs.

### Why the 2026-09-16 batch did not fix it

That batch diagnosed surface A correctly — `--batch <batch-slug>` plus
`lint_gate_placeholder_unresolved` — and then applied the same reasoning to
exactly **one** field of surface B. `verified_head` and `suite_scope` moved into
a tool; the other five fields stayed in prose. The next run failed on one of the
five, at the first opportunity.

The structural reason is visible in the template today. Step 0 runs **two
independent gate commands**:

```yaml
- command: "... _resolve_expected_invocation ... subprocess.run(cmd).returncode"   # runs the suite
- command: "... verification_record.py --project . --batch <slug> --compute-scope"  # writes the record
```

The suite's output goes to the **first** command's stdout. The emitter runs
*beside* it and never sees it. So the emitter **cannot** know the failure count.
That is not an oversight in the emitter — it is a data-flow gap in the step, and
it is why that field was left to prose.

## Architecture

### The principle

> **Nothing the gate parses may be authored by prose.**
> The tool measures and writes; the checker re-derives; the worker judges and
> fixes, in sections no parser reads.

### Three roles, cleanly separated

| Role | Component | Owns |
|---|---|---|
| **Measure + write** | `verification_record.py` | every machine-read field, obtained by running things |
| **Re-derive + refuse** | `verify_attribution.py` | applying the attribution rule to those measurements |
| **Judge + fix** | the loop worker | fixing attributed regressions; narrative in unparsed sections |

The writer/checker split is deliberate and must survive: a single component that
both measures and declares its own verdict is the self-graded exam this whole
mechanism exists to prevent. The checker's value is that it re-derives the
verdict from measurements rather than reading a conclusion.

### The at-base rerun is mechanical, not judgment

This is the load-bearing realisation. The template presents the at-base rerun as
a *procedure the worker performs*:

```bash
git worktree add --detach "$WT" "$BASE_SHA"
( cd "$WT" && <suite runner> <failing node id> ... )
```

Every input is available to a program: the failing node ids come from the suite
run, `base_sha` from the master, `baseline_red` from `.ilk-launch.json`. "Did
this node id pass at base" is a **measurement**, not a judgment. There is no
step in it that requires a language model.

So the tool runs it, and the table becomes output rather than input.

### The record's parsed surface, after

The tool writes measurements only. **The `attributed` column disappears.**

```
batch: batch-2026-09-16
verified_head: f0579678743fc59263871bd341e8948546400d2e
verified_tree: 7d3900d2eb80...
suite_invocation: python3 -m pytest --timeout=60 --timeout-method=signal
suite_scope: full
selection_size: 3053
suite_total: 3053
suite_passed: 3022
suite_failed: 24
suite_errors: 7
suite_skipped: 21
base_sha: 1071f04a526e10cea69c189577ed5639184e4d0f
baseline_source: measured | reused from <record>
record_writer: verification_record.py@<version>

## At-base rerun

| node id | at base | in baseline_red |
|---|---|---|
| tests/test_foo.py::test_bar | passed | no |
| tests/test_baz.py::test_qux | failed | no |
```

`attributed` is **derived by the checker**: `at_base == passed AND NOT
in_baseline_red`. Nothing writes a verdict cell, so the `no (fixed)` class is
not fixed — it is **unrepresentable**. There is no cell to put prose in.

`N/A` is likewise gone: the tool writes what it measured. A node id that does
not exist at base is a distinct, named measurement (`absent-at-base`), and the
checker treats it as attributed — a test this batch introduced and that fails
now is the batch's own damage.

### Refusal semantics

Unchanged in spirit, stricter in application:

1. **No `record_writer:` signature ⇒ refuse.** The checker no longer attempts to
   parse hand-written markdown at all. This closes the dialect problem
   permanently rather than by adding a third grammar.
2. **Unparseable field ⇒ refuse**, never read as zero. Existing behaviour; keep.
3. **Tree moved since the record ⇒ no proof written**, loud but not fatal
   (v0.9.105). Keep: the gate must not go red on a diagnosis problem, because a
   red gate reverts correct work.
4. **Absent record ⇒ refuse.** Existing; keep.

### What the worker still owns

Everything that is genuinely judgment, in sections no parser reads:

- **fixing** attributed regressions (step 1's actual work);
- deciding whether a persistent base failure belongs in `baseline_red`;
- the narrative: what broke, why, what was changed.

Findings prose stays valuable and stays unparsed. The rule is not "remove the
worker" — it is "remove the worker from the grammar".

### Data flow, corrected

One command, not two, because the count cannot cross the gap:

```yaml
- command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py \
             --project . --batch <batch-slug> --base-sha <base_sha> --run-suite"
  timeout: <suite timeout>
```

`verification_record.py` then:

1. resolves the invocation (`ship_audit._resolve_expected_invocation`) — already
   implemented;
2. computes the scope — already implemented;
3. **runs the suite** and captures structured results;
4. extracts failing node ids;
5. **runs the at-base rerun** in a detached worktree, per node id;
6. reads `baseline_red`;
7. writes the record, signed;
8. exits non-zero only on its own failure to measure — *not* on a red suite,
   because step 0's job is to record, and step 1's is to judge.

## What measurement showed, and what is still open

**Showed.** One of seven parsed fields is tool-written. Five of six stuck runs
are this design. The emitter cannot see the suite output it is expected to
summarise.

**Open — the cost question.** Step 0 currently pays a full suite, and the
at-base rerun is the *failing selection only* (measured 0.14s for 2 node ids on
gh-resolve 2026-09-07). Moving the rerun into the tool does not change that
cost. The baseline-reuse work shipped in this batch still applies.

**Open — a tool that runs the suite is a tool that can hang.** The single-command
design puts the suite timeout and the record write under one gate. If the suite
exceeds the bound, the record is never written and the step leaves nothing —
which is the failure mode the template's "commit every product fix immediately"
rule already fights. The tool must write a partial, signed record (`suite_failed:
unmeasured`) before it can be killed, and the checker must refuse that record
loudly rather than read it as zero.

**Open — `verify_attribution` shrinks.** Once the record is tool-signed, several
of its tolerant-reader defences become dead code. They should be *kept and
tested* as long as any unsigned record can still reach it, and removed only when
the corpus is clean. Deleting them early re-opens every closed hole.

**Not in scope.** Whether a named test actually covers the change
(`must_add_tests` / tier consistency) — that is a separate tracked defect and a
different mechanism.

## The outward-facing surface, and what may change

**The record is an interface, not an internal file.** Records written by every
project sit on disk under `~/.ilk-data/projects/*/logs/verification/`, and the
checker in this repo reads all of them. A format change here does not break a
feature — it invalidates other people's evidence.

The enforcement half of this mechanism has its own contract, written in the
house style with invariants and a compatibility policy:
**`skills/ilk-loop/references/detached-component-contracts.md` → Contract 11,
"The enforcement scope"**. Read it before touching `ship_integrity`, the
runner's `test_ship_integrity`, or the `--gate-passed` vocabulary. Its lesson
generalises to everything below: *measure the blast radius over the full corpus,
not a recent slice.*

### Two record dialects, and both must keep working

| dialect | written by | read by | status |
|---|---|---|---|
| **signed** — carries `record_writer:` | `verification_record.py` | `derive_attributed` (measurements → verdict) | current |
| **unsigned** — hand-written markdown | a worker following prose | `attributed_rows` (legacy `attributed` column) | **supported, frozen** |

The legacy path is not deprecated-and-scheduled-for-removal. It is **load
bearing**: parked batches on other projects carry unsigned records, and a batch
cannot be un-parked if the checker refuses to read the record it already has.
Deleting that path strands them.

**It may be removed only when the corpus is measurably empty of unsigned
records** — a count over `~/.ilk-data/projects/*/logs/verification/*-batch.md`,
not an assumption that everyone has migrated.

### What may and may not change

**May change freely:** anything the checker does not parse — narrative, the
Findings section, field ordering, headings that are not `## At-base rerun`.

**May change with care:** adding a field the checker reads. Older records lack
it, so absence must be *tolerated* where it is genuinely optional and *refused*
where it is not — and the refusal must say which. `verified_head` is the worked
example: absent ⇒ no proof is written, loudly, rather than a proof for a tree
nobody measured.

**May not change without a migration:** removing or repurposing a parsed field;
changing the at-base table's column meanings; making the checker reject a
dialect it used to accept.

### Two rules that came from getting this wrong

**A reader must be told where to look, and in what shape.** `read_baseline_red`
checked the top level of `.ilk-launch.json` while the list lives at
`ship.baseline_red`, and assumed strings where the schema requires dicts with
`node_id` + `reason` (`ship_config.py:140-157`). Six correctly-declared entries
covering 32 known failures were invisible; every record that day recorded
`in baseline_red: no` for all 35 rows. **An empty answer from the wrong location
is indistinguishable from an empty answer from the right one** — and it was then
published as a finding ("baseline_red is empty") that was simply false.

**A tolerant reader is a silent wrong answer.** `attributed_rows` treated
anything-not-`YES` as not-attributed, so `no (fixed)` and `N/A` passed. In the
signed dialect the `attributed` column does not exist — the checker derives the
verdict from measurements — so that class is not fixed, it is *unrepresentable*.
Prefer removing the cell over validating it.

## How to continue in a fresh session

1. Read this file, then `templates/batch-verification-subplan.md` step 0/1, then
   `scripts/verification_record.py` and `scripts/verify_attribution.py`.
2. The corpus of real records is under
   `~/.ilk-data/projects/*/logs/verification/*-batch.md` — every dialect that has
   ever been written is there, and the migration question ("what happens to
   unsigned records") is answerable by counting them.
3. `docs/retros/retro-2026-09-16-batch-verification-gate.md` has the nine prior fixes
   to this mechanism and why each was too narrow.

## See also

- `docs/retros/retro-2026-09-16-batch-verification-gate.md` — the recurring shape.
- `docs/runtime/ship-gate-design.md` — the release-side consumer of the proof this
  record produces.
- `skills/ilk-loop/references/decomposition-principles.md` §12 — verification
  tiers, and the separate `must_add_tests` contradiction.
