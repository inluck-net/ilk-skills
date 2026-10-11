# Manual owner plan: reliable, smooth, fast batch execution

Status: ACTIVE — manual implementation authorized by Chad on 2026-10-11.
This file is the durable execution tracker, not an autonomous scheduler master.
Keep it updated after each checked increment and before any context handoff.

## Resume here

1. Read this file and the linked designs. Do not reconstruct scope from chat.
2. Work in `/Users/chad/Projects/github/inluck-net/ilk-skills-worktrees/owner-loop-performance-20261011`, branch `owner/loop-performance-20261011`.
3. Inspect source and git status before changes. The shared main checkout has
   old worker has exited; back up and reconcile owner prose before integration.
   Do not stage, revert or overwrite unknown files.
4. Finish the first unchecked item below. Record actual check output, affected
   paths, remaining limitations, and the next action here. Candidate-tested,
   landed, installed and consumer-measured are distinct states.
5. Continue by hand; do not delegate kernel work to an autonomous ilk batch.

Current next action: complete kernel admission and caller checks, then finish
B3 recovery replay and C2/C3 driver ownership. Safe boundary is verified: old
run `20261011-102057` ended at 12:53 after its own timeout, PID/PGID gone.
Owner checkpoints are rebased onto main `5081038b`; implementation bytes match
the pre-rebase tested candidate. Do not restart the pending overlapping batch.
PowerShell function parity passed on macOS; full Windows entry remains untested.
No new installation or consumer benchmark has occurred.

## Authorization and invariants

Chad: “get everything said inside done one by one, by hand” and “you are
authorized to do so, go ahead”. Scope is the five increments proposed in chat,
including integration, safe activation and consumer validation. Improve the
ilk-skills loop first so consumer batches inherit durable continuity, exact
next-step selection, recovery and measured execution without repeating this
manual coordination. Existing dispatched work stays intact. No full suite or
routine final verifier; focused step, caller and invariant checks remain.
No hot replacement of active worker code/settings; no unnecessary park/stop.
Do not claim speed from tiny probes, or proof from worker reports.

Designs:
- [Execution performance](../architecture/loop-execution-performance-design.md)
- [State and ownership](../architecture/loop-state-and-ownership-design.md)
- [Worker writes code only](../architecture/worker-writes-code-only-design.md)
- [Verification evolution](../future-work/batch-verification-smooth-and-fast.md)
- [Historical batch baseline](../verification/batch-time-baseline-2026-10-11.md)

## Entire delivery checklist

### A. First usable improvement

- [x] A0 Candidate: reporter, compact brief, prefix preservation, stdout red
  evidence, additive summary observations. 72 focused tests passed in 6.31s;
  shell syntax/diff checks passed. Not landed or installed.
- [x] A0e Early same-model effort capability probe: two alternating pairs on
  actual MiMo PRO, all cases accepted, speed reversed and effort inactive.
  Keep defaults. This closes capability screening, not batch benchmarking.
- [x] A1 Resolve the selected exact plan in the pinned master; refuse duplicate
  identity rather than suffix/first-match selection. Complete started/summary
  provenance, bootstrap-before-edit, retries, child activity and missing data.
- [ ] A2 Focused caller/production-entry checks; reconcile the running batch's
  overlapping output/context requirements without rewriting a dispatched step.
  Add PowerShell parity for the new dispatch/observation contract.
- [ ] A3 Land and safely install the first increment after a verified safe
  boundary. Active old runner must exit before its replacement launches.
- [ ] A4 Representative baseline/brief comparison with the same task, model,
  provider, settings/hooks, fixtures and focused gates. Record acceptance,
  elapsed, turns/thinking, discovery, reds and host concurrency; no unsupported
  speed claim. Use a consumer first, then a small toolkit batch.

### B. Canonical context and durable records

- [ ] B1 D1: resolve clone/exec/observe/code/data/plans/runtime/master/target
  once before snapshots; persist correct selfmod re-entry across deferral,
  merge and restart. Consumers read this context; remove competing derivations.
- [ ] B2 D2: additive open/close records in existing JSONL, close after gates,
  integrity and landing. Migrate gate targeting, proof projection, integrity,
  audit, feedback and batch identity one reader at a time; preserve old readers.
- [ ] B3 Durable gate attempts/output and diagnostic recovery across launches;
  fix failing-ID read path; transcript salvage after interruption. Preserve
  attempts, frozen base and caps; diagnostic history never carries green proof.
