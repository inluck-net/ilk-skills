# Manual owner plan: reliable, smooth, fast batch execution

Status: ACTIVE — manual implementation authorized by Chad on 2026-10-11.
This file is the durable execution tracker, not an autonomous scheduler master.
Keep it updated after each checked increment and before any context handoff.

## Resume here

1. Read this file and the linked designs. Do not reconstruct scope from chat.
2. Work in `/Users/chad/Projects/github/inluck-net/ilk-skills-worktrees/owner-loop-performance-20261011`, branch `owner/loop-performance-20261011`.
3. Inspect source and git status before changes. The shared main checkout has
   an old runner idling at pause; back up and reconcile owner prose only at a
   fresh verified quiescent integration boundary.
   Do not stage, revert or overwrite unknown files.
4. Finish the first unchecked item below. Record actual check output, affected
   paths, remaining limitations, and the next action here. Candidate-tested,
   landed, installed and consumer-measured are distinct states.
5. Continue by hand; do not delegate kernel work to an autonomous ilk batch.

Current next action: wire and verify D1 registry lifecycle/readers, then C3
code-only runner acceptance/recovery and the remaining checklist. E2 display-tail
false green is fixed; actual-base provenance still open. C4 now audits frozen
owned state against the journal; legacy worker writes are observed, code-only
writes enforced. Candidate APIs remain unactivated. Continue here per Chad:
“no more handover, until the whole thing done”. Overall estimate 35% is a
judgment against delivered/installed/measured scope, not a checkbox count.

Old run `20261011-102057` timed out by itself. Old run `20261011-130246`
was freshly observed at handoff with PID 1016, PGID 958, start Oct 11 13:02:46,
and a sleep child; the pause flag remains. Watchdog PID 354 is no longer present;
a detached scheduler PID 27687 exists. Re-check all identities before activation.
Owner checkpoints preserve main `5081038b` WIP prose ancestry. No new install,
consumer benchmark, full suite, or production enforcement has occurred.

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

- Retry-state durability increment: strict whole-history validation plus
  private atomic replacement/fsync for canonical count/ID files. Corrupt counts,
  wrong ID types and invalid values cannot be overwritten or reset; write
  failures stop the iteration. Frozen-context callers use this shared helper;
  historical no-context behavior remains compatible. Missing current/prior
  failing IDs no longer imply progress beyond the existing initial allowance.
  Numeric retry limits and terminal reason vocabulary remain unchanged.
  Retry policy helper added to the protected kernel list. Focused recovery,
  existing retry callers and production entry: 59 passed in 10.73s; Bash syntax
  and diff checks passed. PowerShell retry recovery parity remains open.

- Coordination update: fresh process inspection found watchdog PID 354 and
  relaunched runner PID 1016, run `20261011-130246`. Sentinel reports running;
  child `sleep 5`, absent iteration logs and retained pause.flag confirm it is
  waiting before dispatch. No worker was interrupted. Do not merge/install on
  the strength of the previous run's terminal state; keep owner work isolated.
  Consumer/provider probes use disposable homes and never release that pause.

- Actual-provider resume screening: disposable tools-disabled MiMo PRO home,
  explicit session ID then `claude -p --resume` same ID, opaque prior token
  correctly recalled. Both calls accepted with matching actual model/session;
  initial 21.158s, resumed 8.284s. Reported resumed API 27.958s exceeds wall time;
  do not add it or claim performance. External evidence:
  `runtime/analysis/design-revision-20261011/resume-capability-probe.json`.
  Timeout 60s is an experiment judgment (sampled normal latency fits; a slow
  successful run would falsify it), not a batch default. Production hooks,
  interrupted transcript and real driver-red repair remain C5 requirements.

