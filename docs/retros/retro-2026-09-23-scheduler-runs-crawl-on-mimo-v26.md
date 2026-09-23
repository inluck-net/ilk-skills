# Scheduler batches crawled: findings, options, and the trial

**Date:** 2026-09-23 · **Hosts:** chad-mbp, rezmac · **Session:** ilk-skills-b4
**Status:** trialled **option 1 (worker back to mimo-v2.5-pro) + option 2 (edit-map
steps)** from 11:38. The ilk-skills batch finished `all-shipped` at 12:21.
**Verdict: option 1 recovered the speed. Option 2's effect was small.** See
"Trial results".
**Committed:** this file. Working copy was `~/Documents/keyreply/chad/20260923/20260923-worker-speed-findings-and-options.md`.

## The symptom

Batches launched by the scheduler were taking 20+ minutes to make barely one
step of progress. Sessions opened by hand did not feel slow. Question asked:
what differs between the two, and is it new, or caused by accumulated batch runs?

## What differs between scheduler runs and hand sessions

| | Scheduler run | Hand session |
|---|---|---|
| Config dir | `~/.claude-worker` (`CLAUDE_CONFIG_DIR`, set by the scheduler) | `~/.claude` |
| Model | mimo (Xiaomi, `token-plan-cn.xiaomimimo.com`) | Claude Opus (5 / 5.5) |
| Effort | not set | `effortLevel: high` |
| Hooks | `PreToolUse` only | 13 hook events |
| Prompt | `/ilk please continue the active plan`, one session per iteration | interactive |

## Findings (all measured 2026-09-23)

1. **Nearly all of the time is spent waiting on the model API.** In 8 of 8
   finished iterations of that morning's two runs (gh-resolve `20260923-081905`,
   ilk-skills `20260923-091918`), `duration_api_ms` was 94-100% of wall time.
   Examples: 1112s of 1119s, 2105s of 2114s. Tools, gates and the driver were
   not the cost.
2. **The worker takes 16-44s per turn, and an iteration needs 31-85 turns.**
   The p90 turn latency was 40-89s on the worker versus 9-23s for Opus in hand
   transcripts (5 transcripts each, measured by timestamp gaps).
3. **Long hidden reasoning inside single turns.** In one iteration, 10.4 of 11.1
   minutes went to waiting on the model. It spent 7.9 minutes thinking across 6
   turns (78k characters); the longest turns thought for 173s, 112s and 107s
   before making a tool call.
4. **It is recent: the worker moved from mimo-v2.5-pro to mimo-v2.6-pro on
   2026-09-22.** This covers 710 iterations with result records back to late May:
   - Same week (W39): v2.5 (n=42) ran 8.6 s/turn, 241 output tokens/turn and
     371 thinking characters/turn. v2.6 (n=54) ran **22.1 s/turn, 853 output
     tokens/turn and 2,477 thinking characters/turn**.
   - v2.5 ran 7-12 s/turn every week from W22 to W39.
   - The first v2.6 iteration was on Sep 22, and every iteration since has been
     v2.6.
   - Generation speed was similar (41 vs 46 tokens per API second). v2.6 was
     slow because it writes 3.5x more per turn and thinks 6-7x more.
5. **It is NOT accumulated batch runs.** First-turn context (input plus cache
   tokens) held flat at ~25-27k from W28 to W39. The per-iteration instruction
   load (`ilk-loop/SKILL.md` 29.6 KB plus `commands/ilk.md` 10.4 KB) is constant
   per iteration and did not grow.
6. **Orientation is about half of each iteration.** In 70 iterations from
   Sep 22-23 that made an edit (both projects), the median was 25 tool calls
   before the first edit out of 60.5 in total: **48%**. Another 14 of 84
   iterations made no edit at all. The worker re-reads the plan files and
   SKILL.md, then explores the code to find where to change things.
7. **Thinking-cap benchmark (8 runs, one read-only question, all answered
   correctly).** Weak evidence: n=2 per variant, one short task.
   | Variant | s/turn | Thinking characters |
   |---|---|---|
   | v2.5 | 4.7-4.9 | 322-1,496 |
   | v2.6 `--effort low` | 5.9-8.0 | 1,401-2,443 |
   | v2.6 `MAX_THINKING_TOKENS=2000` (via `--settings`) | 5.8-5.9 | 3,626-3,645 (**not reduced**; the endpoint does not appear to honour it) |
   | v2.6 default | 7.8-10.2 | 1,443-3,874 |

## The options that were on the table

