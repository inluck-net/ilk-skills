# ilk-skills delivery tracker — D1/D2 only

ACTIVE; updated 2026-10-12T14:59:09+08:00. Chad reassigned owner work to D1 and D2. **D3 is deferred until D2 is resolved; do not start a row.** Earlier whole-checklist/reporting-stop instructions are superseded.

Active integration worktree: `/Users/chad/Projects/github/inluck-net/ilk-skills-worktrees/owner-release-integration-20261012`, branch `owner/release-integration-20261012`. Original owner source: `../owner-loop-performance-20261011` at dd493098 (tracker prose may be dirty). Full dated evidence: `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/owner-readiness-report-20261012.md`. Git and earlier handoffs retain history; handoff2's estimation-only pause is revoked by Chad's continuation.

## Outcomes and order

- [ ] D1:11b and10c masters show shipped with loop-authored ship commits; unattended runs on194+; no owner plan-state edits.
- [ ] D2:real two-step code-only feature qualifies, or is ruled out after at most3failed attempts with measured root cause. Qualification requires>=2driver-accepted steps, no human intervention, and all-attempt wall/accepted step no worse than comparable default steps. If qualified, release core-loop default on both hosts, verify, then observe a live accepted step. If ruled out, keep opt-in.
- D2 may start once11bships and195is verified on both hosts; it does **not** wait for10ccompletion.
- D3:all9backlog rows remain deferred. Canonical IDs/order in `owner-scope-freeze-backlog-20261012.json` and readiness report; no work started.

Rules: focused tests only, no full suites/final verifier unless explicitly changed. No main hand commits under a live batch. No stop/park/hotpatch of progressing runs. Side-ref landing at toolkit batch boundary. Both-host release verification includes scheduler exact head, fresh decision and deploy status; keep rezmac host-compat.json. No preview flips, gate-result reuse, fabricated batch proof, or silent attribution of baseline reds to workers. Fix only evidenced blockers with failing controls. Dated milestones here and in report, then continue. Label numeric/context/model/effort changes with judgment basis/falsifier.

## Current state

- Installed/published194:57efa856e352bbea59e724bc9077f5fff69b95fa on both hosts, scheduler exact head and deploy ok. Code-only opt-in. Six freeze-brief defects already delivered4010e7e2 plus precursors; do not redo.
- Rezmac consumer activation DONE:8d079263→3987a299 while idle; doctor0; host-compat hash preserved. One contacted gh-resolve owner serves both hosts; no separate rezmac session. No further consumer changes.
-11b:master active, base194. Registered1/2/4loop-shipped; launcher6has2steps left. Retired3/5/7helddrafts outside registry. Run20261012-133021/controller60813, iteration4launcherstep0 active at last check. Postmortem step1 driver accepted14:26,1536s, ship580d05d7.
-10c:master active, base3987a299. Plans1–6loop-shipped; planning7three steps, then delegation8two steps. Run20261012-101406/controller38716, iteration6planningstep0 active. Parallel plan6ship494f3ef0, driver3778s. Final verifier9helddraft outside registry.

## Prepared integration / next release

-7cd35573 merges accepted main snapshot8e5b1fbf into owner source; all4conflicts resolved. Stronger child fixture/native-mode controls, narrow inconclusive reconciliation and worker canonical gate inventory retained. Side branch pushed through e8bb370f tracker milestone.
- Actual code commit validation:10focused files,108passed132.94s; runner/install/duplicate-read hook Bash syntax all0. Evidence `integration-7cd35573-focused-20261012.json`.
- Main advanced to580d05d7 afterward (5commits: child fixture, taxonomy docs, narrative repair, bump, ship). Wait until11bships, then merge its final HEAD on this side ref, reconcile duplicate owner fixes, run final scoped validation/syntax, and cut authorized direct195. Do not main-merge/tag before11bship.
- Draft change-scoped selection402test files (changed tests + production importers/readers), not executed; recompute after final merge. No full suite selected.
- Pre-ship audit reads an old shared batch verdict02753d20 as stale_head; no11b-specific record exists. Keep automatic refusal. Direct-release audit may use supported explicit manual gate input **only after fresh applicable scoped gates pass**, with author/provenance stated in tag. No replacement verdict record or independent-batch claim. Draft tag body/evidence in analysis directory.
-195activation: use each host's upgrade/install path, scheduler launchctl bounce; reconcile released PostCompact event in all numbered worker homes, verify head+freshdecision+deploy, preserve host-compat. Active10c keeps frozen194.

## D2 preparation / comparison

- Not launched. `D2-predeclared-comparison-20261012.json` and `D2-trial-preparation-20261012.md` record protocol.
- Comparable default postmortem feature:6finished attempts,2accepted,9377driver seconds =78.14min/accepted step. Failures, timeout and refusal retained. Count all trial attempts and disclose controller gaps separately.
- Trial:private DataHome outside watched ~/.ilk-data; real pre-feature source; unique trial slug; same authored two-step gates; released root conftest/child-authority isolation; MiMo-v2.6-pro/default thinking/1M declaration. Code-only native coding tools match used default Read/Edit/Write/Bash; advertised tool sets differ and actual-call/gate comparability must be checked. PostCompact reset is an explicit195profile change.
- No steering during an attempt. If failure, diagnose stream clocks/first-edit without summing overlaps; only evidenced cause fixes between attempts. Three failures is Chad's bound.
- Original085944trial timed out3603s,0/2accepted,first edit1895s; uncontrolled default replay was stopped/held and is never qualification evidence. New trial starts after both-host195activation.

## Planning / reporting

Plan with independent all-attempt throughput60–95min/accepted step and~1/3iterations without progress. Estimates: D1critical path5–8h; integration+release2–3h; D2hands-on5–10h over1–2days. These are planning judgments, not promises. `D1-D2-delivery-clock-20261012.json` records continuation14:39 and evidence; distinguish hands-on from waiting and report actual hours when D2 resolves.

Latest milestones2026-10-12:14:26postmortem loop-shipped;14:40parallel-readiness loop-shipped;7cd35573integration committed,108focused tests/syntax passed;comparison fixed78.14min/accepted step. Hosts and live frozen inputs unchanged. Next:observe launcher/planning steps, final integration at11bship, direct195bothhosts, then unsteered D2; no D3.
