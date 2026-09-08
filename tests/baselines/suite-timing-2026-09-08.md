# Suite wall-clock — full vs subset, serial vs xdist, scan vs cache — 2026-09-08

Measured while investigating "the full test suite takes so long to run". The
headline is that **the full suite does not take long** — it is 274s, unchanged
since 2026-08-27. What is slow is running a *subset* of `plan_lint` files, and
that turned out to be a different defect entirely.

Supersedes nothing. Complements `gate-timing-2026-08-27.md`, which measured
serial vs xdist over the same suite and whose verdict still stands (see
§"xdist" below).

## Host and invocation

| field | value |
|---|---|
| host | `chad-mbp` (`rezmac` NOT measured) |
| platform | macOS Darwin 25.6.0 |
| `sysctl -n hw.ncpu` | 10 (`hw.physicalcpu` also 10) |
| python / pytest | 3.9.6 (CommandLineTools) / 8.4.2 |
| pytest-xdist / pytest-timeout | installed, **not** in `addopts` / installed, `--timeout=60` in `addopts` |
| HEAD | `9ee4d3f` (+ uncommitted `collect.py` fix for the last two rows) |
| declared `ship.suite` | `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25` |
| box idle | **not gated** — see the caveat at the end |

`pytest.ini` `addopts` already supplies `--timeout=60 --timeout-method=signal
--import-mode=importlib`, so `python3 -m pytest -q --durations=N` differs from
the declared invocation only by `-q`. Every row below is serial unless stated.

## The comparison

