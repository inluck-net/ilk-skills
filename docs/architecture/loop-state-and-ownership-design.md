# Loop state and ownership: design

**Status:** accepted 2026-09-28 (Chad). This is the target design, and the
code does not meet it yet. Section 7 lists every current divergence, and
section 8 gives the order in which to remove them. When this document and
`skills/ilk-loop/references/detached-component-contracts.md` disagree on
anything listed in section 7, **this document is the intended behaviour**
and the contracts document describes the code as it stands.

**Why.** On 2026-09-28 the toolkit surfaced about 35 defects in one day, and
three consecutive point fixes each exposed the next defect in the same code.
The defects share five missing sources of truth. Each component re-derives
those truths for itself, from state that has already changed. The evidence
and the per-defect mapping are in the retros:

- `docs/retros/retro-2026-09-28-the-selfmod-batch-could-not-land.md`
- `docs/retros/retro-2026-09-28-an-hour-on-a-pin-that-could-not-pass.md`

**Rule for every change until section 7 is empty:** a fix that adds another
derivation of one of the five truths is a misleading patch, even if its test
passes. Fix by making a consumer read the single source.

## 1. Invariants

| # | Invariant |
|---|---|
| I1 | An iteration's trees (where the worker runs, what gates observe, where plans and runtime live, which code runs) are resolved **once**, before anything reads them, and do not change during the iteration. |
| I2 | What an iteration did (its base, head, commits, targeted steps, gates run, and outcome) is **written once by the driver** and read by every consumer. No consumer re-derives it. |
| I3 | Plan and master state (`status`, `current_step`, `parked_*`) changes only through **one transition API**. The driver verifies after every iteration that nothing else changed it. |
| I4 | "Is a run live, and which processes are its own?" is answered from **one run registry** plus a pid check. It is never answered from argv patterns. |
| I5 | A check's verdict is derived from **all its recorded attempts**, and a red is attributed by measurement at the batch base. A single run never reverts, strikes or parks. |
| I6 | A running loop's code never changes under it. Each run executes a **pinned toolkit snapshot**. |
| I7 | Tests are **hermetic**: no real process table, no real `HOME` / `ILK_DATA_HOME`, no wall-clock races without a seam. A gate lists a test only if that test is hermetic. |

## 2. Iteration context (I1)

`resolve_iteration_context` runs right after the steer gate, before any
snapshot. It writes a read-only context to `RUN_LOG_DIR/ctx-<i>.json`:

```
{clone, exec_tree, observe_tree{repo}, mode: none|selfmod|work_tree,
 plans_dir, runtime_dir, ledger_path, registry_path, data_root,
 code_root, toolkit_sha}
```

- After run start, nothing writes `PROJECT_PATH`, `SELFMOD_*` or
  `ILK_SKILL_HOME`. Phases read the context.
- **Selfmod is a persisted state machine**: `NONE → ISOLATED →
  MERGE_DEFERRED → MERGED`. It is derived from disk (the worktree directory
  and its marker) and has one transition function, shared by the entry retry
  and the post-iteration merge. MERGED re-enters ISOLATED for the next
  iteration and never leaves a worker pointed at the live clone.
- `code_root` (the code being run) is separate from `registry_path` and
  `data_root` (live data). A gitignored registry must never be looked up
  under the code root.

## 3. Iteration record (I2)

The driver writes the record at two points in each iteration:

- **Open**, before dispatch:
  - `run_id`, iteration, context
  - `base_commit`, `base_tree`
  - `master` (a pinned path, never re-selected)
  - the dispatched slug and step
  - a map of every non-shipped slug to its step
  - fingerprints of the targeted sub-plan and of the master: body above
    `## Findings`, excluding `current_step` / `status` / `last_updated`
- **Close**, after gates, ship-integrity and merge:
  - `head_commit`, `head_tree`
  - `commits[]`, computed once in `exec_tree`
  - `step_to` for each slug, read from the pinned master's files
  - `gates[{slug, step, cmd, attempts[{rc, cmd_rc, duration, node_ids}], verdict}]`
  - `merge` outcome, `stop_reason`

Consumers:

| Consumer | Reads from the record |
|---|---|
| gate targeting | `dispatched` + `step_to` |
| ship-proof ledger | a projection of the close record. Its existing fields and shape stay; any new fields are additive. |
| ship-integrity | `gates[].verdict` |
| ship audit | `commits ∩ ancestors(HEAD)` (not `git log --all`) |
| batch verification base | the first `base_commit` of the batch |
| `collect.py` | its counts |
| `batch-gate.json` | gains batch and master identity |

The JSONL loop log's `started` and summary records are the closest existing
artifact, so the record is written there first, additively.

