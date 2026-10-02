# Retro 2026-10-02: a fix that shipped inert, and a verify blocked by its own batch

`MASTER-2026-10-02b-a-gate-proves-what-it-claims` (I1) shipped all 8 work
sub-plans by 15:03. Its verify should have started in the next iteration.
Instead it was wrongly blocked, the run halted, and the verify's real gate
started 37 minutes later, at 15:39:49. The cause was one of I1's own
sub-plans: it shipped green and did nothing in production.

- **Host:** chad-mbp. Times are local (+08).
- **Logs:** `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/logs/launcher/`.
- **Runs:** `20261002-144216` (halted `blocked-no-runnable`, 5 iterations),
  `20261002-153231` (watchdog relaunch).
- **Standing rules (Chad):** batch verification is smooth and fast AND
  cannot be excused by its own batch (2026-09-24). Anything that blocks smooth
  looping is fixed at the root (2026-09-28).

## Timeline

| Time | Event |
|---|---|
| 14:42:16 | Run `144216` starts; 1 work sub-plan left (`install-recognises-a-hand-wired-hook`). |
| 14:51:30 | `24a6ec4` ships sub-plan 7, `one-sub-plan-per-iteration`: the hook blocks further work after a ship. Gate green. |
| 14:59:19 | Iteration 5 targets `install-recognises-a-hand-wired-hook` at step 2 of 2 (`declared-gates-5.txt`). |
| 15:03:28 | The worker ships it via `ship_transition.py`, then takes up the verify **in the same iteration**. |
| 15:03-15:29 | The worker finds no gate-first record for the verify (correct, since it was not the target), blames the launcher, and sets the verify `blocked`. 1826s, $5.39. |
| 15:30:27 | Run halts: `blocked-no-runnable`, 0 runnable. The merge to the clone lands (`a67ec94`). |
| 15:31 | Operator (ilk-skills-c2): verify back to `pending`; sub-plan 7 re-opened with a step 2. |
| 15:32:31 | The watchdog relaunches (`153231`; classification `interrupted`, not blacklisted). |
| 15:38:43 | Step 2 done (`8dd093c`); driver gate `[local_checks OK]` at 15:38:53. |
| 15:39:38 | Merged to clone. |
| 15:39:49 | The verify's step 0 dispatches through gate-first: the full suite via `verification_record.py`. |

## What went well

- **The driver's gate-first path worked as soon as it was given the
  verify.** Iteration 2 of `153231` dispatched it with no agent turn.
- **Nothing broken reached `main`.** The wrong `blocked` stopped the loop,
  as designed; it did not ship anything.
- **The worker's diagnosis was checkable.** `declared-gates-N.txt` per
  iteration showed in one read what the iteration's target was.

## Findings

### F1: sub-plan 7's fix was inert in production

- `hooks/no-live-clone-edit.py:306` and `ship_transition.py:314` read
  `ILK_SHIPPED_MARKER`. At `a67ec94`, `git grep` finds it 0 times in
  `run_ilk_loop_claude.sh`. Nothing set it.
- Both readers **fail open** when it's unset, so the hook never fired.
- AC-1 passed because the test set the variable itself (test :90). AC-3
  tested a `_clear_iteration_marker` helper defined in the test file
  (:221-229), not the driver.
- **Root fix:** step 2 (`8dd093c`): the runner clears and exports the marker
  at each iteration start (`run_ilk_loop_claude.sh` ~:5202-5216, via
  `_ilk_marker.sh`).
- Same shape as memory `hand-written-capture-vars-are-fixture-traps`
  (2026-09-09 AC-5): a fail-open reader plus a test that hand-sets its input
  passes forever.

### F2: the worker blocked the verify on a wrong diagnosis

- The note claimed "gate-first never dispatched". The verify was never the
  iteration's target, so no gate-first could have run for it.