- [ ] B4 Record separate driver gates, commit/hooks, integrity, landing and
  cleanup intervals. Count identical driver gates; retain overlapping spans.

### C. Driver-owned execution and state

- [ ] C1 Enforce the accepted kernel freeze for new loop builds, including
  session-planned batches and expanded deployment paths; owner edits allowed.
  Historical base-policy interpretation must remain explicit.
- [ ] C2 Write-scope protection: report-only consumer observation, then deny
  writes outside the execution worktree and git commit/push by workers.
  Protect plans/data/proof and evidence; controlled exceptions documented.
- [ ] C3 Driver commits accepted diffs, advances exactly one step only after
  valid gates, and imports findings; worker code-only prompt and hooks agree.
- [ ] C4 D3 compare-and-set transition API for all plan/master writers; human
  holds/drafts and unmerged work remain respected; explainable transition log.
- [ ] C5 Validate same-session resume/red recovery under actual provider/hooks.
  Move authoritative gates fully to driver only when recovery is viable;
  otherwise measure the fresh-session fallback and retain worker-run gates.
- [ ] C6 Retire redundant policing checks individually only after replacement
  production tests show the original failure class is prevented.

### D. Predictable lifecycle and upgrades

- [ ] D1 D4 additive registry with launching/expiry and verified PID/start/PGID;
  migrate scheduler, watchdog, stop and merge/migration readers without changing
  gh-resolve's existing artifact paths/shapes or worker cwd.
- [ ] D2 Pin all run inputs: runner/helpers, commands/skills, hooks/settings,
  model/effort, plan-invoked scripts and inventories; sanitized provenance and
  explicit credential provisioning. Never mutate a shared active worker home.
- [ ] D3 Consumer compatibility agreement/wiring for gh-resolve; exported pinned
  code root and lint/template/handoff updates; retain terminal toolkit identity.
- [ ] D4 Drain-after-step plus clean relaunch, only after gates/state/integrity/
  landing and lock release. Migrate all terminal readers first; gh-resolve must
  not reap an unfinished claim. Test old/new combinations, holds and races.

### E. Performance and completion

- [ ] E1 Representative controlled comparisons throughout delivery; group
  independent reads, measure compaction/thinking/recovery, evaluate supported
  effort settings and model choice separately. Preserve acceptance quality.
- [ ] E2 Complete remaining all-attempt verdict/actual-base attribution policy
  and consumer hermeticity enforcement. Unknown/inconclusive is not green.
- [ ] E3 Consumer batch then small eligible toolkit batch: durable resume from
  records/plan, truthful phases, accepted steps, safe recovery/upgrade and
  measured costs. Record comparable speed/reliability results.
- [ ] E4 All accepted design requirements mapped to implementation/evidence;
  obsolete divergences removed from docs; delivery installed and documented.

Deferred intentionally, not completion blockers: gate-outcome reuse (removed),
stall killing (observation only), CI-derived baseline reuse (design thought).
A failed capability experiment must result in a documented working fallback,
not an unsupported dependency. Numeric defaults/thresholds need judgment-call
basis and falsifier before activation.

## Evidence and coordination

External evidence directory:
`/Users/chad/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/`.
Contains candidate patch, worker cost report, effort probe and earlier owner
record. This tracker supersedes that narrow owner record for scope/progress.
Existing live batch: `MASTER-2026-10-11b-gates-and-recovery-tell-the-truth-execution-plan.md`,
run `20261011-102057`, iteration 4 observed in agent phase. Plans 3 and 5 overlap
output/context work; verify current state at the actual landing boundary.
No completion/ship/proof markers are inferred from this human tracker.

## Work log

- 2026-10-11: Created complete manual tracker and resume pointer after Chad's
  full authorization. Prior candidate evidence recorded; implementation remains
  isolated. Next: A1 exact identity and missing additive observations.

- 2026-10-11 A1: Added shared selected-master registry identity resolver with
  legacy filename/frontmatter aliases and ambiguity refusal. Startup/summary
  carry dispatch identity/step. Reporter v2 measures explicit retries, child
  activity and bootstrap before first Edit/Write. 12 focused tests passed in
  6.31s, including actual detached entry and ambiguity/alias negative cases.
  Prior candidate checkpoint committed locally and rebased onto main
  `22f72cad`; no shared worker files overwritten.
- Coordination: requested existing steer pause at next iteration boundary,
  after active iteration 4 completes its worker/gates/landing. No signal sent.
  Reason: queued plans 3/5 and this owner build edit the same runner; future
  overlapping autonomous edits must not race the manual landing. Do not
  stop an active worker. Check pause acknowledgment before any rollout.

