# ilk-skills delivery tracker — D1–D3

Status: ACTIVE. Chad's2026-10-12 instruction supersedes the scope-freeze reporting stop. Continue until all three outcomes below are achieved; stop only for an actual Chad decision.

Owner worktree: `/Users/chad/Projects/github/inluck-net/ilk-skills-worktrees/owner-loop-performance-20261011`.
Side branch: `owner/loop-performance-20261011`. Full evidence/report: `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/owner-readiness-report-20261012.md`.
Previous trackers archived in that directory and Git. No reconstruction from chat needed.

## Definition of done, in order

- [ ] D1:11b and10c ship unattended on194or later, accepted by driver gates. No hand edits to plan progress/status.
- [ ] D2:one actual code-only multi-step batch accepts≥2steps without human intervention, wall time per accepted step≤comparable default-path steps. If qualified, release as default on BOTH hosts; otherwise record measured root cause and keep opt-in.
- [ ] D3:all9backlog rows below land from side refs and release on BOTH hosts.

Rules: no full suites; focused tests only. No hand commits on main under live batches. Land owner side refs at batch boundaries. Never park/stop a progressing batch to apply owner work. Every release gets an honest narrow tag, installs on both hosts and verifies scheduler head+decision/deploy status. Keep rezmac host-compat.json. Code-only/default switch requires D2 qualification, not fixtures. Each milestone updates this tracker and the readiness report; continue afterward.

## Resume here

1. Finish D1 blockers:14baseline gate reds verified at194; choose actual repairs, never blind deselections.11b worker is currently repairing remaining runner/harness failures; leave it running. Owner side fixes below are prepared independently.
2.10c has accepted step0. Inspect remaining-gate base reds under correct staged toolkit/role prerequisites; distinguish missing prerequisites and corpus dependency from code defects. No proof from a union-only import-name collision.
3. Observe both live runs through accepted steps/shipped. Fix demonstrated blockers with failing tests from side refs. Do not alter frozen live gates/state.
4. After D1:repeat code-only trial with declared/effective context and orientation/API diagnosis applied. Compare equivalent default steps; keep separate uncontrolled replay out of qualification evidence.
5. Work backlog in explicit priority order below; release each landed increment to both hosts.

## Delivered release

v0.9.194 =57efa856e352bbea59e724bc9077f5fff69b95fa, published side ref. Both hosts installed, scheduler exact head+fresh decisions+deploy status ok. Six original defects fixed4010e7e2 (+state precursors33b45c86/e5fbbc49/ae7873ce); failing→passing focused evidence in report. Code-only stays opt-in.
Rezmac gh-resolve8d079263→3987a299 activated while idle; doctor exit0, host-compat hash preserved. Installer legacy read-only projects bootstrap limitation remains recorded.

## Current batches

-11b:replanned before unpause; overlapping3/5/7retired, remaining2/4/6, base194. Current run20261012-102830,PID83651,194frozen inputs. Plan2step1 driver pass; plan2 driver-shipped. Original resumed run timed out; WIP54e52a6f preserved. Worker implementation commitf0a287f8 exists, with1122passed/2skipped/2xfailed in118.64s worker gate; driver pass confirmed.
-10c:resumed after10b; plan4unblocked, hold/yield cleared, base3987a299. Current run20261012-101406,PID38716,194frozen inputs. Step0driver pass11.13s, driver elapsed967s; step1driverpass; step2attempt rejected, iteration4retry. WIPcc2a7a37 from original timeout preserved.
-Final verification remains opt-in for both; no final suite scheduled.

## Measured repairs, pending boundary landing/release

-5c31956d:ship_audit missingosimport.2CLI reds reproduced;3ledger checks passed1.58s.
-ddcdfc31:inconclusive gate removes unaccepted same-iteration self-ship only. Real negative reproduced;7runner checks passed79.42s. No code rollback/quarantine attribution from an unmeasured gate.
-c68318ed/a1cefd0f:hermetic required-verifier/timeout/prelude fixtures and frozen baseline corpus; exact-source importer parse reuse invalidates changed bytes.82fixture checks4.01s;46scope/corpus/caller checks5.56s;38adjacent checks passed+1existing skip20.04s. No assertions removed or17s cap raised.
-Parse cache4096entry limit is a judgment margin over toolkit test corpus; eviction churn or memory growth falsifies it. Same-size source changes tested.

## Worker time/context diagnosis

All measured runs actually use mimo-v2.6-pro. Old11b thinking1798.06s+reportedTTFT993.74s; old10c1779.56s+TTFT917.37s; code-only1917.74s+TTFT676.87s. Discovery-intent calls93/27/88 respectively. Code-only1230.25s explicitly reported thinking before first native edit1895s after launch. Synchronous discovery replies23.762s total. Tool/TTFT/thinking clocks have limits; async/network/text time remains unallocated. Raw models and classification are in worker-time-*.json.
Old runs auto-compacted~167k. New11b processed211548input tokens without compaction; CLI result now reports contextWindow1000000;238148 actual input tokens observed with no compaction. Backend handling a full1Minput remains unmeasured. Three localMiMo homes nowdeclare1000000; six rezmac homes already did. Active pins unchanged. Judgment basis: documented model capacity and eliminated early threshold; latency/context errors/renewed early compaction falsify usefulness. Continue observing init/context/compaction events.
Original code-only trial085944 timed out3603s,0/2accepted,0driver gates. Extra scheduler replay100903 stopped/held, work preserved, never counted as code-only qualification. New trial not launched before D1.

## D3 priority order (owner judgment, no priority field on filed rows)

Default-path correctness first, then execution coverage, then bookkeeping. Reorder only on new blocker evidence.

|Order|Backlog ID|Work|Status|
|---|---|---|---|
|1|c42945c349bea1d9|Remaining legacy liveness readers/process probes|open|
|2|0f13f9b73fe854ce|Default worker state through CAS|open|
|3|53e0f8975ceee8b6|Canonical context/record migration, remainingB1/B2|open|
|4|104f5973a66095ee|Manifest effort and pin linting|open|
|5|7a70deed5a416a41|Consumer reaping honors data root|open|
|6|4b2947283759a109|Member and dirty/timeout execution adapters|open|
|7|106e0d75fed786fc|Full PowerShell owned execution parity|open|
|8|d568b68a4c7a5cec|Native Windows ownership adapters|open|
|9|b1b81aeb2aef2ace|E1/E2/E4 performance/verdict bookkeeping|open|

Additional side repairs ready: b34859e3 early integrity scope (8passed/1existing skip);3529ee25 registry/taxonomy(15passed);aeb5bd79 measured-green rejection fixture(7passed);3fbf9346 shell floor(14passed);156065e9 compaction reset+event install(56passed);509312ff/b7e7b3bc private child dispatch isolation(8passed). Full dated history and evidence remain in readiness report/archive.

## Latest milestones

2026-10-12:first accepted steps confirmed for both projects;11b plan2shipped.10c step2timeout/rejection remains unaccepted,3856s worker/4382s driver;iteration4recovery started automatically.11b postmortemstep0 retry ongoing. No hand progress edits.

2026-10-12 12:53:private child also inherited code-only mode, reproducing iteration_context_invalid. Same boundary isolation now clears mode for this legacy red-rejection fixture; parent stays restricted. Single regression passed7.10s. No production semantics changed; no full suite. Continue D1driver gates, then release/D2/D3.
