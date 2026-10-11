# Loop state and ownership: design

**Performance extension (2026-10-11):**
[Loop execution performance](loop-execution-performance-design.md) specifies
model-specific thinking/turn measurements, additive JSONL fields, and compact
worker dispatch. The review snapshot preceded canonical I1/I2; the later
manual owner candidate implements context and supported Mac close records.
Gate-outcome reuse remains removed; stalls remain observation-only. New
code-only/drain mechanisms stay opt-in with explicit platform/entry refusals.
The implementation tracker distinguishes installed capabilities from candidates.

**Status:** accepted 2026-09-28 (Chad), with later manual owner implementation.
Section 7 preserves the historical divergence snapshot at HEAD 90513da; it is
not the current remaining-work list. Section 8 preserves the intended order.
See [the durable tracker](../implementation/loop-execution-improvement-plan.md)
for current evidence and unresolved adapters. Where historical detached
contracts disagree with an accepted invariant, this design defines the target;
only measured implemented behavior may be claimed as delivered.

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

### Scoped acceptance across successive subplans (owner candidate, 2026-10-11)

Measured with two real driver gate/commit/CAS transactions in a disposable Git
worktree: the second independent accepted commit made the first ship audit
return `stale_head`. Requiring every earlier subplan's accepted tree to remain
the final tree prevents an ordinary multi-subplan batch from finishing.

The candidate preserves **historical scoped acceptance**, not a current-tree
suite verdict. Each later code commit must have its own positive closed driver
acceptance, with complete passed gates, integrity, landing, exact parent/base
and accepted tree, and unchanged selected master and plan contracts. The chain
is single-parent; only corresponding empty ship markers may intervene. Unknown
commits, merges, unclosed/red steps, missing identities and changed contracts
refuse. New steps still run their authoritative gates against their actual tree.
No test outcomes or aggregate counts are carried or merged: plan 07m R1/R2
remains binding. A historical accepted step does not establish that every earlier
requirement still passes at the final tree; final verification remains opt-in.

New candidate fields `acceptance_base_commit` / `acceptance_base_tree` are
projected by `iteration_record.py`; `ship_audit.py` validates the continuation.
Historical rows without these fields can prove their exact accepted tree but
cannot authorize a changed-tree continuation. Scoped continuation and the
fresh-base selfmod landing adapter are checked in the locally installed
2ed78097 preview; member-repository acceptance remains unsupported in the new
owned profile. Archived multi-subplan and selfmod caller fixtures passed (see
the durable tracker for checkpoint-specific output). Later owner candidates
remain uninstalled. Negative fixtures continue to reject unaccepted code and
corrupted or changed contracts; this does not authorize gate-outcome reuse.

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
- **Revised target, 2026-10-11:** workers read toolkit skills and commands from
  the same pinned release root as their driver, with hooks, settings and
  plan-invoked scripts included in the pinned inputs. Mutable worker-home
  links/settings must not introduce version skew. A drain-after-step exit and
  scheduler relaunch is the activation candidate; it is unbuilt and requires
  consumer mappings before adoption. Otherwise use normal exit and a new
  launch. Fingerprints remain a backstop for plan changes, not a mechanism
  for hot-upgrading toolkit code.

**gh-resolve's conditions** (gh-resolve-59, 2026-09-28; agreed, and binding on
the implementation):
1. `runtime/launcher/{run.lock, running.pid, last-exit.json, ship-proof.jsonl}`
   keep their paths and shapes. `runs/<run_id>.json` is **additive**. Having
   reap read the registry instead would be a separate contract change,
   proposed first.
2. `toolkit_sha` goes on the run's terminal record (a `last-exit.json` field,
   joinable by `run_id`), so every artifact names the ilk version that wrote
   it.
3. The worker's cwd stays the project worktree. gh-resolve's reconcile finds
   workers by cwd (`lsof -d cwd`), because a worker's argv carries no path.
4. **Version skew between a pinned runner and plan-invoked scripts:**
   - Plans invoke ilk scripts by clone path (`~/.claude/skills/ilk-loop/scripts/…`).
   - The fix is that the runner exports its pinned root as `ILK_CODE_ROOT`,
     and every plan-invoked ilk script is resolved through it. That covers
     ilk templates, `/ilk-plan` output and gh-resolve's `handoff.py:1532`.
   - A plan that hardcodes `~/.claude/skills` becomes a lint finding.

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

## 7. Historical divergence snapshot (HEAD `90513da`)

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

   Each item gets a targeted test that fails without it. The original
   2026-09-28 order also required a full suite before commit; the current
   2026-10-11 operator preference supersedes that default. Use focused
   component/caller/invariant and wiring checks; explain a concrete need for
   broader evidence before changing that approach.
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


### 2026-10-11 owner candidate: exact selfmod landing and recovery

New candidate mechanism `selfmod_landing.py:39` supports a clean source clone
whose frozen base equals the execution base. It independently validates the
accepted receipt, exact commit chain/tree, source branch/base, shared Git store
and host registry before a bounded fast-forward. It retains the worktree through
close. Historical unmerged bases, moved/dirty source inputs and unsupported
platforms refuse; no generic WIP merge or daemon restart is implied.

