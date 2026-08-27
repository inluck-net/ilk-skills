# Batch-gate wall-clock — serial vs parallel — 2026-08-27

Sub-plan `the-batch-gate-runs-in-parallel`, step 1 ("Measure before choosing")
and step 2 (`-n 2`, the point step 1 left unmeasured).

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
| `-n 2` (step 2) | **616.66** | **2.34× SLOWER** | 31 | 2361 | 21 | 3 | 7 |
| `-n 4` | **430.52** | **1.63× SLOWER** | 31 | 2361 | 21 | 3 | 7 |
| `-n auto` (= 10 workers) | **404.12** | **1.53× SLOWER** | 30 | 2362 | 21 | 3 | 7 |

**All three parallel variants are slower than serial, and all three turn green
tests red.** This is the opposite of the sub-plan's premise.

### The curve is monotone in worker count, and it never crosses serial

Sorted by workers: 2 → 616.66s, 4 → 430.52s, 10 → 404.12s, and serial (no
xdist at all) → 263.31s. Wall-clock falls as workers rise, which is the normal
xdist shape — but the whole curve sits **above** serial. More workers buy back
some of a cost that only exists under xdist; they never buy back all of it.

That is the decisive shape. A tuning search over `N` is not a search for a
winner, because the asymptote is on the wrong side of the line: the per-test
xdist overhead does not shrink with `N`, only the amount of it that runs
concurrently does. `-n 2` was the last untested point and it is the *worst*,
not a hidden win. There is no `N` to pick.

## Node-id sets

All **four** runs collect the **same 2423 node ids** — 0 added, 0 removed in
either direction (`serial` vs each of `-n 2`, `-n 4`, `-n auto`). Only
*outcomes* move:

`-n 4` and `-n 2` — the identical 5 flips, all PASSED → FAILED:

```
skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox
skills/ilk-loop/tests/test_plan_lint_e2e_env_prereq.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_escaped_bug.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_frontmatter_path.py::test_existing_plan_lint_tests_still_pass
skills/ilk-loop/tests/test_plan_lint_one_branch.py::TestSupervisedOnlyUnaffected::test_supervised_only_tests_still_pass
```

`-n auto` — the same 4 minus `test_pidfile_in_sandbox`, which passed on the
10-worker run and failed on the 4- and 2-worker ones. That non-monotonicity is
itself the signal: these are load-sensitive, not worker-count-sensitive.

`-n 2` adds nothing new. Its 2423-line outcome file is **byte-identical to
`-n 4`'s** (`diff` → 0 differing lines), and differs from serial in exactly the
5 lines above. So the flip set is a property of running under xdist at all, not
of how many workers.

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

**`-n 2` (step 2) does not fit that last paragraph, and the paragraph is the
part to distrust.** Read off the `-n 2` `--durations=25` block directly: the
four nested-pytest tests sit at 60.14s / 60.12s / 60.11s / 60.08s — the outer
`--timeout=60`, *not* the 120s `subprocess.run` bound. So a low worker count
does not imply the 120s path, and "4 workers burned 120s each" cannot be what
separates 430.52s from 404.12s. `-n 4`'s raw output was not retained, so that
claim is neither confirmed nor refuted here; treat it as unverified.

What the `-n 2` durations *do* show is a per-test floor that serial does not
have. Its top-25 is dominated not by the four timeouts but by ~20 ordinary
`test_plan_lint_*` tests pinned at **6.1–6.5s each** (e.g.
`test_spec_file_pattern` 6.53s, `test_vitest_no_env` 6.20s,
`test_main_surfaces_mock_only_gate` 6.20s). Serially the *slowest test in the
whole suite* is 8.25s and the top-25 sums to ~100s; here the top-25 sums to
~370s. There are far more than 25 tests of that shape, so a ~6s floor applied
across them is the right order of magnitude for the +353s.

That reading is what makes the curve monotone-but-never-crossing: the floor is
per-test and independent of `N`, and workers only decide how many instances of
it elapse at once. It is an inference from two durations tables, not a traced
mechanism — the falsifiable form is: if the floor is real, `-n 20` on a
20-core host still loses to serial.

## AC-5 negative control — the live daemon was untouched

Captured before the first run and after the third, and again around the
step-2 `-n 2` run. Byte-identical throughout:

| file | mtime | sha256 (first 16) |
|---|---|---|
| `~/.ilk-data/scheduler.pid` | 1787769286 → 1787769286 | `0ab8ff9b0d1970d7` (unchanged) |
| `~/.ilk-data/scheduler.state.json` | 1787769286 → 1787769286 | `c75db9fd782c74dd` (unchanged) |

**Four** full-suite runs, ~28 minutes of wall-clock, real launchd scheduler
alive: the hermetic sandbox from SP1/SP2 held. The `-n 2` run's own before/after
capture recorded the same `mtime=1787769286` and the same two sha256 prefixes,
i.e. unchanged across a further 617s of maximally process-heavy load.

## The choice this measurement forces

> **judgment call: recommend `-n` NOT be adopted on this host, at any `N`** —
> because all three measured variants are slower than serial (616.66s / 430.52s
> / 404.12s vs 263.31s), the curve is monotone in worker count and does not
> cross serial, and every one of them turns 4–5 green tests red via timeout
> starvation. AC-2 (identical pass/fail set) and the implied wall-clock win both
> fail, and they fail in the same direction at every point measured.
> **Wrong if**: (a) the four nested-pytest tests are first rewritten to not
> shell out to pytest, which would remove the cost this measurement is
> dominated by — this is the one change that could make the question worth
> re-asking; or (b) `rezmac` profiles differently — its core count and
> subprocess cost are unmeasured, and this number is a claim about `chad-mbp`
> only.
>
> Step 1's falsifier (a) — "`-n 2` beats 263.31s while holding the outcome
> set" — is now **closed, not inherited**: `-n 2` is the slowest variant
> measured and its outcome set is byte-identical to `-n 4`'s. It was the
> strongest remaining case for adopting a flag and it does not survive.

## Measurement gap — closed by step 2

Step 1 recorded that `-n 2` was skipped for budget, not to support its
conclusion, and that step 2 should measure it first. Step 2 did:

| | value |
|---|---|
| invocation | `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25 -v -n 2` |
| HEAD | `5c8ba73` (step-1 artifact landed) |
| wall-clock | **616.66s** (`date`-measured 617s) |
| exit | 1 — 31 failed / 2361 passed / 21 skipped / 3 xfailed / 7 errors |
| node ids | 2423 — 0 added, 0 removed vs serial |
| outcome set | byte-identical to `-n 4`; 5 flips vs serial |

Still unmeasured, and deliberately so: `-n 1`, `-n 3`, and any `N > 10`. With
the curve monotone and its best point (10 workers, 404.12s) still 1.53× serial,
another interior point cannot change the conclusion — and `N > 10` oversubscribes
a 10-core host. `rezmac` remains unmeasured, which is a host gap, not an `N` gap.

## Committed alongside

`gate-nodeids-2026-08-27-sp3-{serial,n2,n4,nauto}.txt` — one
`<node-id> <OUTCOME>` line per test, **sorted by node id**. Note the format difference from
`gate-nodeids-2026-08-27-after-sp1.txt`, which is in collection order: xdist
does not preserve collection order, so a set comparison against it must sort
both sides rather than `diff` them raw.