- The real defect was F1: the worker should have ended its turn at the ship.
- **Root fix:** F1. With the marker live, the hook denies Edit/commit after a
  ship, so a worker can't reach a second sub-plan to misjudge it.

### F3: step 2's pins still don't pin the runner

- Positive experiment, on a `git archive` copy of `8dd093c` with isolated
  `HOME` + `ILK_DATA_HOME`: with `run_ilk_loop_claude.sh` restored to
  `a67ec94`, `test_one_sub_plan_per_iteration.py` still passes **6 of 6**.
- The pins source `_ilk_marker.sh`, which proves the helper, not the call site.
- **Judgment call:** don't re-open a second time, since the code is correct
  by reading. Prove it live instead: the first worker launched on `8dd093c`
  must have `ILK_SHIPPED_MARKER` in its environment. **Wrong if** that comes
  back empty.
- **Backlog:** a pin that the iteration body calls `export_iteration_marker`
  before dispatch.

### F4: fixture rows are back in the live ship-reverts.jsonl (backlog 17)

- Live `runtime/launcher/ship-reverts.jsonl`, mtime 14:42: 100 rows, **92
  fixtures** (69 `test-slug`, 23 `per-step-slug`). The same 92 were cleaned on
  2026-09-29.
- They reached the verify worker's prompt as two `REVERTED BY THE RUNNER`
  lines (`[revert-notice] injected revert notice into prompt`).
- Cause (memory `runner-sourcing-tests-write-live-reverts`): sourcing the
  runner empties `PROJECT_PATH`, so the runtime dir resolves from cwd.
- gh-resolve's file is clean: 16 rows, 0 fixtures (gh-resolve-b4).
- **Root fix:** I2 sub-plan 8. `get_ilk_runtime_dir` refuses an empty
  `PROJECT_PATH`, and the 8 `$(get_ilk_runtime_dir … || true)/…` sites fail
  closed. Also, `test_ship_audit.py:29` replaces the whole env (no `HOME`),
  so pinning `ILK_DATA_HOME` around it does not contain it: an operator run
  with the pin still added 4 live rows at 16:42.

### F5: every iteration pays ~24s to skip other batches' plans

- After each iteration, the integrity check (`test_ship_integrity`,
  `run_ilk_loop_claude.sh:3057`; loop at :3158) runs over every shipped
  sub-plan in the plans dir. That is 463 plan files on 2026-10-02.
- Per plan it starts **3 `python3` processes** (verdict :3176, slug :3234,
  raw outcome :3246). Only **then** does it check scope (:3271-3279) and
  skip plans outside the active master.
- Iteration 1 of `153231`: 360 of 360 integrity lines were
  `not in the active master`, about 1,080 Python starts. At a measured
  22.5 ms per start, that's ~24s.
- The gap from gate pass to merge (15:38:53 → 15:39:38) was 45s.
- It grows with every shipped batch, since plans stay in the dir until
  archived.
- **Root fix:** planned as I2 sub-plan `ship-integrity-skips-foreign-plans-cheaply`:
  (1) do the scope test first, in bash, before any `python3`; (2) one Python
  pass for the in-scope plans instead of three spawns each.

### F6: the verify loosened a test without adding the stronger pin

- Step 1 attributed 3 reds (3/3 head reruns each). Two were `merge-deferred`
  with no `collect.py` mapping, owned by `56c1482`. One was
  `test_no_awk_receives_a_multiline_verify_list`, owned by `dd81fe5`, which
  removed the merge-site `awk` on purpose (replaced with `sort -u`).
- Verify commit `afba06b` changed `len(sites) >= 2` to `>= 1`. The planner
  note asked for an added assertion on the `sort -u` merge, and the worker
  didn't add it.
- **Judgment call: accepted.** The old test pinned only a variable name at
  the merge site, never the merge's behaviour, so no protection was lost.
  But the merge (`sort -t' ' -k1,1 -k2,2n -u` in the main loop) is unpinned.
  `test_a_step_is_proven_only_by_its_own_gate.py` AC-4 (:311) pins the
  producer `get_ledger_check_targets`, not the consumer.
  **Wrong if** a revert of the merge to the old max-step collapse goes
  undetected by the suite. That is the experiment to run.
