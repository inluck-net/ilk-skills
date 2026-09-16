---
plan: <short-slug>-verify
batch_verification: true
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: YYYY-MM-DD
verification_tier: loop-verified
regression_for:
depends_on: []
data_prereqs: []
env_prereqs: []
recommended_iteration_timeout_min: 90
local_checks: []
scope_paths:
  - "<glob/prefix for files this batch touched>"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: github
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: batch verification — full suite

Part of [MASTER-YYYY-MM-DD-execution-plan](./MASTER-YYYY-MM-DD-execution-plan.md).
**Order #N (last)** — runs the batch-wide verification, scoped to the batch's
changed area (see step 0) and falling back to the whole suite when that scope
cannot be established. No other sub-plan runs a broad gate; they stay
change-scoped.

This sub-plan is **not optional**. A master without a batch-verification
sub-plan is a HARD lint finding (`lint_master_has_verification_subplan`).

## Before you start

1. The MASTER plan referenced above — workstream map, cross-cutting rules,
   execution rationale.
2. `skills/ilk-ship/SKILL.md` Phase 1-2 — the baseline-diff and
   attributed-regression rule this sub-plan reuses.

## The exit condition

**"No failure attributed to this batch"**, not "zero failures".

`.ilk-launch.json` carries a `baseline_red` list — entries that fail on
this platform and are not caused by this batch (e.g. Windows-only tests
on macOS). "Until all pass" would send an agent chasing those, and the
cheapest way to "succeed" is to weaken a test.

**Attribution rule** (from `/ilk-ship` Phase 1): a failure is attributed
to this batch iff it **fails now**, **passed at the batch's base commit**,
and is **not in `baseline_red`**.

**Fix-until-green needs no new machinery.** A step whose gate is red does
not advance — `current_step` stays put, the failure output lands in
Findings, and the next iteration retries. The bound is
`quarantine_subplan.py`'s threshold of 2 — two confirmed reds flip the
sub-plan to `status: blocked` with the failures named.

## The at-base rerun — the only thing that exonerates a failure

The attribution rule has three terms, and two of them are free: "fails now"
comes from the suite output and `baseline_red` is a list on disk. **The middle
term — "passed at the batch's base commit" — is the only one that requires an
act, and it is the one that gets skipped.** Skipping it does not leave the
question open; it leaves it to be answered by argument, and an agent asked to
argue about its own batch will find a reason.

So: **a failure is exonerated by a measurement, never by an explanation.** For
every failing node id, re-run *that node id* at `base_sha` in a detached
worktree:

```bash
BASE_SHA=<the master's recorded base_sha>
WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" "$BASE_SHA"
( cd "$WT" && <suite runner> <failing node id> [<failing node id> ...] )
git worktree remove --force "$WT"
```

This is cheap — it is the failing selection, not the suite. Measured on
gh-resolve batch-2026-09-07: **2 failing node ids, 0.14s at base.** The
narrative that replaced it that day cost 43s of reasoning and five greps, and
was wrong on both failures.