## 4. Plan state ownership (I3)

- **API:** `plan_state.transition(path, from_, to, actor, reason)`:
  - re-reads the file and compares its current value with `from_`;
  - refuses to leave `parked` or `draft` unless `actor=human`;
  - appends each transition to `state-transitions.jsonl`.

  It is the only writer of status fields. `promote_next_master`,
  `park_master`, `reconcile`, `ship_transition`, `quarantine`, the runner's
  reverts and `advance_subplan_current_step` all call it.
- **Workers never write status.** They request a ship or a step bump through
  the API. The worker prompt (`commands/ilk.md`), `ilk-loop/SKILL.md` and
  the templates all say that one thing.
- **Enforced by the driver:** at every iteration close, the driver diffs the
  frontmatter of every plan **and master** against the open snapshot plus the
  transition log. It reverts any change the log does not explain and records
  a violation. This applies under every profile.
- **`parked` and `stalled` are separate states.**
  - `parked` is a human hold. **Decision (Chad, 2026-09-28): a park stops
    the whole project.** Nothing else of that project is promoted or
    dispatched unless the park carries `yield: true`.
  - `stalled` is the loop's own blocked state. It yields the slot per L4.
  - The token `blocked` stays readable for backward compatibility.
- **Selfmod promotion:** nothing is promoted while the selfmod worktree
  holds commits that are not on the clone's HEAD.

## 5. Run registry (I4) and toolkit snapshot (I6)

**Run registry.**
- The lock winner writes `runtime/launcher/runs/<run_id>.json` with
  `{run_id, project_key, pid, pgid, lstart, toolkit_sha, clone, exec_tree,
  state}`. The runner removes it on exit.
- At dispatch, before the lock, the launcher writes `state=launching` with
  an expiry, which closes the launch-to-lock gap.
- Every liveness question (scheduler busy, watchdog alive, stop, bounce,
  merge guard, migration) is answered from the registry plus a check that
  the pid, `lstart` and pgid all match.
- `stop` kills by pgid from the record.

**Toolkit snapshot.**
- **Decision (Chad, 2026-09-28): adopt it.** Each run executes from a
  `code_root` pinned at its start: a detached worktree, or a `git archive` of
  the clone HEAD keyed by `toolkit_sha`.
- A merge into the clone then cannot change a running loop's code, so the
  merge guard reduces to the registry and a merge no longer waits for every
  loop on the host.
- This relaxes gh-resolve's "no merge while any loop runs the clone" rule.
  **It is built only after gh-resolve agrees** (standing agreement).
- Workers keep reading skills from the clone when a session starts. A change
  between iterations is intended (it is the deploy). A change during an
  iteration is caught by the fingerprints in the open record, which end the
  iteration.

## 6. Verdict policy (I5, I7)

- Every gate keeps all of its attempts.
  - Red on every attempt ⇒ `red`.
  - Mixed results ⇒ `flaky`.
  - Stopped by the driver's cap ⇒ `inconclusive`.
- **Only `red` may revert, strike or park.**
- `flaky` is recorded and counted per check id. **Decision (Chad,
  2026-09-28):** it escalates to blocking at **≥2 flaky results in that
  check's last 10 runs.**
- **A strike needs attribution.** The failing node ids are re-run in a
  detached worktree at `base_commit`:
  - green at base, red at head ⇒ owned by the batch, and a bisect over
    `commits[]` with the real command names the commit;
  - red at base ⇒ pre-existing. The step advances, and the ship-proof row
    carries an additive `pre_existing_red` field, so the sub-plan reads
    unproven with a named reason.
- **Hermetic tests (I7)** are enforced by lint. A test that probes the
  process table, the real data home, or timing without a seam is not
  gate-eligible.
- **Plan-lint rule:** an acceptance criterion of the form "the runner
  contains string X" is not a runtime check.

## 7. Current divergence (HEAD `90513da`)

**D1, context (I1):**

