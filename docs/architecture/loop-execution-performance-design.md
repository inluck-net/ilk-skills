# Loop execution performance

**Status:** revised after independently checking the 2026-10-11 review.
Prose only; no implementation, deployment, or batch changes are authorized by
this document. Final verification stays opt-in; focused step/caller checks stay.

This extends [state and ownership](loop-state-and-ownership-design.md) and
[worker writes code only](worker-writes-code-only-design.md). **I1's canonical
iteration context and I2's unified open/close record are not built yet.**
The accepted ownership design describes targets, not existing APIs. No
`resolve_iteration_context`, `ctx-`, `base_tree`, `head_tree`, or `"attempts"`
occurs in the current bash runner; no `def transition` occurs in its scripts.
An initial performance change must not create a competing context/record.

Source notation: **R** = `skills/ilk-loop/scripts/run_ilk_loop_claude.sh`.
Code anchors below were opened on 2026-10-11; line numbers describe that
working-tree snapshot, not every installed release. Mechanisms are explicitly
labelled existing, partial, or new. Current source may include ongoing worker
changes; none were edited during this revision.

## Problem: measured thinking, turns, discovery, and tests

The [batch baseline](../verification/batch-time-baseline-2026-10-11.md) is
historical cost evidence, not a controlled comparison: tasks differ and models
can change within a run. In particular, ilk-skills run `20261010-143933`
iteration 02 used Flash while iteration 14 used Pro. Use stream `system/init`
model identity, not a run-level configured/default model.

Session measurements re-extracted from the original logs:

| Repository / run / iteration | Model from init | Worker elapsed min | Completed thinking blocks / min | Reported API min | Reported turns | Root tool calls | Coarse bootstrap calls / before first Edit | Foreground pytest tool intervals min |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ilk-skills 20261010-143933 / 14 | mimo-v2.6-pro | 35.5 | 86 / 23.4 | 34.1 | 127 | 124 | 9 / 5 | 1.3 |
| ilk-skills 20261010-143933 / 02 | mimo-v2.6-flash | 41.8 | 122 / 22.0 | 32.7 | 126 | 123 | 11 / 4 | 8.0 |
| gh-resolve 20261011-061345 / 05 | mimo-v2.6-pro | 57.8 | 119 / 32.8 | 46.4 | 166 | 163 | 32 / 20 | 8.8 |
| gh-resolve 20261010-185714 / 08 | mimo-v2.6-pro | 51.9 | 111 / 16.2 | 29.0 | 129 | 127 | 34 / 8 | 19.4 |
| gh-resolve 20261011-050259 / 01 | mimo-v2.6-pro | 60.2* | 37 / 34.2 | unknown | unknown | 59 | 8 / 8** | 8.8 |

*60.2 is the runner iteration timer for an interrupted session with no final
worker result; it is not measured by the same boundary as completed rows.
**There was no Edit/Write, so all bootstrap calls preceded the timeout.

Methods and limits:

- Thinking sums only completed renderer lines matching
  `\[thinking\] \d+ chars in ([\d.]+)s`. Do not sum cumulative progress
  lines. These are elapsed generation-block observations, not server compute
  or complete reasoning budgets. The longest completed block in verifier
  repair is **391.1 seconds**, slightly more than the review's rounded 390.
  The timed-out row also ends partway through a thinking block; its completed
  total undercounts it. Renderer behavior: `_stream_json_render.py:17-24,115`.
- Completed worker elapsed, API time, and turns come from the final result.
  Thinking occupies about 56–71% of reported API time in these completed
  no-Agent-tool rows; categories overlap and must not be added. This is strong
  evidence for examining extended thinking early, not proof it is unnecessary.
- Root tool calls count assistant `tool_use` blocks with no parent tool ID.
  Bootstrap is a reproducible coarse classifier over serialized tool inputs:
  `.claude*/`, `.ilk-data`, `.ilk/`, `plans/`, `MASTER-`, `loop_status`, or
  `find ~`. It also matches control-plane writes, so it is not pure wasted
  discovery. Different regexes change the review's approximate 8–41 count.
  Do not multiply an average turn time by this count to promise savings.
- Pytest intervals pair timestamped Bash calls containing `pytest` with the
  next result. The five rows have 6, 21, 14, 10, and 2 such pairs respectively.
  Background work, polling, truncated commands, and API overlap prevent this
  from being complete test execution time. Actual test wall time remains
  unknown until tool/child intervals are recorded. Existing runner counters
  report 6, 29, 18, 15, and 5 test invocations, respectively.
- The four completed rows dispatched no Agent tool. By contrast, gh-resolve
  `20261010-185714` iteration 04, **mimo-v2.6-pro**, reports **64.3 API minutes
  in 46.4 elapsed minutes**, with one background Agent and 196 subagent
  assistant messages. API time aggregates overlapping calls. Never compute
  `elapsed - API` as tool/test time; even without subagents it is only a
  residual diagnostic, not an exclusive phase measurement.