Record the outcome as a table in the record file, one row per failing node id,
under the exact heading `## At-base rerun` (step 1's gate parses it):

```markdown
## At-base rerun

Base: <base_sha> · worktree: detached · command: <suite runner> <node ids>

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_foo.py::test_bar | passed | no | YES |
| tests/test_baz.py::test_qux | failed | no | no |
```

- `at base: passed` and not in `baseline_red` ⇒ **attributed**. There is no
  third column that makes it not-attributed.
- `at base: failed` ⇒ not attributed, and the row is its own evidence. Add it
  to `baseline_red` if it will keep failing.
- **The table must have exactly one row per failure.** Step 1's gate asserts
  `rows == failed`, so a record that reports 2 failures and explains them in
  prose cannot pass.

**No prose overturns a row.** "Environmental", "pre-existing", "flaky",
"a line-number shift", "the batch did not touch that file" are hypotheses about
*why* a test broke — they are the beginning of the fix, not grounds to set the
count to zero. If one of them is true, the rerun says so: the test fails at base
too. If the rerun says it passed at base, the batch broke it, and which commit
did it is found by `git bisect`, not by reading the diff and forming a view.

**A project-specific amnesty binds to `failed == 0`, never to the exit code
alone.** Some projects have a known non-zero exit with an empty failure list — a
host-level guard tripping on a live mutation, say. Where this sub-plan documents
one, it must say so as `exit != 0 AND failed == 0`, because an amnesty written
about the exit code is an amnesty an agent will apply to a run with real
failures. That happened on gh-resolve batch-2026-09-07: the sub-plan carried a
section headed "exit 1 with zero failures is not a regression", the run recorded
`Failed | 2`, and the section was invoked anyway.

## Objectives

1. Run the full test suite for this batch.
2. Re-run every failing node id at `base_sha` and record the verdicts.
3. Fix every attributed failure until zero remain.

## Steps

### Step 0 — Run the full suite, record the result

```yaml
local_checks:
  - command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py --project . --batch <batch-slug> --base-sha <base_sha> --run-suite --suite-timeout <suite timeout>"
    timeout: <suite timeout>
  - command: "python3 -c \"import sys; sys.path.insert(0,'<skill-root>/ilk-loop/scripts'); import verify_attribution as va; rec=va.resolve_batch_record(__import__('pathlib').Path('.'),'<batch-slug>'); text=rec.read_text(errors='replace'); assert not va.has_emptied_record_fields(text), f'record {rec.name} carries heredoc-emptied fields — rewrite with Path.write_text, not a shell heredoc'\""
    timeout: 30
```

**ONE command, and you do not write the record.** `verification_record.py`
resolves the suite invocation, runs it, re-runs every failing node id at
`base_sha` in a detached worktree, reads `baseline_red`, and writes the whole
record itself — signed with `record_writer:`.

This replaced two independent commands (run-the-suite, then write-the-record)
whose defect was that **no data flowed between them**: the suite's results went
to the first command's stdout, so the emitter could not know the failure count
and that field was left to prose. Five of six stalled verification runs across
three projects on 2026-09-15/16 were a mismatch between prose a worker wrote and
the grammar the step-1 gate parses. See `docs/verification-record-design.md`.

**Do not hand-write any field above `## Findings`.** Everything the gate parses
is emitted. Findings is yours and no parser reads it. A record you typed is
unsigned, and the checker falls back to a legacy path kept only for batches
parked before this change.

**A record is written twice on purpose.** A signed stub carrying
`suite_failed: unmeasured` lands *before* the suite starts, so an iteration
killed at its bound leaves something the checker can refuse loudly, rather than
nothing at all — which reads as "step 0 never ran" and costs another iteration
to diagnose.

**Resolve the suite command, never hand-type it.** The command above uses
`ship_audit._resolve_expected_invocation(Path('.'))` to compose the one true
invocation from `.ilk-launch.json` → `ship.suite.command + flags`. A hand-typed
command is a copy that can drift — the defect that produced two wrong full-suite
runs on 2026-09-07 (gh-resolve's `--dist loadfile` carried into a repo whose
own baseline measures xdist slower at every worker count).

**Commit every product fix the moment it passes — never batch them behind the
record.** Getting a track to yield a meaningful result routinely forces real code
changes: a typecheck error or a failing assertion has to be fixed before the
suite says anything useful. Each such change is a product fix and gets its own
commit, immediately:

```
git commit -am "fix(<scope>): <what changed> [plan:<slug>#step-0]"
```

Commit when the command that was failing *because of that change* now passes.
This is a checkpointing rule, not a licence to commit broken code. Several
commits carrying the `#step-0` trailer is correct; nothing in the loop requires
one commit per step, and the empty marker at the end still marks completion.

**Why, measured.** This step's gate asserts the verification record exists, and
the record is written at the very END of the step. With only the terminal marker
commit, an iteration that runs out of time leaves **nothing**: no commit, no
record, an empty Findings section — and the gate then fails "record missing" on
the next iteration too. At `quarantine_subplan.py`'s threshold of 2 consecutive
failures the sub-plan is auto-blocked, and the run exits `blocked-no-runnable`.

Observed on kira-cloudflare 2026-09-15, run `20260915-123334` iteration 5: 45.0
min, 106 tool calls totalling 25.7 min (three suite runs at 153s/301s/302s, all
of which completed with real output, five typechecks, `convex codegen`, a fresh
at-base worktree needing a 151-package install), real fixes across 6 files —
and **0 commits**. Iteration 4 of the same run made 13 commits in 43 min and
advanced normally. The sub-plan was auto-quarantined two gate-failures later.

The failure is invisible without this rule: it presents as a slow step rather
than a step that discards its own progress, and the natural response — raising
the iteration timeout — buys a longer iteration that still ends in
commit-or-nothing.

**On re-entry, do not start over.** Before re-running anything, check what
already exists: the record file, the sha-keyed baseline cache, and
`git log --grep '\[plan:<slug>#step-0\]'` for fixes an earlier iteration
already landed. Re-run only the tracks whose results you do not have.

- **Scope the run to the batch's changed area, and say so in the record.**
  Resolve the invocation from `.ilk-launch.json` → `ship.suite` (via
  `ship_audit._resolve_expected_invocation`, never hand-typed), then restrict it
  to a selection derived from the batch's own diff:

  1. `git diff --name-only <base_sha>..HEAD` — the batch's changed files.
  2. The test files among them.
  3. **Plus the test files of every module that imports a changed module.** This
     is the load-bearing half. A selection of only the batch's own test files is
     the per-file-gate anti-pattern (§8): it re-tests what the batch wrote and
     never exercises the callers a shared-module change can break.
  4. Run the resolved invocation restricted to that selection.

  **If the importer set cannot be computed, run the full suite.** An unresolved
  import graph is not an empty one, and a narrow run justified by a failed scan
  is exactly the "empty answer nobody looked for" shape this batch's own gates
  exist to prevent. The same applies when the diff touches build config,
  fixtures, conftest, or anything global: widen to the full suite rather than
  reason about blast radius.

  Record the decision as a machine-readable line, `suite_scope: scoped` or
  `suite_scope: full`, plus the selection size. A green result means different
  things under each, and a reader of the record must not have to infer which.

  **What scoping gives up.** A scoped run cannot see breakage outside the
  importer set. It is a deliberate wall-clock trade: measured on kira-cloudflare
  2026-09-15, the verification tracks were 7.5 min of a 27.2 min iteration
  (27.5%), against 19.5 min (71.6%) of model/API latency — so scoping buys back
  roughly a quarter of an iteration, not the bulk of it. Do not let it become a
  reason to skip the at-base rerun below, which is what makes the verdict a
  measurement rather than an opinion, and which costs seconds.
- Record the result: which tests failed, which passed, which were skipped.
- **Re-run every failing node id at the base commit** and write the
  `## At-base rerun` table — see "The at-base rerun" above. Do this even when
  the failure looks obviously unrelated; that judgment is exactly what the
  rerun exists to replace. When the suite is green the table is empty and says
  so (`_(no failures)_`).
- **Reuse the base-commit baseline when it already exists.** The base sha is
  fixed for the batch and identical on every host, so re-measuring it from
  scratch each batch pays a full suite run to learn something already known.
  **This cached baseline does not replace the at-base rerun.** It is the
  aggregate — counts, and a failure list that is normally empty. An empty
  baseline failure list is not evidence that a failing test passed at base; it
  is the reason every failure is attributed by default, and the rerun is what
  confirms it per node id. Reading `failed: 0` out of the cache and reasoning
  from there is the shortcut that shipped gh-resolve batch-2026-09-07 green.
  Look for `<external logs>/verification/baseline-<base-sha>.json` first; only
  measure when it is absent, and write it back at that sha-keyed path:
  **Resolve the base from where the BATCH began, not from the branch it
  commits onto.** `git merge-base HEAD <base_branch>` is WRONG here and fails
  silently: when the loop commits onto `base_branch` — the normal case for a
  project whose loop commits to `main` — merge-base returns HEAD itself, so
  the "baseline" is measured on the batch tree and the attribution compares
  HEAD against HEAD. Verified in ilk-skills 2026-09-06: `merge-base HEAD main`
  == HEAD exactly. Observed on gh-resolve batch-2026-09-06 as byte-identical
  baseline and batch records (4382 passed / 192.70s in both) — one suite run
  filed under two names.

  A green suite hides this completely. "0 attributed regressions" comes out
  correct from an empty method, and the step only bites on the run where
  something actually fails, which is the run it exists for.

  Take the base from the master's recorded base sha, or the tag the batch
  started from — some value fixed BEFORE the batch's commits existed. Then
  assert it, because any resolution scheme can go degenerate the same way:

  ```python
  base_sha = resolve_batch_base_sha()   # master's recorded base / start tag
  head_sha = subprocess.run(["git", "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
  # Fail loudly rather than silently self-comparing. This assert is the
  # load-bearing half: it catches every future base-resolution mistake,
  # not just the merge-base one.
  assert base_sha and base_sha != head_sha, (
      f"degenerate base resolution: base {base_sha} == HEAD {head_sha}; "
      f"the comparison would be HEAD against itself and could not detect "
      f"an attributed regression"
  )
  cached = verification_dir / f"baseline-{base_sha}.json"
  if cached.is_file():
      baseline = json.loads(cached.read_text())   # no suite run needed
  else:
      baseline = measure_base_commit()            # detached worktree, full suite
      cached.write_text(json.dumps(baseline, indent=2))
  ```
  Key it on the **sha**, never on the batch slug or the tag — two batches off
  the same base must hit the same entry, and a cache keyed on anything that
  moves independently of the tree silently returns another commit's results.
  A cache miss is normal and costs exactly what today costs; state which
  happened in the record.
- Compare against `.ilk-launch.json`'s `baseline_red` list to exclude
  pre-existing platform failures.
- **Write the record to the external logs directory** (not into the project tree):
  ```python
  from skills.ilk_loop.scripts.ilk_paths import external_logs_dir, resolve_project_key
  key = resolve_project_key(Path.cwd())
  verification_dir = external_logs_dir(key) / "verification"
  verification_dir.mkdir(parents=True, exist_ok=True)
  ```
  Write `<batch-slug>-baseline.md` and `<batch-slug>-batch.md` to that directory.
- **The record must name the commit its suite ran on, machine-readably:**
  ```
  verified_head: <full sha of HEAD when the suite ran>
  ```
  Step 1's gate resolves that commit's **tree** and refuses to write a proof
  when the tree has since moved — otherwise a gate re-run stamps `verdict:
  pass` over code no suite has seen. It compares trees, not heads, so the empty
  marker commits this step makes are correctly ignored.

  **A record with no `verified_head` cannot be proven**, by design: unknown is
  not the same as unchanged. Surveyed 2026-09-16, 6 of 15 existing records
  across all projects carried any head line at all, in 6 different spellings,
  two of which were prose (`current main`, `asserted and confirmed`) — which is
  why the field is now mandatory and parsed strictly as a sha.
- **Write the record with `Path.write_text`, never a shell heredoc.** An
  unquoted heredoc command-substitutes every backtick in the prose, and a
  verification record is nothing but backticked file paths and test names. On
  gh-resolve batch-2026-09-07 this silently emptied every `**File:**` field and
  spliced the whole of `gh --help` into an error field, because the prose said
  "the monkeypatch captures `gh` calls". The one artifact a human reads was
  mangled precisely where its evidence belonged. If a heredoc is unavoidable,
  quote the delimiter (`<<'EOF'`). **Step 0's gate refuses a record that
  carries the mangling signature** — the third `local_checks` command calls
  `verify_attribution.has_emptied_record_fields`, which keys on the output
  shape (a list item or `**Field:**` whose value is empty or starts with a
  separator) rather than a field whitelist, so the next variant is caught too.
  Detected on batch-2026-09-15d (every backticked value gone, the step-1 gate
  passing silently because it reads `suite_failed` and the at-base table,
  neither of which is affected).
- **Commit an empty marker** (the record lives outside the repo):
  `git commit --allow-empty -m "test(verify): record full suite result for <batch-slug> [plan:<slug>#step-0]"`
- The gate asserts **the external record exists and is non-empty**, not that the
  suite is green — otherwise step 0 can never pass when there is something
  to fix.

### Step 1 — Fix every attributed failure

```yaml
local_checks:
  - command: "python3 <skill-root>/ilk-loop/scripts/verify_attribution.py --batch <batch-slug>"
    timeout: 120
```

**`--batch` takes the same `<batch-slug>` step 0 writes its record under**, and
resolves `<ext logs>/verification/<batch-slug>-batch.md` itself. Do not pass an
absolute record path: the external logs dir differs per host, so a path baked
into the plan is correct on the machine that planned the batch and wrong on the
other one — a conventional path where a resolved one belongs.

This gate used to take a record path, and that placeholder was the only one in
the template with no mechanical value to fill. On 2026-09-15 the
planner resolved `<skill-root>` and `<batch-slug>` in the same file and left it
literal on **both** gh-resolve batches of that day. The gate then failed as
"record not found" — indistinguishable from *step 0 never wrote its record* —
and ship-integrity reverted each sub-plan from `shipped` back to `in-progress`
**after its suite had run green**. The script now refuses any argument still
containing `<...>` and says to fix the plan file rather than re-run the suite.

On a clean verdict it also **records the proof** — it writes
`runtime/batch-gate.json` (verdict, the resolved invocation, `head_sha`,
`tree_sha`, `excused_count`), which is what `loop_status` and `ship_audit`
actually read. Before v0.9.104 the sub-plan wrote its record under
`logs/verification/` and nothing bridged the two, so a batch that verified green
still reported `SHIP PROOF MISSING` — measured on two projects the same day.
It passes `--project` as the cwd by default; add `--no-write-gate-record` only
if you deliberately want to verify without recording proof. A failed
verification writes nothing, leaving the previous record to be caught as stale.

`verify_attribution.py` is a real script in the toolkit — resolve
`<skill-root>` and `<batch-slug>` and leave the rest alone. **Do not inline your
own copy of this check.** It has two subtleties that were each got wrong once on
2026-09-15: the verdict is the row's **last cell** (a substring search for `YES`
also matches the `yes` in `in baseline_red`, failing a correctly-exonerated row),
and a record whose failure count cannot be parsed must **refuse** rather than
read as zero. One tested implementation beats a copy per sub-plan — the same
reason step 0 resolves the suite command instead of hand-typing it. The timeout
is 120s because this parses a file; it does not run tests.

