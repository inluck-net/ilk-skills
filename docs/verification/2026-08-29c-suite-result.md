# Full suite result — MASTER-2026-08-29c (a killed iteration is still classifiable)

Measured on macOS / Python 3.9.6, commit `2ffe9f6`, via
`python3 -m pytest --timeout=60 --timeout-method=signal --durations=25`
(the `ship.suite` invocation declared in `.ilk-launch.json`), driven through
`batch_gate.py --run`.

```
30 failed, 2455 passed, 21 skipped, 3 xfailed, 7 errors in 285.48s
```

`batch_gate` reported: **31 excused by `ship.baseline_red`, 6 undeclared.**

## Classification

Every failing node id at this commit, classified. 37 total = 30 failed + 7 errors.

- baseline_red: **31** (24 failed + 7 errors) — matches the declared set exactly.

  | count | node | declared reason |
  |---|---|---|
  | 20 | `skills/ilk-lark-tickets/tests/test_init_project.py` | Lark API fixtures missing from CI env (`as_of` 2026-08-19) |
  | 7 (errors) | `skills/ilk-loop/scripts/test_meta_paths.py` | Python 3.9.6 — module-scope `list[str] \| None` without `from __future__ import annotations` |
  | 2 | `skills/ilk-watchdog/tests/test_label_action_totality.py` | Windows-only: spawns `powershell`, absent on macOS |
  | 1 | `skills/ilk-watchdog/tests/test_watchdog_log_utf8.py::TestUtf8RoundTrip::test_write_read_utf8_no_bom` | Windows-only: spawns `powershell` |
  | 1 | `tests/test_subprocess_encoding_lint.py::test_toolkit_scan_clean` | linter finds pre-existing `subprocess` calls without `encoding=` |

  Arithmetic: 20 + 2 + 1 + 1 = **24 failed**; 7 = **7 errors**. The declared
  baseline is 24 failed / 7 errors. No `baseline_red` entry passed
  unexpectedly, so the list is not stale (AC-4).

- attributed: **6** — all introduced by this batch, all fixed in step 1.

  | count | node | cause |
  |---|---|---|
  | 5 | `skills/ilk-loop/tests/test_iteration_record_precedes_work.py` (all) | `ILK_DATA_HOME` leak — see below |
  | 1 | `skills/ilk-loop/tests/test_data_home_sandbox.py::test_meta_no_unguarded_scheduler_harness` | new scheduler harness did not request `scheduler_sandbox` |

## The attributed failures, and why they were invisible until the full suite

Both are defects in **this batch's new tests**, not in the product.

**1. The `ILK_DATA_HOME` leak (5 tests).** `RunnerSandbox.env()` pinned `HOME`
but merely inherited `os.environ`. `ILK_DATA_HOME` wins over `HOME` when
resolving the data root, and **4 of the 8 places that set it in the suite use a
raw `os.environ["ILK_DATA_HOME"] = ...` rather than `monkeypatch.setenv`**, so
the value survives the test that set it:

```
skills/ilk-feedback/tests/test_local_checks_broken.py:297, :338
skills/ilk-feedback/tests/test_source_id.py:35
skills/ilk-feedback/tests/test_upstream_candidates.py:57
```

`ilk-feedback` sorts before `ilk-loop`, so in a full run those land first. The
runner then wrote its artifacts to a data home the sandbox never inspected, and
the tests reported "no record was written" when a record had been written
perfectly well, somewhere else.

The signature is worth recording: **passed alone, passed in every subset,
failed all five in the full suite.** A subset that cannot reproduce a failure is
not evidence the failure is flaky.

Fixed by pinning `ILK_DATA_HOME` explicitly and stripping `ILK_DATA_DIR`,
matching `conftest.py`'s `scheduler_sandbox`.

**2. The unguarded scheduler harness (1 test).** `test_data_home_sandbox.py`'s
meta-test requires any file executing `scheduler.sh` to request
`scheduler_sandbox` or carry `allow_real_data_home`. SP3's step 0 said to use
the fixture; it was not used. Fixed by threading the sandbox env through the
harness — real isolation, not merely satisfying the lint. The helpers are pure
and touch no data home today, but a harness that evals `scheduler.sh` without
isolating the data root is one edit away from writing to the live `~/.ilk-data`.

## Also fixed (not a suite failure, but attributable)

This batch's new test files added **10** `subprocess` calls without `encoding=`
to `test_subprocess_encoding_lint.py`'s violation list. That test is
`baseline_red` and was failing either way, so it did not change the count — but
the additions were this batch's. All 10 now carry `encoding="utf-8"`; the batch
contributes **0** violations.

## Not in the suite, checked by hand

`pytest`'s `testpaths` collect `.py` only, so the repo's `.sh` and `.ps1`
harnesses are in neither the 2492-test suite nor the baseline:

- `test_watchdog_action_vocab.sh` — **PASS** (confirms the new `timeout` arm
  keeps the `.sh`/`.ps1` action vocabularies total)
- `test_watchdog_empty_classification.sh` — **PASS**
- `test_scheduler_scan_error_reason.sh` — **PASS**
- `test_scheduler.sh` — **FAIL**, and fails identically at `6aaf28b` in a clean
  worktree. Pre-existing, not attributed.

---

# AC-5 — the two behavioural claims, proven end to end

A green suite does not prove either of the things this batch exists to deliver.
Both were exercised for real.

## Claim 1 — a `gtimeout`-killed iteration produces a postmortem

Real runner, stub agent on `PATH`, real `gtimeout` bound, dirty tree at kill
(the condition that used to destroy the record):

```
sentinel state:   timeout
.ilk-loop.log:    720 bytes          (start record + summary)
postmortems/:     absent before collect.py
collect.py  ->    postmortems/20260829-203015.md
                  classification: "timeout-bound"
```

The full chain the batch exists to deliver: killed iteration → record →
classifiable → postmortem, which is the artifact `scheduler.sh:510` reads.

## Claim 2 — three no-progress launches stop being dispatched, no postmortem present

`postmortems/` absent throughout, verified before and after.

**A project the scheduler has already seen** — the observed rezmac scenario:

```
launch 1 (12:01)  count=1  allow
launch 2 (12:37)  count=2  allow
launch 3 (13:12)  count=3  BLOCK   <- the relaunch that actually happened
```

**A project never seen before** gets one free launch first, per invariant 5
(nothing may be bounded for never having been seen), so it blocks on the 4th:

```
launch 1  progressed=true (first sight)  count=0  allow
launch 2  count=1  allow
launch 3  count=2  allow
launch 4  count=3  BLOCK
```

Both are correct and are stated here because "three launches" is true of the
observed case and off by one for a brand-new project — worth knowing before
someone reads a 4th dispatch as a bug.

**The ack clears it** (AC-8): writing
`runtime/launcher/blacklist-cleared.json` — the same file `/ilk-resume` and
`blacklist_status.py` use — yields `cleared=true`. One gesture, both bounds.