The dominant observed costs are thinking generation and many serial turns;
bootstrap is a smaller measurable opportunity. Tests are a major secondary
cost in the suite-opt-in session. Compaction also occurred: the first three
rows have one boundary each, lasting 204.0, 138.3, and 160.5 seconds; suite
opt-in has none. These are labelled compaction observations, not additional
non-overlapping buckets.

The interrupted Pro session has six explicit 429 retry delays totalling
**36.9 seconds**, 27,010 `system/thinking_tokens` events, one compaction, and
no Edit/Write. It remained active after the initial retries. It is evidence
of expensive unfinished investigation, **not a measured sustained API stall**.

Evidence root:
`~/.ilk-data/projects/users-chad-projects-github-inluck-net-<repo>-<key>/logs/runs/<run>/iter-NN.log{,.jsonl}`,
with keys `604d727` (ilk-skills) and `a7b5462` (gh-resolve). The external review
and original report live under the ilk-skills `runtime/analysis/` directory.
Their older API-stall interpretation is superseded here; original baseline
files are left intact rather than silently rewriting historical evidence.

## 1. Scope and immediate priorities

Prioritize measurement, a compact dispatch brief, and controlled same-task
thinking/effort experiments. Preserve acceptance criteria, gate inventories,
verdict policy, worker-home isolation, and frozen-base attribution. Keep
source-repository CI baseline reuse [deferred](../future-work/batch-verification-smooth-and-fast.md).

Do not add outcome caching, a new context file, stall termination, or live
upgrade machinery to the first change. No numerical default, timeout increase,
stall threshold, thinking budget, or recovery limit is introduced here.
Existing caps remain unchanged. Any future value must be explicitly labelled
**judgment call**, with its basis and falsifier, before being enabled.

## 2. Compact dispatch, without pretending I1 exists

**Existing:** R:6253-6261 injects the prior suite result; R:6313-6327 injects
a red notice; R:6331-6355 injects declared gates through
`skills/ilk-loop/templates/worker-gate-notice.md:1-8`. These are useful partial
mechanisms, not a canonical I1 context.

**New, narrow first change:** compose one brief from the already-selected
iteration target and existing resolved paths. Carry the master, exact subplan
path and step, physical skill/command paths, existing base-provenance evidence,
gate commands, and previous failure output/IDs. Label absent or inconsistent
facts unknown and use the existing refusal path; do not infer a new base.
Record the brief's source fields additively in existing JSONL records, not a
new `ctx-<i>.json`. Canonical resolution waits for D1; unified authoritative
records wait for D2's reader-by-reader migration.

**Existing defects to amend in that owner change:** R:6265-6268 rebuilds from
`PROMPT`, losing the prior suite prefix on an interjection. Compose prefixes
without discarding earlier sections. R:6343's suffix `find | head -1` can
select another plan ending in the same slug. Carry the selected exact path;
if unavailable, resolve exact identity and refuse ambiguity. Preserve all
declared and driver-derived checks; do not silently drop collisions.

The brief is a template, not new instruction authority. It points to project
instructions and the selected step without preloading the whole architecture
or backlog. Additional investigation remains allowed. It can prevent known
path/status rediscovery but cannot promise to eliminate substantive thinking.

**Observation, not a claimed solution:** the five sampled sessions have zero
root assistant messages with multiple tool calls. ilk-skills workers mostly
read through Bash; `hooks/no-duplicate-read.sh:51-52` only handles Read, and
worker `settings.json:33-38` attaches it only to Read. Encourage grouping
independent reads, but first compare grouped Bash reads and native multi-tool
calls on the actual provider. Whether serial behavior is a provider capability,
model choice, or prompting effect is unproven. A deny hook for arbitrary Bash
reads is not proposed.

## 3. Gate ownership later; outcome reuse removed

**Existing:** the worker notice requires fixing and rerunning gates inside the
same session. The driver also checks afterward. Do not remove that worker
requirement while the ownership transition is unbuilt.

**New, later:** the accepted worker-code-only architecture moves authoritative
gates/state to the driver. It first needs its write-scope hook and control-plane
ownership changes (`worker-writes-code-only-design.md`, section 6), including
protection of the evidence/data root. Worker claims are not proof.

Moving a gate out of the worker can make every red pay for another cold
session. Evaluate resuming the just-finished session with exact driver output
via `claude -p --resume <session_id>`, under the existing attempt and iteration
limits. Local `claude --help` advertises resume; **the runner does not use it**
(R:4629-4648 has no resume/session argument). Provider behavior, transcript
retention after interruption, hook scope, context freshness, and unchanged
worker restrictions are unverified. No resume is assumed to work on MiMo.
If the capability experiment fails, retain the current ownership protocol
until a fresh-session fallback is measured; do not ship a guaranteed cold
restart for every red.

