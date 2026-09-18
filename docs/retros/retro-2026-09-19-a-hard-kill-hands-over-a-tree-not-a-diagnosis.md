# Retrospective — a hard kill hands over a tree, not a diagnosis, 2026-09-19

**Status:** open — observations recorded, no fix authored. Improvements
deferred by operator decision (2026-09-19): "we will consider improve on
this later."

**Project affected:** keyreply/kira-cloudflare, batch `pv4-rereview`
(MASTER-2026-09-18-pv4-rereview), sub-plan `pv4-session-link`. Observed
live overnight Sep 18-19 from the ilk-skills session; every claim below
was verified against logs/code in that session.

## 1. What happened

Iteration 4 of run `20260918-234608` (started 00:36:26) worked
`pv4-session-link` for its full 45-minute window and was killed by
`gtimeout` at exactly 01:21:26 (00:36:26 + 2700s) — no `[done]` line, no
commits. The window's shape, from `iter-04.log`:

| Window | Activity | Evidence |
|---|---|---|
| 00:37-00:48 (~11 min) | deep-stack exploration: `livekitToken.ts`, `agentBuilder.ts` (read around :3296, :4346), `schema.ts` (6800 lines, read in slices), `agentDefinition.ts`, `mcpRuntime.ts` chain | tool timeline |
| 00:48-00:58 (~10 min) | the edits: 3 files (`convex/patientVerification.ts`, `PatientVerificationPage.tsx`, its test) | `git status` |
| 00:58-01:21 (~23 min) | test-debug loop, never green: 12+ `bunx vitest run PatientVerificationPage.test.tsx`, 3 scratch repro files (`/tmp/test_debug.tsx` 01:01, `_debug_test.test.tsx` 01:01, `_minimal_room.test.tsx` 01:20) | `iter-04.log:490-927` |

Zero commits + completed=0 → stop reason `"timeout"`
(`run_ilk_loop_claude.sh:2205-2207`) → run ended `=== Loop ended:
timeout ===`. The scheduler re-dispatched at 01:26:34 (run
`20260919-012634`) with no-progress count **0** — correct: the same run
had shipped `pv4-mint-authz` (iter-03, 3/3), and progress resets the
launch-level counter (`scheduler.sh:466-469`).

The successor resumed in ~2 minutes: read plan (01:28:00) → read the 3
dirty files → `git diff` on each (01:28:57-59) → ran the failing test
(01:29:19) → continued editing (01:29:40) → moved forward to the Audio
test and convex tests (01:30:00-26). **No re-exploration, no
restart-from-zero.** The recovery design worked.

## 2. What did not work, or works only by accident

**G1 — WIP preserve no-op'd silently.** The designed safety net,
`preserve_dirty_tree_on_timeout` (`run_ilk_loop_claude.sh:2914-2917`,
called when `ITER_COMPLETED=0`), should have committed a `[wip:timeout]`
snapshot of the dirty tree. It did not: `git log` head unchanged
(`34a5294db`), no WIP commit, tree still dirty. The launcher log's only
"WIP" match in the entire run is an *agent-side* `git stash` inside
iter-03 (00:28:20, `launcher …234608.log:1343`, alongside a
`bash: scripts/no-ab…` error suggesting a missing hook script). The
function's failure path (`set +e`, log-and-continue) left **no trace**,
so the safety net failed without anyone — including the retro — able to
say why from the artifacts alone. Recovery survived only because the
dirty tree itself persists across restarts, which is a filesystem
property, not a runner feature.

**G2 — a hard kill skips the diagnosis hand-off.** The designed
hand-off channel between iterations is the sub-plan's `## Findings`
section ("filled by the loop during execution"). A killed agent cannot
write it: `pv4-session-link.md`'s Findings is still empty after 45
minutes of real debugging. The successor also never reads the
predecessor's log — 0 matches for `20260918-234608` / `iter-04` /
`.ilk-loop.log` in the new run's `iter-01.log` — which is by design
(plan + tree are the state). Net effect: the *edits* and even the
scratch repro files carry over, but the *understanding* of why the test
is red does not; the successor re-derives it from the diff and the
failing output. Tonight that cost minutes; its true cost scales with
how hard-won the diagnosis was.

**G3 — the in-run streak guard cannot bound barren-timeout relaunches.**
A barren timeout ends the *run* at the first occurrence
(`stop_reason="timeout"` → `break`, `run_ilk_loop_claude.sh:3326-3329`),
so the in-run `no_progress_streak -ge 3` guard (run-local, reset at
`:2677`) never accumulates across timeout cycles. The effective bound is
the scheduler's launch-level counter (3 consecutive non-clean,
no-progress launches, `scheduler.sh:428-483`) — which exists and is
correct, but (a) resets on any progress in the run, so a batch whose
runs alternate *ship → barren-timeout, barren-timeout* never
accumulates toward it, and (b) is documented in its own comment as
absent for manual `/ilk-run` + watchdog with no scheduler
(`scheduler.sh:438-440`).

## 3. What worked and should stay

- **Dirty-tree persistence across restarts** — the actual recovery
  mechanism tonight. Work accumulated across the kill; the successor's
  first substantive act was `git diff` on the inherited changes.
- **Timeout → run stop → whitelist relaunch** — 5-minute turnaround,
  no banner, no human needed.
- **Progress-reset on the launch counter** — the mint-authz ship kept
  the counter at 0; a slow-but-advancing batch was not mis-blocked.
- **Two-strike quarantine, earlier the same evening** —
  `pv4-reimport-guard-delete`'s genuinely-failing parity check
  (56-line `appsPlatform.ts` diff vs `origin/dev`, still open, needs a
  human decision) was isolated and the loop continued to runnable work.

## 4. Candidate improvements — deferred, not authored

| # | Idea | Addresses | Note |
|---|---|---|---|
| C1 | Make WIP preserve loud: on failure, a stderr line + a `last-exit.json` field naming the repo and the commit failure; reproduce tonight's silent no-op first (hook rejection vs REPOS resolution vs never-called) | G1 | smallest, highest value — a safety net must be heard when it breaks |
| C2 | Pre-kill hand-off substitute: on resume after a timeout stop, point the `/ilk` prompt at the dead iteration's `iter-NN.log` tail (or a runner-distilled "where it died" note), so the successor inherits the diagnosis, not just the tree | G2 | judgment call pending: adds a log-read to a state model that today deliberately excludes logs |
| C3 | Decide whether the launch-level bound should distinguish "barren timeout in a run that also shipped" from "barren run", or leave as-is and accept the alternating-cycle hole | G3 | needs a real alternating-cycle incident before designing |
| C4 | Planning-level: deep-stack sub-plans (frontend→convex→schema) either get a larger `recommended_iteration_timeout_min` or an explicit step-0 "map the wiring" step, so the debug loop inherits more of the window | §1 shape | planner convention change, not runner code |

## 5. What was left in a good state

- kira-cloudflare: loop running (run `20260919-012634`, active on
  `pv4-session-link`, successor iteration productive); `pv4-verify`
  blocked behind the quarantined `pv4-reimport-guard-delete` (its
  `depends_on` lists all four) — the 56-line parity decision remains
  the human gap for that batch
- No ilk-skills code was modified; this retro is the only repo artifact
