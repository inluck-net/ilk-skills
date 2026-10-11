# Batch execution time baseline — chad-mbp, 2026-10-11

Preserved performance baseline for improvement comparisons. See the adjacent CSV for batch IDs and measurements.

# Recent batch time on chad-mbp

Measured 2026-10-11, from local runtime logs. Completed recorded launches only; active runs and idle gaps between launches are excluded. These are execution costs, not a claim that every batch was independently verified or released.

| Repository / batch | Completed launches | Iterations | Run elapsed (min) | Final-verifier phase (min) | Recorded worker sessions (min) | Outside iteration timer (min) |
|---|---:|---:|---:|---:|---:|---:|
| ilk-skills 10-10a — Pipeline waits | 1 | 10 | 75.4 | 27.3 | 33.6 | 15.3 |
| ilk-skills 10-10b — Missing baseline | 3 | 8 | 206.7 | 150.0 | 109.4 | 49.9 |
| ilk-skills 10-10c — Verify fits its gate | 1 | 20 | 420.2 | 83.9 | 335.3 | 62.5 |
| ilk-skills 10-10d — Resolver issue planning | 1 | 11 | 173.7 | 20.9 | 127.3 | 26.3 |
| gh-resolve 10-09d — Green base | 8 | 25 | 318.1 | 81.3 | 186.4 | 97.1 |
| gh-resolve 10-10a — Pin correction | 4 | 11 | 162.5 | 32.8 | 108.9 | 22.4 |
| gh-resolve 10-10c — Timeline / parallel runs | 2 | 15 | 445.9 | 0.0 | 363.2 | 65.9 |
| gh-resolve 10-10b — Production contracts | 7 | 17 | 418.9 | 83.9 | 261.6 | 45.7 |

## What the numbers mean

- Run elapsed: launch time encoded in run ID through terminal `run_exit` timestamp; excludes time between launches. Each selected run maps to one master via gate snapshots and registry references.
- Final-verifier phase: iteration-start through next iteration-start or terminal exit, for plans explicitly marked `batch_verification: true`. Includes worker repair, pre/post gates, and between-iteration work. It is not pure test execution time.
- Recorded worker sessions: final Claude result `duration_ms`; includes model/API waits, tools, coding, and tests. Sessions killed without a final result are absent, so this is a lower bound. Gate-only iterations legitimately have no worker result.
- Outside iteration timer: elapsed minus summed JSONL `duration_sec`. The timer ends before driver post-worker gates and cleanup. This residual also includes startup, coordination, and inter-iteration pauses; it cannot be labelled exclusively test time.
- Columns overlap: worker and verifier costs are portions of elapsed. Do not add them together. Synthetic iteration `999999` is a terminal record and is excluded from iteration counts.

## Findings

1. Final verification cost varies sharply. For ilk-skills it used 150.0 / 206.7 min (73%) in the missing-baseline batch, 83.9 / 420.2 min (20%) in the gate-budget batch, and 20.9 / 173.7 min (12%) in issue planning. Skipping it saves meaningful time but does not address slow implementation sessions.
2. gh-resolve production-contracts has consumed 418.9 min over seven completed launches (17 iterations). The last launch alone took 229.6 min: 186.7 min of recorded worker sessions, 16.2 min of pre-worker/gate-first or other iteration-timer work, and 26.6 min outside the iteration timer. Its final-verifier phase lasted 83.9 min; the preceding implementation phase took about 145.7 min.
3. gh-resolve launch 20261011-050259 took 61.4 min and timed out. It began with API 429 retries; the worker log shows long no-output waits and five recorded test invocations. This is a timed-out worker session with observed API delays, not evidence of an hour-long full suite.
4. gh-resolve timeline/parallel-runs used 445.9 min across two launches with no final-verifier iteration in these launches. There were 363.2 min of recorded worker sessions and 65.9 min outside the iteration timer. Slow work and recovery exist even without the final verifier.
5. ilk-skills gate-budget batch used 335.3 min of recorded worker sessions. Individual implementation sessions ran about 42.0 min (owner search), 38.4 min (serial rerun arms), and 35.8 min (ungated-step proof); verification is not its only bottleneck.

## Practical next steps

Keep final verification opt-in as instructed. Prioritize reducing repeated orientation, test invocations, slow scoped caller gates, and API-wait retries. Add trustworthy per-phase timing before claiming a precise split between tests, model waits, and cleanup. The existing timer undercounts overall run cost.

## Coverage and exclusions

The source scan begins 2026-10-08, so earlier launches for the same masters are outside this report. Local operator repairs, release/baseline measurements outside the runner, and other machines are excluded. gh-resolve 2026-10-09c has seven recorded iterations (166.8 min) but no terminal record; it is excluded from the elapsed comparison. Active runs are excluded even if some iterations have completed.