**Removed from this revision:** gate-outcome reuse. We have no measured count
or duration of repeated identical authoritative driver gates. The JSONL gate
rows inspected for `20261011-061345` carry command/head/outcome but no duration.
Existing suite-result prompt carry-forward is diagnostic context, not permission
to skip current authoritative checks.

History matters: plan `2026-10-07m-a-reverify-carries-only-what-it-can-count.md`,
lines 39-54, confirms **R1** (changed-file/direct-import selection carried
false greens through shell/data/transitive dependencies) and **R2** (merged
counts double-counted outcomes). See also
[slow-verify dossier, section 8](../verification/slow-verify-dossier.md).
Plan `2026-10-10c-a-re-measure-reuses-what-cannot-change.md:32-34,68-70` keeps
current-tree suite outcomes fresh while reusing owner metadata; `_try_remeasure`
is absent from current `verification_record.py`.

No partial selection/carry or aggregate-count merge returns here. A future
reuse proposal requires measured identical whole-command reruns and a separate
review of the entire tree (including read untracked/gitignored inputs), host,
interpreter/venv/lock/plugins, HOME/PYTEST_ADDOPTS/ILK environment, worker count,
timeout, toolkit/hooks, inventory-derivation code, and any ledger/cache state.
Unknown/state-dependent inputs make reuse unavailable. A reference to a prior
attempt is never a new attempt or confirm run and cannot dilute the existing
flaky window. This is a reopening bar, not an implementation mechanism.

## 4. Improve existing recovery and enforce hermeticity

**Existing partial recovery:** R:1937-1988 stores run-local gate counts and
failing IDs; R:7240-7268 applies B2 and writes `gate-red-<i>.txt`;
R:5336-5366 reads `suite-result.json`; R:6282-6311 adds revert notices.
Amend these rather than create parallel records or another verdict policy.
The stderr-only writer at R:7261 omits pytest stdout failing IDs; the extractor
at R:2037-2058 reads stdout and stderr. Full output links and both streams
must survive when the existing carrier is expanded.

An additional source observation qualifies the review: R:1995 ends with an
unconditional `return 0` after the assignment/OR-list, before the reader body.
Therefore presence of the failing-set functions does not establish a working
read path. A later focused wiring check must verify it; no code is fixed here.

**New:** carry diagnostic history across launches by extending the existing
carrier/record migration, keyed by master, slug/step, tested tree, command,
and attempt ID. Today these artifacts live in RUN_LOG_DIR and a new run starts
with new files. Diagnostic carry never carries green proof. Existing B2,
confirmation, and caps stay; this design adds no new retry allowance.

**Already fixed locally, not future work:** gh-resolve
`tests/test_handoff_content.py:602-604` pins the timing data home. Commit
`437f0159` fixed live-corpus scanning after an earlier hermeticity change
`893ca72d`. Merely prescribing temporary fixtures again will not prevent
recurrence. The current test still locates a live skill at lines 580-584.

**New enforcement requirement:** use consumer test infrastructure to set
sandbox HOME/data-home and explicit toolkit fixture paths for relevant
subprocess tests, with a guard rejecting escapes and explicitly reviewed
integration exceptions. Preserve actual gate-budget coverage using seeded
fixture timing data when that assertion is required. I7 lint is a target,
not already universal enforcement. Existing ilk-skills `conftest.py:463-464`
host guard and `conftest.py:641-655` sandbox helpers are partial examples, not proof that gh-resolve
reads are isolated. This consumer-infrastructure work is separate from the
small dispatch change; do not alter consumer assertions through this prose.

## 5. Observation only; preserve diagnosis after a terminated print session

**Existing:** R:4629-4648 starts one `claude -p` process; total iteration
limits remain. The renderer records thinking, retries, and waiting events.
There is no runner session-resume or mid-session checkpoint-request channel.

**New observation:** distinguish explicit retry-delay totals, streamed thinking,
compaction, tool/child activity, and unexplained silence. The observed 36.9 s
of backoff does not justify a new stall-killing threshold. No stall termination,
new sentinel, watchdog policy, or backoff mechanism is added in this phase.
Only a measured sustained stall, with active-tool and thinking negative
controls, can motivate a later reviewed policy.

**Feasible new recovery:** after a terminated `claude -p`, extract the last
visible task question, tool results/failure paths, observed changes, and last
completed actions from the retained stream/log. Add a labelled diagnostic
summary to the next brief. It is transcript salvage, not a cooperative
mid-session checkpoint, completed findings, or ship evidence. Preserve dirty
work using R:5079's preservation function and its R:6745 call when it exists;
do not invent WIP in the
no-Edit timed-out case. Whether the CLI can resume that interrupted session
is a separate capability check in section 3.

