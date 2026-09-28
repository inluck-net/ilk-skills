# Retro 2026-09-28: the self-modifying batch could not land

`MASTER-2026-09-28b-autonomy-blockers` exists to make ilk-skills
self-improvement run unattended. It spent its first three hours unable to
land anything. Each stop came from a different defect, and each needed a
human to clear. This retro lists those defects and the root fix for each.

- **Host:** chad-mbp. Times are local (+08).
- **Logs:** `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/logs/launcher/`.
- **Runs:** `20260928-173336` (stopped by the operator), `182346`, `183349`,
  `184402`, `185427`.
- **Standing rule (Chad, 2026-09-28):** anything that blocks smooth looping
  is fixed at the root. No workarounds.

## Timeline

| Time | Event |
|---|---|
| 17:33 | Released `draft → queued` and dispatched at once. gh-resolve-57's constraint arrived after the dispatch: no merge into the clone while any loop is live. |
| 17:35-17:40 | Parked, then stopped by pid. `/ilk-stop` was unusable (F6). Nothing merged. |
| ~17:41 | Fleet b's drain unloaded on Chad's instruction. |
| ~18:20 | Unparked. |
| 18:33-18:42 | Run `183349` shipped sub-plan 1 (gate green). The worker had also edited the LIVE clone, so the merge was refused (F1). |
| 18:44-18:51 | Run `184402` shipped sub-plan 2 (gate green). Merge refused again: the live edit was still in place (F1). |
| 18:54-18:59 | Run `185427` did sub-plan 3 step 0. Ship-integrity re-ran both shipped gates, 10 failed / 30 passed (F2), reverted both, and parked the master. |
| ~19:40 | Operator fix: live clone restored, `6cb41de`, both sub-plans re-shipped, live-clone-write hook installed, unparked. |

## What went well

- **Every guard failed closed.** The live-clone check refused 2 merges. The
  ship-integrity re-gate reverted 2 ships on a red gate. Nothing broken
  reached `main` or disturbed a resolver loop.
- **The positive experiment settled the cause.** With `6cb41de` reverted,
  the same 10 tests failed again while a foreign loop was live. With it
  applied, 43/43 and 38/38 passed.
- **The standing agreement with gh-resolve held.** Its sessions traced two
  of the defects below (F4, and the backlog in F9).

## Findings

### F1. A worker edits the live clone through the skills symlink, and the edit stays

- **What happened.** In run `183349` the worker made 3 Edits to
  `~/.claude/skills/ilk-loop/scripts/selfmod_worktree.py` and 2 to its test.
  That path is a symlink into the live clone. It made the same changes in
  the worktree.
- **Effect.** The runner logged `[selfmod] LIVE CLONE TOUCHED`, refused the
  merge, and left the edit in place. The clone then ran an unreviewed draft,
  +170/−2 lines matching no commit, for about 1 hour. The next run was
  refused for the same reason.
- **How often.** 9 refusals on 2026-09-25, 2 today.
- **Cause.** The worker home's skills are symlinks into the live clone.
  Nothing stops a write through them. The only check runs after the
  iteration, and it does not undo anything.
- **Root fix.**
  - A PreToolUse hook that denies Edit/Write/MultiEdit/NotebookEdit whose
    realpath is inside the clone.
  - Stop-gap installed today as `~/.claude-{worker,manager}/hooks/no-live-clone-edit.py`.
    Verified on 8 sample inputs: 4 denied, 4 allowed.
  - Still owed: a repo-tracked version with tests and bootstrap wiring, and
    Bash-write coverage.

### F2. The new merge guard's tests read the real host

- **What happened.** Sub-plan 1's host-wide probe matched `ilk please continue`
  on any host process. That prompt names no clone. Ship-integrity re-ran the
  gates while a gh-resolve loop was live (worker pid 83399). The 10 merge
  tests that expect a merge to proceed got `MergeBlockedError`, and 2 shipped
  sub-plans were reverted.
- **Cause.**
  - The plan's AC ("tests never probe real processes") was met by the new
    test file only.
  - The design scoped the runner half to the clone but left the worker half
    host-global.