- 2026-10-11 A2 progress: 90 focused dispatch/partition/final-step/prompt/reporter
  checks passed in 11.63s. PowerShell 7.6.6 private portable runtime parsed the
  production script and exercised exact selected-master dispatch, shared brief
  and missing-observation behavior: PASS. Reproducible function probe is
  `skills/ilk-loop/tests/owner_dispatch_parity.ps1`; full Windows entry is not
  established by this macOS check. New helpers use the runner's existing
  `python` contract. Declared-gate extraction now reads the same pinned path.
- B3 partial: complete stdout/stderr artifacts have unique attempt IDs and
  monotonic spans; additive check observations survive result/history records.
  Retry diagnostics read full artifacts; fixed the existing unconditional
  failing-ID reader return. 35 focused recovery/capture/record/entry checks
  passed in 6.45s. Cross-launch history and interrupted-transcript recovery
  are still pending. First failing harness was corrected to assign RUN_LOG_DIR
  after sourcing; it did not establish a product defect.
- Active worker still observed in iteration 4 at 12:19; boundary pause has
  not yet been acknowledged. No deployment, signal, push or release performed.

- B1 partial: immutable per-iteration context is resolved before HEAD/dirty/
  gate snapshots; execution and observation trees, separate code/data roots,
  selected master/plan, frozen base commit/tree and unknown registry/SHA are
  explicit. Started/summary records include the same context; worker cwd and
  selected gate/snapshot pin consume it. 14 focused tests passed in 6.70s,
  including detached entry, conflicting-pin refusal and installed-root unknown
  SHA. The earlier combined context/selfmod checks had 18 passes and one old
  summary harness missing the newly required context; updated that harness.
  Full context reader migration, persisted selfmod state transitions and
  PowerShell context wiring remain pending; I1 is not yet complete.
- Checkpoint `c702b153` saves exact dispatch and full attempt evidence locally.
  Current next action: finish B1 reader/PowerShell migration and selfmod state,
  then B2 record close after integrity/landing. A3 still waits for a confirmed
  idle boundary. Live worker progressed to compaction at 12:23, not paused.

- B1/B2 progress: scheduler `next.fname` now selects dispatch on both runners;
  `next: null` does not choose a blocked plan. 21 focused dispatch/final-step/
  draft-master checks passed in 8.98s. Context path caching removes repeated
  plans/runtime derivation during a dispatch; the legacy launcher-dir helper
  is explicitly normalized to the runtime root. 16 context/entry/identity
  checks passed in 5.96s.
- B2 partial: Bash summary now follows integrity, merge and reconciliation;
  PowerShell writes its summary in `finally` after its integrity/gate path.
  Open/close labels are additive; old duration stays compatible and full
  driver elapsed is separate. 26 interrupted-record/landing/proof/caller
  checks passed in 39.28s. Exact registered plan snapshots/fingerprints and
  actual execution HEAD/tree/commit range are captured before merge removes
  the execution tree. 18 scoped snapshot/proof/final-step checks passed in
  8.68s. Proof/audit/feedback reader migration remains pending.
- B1 selfmod state: one persisted disk reconciliation function records
  NONE/ISOLATED/MERGE_DEFERRED/MERGED; missing worktree is not MERGED unless
  its recorded commit is an ancestor of the clone. Re-entry preserves the
  editing tree. 22 focused state/entry/selfmod/observation checks passed in
  28.01s. Additional full-entry gate-tampering close-record check passed: the
  final record reports ship_integrity_violation after restoring the gate.
  All 7 entry checks passed in 9.37s.
- PowerShell parser/dispatch/context probe passes after adding context and
  close-record handling. Windows full entry and declared-worktree/selfmod
  parity are still pending. No claim that I1/I2 are fully built.

- Checkpoint `32660439`: persisted context/state and post-integrity close
  records saved locally. B2 audit migration: all Git history reads are scoped
  to HEAD; ledger rows carrying identities must be reachable from audited
  HEAD. Empty commit lists without gate-at-head proof are refused. A fixture
  that used an invented SHA now uses its real commits; another now asserts
  that empty attribution is unknown before checking the valid normal shape.
  52 focused audit/context/ledger/no-op checks passed, 1 pre-existing xfail,
  in 13.16s. Legacy rows lacking identity fields still use their historical
  interpretation; E2 must finish strict new-batch policy before completion.