## 6. Add measurements to existing records first

**Existing:** R:6384-6403 emits `started`; R:7386-7446 emits a summary with
model, duration, tool/test counters, and local checks. R:5989-5990 starts the
legacy timer and R:6628-6630 ends it before post-worker checks/cleanup.
Do not relabel that duration as whole-iteration or run time. I2 is not built.

**New first runtime increment:** additive fields on those existing JSONL
records for actual init model/provider identity (sanitized), session ID,
selected target/path, source provenance, turns, completed thinking seconds,
unfinished-block indication, retry delay total, compaction count/time, root
and child tool calls, and coarse bootstrap calls before first edit. Record
unknowns and parser versions. Do not claim these observations resolve I1/D1.

**New subsequent additive measurements:** driver gate attempt ID/command,
observed tree/input snapshot reference and inventory, monotonic start/end,
stdout/stderr artifact links, and separate commit/hooks, integrity, landing,
and cleanup intervals. Count repeated exact driver-gate invocations before
considering reuse. Preserve attempts and existing verdict semantics. Keep
nested/concurrent spans separate; never sum overlapping API or child spans
into wall time. Gate outputs must remain complete even if the display is brief.

There is no new context artifact or alternate proof ledger in the first phase.
Grow I2 from the existing records in the ownership design's D1-before-D2,
reader-by-reader order. Report model-specific per-step elapsed, thinking,
turns, discovery, observed tool/test intervals, reds, and recovery; keep final
verifier separate. Batch totals are context, not causal speed claims.

## 7. Pin all inputs; evaluate a drain-after-step exit

**Existing partial pin:** R:21-24 pins the physical release for the runner.
Worker commands/skills currently point through mutable `~/.ilk/current`;
worker settings/hooks are shared copied files. R:4663-4670 rereads settings.
There is no `ILK_CODE_ROOT` reference in R, despite ownership section 5's
plan-script condition. A symlink flip is not a safe worker upgrade.

**New complete pin requirement:** include runner/helper code, commands/skills,
hook executables and their settings/matchers, provider/model/effort settings,
and plan-invoked scripts, including derived inventories. Record CLI/toolchain
versions too. Protect secrets separately: provenance hashes/identities must
not log credential values. A per-run config home with pinned non-secret inputs
and explicit credential provisioning is a candidate, not an existing isolation
guarantee. Path injection alone does not pin CLI-loaded hooks/settings. Never
rewrite the shared worker home while another run uses it.

**New activation candidate:** a drain-after-step request names the next release
and exits only after the current worker, authoritative gates, required commit/
state writes, integrity and landing have completed. A red step follows its
existing recovery path; pending landing is not a completed drain. Preserve
master/base/step/WIP/attempt identities and launch the remainder as a new run
with the new toolkit SHA. This is a clean exit plus scheduler relaunch, not
an in-memory handoff. The old runner needs drain support; otherwise wait for
normal exit. No current worker is interrupted by this design.

A dedicated drained terminal reason (name not yet selected) requires coordinated
reader support before activation:

| Reader | Required drain interpretation (new) | Existing integration anchor |
|---|---|---|
| Scheduler | New release runnable only after old lock/process exit, normal hold/queue checks; preserve pending master, no failure blacklist | `skills/ilk-watchdog/scripts/scheduler.sh:753` blacklist handling |
| Watchdog | Do not restart the old pinned release; leave intentional relaunch to scheduler | `relaunch_guard.py:30-32` and watchdog policy |
| Postmortem | Intentional drain/clean stop, not shipped or failed; retains model/run/timing and pending work | `collect.py:1219-1234,1653` terminal classification |
| Status/stop | Old run terminal, next run has distinct identity; existing paths/shapes retained | `status_all.py:61-71,158-176` |
| gh-resolve | Must not treat temporary driver exit as permission to release/reap an unfinished consumer run | consumer `reap.py:6957-6970` treats a non-running ended sentinel as dead |

The gh-resolve check is especially important: deployment of a new exit reason
without its consumer mapping could change claim recovery. Reader parity on
PowerShell and bash, lock races, holds, and both old/new release combinations
need focused wiring evidence. The activation mechanism is deferred until
pinning and consumer agreement exist. Append-only future work does not justify
park/stop, and installing a release does not change an already-loaded shell.

## 8. Order, controlled evaluation, and decisions

1. **First: a log-cost report and paired-replay protocol** using existing
   streams, with model and parser identities. A report under `tools/` and a
   brief template are non-kernel work; they need not wait for all D1/D2 work.
   No task is dispatched by this prose revision.
