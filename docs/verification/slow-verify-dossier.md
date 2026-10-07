# Slow batch verify: dossier

Status: **open**. Written 2026-10-08 by the ilk-skills RSI owner session, for review by a Codex session.
Scope: the ilk-skills project on chad-mbp. gh-resolve and rezmac appear only where the same mechanism showed up there.

**How to read the claims.**
- Numbers labelled *measured* come from the scripts in [Appendix A](#appendix-a-reproduce-the-numbers) or from files cited inline.
- Numbers labelled *reported* come from a handoff, retro or plan and were not re-measured for this doc.
- `file:line` refs are at the commit named next to them. Where none is named, they are at `1dced52a` (HEAD when this was written).

---

## 1. The problem

Every ilk batch (one MASTER plan plus its sub-plans) ends with a **batch-verification sub-plan**: the "verify". It has two steps:

- **Step 0, the gate-first record.** The driver runs `verification_record.py --run-suite`. That runs the suite, re-runs each failure at the batch base ("at-base"), re-runs failures at HEAD to classify flakes ("head reruns"), and writes a signed record.
- **Step 1, the attribution gate.** `verify_attribution.py` judges the record (are any failures attributed to this batch?). A plain pytest gate over the importer tests also runs.

A green verify writes the signed verdict (`runtime/batch-gate.json`) that the release train consumes. **No release happens without it.**

The problem is that the verify has been the slowest and least predictable phase of every batch. It stalls, it repeats full suites, and it waits on things that never finish. All of this happens after the batch's real work is done.

### Goal and rules (Chad's words, standing)

- 2026-09-24: the verify must be **"smooth and fast on all projects"**, and it must never be something the batch under test can talk its way past (memory `verification-smooth-and-fast-goal`).
- 2026-10-07 ~17:00: "I don't want any unnecessary full suite run, especially the repeated ones. by extending any waiting timeout which then call a more longer wait until timeout is not acceptable."
- 2026-10-07 ~17:20: "I want the slow-verify completely away and never come back again after at most the next 2 batches"; "if the slow-verify come back again, then any of the things you did or going to do (before we see the problem again) is failure, need to re-do"; "harden the idea and my goal … rather than keep fixing this 1000 times".
- 2026-10-07 ~21:50, about 07k: "is it normal to take that much time? especially the verification and the 49 min with nothing running".
- 2026-10-08 ~00:45: "if you are the one to do the job and you get red check … you either fix the code, or fix the test, or both, until the test green. why can't the ilk loop do the same but stop?"

Rules derived from those quotes (doctrine retro `docs/retros/retro-2026-10-07-one-suite-per-batch.md`):

1. **One full suite per batch,** and it is the verify's own. A re-measure after a fix covers only what changed.
2. **Nothing waits for a suite.** The producer of the wait is removed; the wait is not tuned.
3. **No timeout, budget or threshold is raised to pass a wait.**
4. **Success metric:** verify ≤ 15 min, one full suite, and 0 `batch verify exceeded 15 min` backlog rows (self-filed since 07k #5).

---

## 2. Measured history

### 2.1 Verify span by period (measured)

**Span** = start of the first iteration that ran a verify step, to the verify's `#ship` commit. It includes the verify's waits, retries and triage idle. It excludes the previous sub-plan's tail.

The cruder "previous commit → ship" measure was rejected. For example, `at-base-rerun-one-process-verify` reads 18.6 min on it, but the verify itself ran 13:17:23 → 13:18:51. The other 16 min were the previous sub-plan's iteration running past its own ship (13:00:12 → 13:16:22), which is the ledger-spawn hold in §3 M6.

| Period | What changed | Verifies | Median span | Mean | Max | > 15 min |
|---|---|---:|---:|---:|---:|---:|
| A: Sep 16-27 | worker-run verify | 20 | 31.2 min | 70.1 | 302.2 | 16 |
| B: Sep 28-Oct 3 08h | gate-first record, serial suite | 13 | 33.0 min | 55.1 | 342.3 | 12 |
| C: Oct 3 08h-Oct 5 | `-n 8 --dist loadfile` (6f82fa4) | 17 | 22.3 min | 30.1 | 71.6 | 13 |
| D: Oct 6 | scoped ledger and one-process at-base | 16 | 4.4 min | 11.2 | 59.8 | 4 |
| E: Oct 7 | `-n auto`, ledger waits, full-scope kernel batches | 9 | 15.2 min | 47.9 | 142.0 | 5 |

Source: `.ilk-loop.log` joined to the `#ship` commits (`verify_span.py`, Appendix A).

### 2.2 Suite seconds per attempt (measured)

Source: `logs/verification/*.history.jsonl`, field `suite_duration_sec`.

| Period | Full-scope suites | Median | Range |
|---|---:|---:|---|
| B (serial) | 20 | 904 s | 749-1161 s |
| C (`-n 8`) | 22 | 270.5 s | 217-1193 s (the 1193 s ran before 6f82fa4 that morning) |
| E (`-n auto`) | 9 full-scope suites | 485 s | 398-590 s |

Idle-machine references:
- `-n 8`: 2:44 and 2:45 (memory `suite-not-xdist-safe-yet`).
- v0.9.165 ledger measure, 18:12-18:16 Oct 7: 238 s, reported.

**The suite got slower on Oct 7 (median 485 s against ~270 s) even though nothing in it changed scale.** Load was 13-25 on 10 cores (reported), caused by concurrent suites (§3 M7).

### 2.3 What the Oct 7 numbers say

| Verify (Oct 7) | Scope | Span | Suites | Where the time went (reported unless marked) |
|---|---|---:|---:|---|
| 07c quiet-fleet ("R2b") | full | 89.8 min | 2 (485, 428 s) | step 0: 52 min. Suite 485 s, at-base 2 min, **head reruns ~38 min** (serial, 3 passes × 23 ids) |
| 07i verify-reruns-only-what-can-change | full | 142.0 min | 1 (444 s) | step 0 ~15 min, including ~4.5 min starved ledger wait. **Step 1 failed 6×, 12:34-14:31**, on a test red AT BASE. Scheduler `no-progress-bound` 12:49-14:17 |
| 07j the-suite-fits-the-free-cores | full | **15.2 min** | 1 (474 s) | clean, the only one at the bar. Owner killed a stale-tree ledger drainer at 15:32 |
| 07e a-gate-never-waits-on-nothing ("R2e") | full | 77.3 min | 2 (535, 398 s) | ~6 min waiting on a per-commit background suite that then timed out. Step 1 red on 5 base reds |
| 07k a-verify-gate-judges-only-this-batch | full | 79.2 min | **3** (546, 535, 590 s) | attempt 1 `phase_seconds`: suite 546, **at_base 451**, head_reruns 13, total 1010, with **24 failing ids** (measured, backlog row 6e75db2b and the 07k history). Then a one-file fix forced a full re-measure, twice |
| 5 scoped verifies, 00:05-04:43 | scoped | 6.8-7.3 min | 1 each (3-7 s) | — |

**Takeaway.** One full-scope suite plus attribution cost about 15 min in the one clean case (07j); that is an observation, not yet a distribution. The large overruns came from the machinery around the suite: head reruns, a base red that the step-1 gate could not pass, waits on background suites, re-measuring everything after a small fix, and quarantine idle between runs. Suite speed was not what produced them, but the suite's rise from ~270 s to 485-590 s used most of a 15-minute budget and makes the target fragile (Codex §8.2 Q6).

---

## 3. Mechanisms (the failure-mode catalogue)

Each row is a distinct way the verify got slow or stuck. Status is as of 2026-10-08. **Live** means it is in the deployed release, v0.9.166. **Shipped, unreleased** means it is in `main` but not yet tagged or deployed.

| # | Mechanism | Evidence | Fix | Status |
|---|---|---|---|---|
| M1 | **Suite runs inside a worker session.** Model turns babysit a 10-18 min run; results come from a dirty tree, so the record goes stale and a third suite follows. | retro-2026-09-25-the-verify-ran-inside-a-worker. ilk 24e: 16 min; gh-resolve 24f: 18 min against ~4 min `-n 8` | 09-24b `verify-without-a-worker`; 09-25a `ILK_WORKER_SESSION=1` refusal plus driver `--remeasure-if-stale` | live. **Leak:** env-var guard can be unset (backlog c34d8d67; forged record, gh-resolve 07c, 2026-10-07) |
| M2 | **Repeated full suites.** The fix loop re-runs the broad suite (09-16: 70 runs/day × ~305 s). Regenerate-until-pass (09-24). Batch-end gate re-ran the suite (09-24b). Per-step gate repeated the batch suite (rezmac 09-23). | retros 09-16, 09-24, 09-23 | 09-24b `one-suite-one-writer`; 09-16 per-module scope (`6aa001f`) | live |
| M3 | **Per-id reruns.** At-base ran one pytest process per id: 16 s suite → 820 s record, 53 ids. Head reruns stripped xdist and ran 3 serial passes per id with no total bound: ~38 min on 07c. | handoff release-gap:256-275; handoff-17:116 | 10-06 `at-base-rerun-one-process` (one process, ~0.2 s vs 2.1 s on a 12-id fixture, reported); 07i `a-head-rerun-stops-when-settled` (keep xdist, drop settled ids, 600 s total bound fails closed) | live |
| M4 | **A base red can never pass a plain-exit-code gate.** Verify step 1 pairs baseline-aware attribution with a baseline-blind pytest gate. plan_lint's importer rule keeps adding files to the blind one. | 07i: `test_ac6_vitest_form` red at base 4b80aaa1; 6 failed attempts, ~2 h. 07e: 5 base reds | owner hand `--deselect` (07i/07j/07e gates); 07k #0 `a-verify-pytest-gate-skips-pre-existing-reds` (`run_local_checks.py`) | **shipped, unreleased** (07k) |
| M5 | **Waits on the background suite ledger.** `suite_ledger.wait_for` slept on a queue whose drainer pid was dead (gh-resolve: 26 min at 0% CPU). A wait on a starved `nice 19` drainer was bounded at 300 s and then started a second suite on top (`verification_record.py:2066` at the time). Variant 2: a live drainer measuring a stale tree. | backlog 7313a335; handoff-17:122, :131 | 07e (live-drainer check, promote/bound/supersede); **07k #4 `a-commit-does-not-start-a-suite` removes the verify's ledger wait altogether** | 07e live; 07k **shipped, unreleased**. Stopgap live now: `.ilk-launch.json` `"ship": {"ledger": false}` (90e1206d) makes `suite_ledger.spawn()` a no-op |
| M6 | **The runner was held by its own background suite.** `ledger_spawn_for_head` captured output with `$(…)`, so the runner waited a full suite after every committing iteration. Median 658 s on 10-04, 653 s on 10-05 (≈50 s before v0.9.143). 17 per-commit suites on 10-07, 10 of the last 12 `unmeasured suite exceeded 600s timeout`. | retro-2026-10-06-the-ledger-spawn-held-the-runner; retro-2026-10-07-one-suite-per-batch | `1b6e5c4e`/`37cc470d` (fds redirected); 07k #4 removes the per-commit suite | fd fix live; removal **shipped, unreleased**; config stopgap live |
| M7 | **Contention.** Concurrent suites on one host: other projects' verifies, per-commit ledger suites, leaked fixture runners. At load 13.4/10, 28 of 34 reds were 17 s timeout kills; the suite went 238 s → 546 s. | handoff-17:116; 07l #5 Why (marked INFERRED) | 10-03i `taskpolicy -b nice 19` for background suites (ratio 2.56 → 1.03, reported); 10-06 per-test timeout marks; 07j `-n auto` = clamp(ncpu − load1, 4, 8); 07l #5 records `contention` in the record (observe only) | live (07j); 07l #5 **shipped, unreleased**. No lock or wait by rule |
| M8 | **Scope silently widened or narrowed.** One unmatched module made the scope `full` (09-16). A ledger cache miss dropped the selection and ran 5,142 tests while the record said `scoped` (10-06 Z). A nested `.claude/worktrees` checkout injected stale test copies: 33 false attributions. | retros 09-16, 10-06 Z; memory `verify-noise-sources-2026-10-06` | `6aa001f`; 10-06 `scoped-ledger-measurement`; `74066a6e` (selection via `git ls-files`); 10-03e (test-infra change → full) | live. **Open:** ledger keyed by tree ignores untracked files (backlog 833dd9a2) |
| M9 | **Caps and thresholds that look like timeouts.** `AT_BASE_CAP=50` on ~90 base reds printed `suite did not finish`, so the loop retried 5× (09-20). `FAILURE_SURGE_THRESHOLD=20` refused to measure, and a worker then hand-attributed with 3 suites in 69 min. | retros 09-20, 10-03, 10-06 surge | 09-20 `a-hard-stop-names-itself`; `02d84506` (surge measured by one batched rerun) | live |
| M10 | **The batch excuses itself.** A worker adds its reds to `baseline_red`, or forges or deletes the record, so the result is a fast verify that is wrong. Included because every speed fix must not reopen it. | retros 09-24 amnesty; gh-resolve 07c forged record (memory `worker-forged-verification-record`) | 09-24 base-only `baseline_red`, digest, append-only history; 07k #1 `a-baseline-red-entry-needs-a-red-at-base` | partly live; 07k #1 **unreleased**. **Open:** c34d8d67, ddbc28eb (digest is worker-writable), f73cb03e (delete resets attempt count) |
| M11 | **A small fix re-measures everything.** The ledger is keyed by exact tree, so any new HEAD misses and the whole selection re-runs. 07k: a one-file fix in `verify_attribution.py` cost a third full suite. | 07l #4 Why (`suite_ledger.lookup` :366, `verification_record.py:2117`, :2215-2230 at fbb5420c) | 07l #4 `a-reverify-measures-only-what-changed`: changed tests ∪ importers ∪ prior failing ids, rest carried with `carried_from` | **shipped, unreleased** (07l) |
| M12 | **Reds reach the verify instead of the step gate.** Nothing runs the tests that import a changed module, so a sub-plan's own `TypeError` is first seen by the verify. | 07l #3 Why (07k #1's bug, fixed 920e143e at the verify) | 07l #3 `a-step-gate-runs-the-importers-of-what-it-changed` (shared `test_importers.py`) | **shipped, unreleased** (07l) |
| M13 | **Idle between runs: a project-wide quarantine.** One `local_checks_failed` exit blacklists the whole project, and triage `park-and-escalate` escalates to nobody. 07k sat 19:44-20:29 with nothing running (the "49 min"). | 07l #2 and #6 Why; scheduler.log `skip-blacklist` | 07l #0 the driver ships only after the gate; #1 one in-run retry; #2 blacklist holds one master; #6 rule-first triage relaunch plus notify | #0-#6 **shipped, unreleased** (07l) |
| M14 | **First-attempt reds end the run.** The worker ends its turn without running its own gate. 5 of 7 ilk-skills run exits from 07k start to 00:30 Oct 8 were `local_checks_failed` on ordinary first-attempt reds. | 07l #7 Why | 07l #7 `a-worker-turns-its-red-green` (worker iterates to green on the exact gate; driver stops only on no progress / environment fault / budget) | **in progress** (step 0 committed `1dced52a`) |
| M15 | **at_base phase is 451 s on 07k attempt 1 and not attributed.** The timer (`verification_record.py:2720`) wraps three things: `run_at_base` (:967, one batched process in a fresh `git worktree add`), `run_at_adding_commit` for `absent-at-base` ids, and `suite_ledger.owner_of` per candidate. Which of them took the 451 s is **not measured**. | backlog 6e75db2b (`phase_seconds`) | none planned | **open, unplanned**; see §6 Q1 |

Recurring shape across M1-M14 (from retro-2026-10-07-one-suite-per-batch): **repeated full suites, waits on a background suite, and timeouts used as the release valve.** Each fix closed one producer and the next producer surfaced. The release valve was used in 09-20 (900→1800 s), 09-23 (120 s default), 10-07 (600 s ledger timeout) and the gtimeout 1920 s.

---

## 4. Batches that targeted verify cost, and their results

*Release* is the first tag containing the verify's `#ship` commit. *Result* is measured where marked, otherwise reported from the plan's Findings. 44 of 62 Findings sections in the 16 masters read for this table (07l excluded) are empty, so most batches never recorded an after-number. §2 is the after-measurement they lack.

| Master | Release | Targeted | Changed (main files) | Result |
|---|---|---|---|---|
| 09-20 verification-cost | v0.9.113 | `AT_BASE_CAP` stop disguised as a timeout; `--timeout=60` absorbing hangs | `baseline_red` census; `at_base_cap_exceeded` named; template teaches baseline_red | per-test timeout 60 → 17 s (2× slowest pass). Its verify itself: 11 iterations, 201 min of iterations (measured). Template later had to be restricted (M10) |
| 09-24 the-batch-cannot-excuse-itself | v0.9.125 | M10 | base-only baseline_red, declared-at-base, history, digest, flaky classifier | verify 14.2 min of iterations (measured); shipped 7 of 7 per-step gates running 0 checks (fixed by 09-24b) |
| 09-24b verify-is-fast | v0.9.126 | 37 min/verify baseline (64 verifies); M1, M2 | gate-first without a worker; one-suite-one-writer; empty-gate refusal; leak guard blames only its own session | criterion "next gh-resolve verify ≈ one suite (~5 min)" never checked. Period C median span 22.3 min (measured) |
| 09-25a the-suite-runs-only-in-the-driver | v0.9.129 | M1 | `ILK_WORKER_SESSION` refusal; dirty-tree refusal; `--remeasure-if-stale` | caused 09-25c (old-form verifies deadlock) |
| 09-25c a-stale-verify-goes-back-to-step-0 | v0.9.129 | deadlock from 25a | gate-first reruns step 0 on a stale record; lint | exposed hand-typed base sha → 09-29b |
| 09-29b the-verify-answers-its-own-triage | v0.9.135 | 28b: ~25 of 39 min was worker triage; wrong bases (0 of 26 attributable) | `--base-sha auto`; `alone` column; `born-red-at`; no head rerun for red-at-base ids; `--from-suite-output` | not recorded |
| 10-03 an-at-base-rerun-runs-base-code | v0.9.140 | at-base reruns ran HEAD's scripts (false excuse) | base env pinned | correctness, not speed |
| 10-03e the-verify-knows-what-it-ran | v0.9.145 | scoped verify running less than claimed | collect floor; test-infra → full; HARD importer lint | lint is why later verify gates list 15-61 files (feeds M4) |
| 10-03i verification-is-continuous | v0.9.143 | 29.7 min/verify, 79% in step 0; target "happy path ≤ 120 s" | `suite_ledger.py`; background suite per gated point; verify reads ledger; red goes to owner | contention ratio 2.56 → 1.03 after `nice 19`. **Created M5 and M6** (17 background suites/day); 07k removes its per-commit suite |
| 10-06 scoped-ledger-measurement | v0.9.154 | M8 (cache miss ran full) | run only the selection; persist scope and command | Period D scoped verifies 1.4-2.1 min span (measured) |
| 10-06 at-base-rerun-one-process | v0.9.154 | M3 (820 s of reruns) | batched `run_at_base`; surge stop | fixture 2.1 → 0.2 s; real-verify after-number not recorded |
| 10-06 flaky-runner-tests-carry-their-budget | v0.9.154 | 17 s kills under `-n 8` (9 of 16 train runs refused) | `@pytest.mark.timeout(90)` on 7 tests (labelled judgment call) | 07j: per-name marks could not keep up |
| 07i a-verify-reruns-only-what-can-change | v0.9.166 | M3, head reruns ~38 min | xdist kept; settled dropped; touched skipped; 600 s total bound | own verify 142 min, all of it M4 (base red in step-1 gate) |
| 07j the-suite-fits-the-free-cores | v0.9.166 | M7 (485 s at load 13.4) | `-n auto` = clamp(ncpu − load1, 4, 8); at-base timeout = unmeasured | own verify **15.2 min, one suite** (measured). Changed the invocation key → train `could_not_compare` (backlog af9a6e1c) |
| 07e a-gate-never-waits-on-nothing | v0.9.166 | M5 | live-drainer check; stalled-gate flag (`bounded_run.py`, flag only); waited-on measure never doubled | own verify 77.3 min (per-commit suite wait + 5 base reds) |
| 07k a-verify-gate-judges-only-this-batch | **none yet** | M4, M5, M6, M10 | step-1 gate skips pre-existing reds; baseline_red needs red at base; detached train owns output; **no per-commit suite, no verify ledger wait**; `phase_seconds` + >900 s self-files a backlog row; tests never wait on the live fleet | own verify 79.2 min, **3 suites** → fails the bar; prompted 07l |
| 07l a-batch-costs-its-work-not-its-waiting | **active** | M11-M14; 07k's 49 min idle | see §5 | in progress |

---

## 5. Current state

- **Deployed:** v0.9.166 on both hosts. The runner and gates run from the release, so a runner-side fix takes effect only after a release.
- **Shipped, not yet released:** 07k (M4, M5/M6 removal, M10 part, the >15 min self-report) and most of 07l (M11-M13). 07l's last sub-plan (M14) and its full-scope verify are still running.
- **Stopgap until 07k is live:** `.ilk-launch.json` `"ship": {"ledger": false}` (90e1206d) disables `suite_ledger.spawn()`, its only reader. That turns off the per-commit background suite behind M5/M6 on any deployed runner.
- **Open backlog rows on verify cost or correctness** (`~/.ilk-data/ilk-skills-improvements/candidates.json`):
  - 6e75db2b `batch verify exceeded 15 min` (M15)
  - af9a6e1c / d9c56757 (the baseline key includes `-n`)
  - 833dd9a2 (the ledger ignores untracked files)
  - 4a87ae86 (`baseline_red` never expires)
  - c34d8d67 / ddbc28eb / f73cb03e (M10 escape paths)
  - fc865c9c, f9e18a42 (order-dependent tests)
  - 2026-10-04:suite-leaks-runner-fixtures (M7)
  - Probably stale, to close after checking: 1d3b9339 (solved by 6f82fa4), 6118449a (superseded), 7313a335 (07e, then 07k).

---

## 6. Planned and proposed next steps

### 6.1 Planned

1. **Finish 07l** (#7, then its `--scope full` verify) and **release 07k + 07l.** No commit between permits and tag: the train matches the verdict by HEAD or tree (`skills/ilk-ship/scripts/release_train.py:191-197`).
2. **Audit 07l's verify** against the bar (≤ 15 min, one full suite, 0 new `exceeded 15 min` rows) using its history row's `phase_seconds`, `contention` and `carried_from`.
3. **Measure the first post-release batch.** It is the first batch to run 07k + 07l on its own runner. Bar: one full suite, a ~5-10 min verify, and no run stops on a first-attempt red.
4. **Land the owner invariant** `tests/invariants/test_a_batch_runs_one_suite.py` (I8a-d: no per-commit suite, no verify ledger wait, …), then I8e.
5. **Decide whether step 0's full suite stays.** Owner recommendation: keep it. It costs 238-328 s uncontended and is the only check that sees shell/subprocess dependencies. Decide on the post-release batch's numbers.

### 6.2 Gaps with no plan yet (proposed for review)

- **G1, M15:** attribute the 451 s `at_base` phase. Add sub-timers for `run_at_base` / `run_at_adding_commit` / `owner_of` to `phase_seconds`. That is observation only and changes nothing else. Then remove whichever repeats work already measured. For example, `owner_of` may walk ship points with fresh runs; this is **unverified**, see Q1.
- **G2:** normalise the worker-count flags (`-n N|auto`, `--dist`) out of the ledger and baseline key (af9a6e1c, d9c56757). Every flag change today orphans the release baseline and forces an extra measure.
- **G3:** close M10's escape paths (c34d8d67, ddbc28eb, f73cb03e) before any further speed change that trusts the record more (for example 07l #4's carry-forward).
- **G4:** make an expiry or kind on `baseline_red` (4a87ae86) a prerequisite for 07k #0. 07k #0 skips pre-existing reds, so a stale `baseline_red` list now silences more.
- **G5:** reap leaked runner fixtures before a verify suite (2026-10-04:suite-leaks-runner-fixtures). They inflate load (M7) and block bounces.

---

## 7. Questions for the reviewer

1. **M15:** from `verification_record.py` around :2600-2720 (at `1dced52a`), which of `run_at_base`, `run_at_adding_commit` and `suite_ledger.owner_of` can plausibly cost ~451 s for 7-9 failing ids? Is any of them redundant with data the suite run already produced?
2. **07l #4 (carry-forward re-verify):** is the ancestor-plus-digest-plus-no-test-infra-change precondition sufficient? Specifically:
   - Can a change in a non-test, non-imported file (data file, shell script invoked by a subprocess test) change an unselected test's outcome?
   - If so, should `carried_from` be refused when any non-`.py` file changed?
3. **07k #0:** does skipping pre-existing reds in the step-1 pytest gate reopen M10 when combined with a worker-writable `baseline_red`?
4. **07l #7:** is "two consecutive red gates with the same failing set and no new commit" a sound no-progress test? Or can a worker loop commits that change nothing relevant until the budget runs out?
5. **Metric:** is the "first verify iteration start → `#ship`" span (§2.1) the right SLO quantity? `_check_slo_breach` (`verification_record.py:784`) judges `phase_seconds.total`, which excludes failed attempts, triage idle and step 1.
6. **Is anything in §3 mis-attributed?** In particular M7: contention as the cause of 238 → 546 s is inferred, not measured. 07l #5 adds the measurement.

---

## 8. Codex review — 2026-10-08

**Verdict:** the dossier is strong and evidence-rich, but 07l should not be released as written. The carry-forward re-verify has one release-blocking correctness gap and one concrete accounting bug. M10 also remains open because the pytest deselection path does not establish a trust boundary around the record it consumes.

### 8.1 Release-blocking findings

#### R1. Carry-forward does not have a sound dependency boundary

The ancestor + digest + no-test-infrastructure-change precondition is not sufficient. `test_importers.importer_tests` finds only test files whose AST directly imports the filename stem of a changed Python module. It does not cover:

- transitive imports (`test → A → changed B`);
- dynamic imports and pytest plugins or fixtures;
- data files, templates, configuration, shell scripts and subprocess executables;
- test helper modules under `tests/` whose consumers, rather than the helper path itself, must run.

Refusing carry-forward whenever a non-`.py` path changed would close only part of the hole. Python changes can still affect an unselected test through an indirect dependency. Until there is a conservative dependency closure, the safe rule is to fail closed to a full re-measure for arbitrary source changes. A narrower carry-forward may be allowed only for a proven-safe change class whose affected tests are complete by construction.

#### R2. The merged counts double-count the rerun selection

`verification_record._try_remeasure` says it substitutes the selection's new results into the prior counts, but the implementation adds the new passing, skipped, xfailed and xpassed counts to the old totals without subtracting those tests' old buckets. `carried_count = prior_total - selection_total` can also become negative when tests were added.

The verdict currently relies more heavily on `failing_nodes`, so this is not by itself a demonstrated false green. It nevertheless corrupts the verification record, proof counts and reuse metric. The tests should assert exact merged bucket counts and cover added and removed tests; presence-only assertions for `carried_from`, `rerun_selection` and `reused` are insufficient.

#### R3. The step-1 deselection path accepts an unsigned record

Adding a new HEAD-only `baseline_red` entry does not directly earn a deselection: `declared-at-base` is derived from the base configuration, and `verify_attribution` checks declarations added by the batch. That part of 07k #1 is sound.

The remaining M10 problem is the record itself. `run_local_checks._load_base_red_ids` verifies `record_digest` only if that line exists, does not require it, and does not independently check the history digest, record freshness or writer provenance before trusting `failed` and `declared-at-base` rows. The digest and history are also worker-writable. A worker able to rewrite the record and history can therefore manufacture rows that the pytest gate deselects.

G3 should be a release prerequisite, especially before carry-forward places more trust in previous evidence. The durable fix needs a driver-owned trust boundary, not another unkeyed digest stored beside worker-writable data.

### 8.2 Answers to the review questions

1. **M15:** the slow attempt had **24 failing ids**, not 7-9 (`attempt: 1` in the 07k history). `run_at_base` strips xdist and runs those ids serially in one pytest process. At the configured 17 s per-test timeout, `24 × 17 = 408 s`; collection and worktree overhead make 451 s plausible. This is the leading explanation. `owner_of` is also capable of hundreds of seconds because its fallback clones a snapshot and may run the id at each first-parent commit with a 120 s bound, separately for every candidate. `run_at_adding_commit` is serial per id as well, but only applies to foreign-added absent-at-base tests. Add separate timers for all three before removing anything.

   The HEAD suite cannot replace `run_at_base`, because it contains no base verdict. `run_at_adding_commit` and `owner_of` are not needed to answer the batch-level question "did HEAD regress relative to the batch base?"; they are needed only to assign a failure among interleaved batches. They should be conditional or moved outside the critical gate if interleaved ownership is not present.

2. **Carry-forward:** no, the current precondition is insufficient. Non-`.py` refusal is necessary but not sufficient; indirect Python dependencies remain. Do not release the general carry-forward path until it has a conservative dependency boundary and exact count-merging tests.

3. **07k #0 and M10:** a worker-writable `baseline_red` alone does not reopen the simple amnesty bug, but a worker-writable record/history does. The pytest deselection helper's optional digest check is weaker than its "signed record" description.

4. **No-progress:** "same failing set and no new commit" is not sound alone. Uncommitted edits or changed failure signatures may represent progress without a commit, while irrelevant commits can evade the rule indefinitely. Compare a fingerprint containing failing ids, normalised failure signatures, tested HEAD/tree, relevant tracked diff or worktree state, and the exact invocation. Keep an outer attempt or time budget so irrelevant commits cannot reset progress forever.

5. **Metric:** keep two metrics. The user-facing SLO should be an explicit `verify_entered` event to a valid `batch-gate.json` (or `#ship` if shipping is deliberately part of the contract), including retries and idle. `phase_seconds.total` should remain a per-attempt diagnostic. The Appendix join can start late when an earlier attempt produces no `local_checks` row, so it should not be the authoritative start signal.

6. **Attribution:** M7 is plausible but still inferred until the new contention measurement produces comparative data. Also soften §2.3's "Suite speed was never the cause": wrapper mechanisms explain the large overruns, but the increase from about 270 s to 485-590 s consumed most of a 15-minute budget and made the target fragile. The 10-15 minute happy-path estimate is currently an observed case, not yet a stable distribution.

### 8.3 Required changes before release

1. Disable or fail closed from the general 07l carry-forward path; retain it only for a proven-safe change class.
2. Correct the merged-count algorithm and add exact-count tests, including added/removed tests.
3. Close the worker-writable record/history escape path before allowing the pytest gate to deselect from recorded at-base rows.
4. Add `run_at_base`, `run_at_adding_commit` and `owner_of` sub-timers; record candidate and subprocess counts with them.
5. Replace the no-progress commit check with a tested-state fingerprint plus a finite outer bound.
6. Emit an explicit end-to-end verify start event and report both end-to-end and per-attempt latency.

Minor editorial correction: §4 says "these 16 masters", while its table and Appendix B describe 17 masters.

### 8.4 Owner check of the review (2026-10-08)

Each finding was checked against the code at `3ab40c0d`:

- **R1: confirmed.**
  - `test_importers.importer_tests` (`skills/ilk-loop/scripts/test_importers.py:87-175`) maps only changed `.py` modules to tests that import them directly by AST.
  - `_try_remeasure` (`verification_record.py:2098-2275`) selects changed tests ∪ those importers ∪ the files of prior failing ids.
  - A change to a shell script, for example `run_ilk_loop_claude.sh`, which many tests source or spawn, therefore selects nothing. Every test that exercises it is carried forward as passing. That is a false-green path, not just a weak one.
- **R2: confirmed.**
  - `verification_record.py:2238-2245`: `passed`, `skipped`, `xfailed` and `xpassed` are `prior + selection`, so every re-run test is counted twice.
  - `carried_count = prior_total - selection_total` (:2229) is test-count arithmetic on file-level selections.
- **R3: confirmed.**
  - `run_local_checks._load_base_red_ids` compares `record_digest` only `if digest_match:`, so a record without the line is trusted.
  - The digest is an unkeyed sha256 of the record surface, recomputable by whoever edits the record. This is M10's class; 07k #0 widens what it buys.
- **Q1: corrected.** Attempt 1 had 24 failing ids, not 7-9 (7 was attempt 3). 24 × 17 s ≈ 408 s fits the 451 s `at_base` only if those ids hit the per-test timeout at base. That is unmeasured; the sub-timers in §8.3 item 4 settle it.
- **Action taken:** the automatic post-07l release job was stopped at 02:08, before 07l shipped, so 07k + 07l are not released. The live permits are consumed and expired, so no train can start without new ones.
- **Decision (Chad, 2026-10-08 ~02:10): "Fix batch first."** MASTER-2026-10-07m `a-reverify-cannot-carry-a-regression` (priority 11, draft until 07l ships) has two sub-plans plus a full-scope verify:
  - **#0 `a-reverify-carries-only-what-it-can-count` (R1 + R2).** Carry-forward runs only when every changed path is a test file AND the prior ledger entry carries per-file counts; the merge is then exact. No ledger entry carries per-file counts today, so every re-verify is a full re-measure until a later batch records them (judgment call: correctness over the carry-forward's speed).
  - **#1 `a-gate-deselects-only-from-a-recorded-record` (R3, minimal).** Deselect only when the latest history digest matches the record (20 of 20 recent records do). The keyed trust boundary stays open (c34d8d67, ddbc28eb).
  - **Release:** 07k + 07l + 07m are released together on 07m's verdict (`~/Documents/handoffs/ilk-skills-release-gap-07m.sh`). 07d/f/g/h are re-queued after that release.
- **Live evidence for R1, same night:** 07l's verify (02:06-02:10) attributed 2 reds its step gates missed: `test_read_blacklist_from_postmortems_is_unchanged` (07l #2 changed `scheduler.sh`) and `test_skill_md_step6_names_ship_transition` (07l #0 changed `SKILL.md`). Neither changed file maps to a test via `importer_tests`. The same verify met the bar on cost: one full suite, `phase_seconds` total 576 (suite 512, at_base 53), contention 0 other suites.

## 9. Codex review of 07m

**Verdict:** 07m is ready to queue after the edits below. The original #0 proposal did not safely close R1: `test_importers._is_test_file` treats every path beneath `test`, `tests`, `spec`, or `specs` as a test file (`skills/ilk-loop/scripts/test_importers.py:59-69`), so its proposed guard admitted `conftest.py`, fixtures, and helper modules whose effects are not file-local. Because the ledger writer has aggregate counts but no per-file outcomes (`skills/ilk-loop/scripts/suite_ledger.py:274-304`), the proposed exact merge was also deliberately unreachable. Carrying that dormant branch into release added risk without reducing any current re-verify cost. I changed #0 to remove the partial carry-forward path entirely; every retry now must use the canonical exact current-tree ledger/suite path. That closes R1 and removes, rather than repairs, the R2 merge (`skills/ilk-loop/scripts/verification_record.py:2098-2271`, `:2388-2445`).

For R3, #1 now accurately claims reuse of attribution's history/digest evidence, not exact policy parity. `verify_attribution` skips history for legacy records without an `attempt:` header (`skills/ilk-loop/scripts/verify_attribution.py:537-540`); deselection is intentionally stricter and fails closed because running the authored pytest gate is safe. Recorder-written records remain compatible: the recorder writes the machine-readable record and then appends its digest to history (`skills/ilk-loop/scripts/verification_record.py:2836-2851`), matching the owner's 20-of-20 recent-record measurement.

Changes made:

- `MASTER-2026-10-07m-a-reverify-cannot-carry-a-regression-execution-plan.md`: changed the release contract from a dormant `file_counts`-guarded merge to deletion of carry-forward; added the broad test-directory predicate and aggregate-ledger evidence; kept `status: draft`, `priority: 11`, `base_branch: main`, filename, and ordering unchanged.
- `2026-10-07m-a-reverify-carries-only-what-it-can-count.md`: requires deletion of `_try_remeasure` and its early-return writer branch, preserves historical-field compatibility, adds the existing 07l carry test to scope and requires rewriting its obsolete assertions, and requires regression cases for `conftest.py`, `_helper.py`, arbitrary helpers, added/deleted/renamed/moved tests, shell, non-test Python, and `SKILL.md`. The current unsafe assertions are visible in `skills/ilk-loop/tests/test_a_reverify_measures_only_what_changed.py:1-24` and `:195-233`.
- `2026-10-07m-a-gate-deselects-only-from-a-recorded-record.md`: requires `_read_history` plus the shared digest helper, fails closed for missing/empty/malformed history, missing or invalid latest digest, and mismatch, preserves a recorder-written matching control and Findings-only edits, and replaces misleading “signed record” diagnostics with “recorded record” (`skills/ilk-loop/scripts/verify_attribution.py:466-493`, `skills/ilk-loop/scripts/run_local_checks.py:1821-1886`).
- `2026-10-07m-a-reverify-cannot-carry-a-regression-verify.md`: reviewed against `skills/ilk-loop/templates/batch-verification-subplan.md`; no edit was needed. It retains a full-scope recorder gate followed by attribution plus the generated importer/caller test gate.

Both implementation sub-plans retain strict-xfail red-first step 0 gates. Their step 1 gates include the new test and the importer/caller closure generated for the touched production file. The full-scope verify remains the only new full-suite run.

Recommendations not changed: keep partial carry-forward out of this release. If re-verify latency later justifies restoring it, first design and separately review a per-test or per-file outcome schema with exact added/deleted/renamed/moved-file semantics. The keyed signature or driver-owned trust boundary remains backlog work (c34d8d67, ddbc28eb); 07m's R3 fix is fail-closed provenance checking, not that boundary.

Validation from `/Users/chad/Projects/github/inluck-net/ilk-skills`:

- `plan_lint.py`: zero `WARN: HARD` lines. It emitted exactly the four known inherited warnings: the 70 s test-file measurement (twice), the 4560 s step-1 timeout sum, and the reported `--remeasure-if-stale` warning. The verify command itself still contains `--remeasure-if-stale` at plan line 78; this is the known inherited lint result.
- `plan_preflight.py`: exit 0, `OK: preflight clean`.

---

## Appendix A: reproduce the numbers

The scripts used are in the session scratchpad. They are reproduced here because they are short; run them read-only from any cwd.

- `D` = `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727`
- Verify sub-plans: `D/plans/*.md` with `batch_verification: true`, keyed by `plan:` (103 found).
- Iteration log: `D/logs/.ilk-loop.log`. JSONL, one row per iteration: `started` rows plus result rows with `local_checks[].slug`, `duration_sec` and `outcome`.
- Verify history: `D/logs/verification/<batch>.history.jsonl`. Fields: `attempt`, `suite_duration_sec`, `failing_nodes`, `head`, `tree`, `digest`, plus `phase_seconds` (07k+), `contention` and `carried_from` (07l+).
- Ship times: `git log --all --format='%ct %s'`, grepping `[plan:<slug>#ship]`.

```python
# verify_span.py: span = first verify iteration start → verify #ship commit
import json,glob,re,subprocess,datetime,statistics as st
R="/Users/chad/Projects/github/inluck-net/ilk-skills"
D="/Users/chad/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727"
def ts(s): return datetime.datetime.strptime(s[:19],"%Y-%m-%dT%H:%M:%S")
vs=set()
for f in glob.glob(D+"/plans/*.md"):
    h=open(f,errors="replace").read(3000)
    if re.search(r"^batch_verification:\s*true",h,re.M):
        vs.add(re.search(r"^plan:\s*(\S+)",h,re.M).group(1))
started={}; first={}
for l in open(D+"/logs/.ilk-loop.log",errors="replace"):
    try: r=json.loads(l)
    except Exception: continue
    k=(r.get("run_id"),r.get("iteration"))
    if r.get("status")=="started": started[k]=r["timestamp"]; continue
    for c in r.get("local_checks") or []:
        if c.get("slug") in vs and c["slug"] not in first:
            first[c["slug"]]=started.get(k) or r["timestamp"]
log=subprocess.run(["git","-C",R,"log","--all","--format=%ct %s","--since=2026-09-15"],
                   capture_output=True,text=True).stdout.splitlines()
for s,t0 in sorted(first.items()):
    sh=[int(l.split()[0]) for l in log if f"[plan:{s}#ship]" in l]
    if sh:
        t1=datetime.datetime.fromtimestamp(max(sh))
        print(t1.strftime("%m-%d %H:%M"), s, round((t1-ts(t0)).total_seconds()/60,1))
```

```python
# suite seconds per attempt
import json,glob,os
V="/Users/chad/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/logs/verification"
for f in sorted(glob.glob(V+"/*.history.jsonl"), key=os.path.getmtime):
    rows=[json.loads(l) for l in open(f) if l.strip()]
    print(os.path.basename(f), [r.get("suite_duration_sec") for r in rows],
          [r.get("phase_seconds") for r in rows if r.get("phase_seconds")])
```

**Known blind spots of these measures:**
- **Iterations with no `local_checks` row are invisible to the join.** Examples are a timeout killed before the gate, or 07i's 6 step-1 attempts, which left no iter log. A span still covers them because it ends at the ship commit, but the iteration count undercounts.
- **Per-iteration `duration_sec` excludes the driver's own pre-iteration gate-first suite.** That is why Oct 6 verifies show 0.4-0.6 min of iterations.
- **Periods are calendar cuts, not release cuts.** The two hosts and the release layout changed mid-period.

## Appendix B: sources

- **Retros (`docs/retros/`):**
  - 2026-09-16 batch-verification-gate
  - 09-20 a-hard-stop-disguised-as-a-timeout
  - 09-21 the-gate-cannot-pass-from-the-worktree
  - 09-23 the-driver-watched-the-clone-not-the-worktree
  - 09-24 the-batch-wrote-its-own-amnesty
  - 09-25 the-verify-ran-inside-a-worker
  - 10-02 a-fix-that-shipped-inert
  - 10-03 a-designed-escalation-loops
  - 10-06 batch-z-scoped-verification-ran-full
  - 10-06 the-ledger-spawn-held-the-runner
  - 10-06 a-regression-surge-read-as-an-environment-fault
  - 10-07 the-first-unattended-release-and-what-stalled-it
  - 10-07 one-suite-per-batch
- **Plans:** `D/plans/MASTER-2026-09-20-verification-cost…` through `MASTER-2026-10-07l-…` (the 17 masters in §4) and their sub-plans, especially each `## Why` and `## Findings`.
- **Owner handoffs** (outside the repo): `~/Documents/handoffs/ilk-skills-rsi-core-handoff-15.md` … `-19.md` and `ilk-skills-rsi-release-gap.md`. Handoff-18's "Owner log (ilk-skills-01)" holds the Oct 7-8 event log.
- **Backlog:** `~/.ilk-data/ilk-skills-improvements/candidates.json`.
