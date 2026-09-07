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
**Order #N (last)** — runs the full test suite for the batch. No other
sub-plan runs the full suite; they stay change-scoped.

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
  - command: "python3 -c \"import sys; sys.path.insert(0,'<skill-root>/ilk-loop/scripts'); from ship_audit import _resolve_expected_invocation; from pathlib import Path; cmd=_resolve_expected_invocation(Path('.')); assert cmd, 'ship.suite not configured'; import subprocess; sys.exit(subprocess.run(cmd,shell=True).returncode)\""
    timeout: <suite timeout>
```

**Resolve the suite command, never hand-type it.** The command above uses
`ship_audit._resolve_expected_invocation(Path('.'))` to compose the one true
invocation from `.ilk-launch.json` → `ship.suite.command + flags`. A hand-typed
command is a copy that can drift — the defect that produced two wrong full-suite
runs on 2026-09-07 (gh-resolve's `--dist loadfile` carried into a repo whose
own baseline measures xdist slower at every worker count).

- Run the project's full test suite (resolved from `.ilk-launch.json` →
  `ship.suite` via the gate above, or `python3 -m pytest --timeout=60
  --timeout-method=signal` if unconfigured).
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
- **Write the record with `Path.write_text`, never a shell heredoc.** An
  unquoted heredoc command-substitutes every backtick in the prose, and a
  verification record is nothing but backticked file paths and test names. On
  gh-resolve batch-2026-09-07 this silently emptied every `**File:**` field and
  spliced the whole of `gh --help` into an error field, because the prose said
  "the monkeypatch captures `gh` calls". The one artifact a human reads was
  mangled precisely where its evidence belonged. If a heredoc is unavoidable,
  quote the delimiter (`<<'EOF'`).
- **Commit an empty marker** (the record lives outside the repo):
  `git commit --allow-empty -m "test(verify): record full suite result for <batch-slug> [plan:<slug>#step-0]"`
- The gate asserts **the external record exists and is non-empty**, not that the
  suite is green — otherwise step 0 can never pass when there is something
  to fix.

### Step 1 — Fix every attributed failure

```yaml
local_checks:
  - command: "python3 <path>/verify_attribution.py <record path>"
    timeout: <suite timeout>
```

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
