# Full suite result — gate-state batch

**Date:** 2026-08-30
**Platform:** macOS (darwin 25.6.0), Python 3.9.6
**Base commit:** `4bbba72` (the commit this batch branches from)
**Re-measured:** `bd6d2fb`, clean tree (0 dirty paths at gate time)
**Command:** `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25`

## Summary

```
24 failed, 2481 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in 231.20s (0:03:51)
```

## Failures

### Baseline-red (not attributed to this batch)

| Test | Count | Reason |
|---|---|---|
| `skills/ilk-lark-tickets/tests/test_init_project.py` | 20 failed | Lark API fixtures missing from CI env |
| `skills/ilk-loop/scripts/test_meta_paths.py` | 7 errors | Python 3.9.6 collection errors (module-scope `list[str] \| None`) |
| `skills/ilk-watchdog/tests/test_label_action_totality.py` | 2 failed | Windows-only: spawns `powershell`, absent on macOS |
| `skills/ilk-watchdog/tests/test_watchdog_log_utf8.py::TestUtf8RoundTrip::test_write_read_utf8_no_bom` | 1 failed | Windows-only: spawns `powershell`, absent on macOS |
| `tests/test_subprocess_encoding_lint.py::test_toolkit_scan_clean` | 1 failed | Linter finds subprocess calls without explicit `encoding=` |

**Total baseline-red:** 20 + 2 + 1 + 1 = **24 failed**, plus **7 errors**.
That accounts for every failure in the summary line above (24 = 24, 7 = 7).

### Attributed to this batch

**None.** All failures are accounted for by the `baseline_red` list in `.ilk-launch.json`.

## Attribution rule

A failure is attributed to this batch iff it:
1. Fails now
2. Passed at the batch's base commit
3. Is not in `baseline_red`

No failures meet all three criteria.

## Conclusion

No attributed regressions. The batch is clean.

## Correction (2026-08-30, human pass)

The first version of this record reached the right verdict off wrong numbers:
`test_init_project.py` was listed as 10 failed (it is 20), and the
"total baseline-red" line read 21 failed — matching neither the table (14) nor
the summary (24). Re-measured on a clean committed tree at `bd6d2fb`; the counts
above are the measured ones and the verdict is unchanged.

## Attributed regression found and fixed by the human pass

11 `subprocess.run(...)` calls with `text=True` and no `encoding=` were
introduced by this batch — 7 in the new `isolate_to_head` code
(`run_local_checks.py:476-555`) and 4 in `ship_gap.py`. Fixed in `bd6d2fb`.

**The attribution rule could not have found these.** They are detected by
`tests/test_subprocess_encoding_lint.py::test_toolkit_scan_clean`, which is in
`.ilk-launch.json`'s `baseline_red` — so the node id was red at the base commit
and red after, and "fails now, passed at base, not in baseline_red" excludes it
by construction. A whole-file scanner parked in `baseline_red` is a blind spot
where new violations accumulate invisibly.

Follow-up for its own batch: a `baseline_red` entry whose test is a *scanner*
should pin a violation **count**, not just a node id.