2. **Small owner change:** additive JSONL fields, brief composition, exact
   target-path use, and stdout failure evidence in existing notices. No
   canonical context, outcome reuse, ownership migration, or stall killing.
3. **Early separate experiment:** same-task thinking/effort variation on the
   actual endpoint, model held fixed. CLI help advertises `--effort`, but
   endpoint support/effect is unverified; measure acceptance, not just speed.
   Model comparison is a distinct paired experiment, not mixed into brief A/B.
4. **Later dependencies:** D1/D2 migration, enforced write scope, and validated
   resume before authoritative gate ownership changes; hermetic guard wiring
   in each consumer. Complete pinning and drain mapping remain separate work.

Every runner change is kernel-tier work: use a supervised owner build.
Templates/report tools are non-kernel under `safety-kernel.json`; do not
unnecessarily subject them to the kernel freeze. Current enforcement allows
session-planned kernel edits (`safety_kernel.py:282,341-350`). Chad confirmed on 2026-10-11 that the freeze covers every loop-built kernel
change, including session-planned work, and the expanded landing paths.
Implementation uses a separate supervised owner checkout. Existing dispatched
work is not interrupted or rewritten; enforcement migration is separate from
policy acceptance.

**Comparable evaluation (new protocol):** retrieve each target's recorded
`heads-before-N.tmp` base, selected step and gate snapshot; verify those match
its history before replay. Use isolated replay worktrees/homes and the same
CLI, actual init model, settings/hooks, fixtures, provider and gate inventory.
Hold host concurrency/load comparable and record it. Compare baseline against
brief-only change first; vary effort separately. Predeclare repeated paired
runs to expose noise; number of repetitions is an unresolved experimental
choice, not a numeric default added here. Judge full step acceptance/diff
quality and identical focused gate outcomes, not elapsed alone. Do not turn
these replays into routine full-suite verification.

Opened replay candidates: ilk-skills `20261010-143933` iteration 14 records
`cd946b43896c91b7f292bc3ccd9054924c9525e8`; gh-resolve `20261011-061345`
iteration 05 records `a1623fba64efa588b682771343e895e7ea4f2912`. These are
iteration starting commits, not substitutes for the masters' frozen batch
bases. Both files exist; no replay was run during this review response.

**Judgment call:** prioritize thinking/turn reduction and the narrow brief,
not reuse or stall killing. Basis: the measured thinking share, low explicit
retry delay, and absent identical-driver-gate denominator. Wrong if paired
runs show no reduction, worse accepted diffs, more reds/turns, or new timing
shows an unmeasured driver cost dominates. No speed target is asserted yet.

**Decisions accepted by Chad (2026-10-11):** expanded kernel freeze and the
narrow reporter/brief plus early same-model effort experiment. No additional
policy approval is pending for that scope.

**Implementation investigation:** does MiMo honor effort/thinking settings,
can print-mode resume retain context under these hooks, and can independent
reads be grouped? Resolve with isolated experiments before depending on them.
Do not change worker defaults based solely on CLI flag acceptance.

**Readiness:** ready for review of the narrowed measurement/brief design.
Not ready to implement outcome reuse (removed), stall termination (observation
only), driver-only gates (write-scope/resume dependencies), or drain activation
(complete pinning and consumer mapping pending).


## 9. Owner candidate and evidence (2026-10-11)

The approved first scope is implemented in the isolated owner worktree on
`owner/loop-performance-20261011`, based on `6d7e8760`. It is a candidate;
not installed, published, or claimed to improve an active batch yet.

- **New:** `tools/loop-performance/report.py` reads existing streams and
  renderer logs. Actual-init models, root message identities/events, tool
  indicators, timestamped foreground-test intervals, completed thinking and
  CLI compaction durations remain separate clocks. Old manual turn counts
  counted assistant events; the report exposes both events and unique message
  IDs so the changed denominator is explicit. Missing evidence stays null.
- **New owner patch:** `run_ilk_loop_claude.sh:5628` composes the dispatch brief
  from the existing selected identity and plan; no I1/I2 state store.
  `:6379` resolves frontmatter identity rather than a filename suffix.
  Operator notice composition retains prior context. Existing gate notices,
  commands, proof authority and budgets remain unchanged. Retry notices now
  include stdout as well as stderr. Additive `worker_cost_observation` on the
  existing summary (`:7423`) is descriptive, never proof. PowerShell parity
  is not implemented by this Mac-focused patch.
- **New experiment:** `tools/loop-performance/effort_probe.py` uses temporary
  homes, explicit provider/model, disabled hooks/tools/session persistence,
  and the same synthetic gate-count task/acceptance. On actual-init
  `mimo-v2.6-pro`, default/low elapsed was 16.102/10.457 seconds in pair 1,
  low/default 11.987/7.860 in pair 2. All four passed the same cases;
  per-turn effort was reported inactive in all four. No default change.
  **Judgment calls:** two alternating pairs and a 60-second per-call bound
  are a cheap capability screen, based on a small tools-disabled task and
  Chad's request for prompt results. They cannot prove batch speed or effort
  support; observed reversal already defeats a speed claim. A representative
  repeated accepted-step comparison is required for any setting change.
