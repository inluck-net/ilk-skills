# Retro 2026-10-06: the ledger spawn held the runner for a full suite

ilk-skills on chad-mbp, run `20261006-174019`
(`MASTER-2026-10-06-a-dead-work-tree-parks-its-master`). Evidence comes from
`lsof` and `ps` taken live at 18:19-18:21 CST, the project's loop JSONL
(`~/.ilk-data/projects/<key>/logs/.ilk-loop.log`), and `git log`. The owner was
session ilk-skills-9a; Chad noticed the stall ("18m already in the step0").

**Headline.** After every iteration that commits, the runner starts a
background ledger measurement of the full suite. That measurement was supposed
to be fire-and-forget. It was not: the measurement inherited the runner's
stdout pipe, and the runner reads that pipe to EOF. So every committing
iteration waited for a whole full-suite run, about 11 minutes at the median
since v0.9.143, before the loop could move on. The loop showed phase
`between` and had no child process, so it looked idle, not blocked.

## What happened (CST)

| time | event | evidence |
|---|---|---|
| 18:13:18 | Iteration 2 (the implementation sub-plan) ends: `[done] success in 770.0s`. | iter-02.log |
| 18:13 | The runner calls `ledger_spawn_for_head` → `result=$(python3 suite_ledger.py spawn …)`. The double-forked measurement (pid 49215, ppid 1) starts the full suite in a temp copy. | `run_ilk_loop_claude.sh:4972`; `ps` |
| 18:13-18:20 | The runner sits in phase `between` with no child process. | `phase.json`; process tree under pid 3145 |
| 18:20 | `lsof`: the measurement's fd 1 and fd 2 are the write end of the pipe the runner reads on fd 4. | `lsof -p 3170` / `lsof -p 49215` |
| 18:20 | The owner kills the measurement's process group. The runner moves on at once to `phase: gate`. | `phase.json` |
| 18:21-18:23 | The verify sub-plan runs both steps in about 2 minutes and ships (`6c1fba36`). | git log |

So most of the "18 minutes in step 0" was this wait. The verify sub-plan had
not started yet.

## Mechanism

- `run_ilk_loop_claude.sh:4962-4980` `ledger_spawn_for_head` captures the
  spawn's JSON with `$(…)`. Bash command substitution returns only when every
  writer of the pipe has closed it.
- `suite_ledger.py` `_spawn_detached` double-forks and calls `setsid()`, then
  `execvp`s the measurement **without redirecting fds 0, 1 and 2**. The
  grandchild keeps the runner's pipe open until the suite finishes.
- v0.9.143 (Oct 4 01:44) shipped both `98530137` (the spawn after every agent
  return) and `50f3e58f` (the background suite runs at the lowest priority).
  The second made each blocked wait longer.

## Cost (measured, an upper bound)

The overhead per iteration is the gap between consecutive iteration records
minus the next iteration's `duration_sec`. It is taken from the loop JSONL
over the 10 most recent days with data, for iterations that made commits
(only those spawn):

| day | n | median s | max s | total min |
|---|---:|---:|---:|---:|
| 09-29 | 39 | 48 | 112 | 37.4 |
| 10-02 | 35 | 55 | 1460 | 113.3 |
| 10-03 | 46 | 60 | 735 | 93.8 |
| **10-04** | 38 | **658** | 869 | **337.5** |
| **10-05** | 25 | **653** | 924 | **199.4** |
| 10-06 | 33 | 61 | 1141 | 165.8 |

The median jumps about 11× on the first day after v0.9.143. The overhead
includes gates and other between-phase work, so the totals are an upper
bound. Against the pre-v0.9.143 median of about 50 s, roughly 10 hours of loop
wall clock went to this wait over Oct 4-6 on ilk-skills alone. Other projects
on v0.9.143+ were not measured.

## Why nothing caught it

- The ledger tests call `spawn()` in-process or discard its output. None of
  them reads the caller's stdout to EOF, which is what the runner does.
- Handoff-14 recorded the symptom as "the driver spawns an orphaned full-suite
  `suite_ledger.py measure` (about 12+ min under load). Today I let it finish
  rather than kill it; the fix is on main and needs a release." No fix for the
  blocking existed; the note named the orphan, not the wait. This retro's rule
  for that kind of note: a phase with no child process for minutes is a hang
  until `lsof` says otherwise.

## Fix and prevention

| commit (owner worktree) | what |
|---|---|
| `1b6e5c4e` | `_spawn_detached` points stdin at /dev/null and stdout/stderr at `<ledger>/measure-<sha12>.log` before exec. Red-first test: spawn under `capture_output` with a 30 s stub measurement timed out at 25 s before the fix and returns in 0.58 s after. |
| `c3653b46` | Class guard over every tracked non-test `.py`: a `start_new_session=True` Popen must set stdout and stderr explicitly (a PIPE only if the function drains or kills it), and an `os.setsid()` child must dup2 fds 1 and 2. Positive control: it flags the pre-fix `_spawn_detached`. |

## Found while fixing it (same session)

1. **A drain verify session clobbers a release-ready sentinel**
   (`8fef00bc`). Twice (15:57:38 and 18:25:29), the tick after a batch shipped
   launched a manager verify session for loop-verified masters. It ran 0
   iterations and overwrote `last-exit.json`, so the scheduler could not start
   the release train. Now it skips when the train is next and the master has
   no unverified compile-only or device-manual sub-plan.
2. **The remote deploy used the incumbent's bouncer, and the rollback passed
   it a bad argument** (`a62a9c53`). That gave rezmac "bounce exit 0" with
   tag-mismatch, and every remote rollback read "unverified: bounce=2".
   Together with `8fb11d13` (the smoke check resolves tags in the repo), these
   are why no train had ever deployed rezmac by itself.
3. **A failed scratch cleanup poisoned every later run, and a fixture commit
   landed in the real worktree** (`e2b80d60`, `802855e9`). The owner ran
   `test_status_all_actions.py` under `-n 8`. The rmtree onerror did
   `chmod 0o666` on a directory and stripped its x bit, so all 24 tests failed
   in every later run, serial included. An empty `init` commit landed on the
   owner worktree's HEAD; it was dropped before landing, and main was
   unaffected.
4. **Owner error:** "-n 8 must run in a real clone, never a worktree" was
   applied from memory without reading it. The real cause of "no tests ran"
   was zsh not word-splitting `$F`. The memory's worktree claim did not
   reproduce today: 61 selfmod/promote tests pass under `-n 8` in a worktree.
   The memory is corrected.
5. **Owner error:** the owner ran runner-spawning tests during a release
   train's deploy window. The bounce guard counts fixture runners as live
   loops (`bounce-guard-cannot-tell-a-fixture-from-a-real-loop`), and the
   v0.9.156 chad-mbp deploy refused (exit 2). Do not run tests that start
   runners while a train is deploying.