- **Backlog:** a pin that the merge keeps every slug+step pair. It goes with
  I2 sub-plan 6 (`a-verify-cannot-reshape-a-test-to-pass`), which is the
  guard against exactly this shape.

## Cost

- **Lost wall-clock:** ~37 min (verify should have started ~15:03; started
  15:39:49). Of that, 26 min was the misdirected iteration 5, $5.39.
- **Per-iteration overhead (F5):** ~24s, on every iteration of every run on
  this host.

## Actions

| # | Action | Where |
|---|---|---|
| 1 | Runner sets the marker | Done, `8dd093c` |
| 2 | Live check of `ILK_SHIPPED_MARKER` in the first worker env on `8dd093c` | **Passed 16:58:48**: worker 21375 (run `20261002-165829`) and gh-resolve worker 38944 both carry it |
| 3 | Pin the runner's marker call site | Backlog |
| 4 | Refuse an empty `PROJECT_PATH`; ledger writes fail closed (F4, backlog 17) | I2 sub-plan 8, `a-sourced-runner-writes-nothing-live`; 104 rows cleaned 16:43 |
| 5 | Ship-integrity scope-first + one Python pass (F5) | I2 sub-plan 7 |
| 6 | Pin the main-loop `sort -u` target merge (F6) | Backlog |
| 7 | `gate_cost.py:139` crashes on a `permission_denied` record (string `message`); plan_lint's budget check is then skipped for every plan | I2 sub-plan 9, `gate-cost-reads-every-record-shape` |
| 8 | A step-0 pin that injects its own stopper or fake for the very boundary under test can never be made green by production code. 7 seen on 2026-10-02 (gh-resolve-b4: 24f15994 argv-matching stopper; impossible ClaimStatus supersedable+needs_human; now=ended+1 against a wall-clock ledger; plus d5's 4). A worker also committed "remove xfail markers — gate is green" with 2 of 7 pins red; local_checks caught it. Hand re-baselines: d7ee3e16/08229de1, 655865bb/989de491 | Backlog 9 / 21: planner or verifier check |
| 9 | `ilk-run.sh` starts the watchdog right after `launch.sh`, before the new runner rewrites `last-exit.json`; the watchdog reads the PREVIOUS run's terminal state and exits (18:12:07, `local-checks-stuck` → block), leaving the live run unwatched. Restarted by hand 18:12:39 | Backlog |
| 10 | A worker's step-0 pin (1ecad72, AC-3) wrote rows to the live `ship-reverts.jsonl` on purpose, asserted the bug instead of the fix (XPASS strict), and cleaned up with `rmtree(~/.ilk-data/projects/<key of cwd>)`. Run from the clone, that key is the live 604d727 data home | I2 sub-plan 2 Findings (rewrite spec); planner/lint check: no `rmtree` on a key not created under `tmp_path` |
| 11 | I2 sub-plan 2's AC-3 (548b756) is vacuous: passes before and after the fix (cwd not a git repo); AC-1/AC-2 do prove the root fix (red at 1ecad72, green at 548b756). Unused rmtree helpers remain at :79/:115 | Backlog |
| 12 | gh-resolve G1b sub-plan 3 (gh-resolve-b4, 18:5x): pin 4 was vacuous (XPASS(strict) at 86f38f46: it raised from ship(), which base already catches as `ship-failed`). Re-pointed at D-476's site, it is RED at 1f75410b, so D-476 is not actually fixed. Separately, a worker committed an EMPTY `#step-2` marker (955f956e) with no step-2 work, before the re-baseline Finding landed; ended by SIGALRM 18:55 | Backlog 9/21 + memory `ship-refusal-bypassed-by-frontmatter-edit` (a step claimed with no work) |