- **Focused validation:** 72 tests passed in 6.31 seconds with isolated
  HOME/ILK_DATA_HOME/ILK_DATA_DIR. Includes actual detached entry delivering
  context to a stub CLI, summary fields preserving red gate authority,
  prefix-loss negative control, stdout recovery evidence, existing worker
  gate/carry-forward/phase callers, overlapping clocks and truncated logs.
  Shell syntax and whitespace checks pass. No full suite or final verifier.

Evidence is saved under external
`runtime/analysis/design-revision-20261011/worker-cost-report.json` and
`effort-capability-probe.json`. Foreground test time in the two reopened PRO
workers is 78.698 and 677.034 seconds, with complete matched timestamps;
thinking is 1404.1 and 1965.7 seconds respectively. The earlier coarse
foreground-call count is superseded by this parser's explicit union/count
model, not retroactively rewritten as a different measurement.

Rollout remains a separate required step: preserve active release pins,
reconcile the existing batch's pending context plan against the owner patch,
then activate at a clean boundary and measure comparable accepted consumer
work. Expanded freeze policy is approved; runtime enforcement, I1/I2,
drain-after-step, driver-only gates and outcome reuse are not built here.


### Later authorized manual candidate (unreleased)

The durable [owner tracker](../implementation/loop-execution-improvement-plan.md)
supersedes section 9's initial patch inventory; it records checkpoint-specific
checks and open migration work. The candidate now has canonical frozen context,
additive open/close records and selected reader migrations, close-projection
recovery, full gate artifacts and durable strict retry history. I1/I2/I3 remain
partial because unmigrated consumers and writers still exist. PowerShell
function/syntax checks passed on Mac; Windows entry is not proven. New bases
carry owner-only kernel policy; release ranges retain the policy at their base.

An additional old-run measurement found MiMo PRO iteration 04 of
`20261011-102057` timed out without an observed edit: 48 unique root turns,
81 tools, 3,117.0s completed thinking in a 3,599s renderer span, and a matched
foreground test interval of 0.178s. Thinking excludes its unfinished tail;
compaction 155.949s overlaps those clocks. No final result supplies API time.
This strengthens the thinking/serial-investigation problem statement without
promising savings from brief delivery alone.

Actual MiMo PRO tools-disabled session resume retained its session and opaque
prior context. Subsequent full-entry opt-in thinking-control red recovery passed
with production hooks (55.559s wall, same session, two turns/gates, one commit/CAS).
This is a synthetic recovery capability sample; representative consumer quality
and speed remain unestablished. Driver-only gates are not activated on installed
hosts. Kernel admission and journaled CAS step advancement are candidate authority
changes, not proof of completed ownership migration.
No full suite, deployment, measured consumer improvement or gate-outcome reuse
is claimed. The watchdog restarted the old installed runner; it is waiting at
the existing pause flag. Activation still requires fresh lifecycle checks.

### Candidate boundary evidence (manual owner implementation, 2026-10-11)

Independent Mac profiles were insufficient: nested `sandbox-exec` measured
exit 71 (`sandbox_apply: Operation not permitted`). The binding API refuses
that adapter. New candidate `skills/ilk-loop/scripts/worker_broker.py:23`
keeps shell execution outside the CLI sandbox and accepts only shell requests;
`worker_shell.py` is the immutable IPC entry, not a state/commit/proof API.
Actual composition fixtures deny plan writes, network and foreign signals.
The observer admits exact wrapper quoting and binds context/mode explicitly.
Per-context settings are additive; the prepared base home stays immutable.
This exists in the owner candidate, not the installed runner. Subsequent
POSIX registry, descendant cleanup, actual CLI enforcement and scoped interruption
checks are recorded in the manual tracker. Remaining adapters and consumer
validation are prerequisites for rollout. No batch speed claim follows from
these fixtures. Test process bounds of ten seconds are judgment calls based
on subsecond fixtures; a valid slow host would show the bound is wrong.

Recovery keys now use the existing `plan_fingerprint.surface` normalizer,
including master registry Status and Progress log exclusions. Ordinary driver
state updates retain counts; real plan contract edits change identity. This
stores diagnostic history only, never reuses a green gate outcome.

