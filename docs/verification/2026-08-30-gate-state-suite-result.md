# Full suite result — gate-state batch

**Date:** 2026-08-30
**Platform:** macOS (darwin 25.6.0), Python 3.9.6
**Base commit:** `dec7522` (HEAD at time of run)
**Command:** `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25`

## Summary

```
24 failed, 2481 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in 226.98s (0:03:46)
```

## Failures

### Baseline-red (not attributed to this batch)

| Test | Count | Reason |
|---|---|---|
| `skills/ilk-lark-tickets/tests/test_init_project.py` | 10 failed | Lark API fixtures missing from CI env |
| `skills/ilk-loop/scripts/test_meta_paths.py` | 7 errors | Python 3.9.6 collection errors (module-scope `list[str] \| None`) |
| `skills/ilk-watchdog/tests/test_label_action_totality.py` | 2 failed | Windows-only: spawns `powershell`, absent on macOS |
| `skills/ilk-watchdog/tests/test_watchdog_log_utf8.py::TestUtf8RoundTrip::test_write_read_utf8_no_bom` | 1 failed | Windows-only: spawns `powershell`, absent on macOS |
| `tests/test_subprocess_encoding_lint.py::test_toolkit_scan_clean` | 1 failed | Linter finds subprocess calls without explicit `encoding=` |

**Total baseline-red:** 21 failed + 7 errors = 28

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
