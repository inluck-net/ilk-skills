# Full suite result — batch-verdict-attribution batch

**Date:** 2026-09-03
**Platform:** macOS (darwin 25.6.0), Python 3.9.6, host chad-mbp
**Base commit:** `d7c378e` (v0.9.80 — parent of the batch's first commit `8813c5c`)
**Measured at:** `fec60f3` (`chore(plans): a-batch-verdict-names-its-blockers shipped`), clean tree
**Command:** `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25 -q`

The declared `ship.suite` flags plus `-q` — the quiet flag is a labelled
addition so the summary line is unwrapped and step 1's gate regex can anchor
on `^`; it changes no counts. No homebrew python3 exists on this host, so the
runner's `path_prelude` (`/opt/homebrew/bin`) is a no-op for python3 — this
run and the driver's gate execute the same `/usr/bin/python3` 3.9.6.

## Summary

```
25 failed, 2498 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in 241.85s (0:04:01)
```

## Attribution verdict: 0 of 32 non-green outcomes attributed to this batch

Denominator: 2547 tests executed (25 + 2498 + 21 + 3) plus 7 collection
errors. The 32 non-green outcomes (25 failed + 7 errors) are, by node id:

| Test | Count | `baseline_red` since |
|---|---|---|
| `skills/ilk-lark-tickets/tests/test_init_project.py` | 20 failed | 2026-08-19 |
| `skills/ilk-watchdog/tests/test_label_action_totality.py` | 2 failed | 2026-08-26 |
| `skills/ilk-watchdog/tests/test_watchdog_log_utf8.py::TestUtf8RoundTrip::test_write_read_utf8_no_bom` | 1 failed | 2026-08-26 |
| `tests/test_subprocess_encoding_lint.py::test_toolkit_scan_clean` | 1 failed | 2026-08-19 |
| `skills/ilk-loop/tests/test_vl_describe.py::TestSmokeGateway::test_hello_image_returns_answer` | 1 failed | 2026-09-03 |
| `skills/ilk-loop/scripts/test_meta_paths.py` | 7 errors (collection) | 2026-08-19 |

**Total: 25 failed + 7 errors, all six declared `baseline_red` entries.**

The attribution rule (`/ilk-ship` Phase 1 — a failure is attributed iff it
fails now, passed at the base commit, and is not in `baseline_red`) has an
**empty candidate set**: every failure is in `baseline_red`, so there is no
failure whose base-commit status is unknown. The base-commit re-run that the
rule would otherwise require is therefore not needed — and was not run.

## Delta vs the pre-batch measurement

Reference: `2026-09-03-gate-identity-suite-result.md`, post-fix line measured
at `13ee358` — `25 failed, 2493 passed, 21 skipped, 3 xfailed, 1 warning,
7 errors`. Between that tree and this batch's base `d7c378e` only the
changelog and the `baseline_red` declaration (`.ilk-launch.json`) landed —
no test outcomes.

| | pre-batch | now | delta |
|---|---|---|---|
| failed | 25 | 25 | 0 — same node ids |
| passed | 2493 | 2498 | **+5** |
| skipped / xfailed / errors | 21 / 3 / 7 | 21 / 3 / 7 | 0 |

The +5 is exactly the batch's new module:
`skills/ilk-loop/tests/test_batch_gate_attribution.py`, 5 collected, all
passing — including the back-compat AC-2 (a legacy four-field record still
loads) that the MASTER flags as the one most likely to break silently.

## The batch's named risk checked

SP2 names eleven modules as the likeliest regression surface (six
`test_batch_gate*.py`, three `test_ship_audit*.py`, `test_ship_integrity.py`,
`test_loop_status_proven_agrees.py`): 137 tests collected, 0 of them appear
in the failure list above. The batch's 4 changed files are exactly the
declared `scope_paths`.

## Gate regex for step 1 (authored from this measurement)

```
^25 failed, 2498 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in
```

Gate command, as declared in the sub-plan (`-q` keeps the line unwrapped so
the `^` anchor works):

```
python3 -m pytest --timeout=60 --timeout-method=signal -q 2>&1 | tail -3 | grep -qE '^25 failed, 2498 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in '
```

Only docs-only commits (this record, plan markers) land between this
measurement and the gate run, so the line is expected stable. Any drift — a
new failure **or** a count that moved without a code change — is investigated
before ship, not re-baselined.

## Conclusion

Step 1 has nothing to fix: zero attributed regressions. The step-1 gate
re-runs the suite to confirm this line on the final tree; a red gate there
means drift between the two runs (flake or environment), not a missing fix.
