# Batch-gate wall-clock — serial vs parallel — 2026-08-27

Sub-plan `the-batch-gate-runs-in-parallel`, step 1 ("Measure before choosing").

## Host and invocation

| field | value |
|---|---|
| host | `chad-mbp` (the other `ship.hosts` entry, `rezmac`, was NOT measured) |
| platform | macOS Darwin 25.6.0 |
| `sysctl -n hw.ncpu` | **10** (`hw.physicalcpu` also 10) |
| python | 3.9.6 (`/Library/Developer/CommandLineTools`) |
| pytest / pytest-xdist | 8.4.2 / **3.8.0** |
| HEAD | `0aa1b4a` (step-0 red test landed) |
| base invocation | `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25 -v` |
| live daemon | launchd scheduler alive and idle throughout (see AC-5 control below) |

`-v` was added to every run so node-id sets could be captured; it is the only
deviation from `ship.suite`, and it is constant across all three, so the
comparison between them is apples-to-apples. The 223.91s serial reference in
the sub-plan was taken at `HEAD e323115` **without** `-v` and over 2395 node
ids; this run collects 2423.

## Result

| variant | wall-clock (s) | vs serial | failed | passed | skipped | xfail | errors |
|---|---|---|---|---|---|---|---|
| serial (today's invocation) | **263.31** | — | 26 | 2366 | 21 | 3 | 7 |
| `-n 4` | **430.52** | **1.63× SLOWER** | 31 | 2361 | 21 | 3 | 7 |
| `-n auto` (= 10 workers) | **404.12** | **1.53× SLOWER** | 30 | 2362 | 21 | 3 | 7 |

**Both parallel variants are slower than serial, and both turn green tests
red.** This is the opposite of the sub-plan's premise.

## Node-id sets

All three runs collect the **same 2423 node ids** — 0 added, 0 removed in
either direction (`serial` vs `-n 4`, `serial` vs `-n auto`). Only *outcomes*
move:

`-n 4` — 5 flips, all PASSED → FAILED:

```
skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox
skills/ilk-loop/tests/test_plan_lint_e2e_env_prereq.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_escaped_bug.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_frontmatter_path.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_one_branch.py::TestSupervisedOnlyUnaffected::test_supervised_only_tests_still_pass
```

`-n auto` — the same 4 minus `test_pidfile_in_sandbox`, which passed on the
10-worker run and failed on the 4-worker one. That non-monotonicity is itself
the signal: these are load-sensitive, not worker-count-sensitive.

## Why parallelism loses here

Not a guess — read off the `--durations=25` blocks of the two runs.

**Serial**: the slowest single test is 8.25s and no test dominates. The whole
top-25 sums to roughly 100s of a 263s run. The remaining ~160s is spread across
~2400 tests that each spawn at least one subprocess.

**`-n auto`**: the top of the durations table is four tests pinned at
**60.1s** — that is `--timeout=60` firing, not work being done — and the
plan_lint tests behind them inflate 4-12× (e.g.
`test_contract_governed_scope_without_ref_fails` 12.58s).

The mechanism, confirmed from the failure traceback in
`test_plan_lint_escaped_bug.py:244`: those four tests are **nested pytest
runs** — they `subprocess.run([sys.executable, "-m", "pytest", ...], timeout=120)`
over three other test files. `test_pidfile_in_sandbox` is the same shape with a
tighter bound: it busy-waits 5s for a backgrounded `scheduler.sh` to write its
pidfile.

So the suite is *already* parallel by subprocess fan-out and already saturates
the 10 cores. Adding N pytest workers multiplies the live process count rather
than dividing the work: each worker's subprocess children now compete with 3-9
other workers' children. Tests bounded by a wall-clock timeout are the first
casualties, and they are exactly the tests that flipped. The extra ~150s is
that contention, not scheduling overhead.

This also explains why `-n 4` is *slower than* `-n auto`: with 4 workers the
nested runs blew past their own 120s `subprocess.run` bound and burned the full
120s each before failing, whereas at 10 workers the outer `--timeout=60` cut
them off at 60s first. Slower and less broken are not the same axis.

## AC-5 negative control — the live daemon was untouched

Captured before the first run and after the third. Byte-identical:

| file | mtime | sha256 (first 16) |
|---|---|---|
| `~/.ilk-data/scheduler.pid` | 1787769286 → 1787769286 | `0ab8ff9b0d1970d7` (unchanged) |
| `~/.ilk-data/scheduler.state.json` | 1787769286 → 1787769286 | `c75db9fd782c74dd` (unchanged) |

Three full-suite runs, ~18 minutes of wall-clock, real launchd scheduler alive:
the hermetic sandbox from SP1/SP2 held.

## The choice this measurement forces

> **judgment call: recommend `-n` NOT be adopted on this host** — because the
> two commissioned variants measured 1.63× and 1.53× *slower* than serial
> (430.52s / 404.12s vs 263.31s) and each turned 4-5 green tests red via
> timeout starvation, so AC-2 (identical pass/fail set) and the implied
> wall-clock win both fail. **Wrong if**: (a) `-n 2` — not measured, see gap
> below — beats 263.31s while holding the outcome set, or (b) the four
> nested-pytest tests are first rewritten to not shell out to pytest, which
> would remove the contention this measurement is dominated by, or (c) `rezmac`
> profiles differently — its core count and subprocess cost are unmeasured and
> this number is a claim about `chad-mbp` only.

## Measurement gap, stated rather than hidden

`-n 2` was **not** measured. The step commissioned exactly three points
(serial, `-n 4`, `-n auto`) and all three were taken; `-n 2` is the one
configuration that could plausibly still win, since less oversubscription means
more headroom for the nested runs. It was skipped to land this artifact inside
the iteration budget rather than to support the conclusion. Step 2 should
measure it before acting on the recommendation above.

## Committed alongside

`gate-nodeids-2026-08-27-sp3-{serial,n4,nauto}.txt` — one `<node-id> <OUTCOME>`
line per test, **sorted by node id**. Note the format difference from
`gate-nodeids-2026-08-27-after-sp1.txt`, which is in collection order: xdist
does not preserve collection order, so a set comparison against it must sort
both sides rather than `diff` them raw.