The actual MiMo PRO CLI with copied production hooks now passes brokered
same-session driver-red repair, and an owned interruption after completed tool
result resumes to green. Evidence is retained externally in
`design-revision-20261011/*brokered-resume-capability-probe*.json`; no batch
speed inference. Claude scratch paths require both CLAUDE_CODE_TMPDIR and
CLAUDE_TMPDIR pinned to private temp. Kernel list is frozen in context;
OS denial includes listed paths, basename creation and ancestor renames.
Broker cleanup handles idle connections and background descendants. The owner
candidate now has an additive POSIX registry (`run_registry.py:1`) and driver
controller (`worker_iteration.py:1`); Windows lifecycle adapters and rollout
remain incomplete. These statements describe the candidate, not installed code.

The owner candidate now contains `driver_step.py:1`: alternate-index tree
capture, driver gates, driver commit and one-step CAS advancement. It checks
that gates did not mutate the shipping tree; changed commit-hook output needs
another measured gate, while an unchanged commit does not repeat identical
gates. `plan_state.transition` has same-journal master guards checked under
lock. Mac Bash caller integration, finding import, bounded commit hooks and
close/ship projection now have focused archived-entry evidence recorded in the
manual tracker. Member-repo/baseline-red and remaining landing/state adapters
are incomplete. Existing worker-runs-gates instructions still apply to the
installed runner. Neither I1 nor I2 is complete across all callers/readers.

`plan_state.py:155` now journals registry Status projection under the shared
state lock, with byte CAS and BOM/CRLF preservation. `plan_fingerprint.py:78`
provides exact registered-row parsing, including legacy headings. This follows
measured reconciler behavior rather than the older named-section-only prose;
unknown/unregistered rows remain contract content. This normalizer now belongs
to the protected owner kernel because driver contract/retry identity uses it.
Neither status projection nor retry history produces green gate evidence.

### Measured provider control mismatch (owner candidate, 2026-10-11)