- State ownership increment: new `plan_state.transition` re-reads under a
  process lock, compares expected fields, refuses worker calls/foreign frozen
  registry plans and driver changes to human holds, and writes intent/applied
  JSONL with before/after hashes around atomic replacement. Interrupted intents
  are classified applied/not-applied by exact bytes; unexplained edits refuse.
  Journal lives beside the plans in `state-transitions.jsonl`; all migrated
  writers of that plans root share one lock/journal. Bash gate-first step
  advancement now uses it; rollback, ship, reconcile, promotion, parking and
  PowerShell writers are not yet migrated. No I3 completion claim.
  Focused API/gate-first/final-step/one-step callers: 19 passed in 13.72s;
  API follow-up with concurrent same-step CAS: 10 passed in 0.12s. Exactly
  one of two concurrent drivers advanced; the stale call refused.
  Bash syntax and diff checks passed. New authority module is kernel protected.

- Worker discovery increment: pinned dispatch has a strict read API and
  `iteration_context.py --print-dispatch` CLI. Command/skill instructions use it
  for dispatched workers instead of repeating queue discovery; interactive
  sessions retain status lookup. Required project/step references and gates
  remain. Invalid/foreign context refuses rather than silently reselecting.
  Context/CLI checks: 10 passed in 0.98s; the earlier dispatch/real-entry run
  passed 22 callers, with one new fixture initially missing its step corrected.
  Skill validator passed using PyYAML installed only in external tooling;
  no global Python/worker-home mutation. No batch speed claim.
- Old timeout measurement independently re-extracted with parser v2:
  run `20261011-102057`, iteration 04, actual init MiMo PRO, 48 unique root
  assistant turns (130 split emissions), 81 tools, no observed Edit/Write,
  3,117.0s completed thinking, 3,599s observed renderer span, one compaction
  155.949s; matched foreground test interval 0.178s. Clocks overlap; no final
  worker result. Evidence `old-worker-timeout-observation.json` under external
  design-revision analysis. This was thinking/investigation, not suite waiting.

- State writer migration: Bash red-pointer rollback now supplies the observed
  pointer to CAS; master reconciliation and queue promotion/demotion use the
  shared journaled API. Reconciliation preserves draft/blocked/parked holds,
  including the former pre_park_status=shipped auto-release exception. Focused
  retraction/hold/promotion/red-pointer/API callers: 42 passed in 0.94s.
  Parking stamps, ship transitions, integrity restoration, quarantine and
  PowerShell writers remain open; API existence is not whole I3 enforcement.