| Divergence | Where |
|---|---|
| heads-before, the pre-iteration snapshot and the target run before selfmod isolation is set | `run_ilk_loop_claude.sh` :4212, :4225, :4245 vs :4343 |
| isolation is re-detected each iteration from a mutated `PROJECT_PATH` and drops after a deferral | :4343, :438-443 |
| after a successful retry merge the worker would be dispatched into the live clone. Dormant: no marker exists. | :4363 |
| two code roots in one process | `_SKILL_ROOT` :17 vs `ILK_SKILL_HOME` :485 |
| the registry is looked up under the code root (#57) | `ship_integrity.py:356-378` |
| meta-project snapshot entries overwrite each other | :4219, :4225 |
| `$plans_dir` is unset in `main`'s red-owner block (#56). A fix exists on branch `fix/red-owner-plans-dir` (`b3133b1`, not on main); reuse it. | :4741, :4825 |

**D2, record (I2):**

| Divergence | Where |
|---|---|
| 9 independent definitions of an iteration's work; the active master is re-selected 4 times | :1116, :1432, :2672, :4723 |
| the ledger is written before the gate, so `gate_pass_at_head` is unreachable | :4498 vs :4635 |
| the JSONL iteration record is written before ship-integrity and merge | P21 before P23/P24 |
| `est_steps` leaks across slugs | :1478 |
| the ship audit reads `git log --all` | `ship_audit.py:224` |
| three different verify-base definitions; `ILK_RUN_BASE_SHA` is never set | `run_result.py:168` |
| one `batch-gate.json` per project is applied to every master | |

**D3, ownership (I3):**

| Divergence | Where |
|---|---|
| about 10 writers of plan state, and only `advance_subplan_current_step` does a compare-and-set | |
| workers are told to write status | `commands/ilk.md:78, :187-188`; `SKILL.md:201-204` |
| masters are not snapshotted | `PRE_ITER_ALL_STEPS` covers sub-plans only |
| the one-ship check skips `MASTER*` | :5091 |
| `reconcile` un-parks a master that a worker has marked shipped | `plan_status.py:553-557` |
| park and stall share `blocked` | `park_master.py:47` |
| promotion ignores the shared selfmod worktree | |

**D4, liveness (I4, I6):**

| Divergence | Where |
|---|---|
| 15 liveness mechanisms; `run.lock` is authoritative and unread | |
| `running.pid` is written about 14 s after dispatch | |
| the watchdog relaunches a stopped run with `--force`. Observed twice on 2026-09-28: 22:58:25, and 23:03:59 after an operator stop. | `watchdog.sh:495-500, :628` |
| the scheduler reads a dead sentinel path | `scheduler.sh:295, :932` |
| the scheduler's own files hardcode `${HOME}/.ilk-data` | `scheduler.sh:21-22, :137` |
| `stop` kills by path substring | `stop.sh:201` |
| the merge guard uses argv patterns and starves merges while any loop runs | `selfmod_worktree.py:364-430` |
| each dispatch's `--master` is the last-scanned project's | `scheduler.sh:899, :1112-1115, :1181` |

**D5, verdicts (I5, I7):**

| Divergence | Where |
|---|---|
| ship-integrity reads the first run and ignores the confirm re-run | :4648-4700, :2730 |
| "at base" runs at HEAD | :4780 |
| the bisect runs an empty command | :4794-4797; `red_owner.py:27-35` |
| quarantine strikes every slug in the first-run file | :4866 |
| host-reading and timing tests sit in gates | e.g. `test_doctor.py::TestProgressOverTimeGate::test_growing_file_is_progressing` |
| only exit 124 preserves a killed worker's work | :3334-3341, :4498 |
| text-grep ACs are accepted as runtime checks | 28c sub-plan 0 AC-2/AC-5 |

## 8. Order

1. **Stabilise, by hand, supervised.** Decision (Chad, 2026-09-28): not
   through the selfmod loop, which is the most divergent part.
   - D1 context: move the resolution ahead of every snapshot; the selfmod
     state machine with correct re-entry; the #56 fix.
   - D5: ship-integrity consumes the confirm attempt; delete the HEAD-run
     "at base" and the empty-command bisect (refuse instead); deflake
     `test_doctor`; exit ≥128 counts as incomplete for WIP preservation.
   - D3: add masters to the snapshot and verify them at close; promotion
     refuses while a park is unyielded or the selfmod branch is unmerged.
   - D4: the watchdog honours the registry / `launching` state and never
     relaunches an operator stop; the scheduler's data-home paths; the
     per-dispatch master.

   Each item gets a targeted test that fails without it, and one full suite
   runs before the commit.
2. **Confirm smooth looping** on a consumer batch, then on a small
   ilk-skills batch.
3. **The remaining design, as small masters, in dependency order:**
   - the D2 record, migrating one reader per sub-plan;
   - the D5 verdict policy;
   - the D3 transition API;
   - the D4 registry, then the toolkit snapshot once gh-resolve agrees.

   The parked MASTER-2026-09-28c and -28d are re-planned from these steps.
   Their sub-plans map as follows:
   - **kept:** 28c-1, 28c-7 (hook part), 28c-8, 28d-2, 28d-3;
   - **replaced:** 28c-2, -3, -4, -5 and -6 by D3, D1/D2, D2 and D5;
     28d-1 by D4;
   - **folded in:** 28d-4 into D2;
   - **superseded:** 28c-0 by the D1 selfmod state machine.