The installed actual Claude CLI sends `thinking: adaptive` with effort high
for MiMo PRO. `MAX_THINKING_TOKENS=0` and `alwaysThinkingEnabled=false` omit
thinking instead of sending disabled. A local fake SSE endpoint measured all
three request bodies without real credentials or provider calls. MiMo's
[official Anthropic API](https://mimo.mi.com/docs/en-US/api/chat/anthropic-api)
documents thinking enabled when omitted and supports explicit disabled.
CLI effort selection therefore cannot establish provider thinking-off.

Two exact-entry synthetic MiMo PRO runs with eight copied production hooks
reached the unchanged 120s fixture deadline without an edit or acceptance.
An explicit-disabled private manual proxy experiment completed in 47.732s wall,
41.840s observed CLI, with one accepted gate/commit/CAS, positive integrity and
clear registry. Model/init provenance and limitations are in the manual
tracker and `full-entry-provider-thinking-disabled-coding/`. The experimental
proxy also removed output_config; it does not isolate that field's contribution.
This is capability evidence on one synthetic task, not a representative speed
or quality comparison. No default/provider settings were changed.

New mechanism `provider_control.py:1` is an opt-in owned MiMo adapter. It
replaces only thinking with explicit disabled, retaining other request fields.
`run_inputs.prepare` freezes worker_thinking (default remains default) and
refuses an unsupported endpoint/model or a snapshot lacking the adapter.
The Bash launcher accepts `ILK_WORKER_THINKING=disabled` only with code-only
dispatch. Each CLI turn gets a private loopback capability; upstream credentials
remain in the driver. Authenticated messages/count-token requests must use the
pinned model and official HTTPS endpoint; redirects/other paths refuse. Private
client settings live inside the existing protected settings directory and are
deleted after joined server cleanup. It retains the original iteration deadline,
CLI retry/budget behavior and same-session resume. No persistent proxy daemon.
This adapter is candidate-only; actual entry/recovery and representative
consumer quality are required before choosing any faster default/profile.

Numeric implementation choices are judgment calls: 64KiB read1 chunks use a
common bounded transfer buffer and return available stream bytes; unexpected
buffering or added time-to-first-output falsifies the choice. The stdlib server's
0.5s shutdown polling is retained initially; material per-turn cleanup cost
would justify a measured shorter interval. Neither creates a new run timeout.
The 120s fixture deadline stays unchanged; valid slower completion falsifies
that experiment bound, not the production run cap. Final verification remains
opt-in; this work invokes no routine suite.


### Prepared-pin activation and tool-profile experiments (owner candidate)

`upgrade_drain.py:1` and the ordinary launcher now implement opt-in accepted-step
request validation, a drained terminal record and prepared-pin relaunch after
verified old PID/children/lock exit. Frozen master/contracts, gates, iteration
limits and budget are retained; an invalid request refuses an old-pin restart.
The owner must first install compatible consumer readers and attest that fact.
That attestation is a trust boundary, not automated compatibility proof.
Archived Mac launcher checks include live old PID, human hold and request tamper
refusals, actual distinct new pin/run and original limits despite caller overrides.
No installed host has activated this protocol; Windows activation remains unbuilt.

The isolated consumer candidate treats both `upgrade-drained` and
`upgrade_request_invalid` as unfinished work. Reap, claim release, reconciliation
and GC preserve it through failed activation; unknown states retain their previous
policy. Exact shared-key worktree inventory supports cleanup readers without a
master argument. A malformed upgrade master identity retains a known worktree;
a valid different absolute master still excludes it. There is no TTL release.

Production-hook MiMo PRO consumer samples with explicit thinking disabled
failed at 120s (two samples: 123.071s and 123.226s wall), and 240s (243.227s
wall). The latter made 38 calls and preserved a partial edit as an unaccepted WIP
commit, with zero authoritative gate/advance/ship. Root-role instructions reduced
toolkit discovery but did not demonstrate speed. The 240s fixture bound was a
judgment after measured discovery falsified 120s; neither changed production
limits or renewed an existing run. Failed shell-wrapper quoting and attempts to
place patch scripts under protected run logs are observed costs, not API stalls.

New opt-in `worker_tools=native` in `run_inputs.py` / `worker_iteration.py`
allows native Read/Grep/Glob/Edit/Write inside the existing Mac CLI OS boundary,
with mandatory scope hooks. Bash still uses the exact broker entry. Tool profile
is frozen in context and checked against prepared inputs before each turn.
Shell-only remains default. Native tools do not gain plan/data/proof/Git/kernel
write authority, commit or shipping authority. Native entry checks pass ordinary
code edits while refusing plan/Git writes; actual consumer feasibility and
controlled speed/quality comparison remain required before selecting a default.


### Strict selected-gate coverage (owner candidate)

New candidate behavior in `driver_step.py:88` allows existing suite baseline-red
declarations while granting no waiver: every selected subplan, step and mention
gate must pass on its raw exit. Attempts record this policy and the declarations
as diagnostics. This requires no full-suite baseline measurement. Baseline-red
exception attribution remains unbuilt and cannot establish acceptance.

New contexts freeze `mention_gate_policy=bounded-existing-cap` by default, retaining
existing optional mention discovery: more than 20 related test files warns and
skips that optional inventory. All authored subplan and step gates remain mandatory.
Driver attempts record discovered files, omitted files, cap and selected policy;
acceptance proves selected gates, not exhaustive related-test coverage. The
existing cap is a scope judgment, not measured performance evidence.

Explicit `mention_gate_policy: complete` opts into subtracting authored full-file
coverage and refusing remaining uncovered inventory beyond the cap. Descriptors
without a frozen policy conservatively require complete coverage on a fresh gate;
already accepted same-tree recovery does not run another gate. No policy triggers
a full suite, raises the cap or waives an authored command's raw failure.

Measured correction: the installed 2c3b4039 native consumer experiment completed
its worker repair and 12 diagnostic tests, then refused acceptance on 87 uncovered
related files. Wall time was 226.468s; authoritative gate/commit/CAS counts were
zero. Making complete coverage the default contradicted the existing bounded
optional-gate design. The earlier 21-file synthetic negative established the
stricter behavior, not a defect in the designed bounded policy. Complete coverage
is now explicit opt-in. The failed experiment remains failed; its worker diff is
preserved and cannot establish accepted-step or speed evidence.

### Named instruction inventory (new candidate)

`instruction_inputs.py` inventories named AGENTS.md, CLAUDE.md and
CLAUDE.local.md inputs from tracked, untracked and ignored repository paths,
plus ancestor named files and ancestor .claude/CLAUDE.md. Each row freezes path,
resolved target and content hash, without file contents. Preparation uses the
original deadline and registers its Git discovery children before exec. Every
provider turn and acceptance validation compares the inventory; recovery uses
its existing supplied driver deadline, without renewing a provider budget.
`worker_sandbox.py` denies creation/mutation of these named files at any depth;
native admission gives instruction writes their own denied scope. Existing
root bootstrap remains brief; inventory is not a request to read every nested
file. Referenced arbitrary imports, source global-home instructions and native
Windows enforcement remain unbuilt; this is not exhaustive input pinning.

### Focus worker diagnostics on implementation (new brief revision)

The corrected-policy native sample spent 46.607s in observed foreground
worker diagnostic-test intervals, including 253 adjacent tests after its
focused repair. Its authoritative driver gate took 0.984407s. These are
observed intervals, not a causal subtraction from wall time. The worker brief
now asks for the smallest diagnostic needed to locate/repair a defect and
leaves routine authored gates to the driver. A broad adjacent run needs a
driver diagnostic demonstrating the need. No authored gate is removed and
no worker report grants acceptance. This is a performance hypothesis requiring
actual acceptance/quality measurement, not an established speed gain.