The gate **re-derives** the verdict from step 0's `## At-base rerun` table. It
must NOT grep the record for a sentence like `Attributed regressions: 0` — that
number is a conclusion the same agent wrote in the same breath as the failures
it was excusing, so a gate that reads it is a self-graded exam. Assert three
things, all of them measurements:

1. The record exists and is non-empty. **Missing ⇒ fail**, never skip.
2. It carries an `## At-base rerun` section, and that section has **exactly one
   row per failure** the record reports (`rows == failed`). This is the load-
   bearing assertion: it is what makes "2 failures, explained in prose, count 0"
   impossible.
3. **No row is marked attributed.** A row reading `passed | no | YES` is red.

- **When the table attributes nothing, this step is a NO-OP.** Do not re-run the
  suite — step 0 already ran it on this same tree, and a second identical run
  answers no new question. Commit the empty marker and move on:
  ```
  git commit --allow-empty -m "fix(verify): no attributed regressions [plan:<slug>#step-1]"
  ```
- Otherwise — the table attributes at least one failure — fix each one, re-run
  the suite, re-run the failing selection at base, and update the table. Find
  the culprit commit with `git bisect` over the batch's own commits; on
  gh-resolve batch-2026-09-07 that was four reruns of one 0.07s test and it
  named the commit exactly. Do not infer it from the diff.
- **Never make a test pass by weakening it.** If a test from this batch blocks a
  correct fix, read it — it may be the test that is wrong, in which case update
  it to the new contract with a comment saying why. That judgment goes in
  Findings.
- Red gate ⇒ retried next iteration (current_step stays at 1).
- Two confirmed reds ⇒ `status: blocked`, naming the failures.
- Commit: `fix(verify): resolve attributed regressions [plan:<slug>#step-1]`

## Findings

_(filled by the loop during execution)_

## Reference reading

- `skills/ilk-ship/SKILL.md` Phase 1-2 — baseline-diff and attributed regressions.
- `skills/ilk-loop/scripts/quarantine_subplan.py` — the bound on the fix loop.
- `.ilk-launch.json` — `ship.suite` command and `baseline_red` list.
- `skills/ilk-loop/scripts/wait_for_background_output.sh` — how to read a long
  gate's output without re-launching it.
- `plan_lint.py` → `lint_verification_attribution_unmeasured` — the HARD finding
  that rejects this sub-plan if the at-base rerun is dropped when it is authored.