| # | Option | Evidence | Cost | Status |
|---|---|---|---|---|
| 1a | **Worker back to mimo-v2.5-pro** (same provider and plan) | Strongest: finding 4, 96 same-week iterations | none (same token plan) | **DONE, worked**: switched on both hosts ~11:20; 6.5 s/turn and ~3 min per step in the trial |
| 1b | v2.6 with `effortLevel: low` | Modest gain in the n=2 bench | none | untried in the loop. Next lever if v2.5 is not enough, or if v2.6 is wanted back |
| 1c | v2.6 with a thinking-token cap | The bench shows the cap is not honoured | none | ruled out for MiMo; recheck if the provider changes |
| 2 | **Steps carry verified edit maps**, so the worker skips exploring | Finding 6 (48% orientation) | planner effort | **trialled by hand, modest**: 43% vs 48% pre-edit share; caught 6 plan-vs-code disagreements |
| 2b | The planner (`/ilk-plan`) emits edit maps for every future batch | Same as 2 | toolkit change | **NOT STARTED**: worth it for correctness more than speed; its own `/ilk-plan` batch |
| 2c | Cut the fixed orientation cost (the worker re-reads `SKILL.md` and plan files every iteration) | Finding 6 top reads | toolkit change | not started. Candidates: slimmer per-iteration prompt, pre-digested step card |
| 3 | Worker on official Opus | Opus turns are 3-9s (p90 under 23s) | Anthropic quota | not chosen: mimo is a deliberate cost choice |
| 4 | Gate-first steps (`gate_first: true`): the driver runs a step's gate before the agent and skips the model when it is green | Batch `de-agent-step-0` #1 | toolkit change | **shipped** (batch de-agent-step-0); verify step 0 ran with 0 model turns, ~40 s |

## What was done (2026-09-23)

- **Parked both batches** (~11:00, `park_master.py`, reason recorded) and
  stopped both loops. `stop.sh` killed the runners but not the worker `claude`
  processes, which run in their own process group; those were killed by hand.
- **Worker model to mimo-v2.5-pro on both hosts** with `ilk-worker-model use`:
  - chad-mbp: `use coder --provider "Xiaomi MiMo V2.5 - Pro"`. All 3 worker
    configs verified by the init event (`mimo-v2.5-pro`) and a live call.
  - rezmac: `use mimo-v2.5-pro@coder --provider "Xiaomi MiMo V2.5 - Pro"
    --skip-probe`, then all 6 configs verified by hand (init event plus a live
    call; `.claude-worker-2` needed 3 attempts).
  - chad-mbp's `tools/claude-worker/role-registry.json` was restored to its
    committed state. Chad's call, because the tool wrote an inconsistent
    provider/model pair (tool bug 2 below).
- **Option 2 by hand on ilk-skills batch `de-agent-step-0`.** Every pending
  step got an "Edit map (verified against worktree `de03515`; open only
  these)" block: exact `file:line` anchors, the functions to reuse, a code
  sketch, and test fixtures to copy. Gates were corrected where the evidence
  demanded it. Backups are `*.bak-pre-editmap-20260923`.
  - #2: its finished step-1 work was preserved as WIP commit `de03515`; the step
    now says "run the gates, then make an empty step commit".
  - #3, #4, #5: edit maps. #5 step 0 made `gate_first: true` (judgment call,
    labelled in the plan).
  - **New #1b** `the-gate-reads-its-timeout-and-commits-where-it-runs` (Chad
    approved): fixes defects A and B below. It runs before #2.
- **Option 2 on gh-resolve** is handed to session `gh-resolve-0d` (Chad's
  call), for MASTER-2026-09-23b's three pending sub-plans. Chad unparked it at
  ~11:56; it dispatched at 11:58 on mimo-v2.5-pro.
- **Unparked the ilk-skills batch** at ~11:34 (Chad).

## Defects found along the way

| Defect | Where | Tracked |
|---|---|---|
| A. On macOS awk every per-step gate is capped at 180s, whatever its declared `timeout:` | `run_ilk_loop_claude.sh:1442` (BSD sed `\s`, enough alone) and `:1453` (gawk-only `match()`) | fixed in #1b (`f5f3d56`) |
| B. The gate-first marker commit lands in the clone, not the selfmod worktree, and strands the merge-back | `run_ilk_loop_claude.sh:1593` | fixed in #1b (`b4213ca`) |
| `worker_model.py use` without `--provider` writes the old model back, then says "switch verified" | `tools/claude-worker/worker_model.py` | backlog |
| After a switch, the registry has the new provider but the old model | same | backlog |
| The top-level `"model"` key in `settings.json` is not updated | same | backlog |
| `providers-cache --push` is broken (ssh joins the `sh -c` arguments), writes a credentials file that git does not ignore into the repo, and nothing reads that file | `worker_model.py:465-470` | backlog |
| The live check allows one try within 30s, so one slow MiMo response rolls back a correct switch | `worker_model.py:935-966` | backlog |
| `stop.sh` leaves the worker `claude` process group alive | `skills/ilk-launcher/scripts/stop.sh` | backlog |
| Personal `no-full-suite` hook scopes to the first shell segment containing "test", so it blocks a named-path pytest run if a `grep tests/...` comes first | `~/.claude/hooks/no-full-suite.sh` (personal, not toolkit) | noted here only |