| # | what was run | tests | wall-clock |
|---|---|---|---|
| 1 | **full suite** (2838 collected) | 2838 | **274.14s** |
| 2 | full suite again, after the `collect.py` fix | 2838 | **279.36s** |
| 3 | banked full suite, 2026-08-27 (`gate-timing-2026-08-27.md`) | 2423 | **263.31s** |
| 4 | 43-file subset — `test_plan_lint*` + `test_ship*` | 467 | **719.38s** |
| 5 | same 43 files, repeat (reproduces #4) | 467 | **701.94s** |
| 6 | same 43 files, `-n auto` (10 workers) | 467 | **191.62s** |
| 7 | same 43 files, serial, timing scan served from a warm cache | 467 | **24.49s** |
| 8 | same 43 files, `-n auto` + warm cache | 467 | **6.50s** |
| 9 | one file alone — `test_plan_lint_vacuous_selector.py` | 13 | **156.81s** |
| 10 | row 9 repeated, quiet box (load1 2.10 at start) | 13 | **155.05s** |
| 11 | 43 files, **shipped cache**, scoped identity only | 470 | **567.25s** |
| 12 | 43 files, **shipped cache**, unscoped identity added | 470 | **25.40s** |
| 13 | `test_plan_lint_vacuous_selector.py` + `_gate_budget.py`, shipped cache | 39 | **2.46s** |
| 14 | **full suite**, both fixes + shipped cache | 2843 | **270.41s** |

Rows 7 and 8 are an **upper bound**, not the shipped behaviour: they were
produced by patching `_load_timing_data` in-tree to read a pre-computed
`gate_cost` JSON *unconditionally*, then reverting. That patch also breaks
`test_loader_marks_a_nonzero_exit_as_failed` by construction (it forces the
loader to always succeed), so both rows read `1 failed, 466 passed`.

Rows 11-13 are the shipped cache. Row 11 is why the bound matters: a cache
keyed only on a **resolved** project identity moved the subset just 19%
(701.94s → 567.25s), because the tests spawn the CLI with `cwd=tmp_path` and so
resolve away from the repo — see below. Row 12 adds a reserved identity for the
unscoped scan and reaches the row-7 bound (**25.40s**, 28x). Rows 11-13 collect
470 rather than 467 because the cache's own tests were added.

**A subset costing 2.6x the whole suite is the anomaly, and it is real** — row
5 reproduces row 4. Rows 1-3 show no regression in the full suite at all.

## Where the subset time goes

`--durations=25` over the 43 files: the entire top-25 is `plan_lint` CLI tests
at **12.7-13.5s each**, plus one at 30.26s. Row 9 confirms it is not an
artifact of the selection — one file alone is ~12s per test.

Per-invocation, measured directly:

| invocation | wall-clock |
|---|---|
| `plan_lint.py <real plan file>`, cwd = repo | **5.57s** (repeat 6.13s) |
| same, on a `tmp_path` file, cwd = repo | **3.59s** |
| same, with `gate_cost.py` removed so the scan short-circuits | **0.25s** |

`cProfile` on one in-process `lint_file` call: **5.474s of 5.686s (96%)** is
`lint_gate_budget` → `_load_timing_data`, a single subprocess out to
`gate_cost.py --by-test-file --json`.

That scan parses every `iter-*.log.jsonl` under the project's run logs —
measured here at **207 files, 461 MB**. A stat-only fingerprint over the same
set (count, newest `mtime_ns`, total bytes) costs **2.7 ms**. Three orders of
magnitude between deciding and recomputing.

`_TIMING_CACHE` memoises within one process, but `plan_lint` is overwhelmingly
invoked as a *fresh subprocess* — once per gated sub-plan by the loop, and
from **53 CLI spawn sites** across the test suite — so each pays again.

### Why the tests pay MORE than 5.57s

`test_plan_lint_vacuous_selector.py:34` (and its siblings) spawn the CLI with
`cwd=str(tmp_path)`. `_resolve_project_root()` then resolves from a directory
that is not the repo, the project key does not map to a data dir, `--project`
is never added, and the scan falls through to the **unscoped** path — the one
the comment at `plan_lint.py:2626` measured at "~11.9s and growing with the
corpus". So the 2026-09-05 scoping fix is defeated in exactly the context that
spawns the most invocations.

Measured from a temp cwd, which is what the tests do:

| unscoped invocation | wall-clock |
|---|---|
| cold (no cache) | **13.14s** |
| warm (cached) | **0.07s** |

13.14s matches the ~13s per test in rows 9-10 almost exactly, which is what
identifies the unscoped scan as the whole of that cost. The unscoped corpus is
**877 files / 2.56 GB** across every project; a stat-only fingerprint over it
costs **12.9 ms**.

Scoped, from the repo root — the shape the loop's own gate uses:

| scoped invocation | wall-clock |
|---|---|
| cold (writes cache) | 2.59s |
| warm | **0.13s** |
| after `touch`-ing a run log (must rescan) | 2.55s |
| warm again | 0.14s |

## UNRESOLVED: the same tests are cheap in a full run

From row 2's `--durations=40`:

| test | in the full suite | standalone |
|---|---|---|
| `test_plan_lint_one_branch.py::…::test_supervised_only_tests_still_pass` | **2.06s** | 30.26s |
| `test_plan_lint_gate_budget.py::test_baseline_unchanged` | **1.64s** | ~45s |
| every `test_plan_lint_vacuous_selector.py` test | **<1.36s** (absent from top 40) | ~13s |

The full run's 25th-slowest test is ~2.5s, so no `plan_lint` test costs 13s
there. 25 tests at 13s would be ~325s and cannot fit inside a 274s run.

**I do not know why**, and three hypotheses were tested and discarded: it is
not the file selection (row 9), not CPU contention (rows 5 and 10 reproduce on a quiet box), and not the recursive `grep` in
`_find_importers` (0.018s, measured). A fourth candidate — that a `tmp_path`-derived project key
collides with one of the 15 `private-var-folders-…pytest-of-chad-…` dirs
already under `~/.ilk-data/projects/`, scoping the scan to a corpus with **0
runs** in it — was examined and looks unlikely: `project_key` appends an
8-char hash of the *full* path, every test gets its own `tmp_path`, and none of
those 15 dirs was created today (newest 2026-09-07 23:16). A collision would
have to be rare, yet the full-suite result is consistent across two runs.

Row 14 is the decisive test and it went the wrong way for every explanation
above: with the unscoped scan cached (13.14s → 0.07s), the full suite is
**270.41s** — statistically identical to the 274.14s it cost *without* the
cache. A run paying 25 unscoped scans could not be unchanged by removing them.
So the full suite was never paying that cost, and the subset was.

So the mechanism is **still unknown**. Rows 5, 10 and 14 make one thing
certain: it is not noise. Anyone acting on this file should resolve it before sizing the
win — the honest reading today is that the scan cost is real and worth removing
for the *loop's gate* and for targeted runs, and that its effect on a full
suite is unmeasured.

## xdist: the 2026-08-27 verdict stands

Row 6 (3.75x faster under `-n auto`) is **not** grounds for putting `-n auto`
in `addopts`, and an earlier draft of this investigation wrongly suggested it.
`gate-timing-2026-08-27.md` measured the **full** suite at every worker count
and found xdist slower at all of them (serial 263.31s vs `-n 2` 616.66s, `-n 4`
430.52s, `-n auto` 404.12s) and red-inducing (5 PASSED→FAILED flips). Row 6 is
a 467-test subset that is pathologically subprocess-bound — precisely the shape
that parallelises well and precisely the shape the full suite is not.

Two things remain genuinely open from that document: it used the **default**
`load` scheduler and never `--dist loadfile`, which is the standard remedy for
the shared-state flips it saw; and 11 test files here touch machine-global
state (`launchctl`, `bootout`, `scheduler.pid`, `pkill`), while only 8 touch
real `Path.home()`.

## Red set (full suite, serial, this host, today)

**31** non-passing: 24 failed + 7 errors, mapping onto 5 of the 6 declared
`ship.baseline_red` entries — 20 `test_init_project` (Lark) + 7
`test_meta_paths` (py3.9 collection) + 2 `test_label_action_totality` +
1 `test_watchdog_log_utf8` + 1 `test_subprocess_encoding_lint::test_toolkit_scan_clean`.
The 6th, `test_vl_describe`, **passed** today (live VL-gateway smoke test,
environment-dependent by its own declared reason).

This settles a question the `ilk-skills-serial-suite-baseline` inbox entry left
open: it predicted serial would show **~26**, with 31 being an xdist artifact.
It is **31 serially too** — the 31-vs-26 gap is not attributable to xdist.

Diffed against `.ilk-baselines/v0.9.88__62005b14ac04.json` (41 node ids,
search space 2799):

- **0 new reds** — nothing in this session's commits regressed anything.
- **10 of the baseline's 41 reds are green today**, all of them
  `test_scheduler_state_file.py`, `test_bouncer_cwd_independence.py`,
  `test_data_home_sandbox.py` — the load-sensitive shape.

So the v0.9.88 baseline was captured on a loaded box and ~24% of its red set is
noise. A Phase 1 diff against it will not say `could_not_compare`; it will
confidently report "10 tests fixed", which is worse.

## Caveat on idleness

None of these runs was gated on the `idle_serial_run.py` runner (0 loop
processes, 0 pytest, load1 < 2.0). The indirect evidence that the box was quiet
for rows 1-2 is that they land within 4-6% of the banked idle 263.31s, and that
the 10 load-sensitive tests above passed. That is not the same as having
measured it. A baseline intended for `/ilk-ship` Phase 1 should be captured
through that runner, with node ids (`-v`); rows here used `-q`.
