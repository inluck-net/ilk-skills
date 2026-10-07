# Retro 2026-10-07: the first unattended release, and what stalled it

RSI owner session ilk-skills-50, 2026-10-07 01:30-10:05 CST, on chad-mbp and
rezmac. It took over from ilk-skills-dc (handoff-16) with one goal: turn RSI on.
RSI mode (a) went live at about 06:30. Every number below was read in-session;
evidence paths are given per item. The full timeline is the "Owner log
(ilk-skills-50)" in `~/Documents/handoffs/ilk-skills-rsi-core-handoff-16.md`.

**Headline.** Every release attempt that night hit a defect in the release
machinery itself, and every defect was invisible until the path was used for
real. None of them was caught by a test. Each had passed review because its
fixture never exercised the real condition: a train under launchd, a plans
directory that is not empty, a ledger queue whose drainer had died.

## What happened (CST)

| time | event | evidence |
|---|---|---|
| 01:52-01:56 | The first scheduler-started train tagged and pushed v0.9.163, flipped chad-mbp, then died with the scheduler. Rezmac was never deployed, the scheduler was left unloaded, and no audit row was written. | scheduler.log `signal (SIGTERM)` 01:56:14; permits consumed 01:56:13; backlog 806f0c3c |
| 02:08-02:38 | Batch a-train-survives-its-own-bounce: the train starts in its own session (spawn_detached.py); the bouncer refuses to boot out its own process group. | 1decf3e7, da4654b0 |
| 02:41-02:44 | An owner-run train released v0.9.164 to both hosts. | audit `released v0.9.164` |
| 04:19-04:21 | The first idle-tick planner run took a stale candidate and wrote no master. A fallback then forced **all 122 masters** to `draft`, and that fallback also hid the stale refusal. | audit `autoplan-drafted` 04:21:59; 122 mtimes at 04:21:24 |
| 04:25-04:30 | Autoplan paused. Statuses restored by derivation (120 shipped, 1 blocked, 1 paused), because nothing logs status transitions. | backup `~/.ilk-data/backups/plans-after-autoplan-mass-draft-20261007-0430` |
| 04:26 | An owner train for the hand fix refused: `prove refused: batch verdict stale`. | audit 04:26:06 |
| 04:44-04:47 | An orphaned golden_batch.py (ppid 1) kept two fixture runners alive. The new permit writer refused "fleet not quiet" until they were reaped by hand. | backlog 630af2e3 |
| 04:50-04:54 | **Go criterion 2 met.** The scheduler started the train in its own process group; it survived its own bounce and deployed v0.9.165 to both hosts. | scheduler.log 04:50:10; audit `released v0.9.165` 04:53:53 |
| 09:57 | Batch R2b sub-plan 1 was marked shipped with its step-1 gate red; the runner reverted it (`ship_integrity_violation`). | launcher log 20261007-084953 :2893-2894 |
| 09:35-10:03 | gh-resolve's verify sat 26 min at 0% CPU in `suite_ledger.wait_for`, waiting on a queued measure whose drainer (pid 81098) was dead. Unblocked by moving the stale `queued.json`/`running.json` aside. | backlog 7313a335 |

## What went wrong, and why it was invisible

1. **The train ran inside the job it bounced.** `scheduler.sh:600` used `nohup`,
   which ignores SIGHUP only. `launchctl bootout` kills the job's process group
   (`launchd.plist(5)`, AbandonProcessGroup). Every earlier train had been
   owner-run from a session, so the scheduler-started path had never run.
2. **A fallback widened the write set to everything.** `autoplan.py:814-822`
   used `if p.name not in before_names or True  # include pre-existing`. The
   stale-path test passed only because its plans directory started empty, and a
   second test (TestAC6) passed only because of the bug. Same family as memory
   `empty-answer-must-be-unconstructible-without-looking`: an "empty" outcome was
   turned into "all".
3. **A wait trusted a queue nobody would drain.** `suite_ledger.wait_for` sleeps
   while `queued.json` names the tree, even when `running.json`'s pid is dead.
   That turns a crashed background measure into a full gate timeout (gtimeout
   1920 s) per attempt. It is the second ledger-coordination stall in two days,
   after `retro-2026-10-06-the-ledger-spawn-held-the-runner.md`.
4. **Test harnesses leak into the fleet signal.** An orphaned golden harness
   read exactly like a live loop to every quiet-fleet check.
5. **Workers still ship past a red gate.** It was caught by the runner's ship
   integrity check, as designed, but it cost a full re-dispatch.

6. **A pin shipped inert, two releases ago.** Batch `a-run-pins-its-release`
   (v0.9.139) committed `1d0206e1`, titled "fix(runner,watchdog,scheduler): resolve
   the script dir by realpath once", which changed only its test file
   (`git show --stat`: 1 file). Every test calls `_env_no_skill_home`, but
   production sets `ILK_SKILL_HOME` (`launch.sh:699`) to the worker's skills
   farm, symlinked through `current` (`install.sh:776-778`). So a live run's
   helpers follow a release flip, which is why every release still needs a
   loop-free host. Same family as `retro-2026-10-02-a-fix-that-shipped-inert`:
   the fixture removed the very condition the fix was for.

## What changed

| item | fix | evidence |
|---|---|---|
| train self-kill | spawn_detached.py + bouncer self-bootout guard | v0.9.164; criterion 2 at 04:50 |
| mass-draft | fallback removed; a regression test with pre-existing masters | 27751e82, in v0.9.165 |
| orphaned golden harness | runner group killed on timeout, SIGTERM and parent death | eb6bfe47 (hand commit, rules tier) |
| fleet hold + quiet wait | R2b (batch MASTER-2026-10-07c) | in progress |
| ledger deadlock | filed; fix needs an owner-planned kernel batch | backlog 7313a335 |

## Lessons for the next owner

- **Exercise the real path once before calling it proven.** One supervised
  scheduler-started train found two defects that four owner-run trains could
  not.
- **After any autoplan run, check master mtimes** (`find plans -name 'MASTER-*'
  -newermt <start>` must list only the new master). The audit row alone said
  `drafted`.
- **A long gate at ~0% CPU with no children is waiting, not working.** Check
  the process tree before waiting longer: `pgrep -P` recursively, `%cpu`,
  `lsof`. Then read the wait loop it is in.
- **A fixture that starts empty proves nothing about "touches nothing".**
- **Check that a frontmatter key has a reader before relying on it.**
  `pause_after_ship` has 0 readers outside tests and docs (gh-resolve-d9's
  search); suggesting it as a gap mechanism was wrong.

## Follow-ups

Tracked in `~/Documents/handoffs/ilk-skills-rsi-release-gap.md` § "Roadmap
after go-live": R2b (in progress), R2d-1 (queued), R2d-2, R2c, R3 (rezmac),
R4. New: the ledger deadlock fix (7313a335) and a stuck-gate detector (a gate
at near-zero CPU with no child process for more than N minutes), both under
Chad's "speed" goal.