During this inspection newer runs were present: ilk-skills 20261011-102057 and gh-resolve 20261011-104410. No runtime state was changed and no tests were launched.

## Evidence

Per-project source directories:

- `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/`
- `~/.ilk-data/projects/users-chad-projects-github-inluck-net-gh-resolve-a7b5462/`

Read `logs/.ilk-loop.log`, `logs/runs/<run-id>/gates-snapshot-*.json`, `master-snapshot-*.json`, and `iter-*.log.jsonl`; cross-check with launcher logs and plan front matter. The adjacent JSON contains the parsed run and iteration measurements.

Selected completed run IDs:

- ilk-skills: 20261010-013915, 20261010-082322, 20261010-112116, 20261010-121149, 20261010-143933, 20261010-214857
- gh-resolve: 20261009-170157, 20261009-174717, 20261009-203219, 20261009-205737, 20261009-210640, 20261009-224648, 20261009-234717, 20261010-000236, 20261010-054633, 20261010-083327, 20261010-083830, 20261010-084242, 20261010-185714, 20261011-014546, 20261011-022942, 20261011-033253, 20261011-042258, 20261011-043611, 20261011-045223, 20261011-050259, 20261011-061345

## Worker-log investigation

Four long completed sessions were inspected directly. API/model time below is
Claude's final result `duration_api_ms`, not a measurement of pure reasoning:
it includes provider/network/model response time. The remainder is session
elapsed minus that field; it includes tools and other client work, and is not
exclusively test execution. Provider usage reports large output-token totals;
these should not be interpreted as user-visible prose.

| Session | Worker elapsed | API/model | Remainder | Reported turns | Reported output tokens |
|---|---:|---:|---:|---:|---:|
| ilk-skills 20261010-143933 iter 02, owner search | 41.8 min | 32.7 min | 9.2 min | 126 | 108,850 |
| ilk-skills 20261010-143933 iter 14, ungated-step proof | 35.5 min | 34.1 min | 1.4 min | 127 | 84,548 |
| gh-resolve 20261010-185714 iter 08, suite opt-in | 51.9 min | 29.0 min | 22.9 min | 129 | 65,269 |
| gh-resolve 20261011-061345 iter 05, verifier repair | 57.8 min | 46.4 min | 11.5 min | 166 | 125,076 |

Observed causes:

- **Many serial decisions and reads.** These sessions had roughly 120–160 tool
  calls. The ungated-step worker began at 19:16:29; its first Edit was at
  19:41:52, after extensive runner/watchdog/collector/contract/test reading.
  That 25-minute interval includes necessary investigation, not just path
  discovery. Its tests were short (one declared-gate result: 112 passed,
  2 skipped in 32.69 s); the 35-minute session is predominantly API/model time.
- **Recurring bootstrap detours.** gh-resolve searched for a missing
  `long-running-commands` skill in several homes; the owner-search worker first
  tried a nonexistent `ilk-loop/scripts/loop_status.py` path and then corrected
  it. These are concrete avoidable round trips, but do not by themselves
  explain most of an hour.
- **Broad gates and repeat repair.** Suite opt-in selected about 2,300 tests.
  Gate attempts returned `1 failed, 2298 passed` twice. The worker diagnosed
  `test_canary_plans_pass_plan_lint` reading the live ~/.ilk-data corpus:
  26.6 s of a 28.6 s isolated test was gate-budget corpus scanning, and it
  timed out under the parallel gate. The session made a sandbox test-infra
  repair. Several pytest tool intervals lasted roughly 2–4.5 minutes.
- **Attribution/base confusion.** Verifier repair used an iteration-base
  marker to call a failure pre-existing; this differs from the frozen batch
  base described in the earlier handoff. It also encountered conflicting
  remote-selection expectations and tried one nonexistent test filename.
  The worker's 18 recorded test invocations and many reads/edits explain
  why the verifier dispatched a lengthy repair session instead of merely
  running one suite.
- **API waiting can be a separate failure mode.** gh-resolve
  20261011-050259 iter 01 had explicit 429 retries, long no-output waits,
  a roughly 8.8-minute gate tool interval, and an eventual iteration timeout.
  The four completed sessions above showed no explicit api_retry lines;
  their API/model durations cannot be attributed to rate limiting alone.

Recommended direction: retain opt-in final verification, but also reduce
bootstrap rediscovery and serial read/decision churn, prevent tests from
scanning live runtime state, and avoid repeating an unchanged broad gate
without a diagnosed change. Measure model/provider alternatives with the same
representative task before assuming a model switch will solve the problem.
No model, gate, plan, or running process was changed during this investigation.