- **Root fix:** `6cb41de`, in the worktree. A worker is attributed to its
  nearest runner ancestor. An orphan counts only when its cwd is inside the
  clone, and the walk fails closed. New tests: AC-2 (worker under this
  clone's runner), AC-2b (worker under another clone's runner), AC-2c
  (orphan, cwd inside or outside).

### F3. Ship-integrity can never check step commits in the selfmod worktree

- **What happened.** Both reverts printed "could not resolve a project root
  from the sub-plan path; step-commit check skipped". That is #57, and the
  selfmod shape hits it every time.
- **Root fix:** #57. An unresolvable root must be a recorded, refusable
  outcome. Resolution must also understand the selfmod worktree.

### F4. The scheduler passes one project's master to another project's dispatch

- **What happened.** The gh-resolve loop dispatched at ~18:53 carried
  `ILK_MASTER='MASTER-2026-09-28b-autonomy-blockers-execution-plan.md'`.
- **Cause** (read in this session):
  - `scheduler.sh:899` sets `master_name` in the scan loop.
  - `:1112-1115` copy key, path, repo and actives into `disp_*`, but not the
    master.
  - The dispatch loop reads the stale `$master_name` at `:1181`, and at
    `:1196` and `:1215` per gh-resolve-59.
- **Latent hazard.** If the leaked name matches a file in the target's plans
  dir, the pin wins whatever that master's status is. Masters are named by
  date, so a match is likely.
- **Root fix:** a `disp_masters` array, with a test that dispatches two
  projects in one poll.

### F5. A park does not stop a live loop, and a live loop can overwrite it

- **What happened.**
  - Run `173336` was dispatched before the park. Only a stop held it.
  - gh-resolve measured #6937's master: parked at 17:13:17, then rewritten to
    `status: shipped` by the loop that was still running.
- **Root fix.** The runner re-reads master status at every iteration start
  and before any ship write. A parked master ends the run cleanly.

### F6. `/ilk-stop` on ilk-skills kills other projects' loops

- **Cause.** `stop.sh:201` greps `ps -ax` for `$project_path`. For ilk-skills
  that path is a prefix of every loop's script path, so it matches the fleet
  scheduler and every resolver runner. The same damage was measured on
  2026-08-12 and 2026-09-17.
- **Root fix.** Match by the pid file and the run's process group. A
  substring match must never decide a kill.

### F7. The master template's registry table fails preflight

- **Cause.** `master-template.md:45-49` puts an `Order` column second.
  `plan_preflight.py:59-63` expects the filename right after the row number,
  so a master built from the template parses 2 of 5 rows.
- **Root fix.** One table grammar, owned by one parser, and a test that runs
  preflight on the template itself.

### F10. Scheduler tests cannot run on a host with a live scheduler

- **Found by** the planning baseline for the root-fix batch.
- **What happened.** `bash skills/ilk-watchdog/tests/test_scheduler.sh fill`
  exits `already running (PID 23490)`.
- **Cause.** `scheduler.sh:21`, `:22` and `:137` hardcode
  `${HOME}/.ilk-data` for the pid, state and log files. They ignore
  `ILK_DATA_HOME`, which the tests pin. The siblings
  `scheduler_health.sh:30` and `bounce_daemons.sh:94` already follow the
  convention.
- **Same root as two older memory notes:** scheduler tests needing an
  isolated HOME, and dry-run lines polluting the real `scheduler.log`.
- **Root fix:** MASTER-2026-09-28d sub-plan 2, step 0.

### F11. The new merge guard starves merges, and each refusal ends the run

- **What happened.** Run `20260928-192945` iteration 2 went green, then got
  `MERGE BLOCKED: live loop(s) detected` (exit 2). The run ended
  `selfmod_merge_failed`.
- **Blocker.** A gh-resolve repo loop with 90-minute iterations (runner
  81451).
- **Effect.** Each ilk-skills run does one iteration and exits, and the
  scheduler re-dispatches every 5 minutes. `main` lags the worktree for as
  long as any other loop runs this clone.
- **It was predicted.** 28b's plan labelled the guard a judgment call:
  "wrong if selfmod merges starve while fleet b runs back to back". The
  falsifier fired within an hour.
