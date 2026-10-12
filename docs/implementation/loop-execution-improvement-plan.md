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
-10c:resumed after10b; plan4unblocked, hold/yield cleared, base3987a299. Current run20261012-101406,PID38716,194frozen inputs. Step0driver pass11.13s, driver elapsed967s; step1driverpass; step2next. WIPcc2a7a37 from original timeout preserved.
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

## Dated milestones

2026-10-12 11:02:D1–D3 authorization recorded; both workers checked live, no interruption.
2026-10-12 11:15:10c first accepted step verified; independent owner repairs tested/committed as above. Context declaration applied/verified across worker homes. Next:finish base-trap diagnosis and first11b acceptance, then D1shipment.

2026-10-12 11:29:both first accepted steps confirmed;11b plan2driver-shipped.10c baseline traps reduced to2actual fixture reds; side refe555971f,25+9focused checks passed. Runtime next-iteration evidence queued, no frozen plan edits. Continue D1.

2026-10-12 11:36:11b and10c dispatched their next iterations. CLI result confirms11b window1000000;10c old pin still200000. Measured integrity phases50.06s(ilk),65.65s(gh); hundreds of foreign shipped plans were parsed before scope rejection. Side optimization skips them before Python readers; failing startup-count regression reproduced,8focused checks passed+1existing skip22.92s. No live pin/gate/state edits.

2026-10-12 11:46:next11b gate base traps verified.3529ee25 fixes optional registry/taxonomy;15no-installed-home checks green. Measured-green red rejection fixture:old control fails,new7checks pass21.27s. Next-iteration evidence queued; live plans unchanged. Continue D1.

2026-10-12 11:49:remaining31existing gate-file selection closes on side tree:317passed,1existing xfail22.55s. Future feature tests remain absent, so not feature acceptance. D1workers progressing; continue.

2026-10-12 11:57:launcher shell caller matrix2old expectations contradicted released worker floor; corrected14checks pass, explicit planner/invalid-engine cases intact. No production engine change. Continue D1.

2026-10-12 11:59:postmortem step0 declared8green but derived callers2red; not accepted. Driver restored step0 and iteration3retry dispatched automatically. Owner fixture diagnosis available; no hand state edits. Retain1533s failed attempt in D2comparison.

2026-10-12 12:23:verified post-compaction read-denial blocker: session ledger survived compaction and caused Bash reread workarounds. Hook now resets only that session on PostCompact; installer declares worker-only event, preserving foreign entries and idempotence. New controls failed before fix; two focused hook files:56passed5.97s. Active frozen inputs untouched. Continue D1, then narrow release at boundary; no full suite.

2026-10-12 12:26:nested runner fixture inherits ILK_WORKER_SESSION before module fixtures; reproduced interrupted instead of gate rejection. Private child now clears only that ambient worker restriction; actual parent remains restricted. New negative reproduced;8checks passed28.58s. Live worker independently investigating same cause; no owner main edits. Hosts re-probed:194 exactschedulerhead,deploy bothok. Continue D1.

2026-10-12 12:43:stronger nested-driver regression also seeds ambient CLI/code/input pins. Clearing worker restriction alone reproduced interrupted; private child now clears live-dispatch inputs as well.8checks pass28.86s; parent restriction remains asserted. This completes the same fixture isolation fix, not a gate waiver. Continue D1.