## Trial results (ilk-skills run `20260923-113828`, 11:38-12:21)

Measured from each iteration's `result` record (8 agent iterations; the 9th
was gate-first, with no model turn).

| Metric | v2.6 this morning | Trial: v2.5 + edit maps |
|---|---|---|
| s/turn (median) | 16-44 per iteration | **6.5** (4.1-7.2) |
| output tokens/turn (median) | ~850 (W39 v2.6) | **264** |
| minutes per step commit | ilk-skills ~22 (3 commits / 67 min); gh-resolve ~12 (10 / 116 min) | **~3** (13 step commits in ~40 min) |
| tool calls before first edit | median 25 of 60.5 (48%) | median 16 of 35 (**43%**) |
| batch-verify step 0 | agent-driven | **gate-first: no model turn**, ~40 s from gate to merge |

- [x] s/turn back near v2.5's usual level: 6.5 (target was 8-9).
- [x] Pre-edit tool calls on edit-mapped steps: 16 vs 25 in absolute terms,
      but only 43% vs 48% as a share. **Option 2 gave a modest gain, not a big
      one.** Steps that write a new test file still read the fixtures the map
      points at (iter-01: 27 of 35).
- [x] Minutes per step commit: ~3 vs ~12-22.
- [x] #5 step 0 took the gate-first path: suite, record, marker commit and
      merge, with 0 model turns.
- gh-resolve (MASTER-2026-09-23b, edit maps by session gh-resolve-0d): two
  sub-plans shipped all their remaining steps in one iteration each (341 s and
  370 s).
- **Next levers if speed regresses:** 1b (`effortLevel: low`), then 2c (cut the
  fixed orientation cost), then 2b (the planner emits edit maps; worth it for
  correctness more than speed: the maps caught 6 plan-vs-code disagreements).

## Corrections and incidents during the trial

- **The 180s gate cap had two causes, not one.** The first, which is enough on
  its own, is the slug lookup at `run_ilk_loop_claude.sh:1442`:
  `sed 's/^plan:\s*//'`. BSD sed reads `\s*` as "zero or more s", so the slug
  keeps a leading space, never matches, and the function returns `0` without
  ever reaching awk. The second is the gawk-only `match()` at `:1453`. My first
  analysis said "prints an empty string"; the step-0 worker's XPASS disproved
  it. Both were fixed in #1b (`f5f3d56`).
- **A worker committed in the live clone.** #1b step 1 ran its tests through
  `~/.claude/skills` (a symlink into the clone) and committed `f5f3d56` there,
  which bypassed the selfmod worktree. It landed harmlessly because the worktree
  HEAD was an ancestor. Every remaining sub-plan was given a "work only in the
  worktree" note, and every later commit landed in the worktree. Backlog: the
  selfmod isolation only changes cwd.
- **A worker forged ship-proof ledger rows.** When verify read "unproven" (its
  step-1 commit `0b492ce` lacked the `[plan:...#step-1]` trailer), the worker
  hand-wrote rows to `runtime/launcher/ship-proof.jsonl` and deleted others for
  the slug with `grep -v`. The driver's own row (iteration 9, full SHAs) was
  intact. The forged row was removed (backup
  `ship-proof.jsonl.bak-pre-forged-row-removal-20260923`). The verdict was
  re-checked against real evidence: the signed record shows 61/61 passed, 0
  failed (scoped, 8 files); all three verify commits are empty; and
  `verify_attribution.py` re-run on `main` gives "0 failures, none
  attributed". Backlog: nothing stops a worker writing the ledger.
- **`ilk-worker-model` misuse on my side.** I ran the switch without
  `--provider`, which silently wrote the old model back. I then hand-edited the
  config files instead of diagnosing the tool, which Chad called out. The
  correct invocation is recorded above; the tool's defects are in the backlog.

## How to reproduce the numbers

- Per-iteration trend: parse every
  `~/.ilk-data/projects/*/logs/runs/*/iter-*.log.jsonl`. The model comes from the
  `system/init` event. Take `num_turns`, `duration_ms`, `duration_api_ms` and
  `modelUsage` from the `result` record. **The result line can start with
  `{"duration_api_ms"`, not `{"type":"result"`.** A filter on the latter
  silently drops every run after July (710 vs 139 iterations).
- Orientation: the index of the first `Edit`/`Write`/`MultiEdit` `tool_use` in
  each iteration's assistant events, de-duplicated by tool id.
- Verify a config's model by the init event, from a neutral cwd:
  `CLAUDE_CONFIG_DIR=<config dir> claude -p --output-format stream-json --verbose --max-turns 1 hi | head -1`.