- **Cause.** The runner treats a designed deferral (exit 2) the same as a
  failure. The deeper hazard is that a long-running bash runner reads its
  own script file as it executes, which is why a merge under a live loop is
  unsafe at all.
- **Root fix.**
  - MASTER-2026-09-28c sub-plan 0: defer, keep working, retry after each
    green iteration and at run start, and classify `merge-deferred`, not
    needs-human.
  - Proposed to gh-resolve, not planned: runners execute from a per-run
    snapshot of their script, which would make a merge safe under live
    loops and let the guard relax.

### F12. A plan amended mid-iteration is invisible to the running worker

- **What happened.** gh-resolve run `20260928-185416` iter-01, sub-plan
  `contract-names-every-areas-gate`, was amended at 19:27:25. The worker
  had read it once, at 18:54:28, and spent the next hour on the approach
  the amendment ruled out: the same assertion red at 19:06, 19:36 and
  19:52, and 0 commits.
- **Cost.** 2932 s of ~3540 s was model thinking. The sub-plan had 1 step,
  so nothing could be checkpointed.
- **Root fix:** MASTER-2026-09-28c sub-plan 8. The driver fingerprints the
  targeted sub-plan and its master above `## Findings`, and ends the
  iteration when a planner edits them (the dirty tree is preserved, and
  the run continues). The worker's own Findings and `current_step` edits
  are excluded.

### F8. The operator's own miss

I told Chad the batch's own bookkeeping commits had made the 09-28 proof
stale. They had not: the empty markers keep the tree. The tree first moved
at `58880b1` (CHANGELOG), then at my docs commits, which tests read. The
agent's trace corrected it before the plan was written. Sub-plan 4's
allowlist is exactly `CHANGELOG.md` for that reason.

### F9. The gh-resolve backlog

Relayed by gh-resolve-59. They are included here because each one stops or
misleads an unattended loop. "Measured" means gh-resolve-59 re-read or ran
it this session. "Relayed" means it came from gh-resolve-57's brief and has
not been re-verified.

| # | Defect | Status |
|---|---|---|
| G1 | The worker writes `status: shipped` on a verify sub-plan and its master after the driver refused the verification (D-427, kira 6944 runs `155259` / `172839`). The memory note `ship-refusal-bypassed-by-frontmatter-edit` is still open. | measured |
| G2 | The ledger says "no commits and no green gate" after an iteration with 3 commits that carried no trailer (D-428). | measured |
| G3 | A red work gate gets no at-base attribution, and nothing re-gates after the base is fixed (D-426). | measured |
| G4 | The worker runs the driver's gate suite inside its own iteration and commits last. It hits the bound with 0 commits (D-423/424, #6937, #6944). | measured |
| G5 | The scheduler plist PATH lacks `~/.bun/bin` on both hosts (D-415). | relayed |
| G6 | One runner per shared key serialises fleet a (#44). | relayed |
| G7 | Exit code 127 is truncated to 1 in the reject/exit path. | relayed |
| G8 | = F6. | measured |
| G9 | = F5. | measured |

## Disposition (superseded 2026-09-28 by the design review)

- **F1 stop-gap and F2** are done today; F2 is `6cb41de`, in the worktree.
- **The rest go into two follow-up masters,** queued behind 28b:
  - **MASTER-2026-09-28c-the-loop-keeps-its-own-state:** F11, F1
    repo-tracked, F5/G9, G1, F3, G2, G3, G4, F12.
  - **MASTER-2026-09-28d-operating-the-loop-is-safe:** F6, F10+F4, F7,
    G7+G5.
- **Not fixed here:** G6 (#44) is designed behaviour and needs per-master
  runtime dirs. G5's plist PATH claim is by design; the real gap was
  kira-cloudflare-scratch's missing `.ilk-launch.json`, which gh-resolve
  owns.

## Follow-up (2026-09-28, later)

The per-finding root fixes planned in MASTER-2026-09-28c/-28d were paused
before they were built. The findings are regrouped by design-level cause in
`docs/architecture/loop-state-and-ownership-design.md`, which replaces the
per-finding plan. Its section 8 gives the order.