- B2 feedback migration: totals prefer measured complete driver elapsed,
  retain legacy worker-duration fallback with an explicit basis, count
  unknown measurements and preserve open timestamps whichever log is read
  first. 32 feedback/partial-record/narrative/fallback checks passed in 0.47s.
  First hermetic CLI check lacked ILK_SKILL_HOME and could not resolve the
  toolkit; rerun used explicit owner skill root, without touching live data.
- Next: finish B2 provisional-proof/close projection and reader migration;
  finish B1 PowerShell execution-tree parity. Current worker remains active,
  so A2 overlap reconciliation and A3 activation are still pending.

- B2 integrity/batch readers: frozen context resolves the exact execution
  tree; a repo flag that contradicts it is refused. Canonical project registry
  is read from data home before bounded legacy fallback. Batch records carry
  batch/master/frozen batch base/run identity and refuse wrong-batch latest
  records. 32 identity/attribution/worker-refusal/integrity/entry checks passed
  in 12.77s. Trailerless fixtures with invented SHAs now name real commits.
- Current next action: B2 provisional evidence must be finalized by the close
  record after integrity/landing, with an idempotent projection into the
  existing ledger. Preserve internal driver gate/ship evaluation; provisional
  rows must not count as external ship proof. Reader and production-entry
  checks are required before enabling the contract.

- B2 close projection: provisional ledger rows are hidden from external
  readers; only the current driver-check scope sees them. Closed rows are
  projected idempotently from existing JSONL after integrity/landing, and
  unlanded/integrity-red/non-pass rows cannot prove a ship. 24 focused
  projection/entry/ledger/no-op checks passed in 15.20s; 17 real-entry and
  interrupted-record checks passed in 34.20s. Worker entry receives no
  driver-check authority. PowerShell parser/function probe still passes.
- B3 diagnostic continuity: canonical recovery paths preserve existing retry
  count/IDs and prior red-output links across launches of the same frozen
  batch/plan; no green proof is carried. Also fixed the retry-count reader's
  confirmed unconditional return. Corrupt persistent counts stop recovery
  rather than resetting its allowance. Interrupted transcript salvage stores
  observed session/model, completed assistant text, tool-result metadata and
  pending tool IDs without replaying tools or storing their raw command/input.
  52 focused recovery/cap/entry/salvage checks passed in 10.50s; cross-launch
  context/counter checks previously had 24 passes in 10.87s. Numeric retry
  thresholds are unchanged. Full Windows recovery and runtime replay of a
  killed runner's unprojected close remain pending.
- SAFE BOUNDARY: old run 20261011-102057 ended on its own with timeout at
  12:53:22, iteration 4. PID 3156 and PGID 77151 are gone; phase.json is gone,
  and no loop/watchdog process was observed. No signal was sent by this owner.
  Main is now 5081038b. Its WIP commit preserves ONLY owner-written tracker
  and ownership prose, not worker implementation; do not credit it to plan 2.
  Reconcile these prose changes and owner checkpoints before integration.
  Keep the pending batch intact and do not restart it into overlapping code.

- Kernel ownership increment: fixed default repository lookup in
  `safety_kernel.load`; added explicit `owner-only` policy and protected
  installation/daemon/deployment and new context/proof/recovery authority files.
  Release-range checks continue to use the frozen base policy; historical
  session allowance remains explicit. Existing unattended/unresolved reason
  codes are preserved. New-policy session edits now require owner builds.
  Admission in plan preflight and frozen-context preparation rejects declared
  kernel writes before dispatch. Consumer repos and report tools are unaffected.
  This is not yet actual write-scope enforcement (C2) or full C1 completion.
  Red-first: 13 failures/1 pass before implementation. Focused policy/history/
  context/production entry checks: 56 passed in 14.45s. Preflight callers and
  admission checks: 34 passed in 0.95s. Diff whitespace check passed.
  Updated the old clean-preflight fixture to edit a test, because its previous
  kernel edit is now correctly refused; a new caller test asserts that refusal.

- Interruption recovery increment: `iteration_record.replay_closed_records`
  projects only pending provisional evidence with an actual matching close,
  using the current project's resolved ledger path. Missing/partial closes stay
  provisional; foreign ledger/contradictory identities refuse dispatch. Both
  runners invoke replay before worker dispatch. Repeated recovery is idempotent
  and integrity-red closes remain unusable as proof. Focused recovery and real
  entry checks: 19 passed in 10.21s; Bash and PowerShell syntax and diff checks
  passed. This covers the close/projection interruption gap, not death before a
  close record or the full lifecycle registry (D1).