- Ship state migration: durable ship-marker projection now writes status via
  CAS, wrapping errors in the existing ShipTransitionError contract. Held plans
  refuse before intent/marker creation; explicit hold=human is respected even
  on runnable status. Ship/driver/API callers: 47 passed in 4.60s, two existing
  integration placeholders skipped (not evidence). Real detached entry: seven
  caller cases passed within the combined follow-up (18 total passes before
  correcting a new held-ship fixture's positional arguments). Corrected API/
  held-ship check: 12 passed in 0.20s; HEAD and ship intent unchanged on hold.
  No ownership-completion or activated-driver-only-gates claim.

- Write-scope observation candidate: `hooks/worker-write-scope.py` classifies
  native targets against frozen worktree/plans/data/runtime/Git metadata and
  resolves symlink escapes. Private diagnostic JSONL records only categories
  and command/target hashes. Report-only is default; optional native denial is
  tested but not registered/enabled in production. Shell hints explicitly do
  not prove containment: arbitrary Bash remains unclassified. Full C2 needs
  the stronger shell boundary and consumer report-first validation before
  claiming/enabling enforcement. Scope/API checks: 22 passed in 0.42s, including
  actual hook CLI observe/deny and no target mutation. Hook is kernel-protected.

- Run-input preparation candidate: `run_inputs.prepare` snapshots a committed
  toolkit revision and copies configured hook scripts into a new private home,
  rewriting settings away from active-home hook paths. Settings credentials
  are provisioned only in a private 0600 copy; manifest stores names/hashes and
  requested model, not credential values. Hook source changes during preparation
  refuse; unknown shell wrappers/auth-helper provisioning refuse rather than
  following live commands. Observation hook can be included in this private
  home without altering production settings. Focused source-change/private-
  credential/interpreter/wrapper checks: 3 passed in 0.19s.
  Not full D2: launcher wiring, resolved CLI model/effort, MCP/plan-script
  adapters, hook dependencies and host runtime provenance remain open. No
  prepared home has yet been activated on a real batch.

- Real hook-enabled continuation screening: committed toolkit 6b39094f,
  private home with nine copied/observer hook registrations; MiMo PRO initial
  native one-file write accepted in 9.069s (two turns), driver measured expected
  assertion red; same-session resume repaired it in 5.413s (two turns), exact
  driver gate green. Two cumulative native writes, none outside scope. Evidence
  `hooked-resume-capability-probe.json`. Timeout 120s is an experiment judgment
  for a handful of tool turns (prior call latency ~20s); a slow valid run would
  falsify it. It is not a runner default. Interrupted resume, Bash boundary and
  representative batch comparisons remain open.
- Mac OS scope candidate: actual sandbox-exec permits execution/private-temp
  writes and denies foreign/control-plane/Git metadata writes; shell profile
  denies network before connection. Parent permits private transcripts while
  keeping settings/hooks immutable. Symlink escape is denied. Fixed observer
  precedence when selfmod execution lives beneath broad data/runtime roots.
  Scoped observation and explicit Mac OS fixtures: 15 passed in 0.66s. Ten-
  second test-process bounds are judgments for subsecond fixture operations;
  a slow valid host would falsify them. No production activation. Required
  next: strict pinned Bash wrapper, enforce-mode hook admission, full copied-
  hook CLI check under the parent sandbox, then driver-controller wiring.

- Canonical shell entry candidate: `worker_shell.py` executes shell commands
  only under the frozen Mac write/network profile, with a frozen private temp
  directory and actual cwd checked. The enforce-mode hook admits only exact
  canonical quoting of that pinned entry; extra shell syntax/expansion refuses.
  Explicit hook arguments pin context/mode so shedding worker environment does
  not disable enforcement. Native writes remain scoped; unsupported tools
  refuse in full mode. OS/wrapper/observer checks: 16 passed in 0.88s; hook CLI
  admission including environment shedding: 12 passed in 0.39s. Production
  remains observe-only/unregistered; need bound per-context private settings
  plus actual CLI under the parent profile before C2 enforcement rollout.

- Important boundary falsifier: applying a shell sandbox from an already
  sandboxed parent returns exit 71, `sandbox_apply: Operation not permitted`.
  Earlier independent profile checks remain valid but cannot prove composition.
  Bind-context activation now explicitly refuses `macos-two-layer`; the base
  private settings remain unchanged. Next mechanism: driver-owned shell broker
  outside the CLI sandbox, immutable wrapper inside requesting only scoped
  shell execution; identity/deadline/cleanup remain driver-owned. No no-sandbox
  exemption or silent unsandboxed fallback. This finding changes the design
  before activation, as Chad required measurement over assumptions.

- Broker composition candidate: driver-owned `worker_broker.py` accepts only
  scoped shell execution through an immutable context descriptor. Actual Mac
  parent sandbox -> wrapper IPC -> separately sandboxed shell passes; plan
  writes/network/foreign signals deny. Full outputs and diagnostic attempt
  spans are retained outside worker scope. Per-context settings bind explicit
  hook mode/context without changing the prepared base settings. These and
  recovery identity checks: 36 passed in 2.66s. Broker lifecycle registry,
  interrupted CLI, background descendants and production activation remain
  open; no complete C2/I4 claim. New broker is owner-kernel protected.
- Recovery identity correction: inspection of `plan_fingerprint.surface`
  confirmed the older candidate duplicated normalization and included mutable
  master registry Status/Progress log. Reused canonical surface; a regression
  verifies relaunch/status/progress preserve the key while contract edits
  change it. This is not gate-outcome reuse and creates no green evidence.

- Actual enforced-provider continuation: snapshot 46290f7b, copied production
  hooks, Bash-only advertised tool under parent OS profile and broker shell
  boundary. Initial expected assertion red: 17.102s/two turns/one broker attempt;
  same-session repair green: 14.069s/two turns/two cumulative attempts. First
  attempt failed correctly at Claude hardcoded scratch mkdir outside scope;
  local CLI binary exposes CLAUDE_CODE_TMPDIR/CLAUDE_TMPDIR, now explicitly set
  to frozen private temp. Retain failed `brokered-resume-capability-probe.json`
  and accepted `brokered-resume-capability-probe-retry.json`, not a speed claim.
- Controlled interrupted continuation: same frozen snapshot/provider/hooks,
  disposable owned CLI interrupted only after completed tool result. Initial
  exit -9, no terminal result, expected driver assertion red at 7.338s; same
  session resumed to green in 19.381s/two turns. Evidence
  `interrupted-brokered-resume-capability-probe-retry.json`. First experimental
  group kill raised EPERM; retry validates owned Popen PID/group and exact-child
  fallback, never process-name scanning. No real worker interrupted. This
  proves the sampled interrupted tool-result boundary, not every interruption.
- C1 undeclared-write candidate: freeze the execution repo's kernel list in
  context; native hook classifies owner-kernel targets, OS boundary denies
  listed writes, basename creation and ancestor rename while allowing sibling
  report writes. Broker cleanup also terminates owned background descendants
  before response and closes an idle owned connection without deadline waits.
  Scope/context/cleanup checks: 34 passed in 2.84s. One initial fixture used
  relative sibling path under wrong cwd; corrected to absolute target. No
  production activation. Short background fixture delay/bound is a judgment
  (0.2s child delay, 0.4s observation); a slow valid host falsifies that check.

- C3 acceptance candidate: `driver_step.accept_step` captures a working-tree
  object via an alternate index, runs declared plus canonical mention gates,
  retains full attempts/output, refuses malformed/absent/mutating gates, then
  driver-commits and CAS-advances exactly one step. An unchanged shipping tree
  uses the measured attempt once; commit-hook tree mutation requires a new
  gate. Red leaves pointer unchanged even if an owner hook created an unaccepted
  commit. Kernel changes and dirty Git state refuse; worker cannot call API.
  Frozen master state guards are checked under the shared state lock so a hold
  cannot race step advancement. Driver/CAS/context checks: 29 passed in 3.13s.
  This API is not runner-wired. Clean entry is required; member-repo dispatch,
  baseline-red attribution, driver finding import,
  provisional proof/ship integration and accepted-tree attempt projection are
  still open. Do not enable code-only prompts globally until those callers
  are complete. No I3/I5/production-completion claim.

- D1 additive registry candidate: `run_registry.py` reserves launching state
  with explicit finite expiry, binds exact POSIX PID/start/PGID, refuses corrupt
  or contradictory records, detects PID reuse/moved groups, signals only fresh
  matching recorded groups and refuses caller group, then removes only matching
  identities. Legacy artifact paths/shapes are untouched. Six focused checks
  passed in 0.07s, including an actual disposable owned-process capture/stop.
  Fixture ten-second expiry/wait are test judgments for subsecond startup; a
  slow valid host falsifies them. Launch/runner/readers, broker identity, Windows
  adapter, orphan reconciliation and drain activation remain open. No I4 claim.

- Real provider through C3 API: frozen snapshot 518cbc75 and private copied
  hooks, actual MiMo PRO/Bash-only enforced scope. First driver gate red,
  zero commits/no pointer advance (12.557s/two CLI turns). Same-session repair
  green, exactly one driver commit/one pointer advance/one gate attempt
  (12.450s/two CLI turns). Evidence `driver-accept-resume-capability-probe.json`.
  Proof remains explicitly provisional; no actual batch shipping claim.
- C3 commit budget: accept_step now requires the caller's existing iteration
  monotonic deadline; Git operations/commit hooks use existing bounded_run
  owned-group cleanup, gates are capped by remaining budget. No new loop
  timeout default. Six driver checks passed in 3.61s, including an actual
  intentionally waiting owned commit hook terminated at the supplied budget
  with no HEAD/pointer advance. Initial one-second fixture budget could never
  reach a gate because its existing parser uses whole seconds; corrected to
  two seconds, a test judgment for that granularity plus sampled subsecond
  index/gate startup. A slower valid host would falsify the fixture bound.

- C4 park writer migration: park status/reason/time/hold/yield (and owner-of
  pre-park status) now change in one journaled CAS instead of sequential file
  rewrites. Explicit unpark metadata and registered strike resets also use
  CAS; existing release-hold refusal is preserved. No real plan was parked,
  unparked or amended. Park/hold/CAS checks: 45 passed in 2.08s; actual owning-
  master violation caller plus atomic journal regression: 37 passed in 7.88s.
  Explicit unpark retains the previous privileged operator-release semantics;
  ordinary driver advancement still cannot lift a hold. Projection writers,
  integrity restores, quarantine and PowerShell state writes remain open.

- C4 quarantine migration: strike count/status/finding update is one CAS;
  exact frozen identity is used when available, legacy ambiguous aliases refuse
  instead of first-glob matching. A driver green may clear only a recorded
  automatic block with positive strike history; bare manual blocks and human
  holds stay byte-for-byte unchanged. Existing quarantine threshold remains 2,
  not a new default. Findings are appended inside the transaction; write-ahead
  journal retains byte hashes without raw finding content. Quarantine/CAS/
  driver checks: 28 passed in 4.78s. No real plan was mutated. Integrity restore,
  registry projection and PowerShell writers still need migration.

- C4 snapshot writer migration: master/subplan status restoration now uses the
  shared journal. Found and fixed a measured legacy bug: restore accepted a
  newly added human hold, then `_write_owned(before)` erased it when restoring
  another field. Restore now merges only remaining changed fields into current
  state. Integrity-repair actor cannot advance pointers or erase a human hold;
  quoted human values remain parse-equivalent. Snapshot/hold/CAS checks: 31
  passed in 1.40s. Bash now refuses integrity proof for any unverifiable status
  restore return code (only 0/3 accepted), not merely logging rc=2. Actual
  production-entry corrupt-journal/invalid-status fixture plus legacy master
  guards: 14 passed in 15.04s. Syntax/diff checks passed. No real state changes.

- C4 registry projection: `plan_state.reconcile_registry` derives exact
  registered-row statuses under the shared lock, CAS-checks master bytes,
  journals replacement and preserves BOM/CRLF and every frontmatter field.
  Passive projection cannot lift a hold or change a contract. Reconciler code
  already owned registered Status cells under legacy headings; canonical
  fingerprint now shares that exact row parser, so those legitimate updates
  cannot reset retry history. Normalizer is owner-kernel protected. Combined
  check: 20 passed/one invalid fixture failed in 64.60s (seven amendment caller
  tests passed). The failed fixture used undated step.md, which the real
  registry parser does not register; changed to a valid dated reference.
  Projection/context rerun: 14 passed in 1.29s. Do not repeat the already
  passing watcher cases just for that fixture correction. No real master edit.

- Context handover: refreshed Resume pointer and live-state observations;
  no implementation after the handover request. Successor must use the full
  checklist, not treat candidate helpers as installed loop behavior.

- E2 complete-output attribution: mention excuses now use complete captured
  stdout/stderr; display tails cannot hide an undeclared prefix and unknown
  capture cannot excuse. 15 focused mention checks passed in 5.64s. This closes
  capture coverage, not actual-base provenance for legacy configured exceptions.
- C4 additional Bash writers: refusal/re-entry findings and inconclusive/red/
  foreign-plan ship retractions use shared journal API with byte CAS, exact
  frozen/registry identity, idempotent notes, strict corrupt-history refusal,
  hold preservation, and BOM/CRLF retention. First caller checks exposed my
  incorrect SCRIPT_DIR reference; corrected to runner's _ILK_SCRIPT_DIR and
  existing _master_tampered flag. Relevant state/findings/gates-off checks:
  31 passed in 3.97s. No live plans changed. Foreign-plan inline production
  seam, remaining PS writer and explained state mutation audit still open.

- C4 explained mutation audit: snapshots freeze all owned fields and a journal
  cursor under the shared lock. Audit folds completed intent pairs, refuses
  rewritten prefixes, pending/corrupt records and detects both unlogged state
  and a driver transition starting from an unexplained worker change. Bash
  close records the audit additively; code-only mode enforces, legacy worker
  writes remain observations until migration. PowerShell's remaining direct
  ship rollback now calls shared API. POSIX atomic plan/journal replacement
  fsyncs directory; Windows directory durability remains an explicit adapter
  limitation. State/context/snapshot checks: 32 passed in 5.08s; Bash and
  portable PowerShell syntax PASS. No production activation or I3 completion.

- D1 POSIX production wiring candidate: launcher writes exclusive launch
  reservation before spawn (120s default is a judgment reusing existing
  scheduler gap window; valid startup beyond expiry falsifies it), runner
  binds exact PID/start/owned group after lock, records worker child groups,
  and finalizes registry on exit. Direct lock helper creates an owned session,
  never registering its caller group. Launcher/scheduler/watchdog, Python
  relaunch/status/feedback, shared process-set helper, merge/migration and
  bounce readers use registry plus fresh identity. Unknown old locks/PIDs
  stay busy and cannot be signaled automatically. Stop now signals verified
  child groups before runner, refuses unknown groups, and terminalizes only
  matching run/PID after confirmed exit. Old orphan-argv scanner is no longer
  called. last-launch.json gains run_id and toolkit_sha with argument-based
  serialization; terminal sentinels gain toolkit_sha. Focused registry + actual
  pre-loop runner entry: 19 passed in 2.97s; actual owned fixture through
  stop.sh: 1 passed in 1.42s; production merge/migration no-argv check passed.
  Windows native lifecycle adapter/readers, private-input pin activation,
  broker child registration and consumer drain still open. No live daemon
  or runner was changed; no installed I4 claim.

- D2 launch pinning candidate: Claude launcher prepares exact committed code
  (release manifests select their SHA, never later source HEAD), private copied
  settings/hooks, immutable MCP bytes and explicit executable path/hash plus
  consumer lockfile inventory. Fixed CLI path is invoked; resources verified
  before dispatch, input drift refuses with named terminal state. ILK_CODE_ROOT
  resolves legacy toolkit plan-script references including spaced paths;
  pinned selfmod keeps its driver code root, resolves source clone separately.
  Source homes unchanged. Runtime libraries are explicitly observed rather than
  copied: a system-runtime upgrade requires drain/relaunch. Eight input-pin
  checks passed in 0.44s (release-SHA isolation, credential omission, drift and
  actual spaced-path script execution). Consumer-map check also exposed three
  earlier candidate terminal states absent from collector; all now map to
  self-hosting-drift / needs-human. Full launcher-through-private-input entry
  check and activation remain pending; no installed I6 claim.

- D2 production launcher entry: actual detached fixture launched from private
  committed code and copied source-home settings, ended shipped-unproven (no
  proof invented), matching run_id/toolkit_sha, source home unchanged, registry
  removed. 1 passed in 11.79s. This is startup/caller evidence, not speedup.
  Terminal-state reader map: 25 passed in 0.10s. Next: C3 code-only dispatch
  preparation and acceptance callers, bounded actual-provider repair; keep
  mode disabled until production-entry evidence and consumer compatibility.