`selfmod_state.py:41` now distinguishes a retained landed worktree from a newly
created one at the same path and commit using its Git-indirection identity.
Uncommitted work returns to ISOLATED; only the exact clean landing can establish
MERGED. New optional deadline/guard handling in `plan_state.py:25` bounds owned
state-lock readiness. Existing registry locking retains its prior contract.
State Git queries use bounded children; `acceptance_recovery.py:65` retains the
recovery caller's supplied deadline across the original-context transaction.
This does not grant another worker turn or provider budget. The 20ms readiness
interval is a responsiveness judgment shared with owned cleanup; sustained CPU
or delayed valid release would falsify it.

Evidence: archived Mac normal selfmod and zero-provider recovery callers passed
2 in 16.76s before the latest state-deadline increment. The state/recovery/driver
checks passed 40 in 15.95s; retained dirty-work and lock-deadline checks then
passed 8 in 4.35s. The latest archived caller recheck is pending. These are
candidate results, not installed-host or all-platform completion.


### Retained red ownership continuation (local preview, 2026-10-12)

New `driver_step.admit_retained_red_entry` identifies an exact prior closed
red diff, unchanged base/input/plan state and absent registered processes.
The frozen context's diagnostic receipt grants entry ownership, never green
proof or permission to advance. Retry counts/history persist; fresh gates and
CAS remain required. Arbitrary initial dirt and unobserved timeout work refuse.
`run_local_checks.find_subplan/main` now read the frozen target and execution
root on legacy-context callers too; unknown or changed identities refuse
before a gate. These are selected I1/I5 reader migrations, not a claim that
every writer/platform adapter is complete. See the durable tracker for actual
archived entry and portable PowerShell measurements.


### Unattended execution-result reader migration (2026-10-12 candidate)

New `run_result._recorded_git_info` reads the driver's existing close JSONL
when Bash passes `--iteration-log`. It selects exact run/master/project,
requires a contiguous range and matching context/execution identities, and
projects the original recorded base, final recorded head and commit list.
Unknown capture, a newer open without close or conflicting records supplies
no range. It never substitutes current mutable Git observations. Additive
`git_provenance` distinguishes this projection from the historical direct-call
fallback; that fallback no longer invents a run base from `HEAD^`. This is a
selected I2 reader migration, not completion of all legacy consumers or an
independent gate/ship proof. Final verification stays opt-in.


### Disposable base samples for measured red progress (planned adapter)

The next owned pre-existing-red adapter must keep two decisions distinct:
progress acceptance may discharge one exact step after measured non-owned
failures, while green acceptance and shipment proof remain false. Original
raw exits, complete failed selectors and named unproven reasons survive CAS,
recovery, landing and close/result projection. A declaration is not evidence.
The current owned path remains strict raw-green until these callers are wired
and checked; the commit/CAS adapter is now a candidate API; loop/terminal callers remain
unwired.

Its first prerequisite is an enforced sample boundary. Before this increment,
red_owner._measure_at_base_worktree created/restored a disposable detached
worktree, but its test child lacked the owned gate sandbox; a registered-driver
fixture reproduced overwriting its control plan. New candidate
red_owner._run_base_sample uses that exact helper-created root as an
ephemeral observation role, retains the original driver/registry authority,
protects plans/data/pinned inputs/source trees/Git metadata, and uses separate
gate scratch. It must not replace the frozen dispatch root or worker inputs.
Node failures need actual matching FAILED evidence; unavailable runners,
wrong selectors and fixture ERROR-only output stay unmeasured. The local
tracker records the checked classification primitive and delivery states.

Every base sample stays inside the original iteration deadline and the
existing shared attribution cap. No baseline suite or cached gate outcome is
introduced. worker_sandbox.profile now accepts an explicit deadline or the frozen
driver authority deadline for its metadata query. Expired metadata starts no
Git subprocess. Unknown registered child cleanup retains scratch/sample instead
of deleting their worktree. These selected Mac sample/metadata changes are
checked candidates. Sample cleanup now shares the attribution allowance and
retains refused/expired worktrees; existing subprocess termination grace and
retained-sample recovery still need integration. Unproven-progress
transaction wiring remain open. This is not Windows/member or timeout entry support.

New candidate base_samples.measure_nodes/load_measurement retain complete
exact-node outputs at both frozen bases, with context/role identity, output
hashes and sample unchanged/cleanup observations. They are diagnostic APIs;
accepted stays false. Simple Python/pytest commands are the supported sampling
subset; compound/custom runners explicitly refuse instead of widening a gate.
The typed progress decision and commit/CAS recovery APIs are candidates;
loop/terminal transaction callers remain unwired. Selected
real Mac checks and local/remote delivery states are in the implementation
tracker; these APIs do not establish native Windows or general hermeticity.

New candidate measured_progress.assess binds the persisted complete head gate
to fresh exact-node observations at both bases, and keeps progress_eligible
separate from accepted/green_accepted. Unknown, runner/fixture error, input
change and flaky inventory refuse. The actual pre-existing/new-regression/
unavailable-runner cases are checked on Mac. This API leaves HEAD and the plan
pointer unchanged. Candidate progress_transaction.accept/recover perform
commit/CAS intent and exact recovery with accepted=false and
progress_accepted=true; landing and terminal wiring remain required before enabling measured-red progress in running loops.
