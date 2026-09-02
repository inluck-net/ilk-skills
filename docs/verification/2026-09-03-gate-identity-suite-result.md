# Full suite result — gate-identity batch

**Date:** 2026-09-03
**Platform:** macOS (darwin 25.6.0), Python 3.9.6, host chad-mbp
**Base commit:** `21e846d` (measured against in the master's `source_status`)
**Measured at:** `13ee358` (`chore(plans): the-driver-reports-what-it-measured shipped`), clean tree (0 dirty paths at gate time)
**Command:** `python3 -m pytest --timeout=60 --timeout-method=signal --durations=25`

## Summary

```
26 failed, 2492 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in 230.71s (0:03:50)
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

### Not in baseline_red — attribution measured at base `21e846d`

Both extra failures were re-run at the base commit in a throwaway worktree:

| Test | At base | At HEAD | Verdict |
|---|---|---|---|
| `skills/ilk-loop/tests/test_vl_describe.py::TestSmokeGateway::test_hello_image_returns_answer` | **FAILED** | failed | **Not attributed.** Fails at base too; the assertion payload shows a live VL-gateway error (`request_id` echoed by the service). Environment-dependent smoke test, red before this batch. |
| `skills/ilk-loop/tests/test_sentinel_path_agreement.py::test_no_stderr_suppression_in_get_ilk_runtime_dir` | **PASSED** | failed | **Attributed to this batch.** See below. |

**26 failed = 24 baseline-red + the 2 above. 7 errors = `test_meta_paths`.** Every
failure is accounted for.

## Attributed regression 1 — stale line-range sentinel test (mechanism)

`test_sentinel_path_agreement.py::test_no_stderr_suppression_in_get_ilk_runtime_dir`
locates `get_ilk_runtime_dir`'s body with **hard-coded line ranges**
(`test_sentinel_path_agreement.py:113-116` — runner `818 <= ln <= 835`, watchdog
`169 <= ln <= 190`; the test's own comment calls them "approximate").

SP1/SP2 inserted ~170 lines above the function, so `get_ilk_runtime_dir` now
starts at `run_ilk_loop_claude.sh:989`, and range 818-835 now lands inside the
new `get_local_check_targets()` (`:816`), whose
`msgs=$(git -C "$repo" log … 2>/dev/null) || return` probe (`:826`) is a
legitimate stderr suppression on a throwaway probe — and is not inside
`get_ilk_runtime_dir` at all.

The real function body (989-1002) is clean: `sed -n '989,1002p' | grep
'2>/dev/null'` → 0 hits. The driver is correct; the test's locator is stale.

**Fix (step 1):** derive the body from the file — scan from
`^get_ilk_runtime_dir() {` to the next `^[a-z_]+() {` — in both shell files,
instead of hard-coded ranges. Same fix shape for the watchdog range.
`test_sentinel_path_agreement.py` is not in the sub-plan's original
`scope_paths`; scope is extended with it and this record is the documentation
(the regression is measurably this batch's: passes at `21e846d`, fails at HEAD).

## Attributed regression 2 — 14 new `encoding=` lint violations (scanner blind spot)

`test_toolkit_scan_clean` is in `baseline_red`, so the attribution rule
("fails now, passed at base, not in baseline_red") **cannot see** new
violations — the blind spot the 2026-08-30 record predicted. Measured directly
with `lint_subprocess_encoding.py --scan` at base and HEAD:

| | violations |
|---|---|
| base `21e846d` | 272 |
| HEAD `13ee358` | 286 |
| **delta** | **+14, all from this batch** |

Per file (all in `scope_paths`, all files this batch created or edited):

| File | Delta |
|---|---|
| `skills/ilk-loop/tests/test_driver_wiring.py` | +8 (new file) |
| `skills/ilk-loop/tests/test_gate_record_identity.py` | +3 (new file) |
| `skills/ilk-loop/tests/test_gate_record_format_contract.py` | 2 → 5 (+3) |
| `skills/ilk-loop/tests/test_ship_gap.py` | +0 (its 7 hits pre-date the batch) |

**Fix (step 1):** add `encoding="utf-8"` to the flagged capture calls —
16 in the three files above (the 14 attributed, plus 2 pre-existing in
`test_gate_record_format_contract.py`, indistinguishable by line and swept by
the same one-line replace), plus 2 pre-existing in
`test_sentinel_path_agreement.py` (a labelled choice: the file was already
open for the locator fix; one token each). 286 → **268**; 0 violations remain
in any touched file. `test_toolkit_scan_clean` stays red (268 ≠ 0) — the
fixes remove this batch's contribution, they do not repair the pre-existing
noise. The scanner pinning a count instead of a node id remains open.

## Gate regex for step 1 (authored from this measurement)

Expected post-fix line (fixing the sentinel test moves 1 failed → passed; the
lint fixes change no test outcome — the calls already used `text=True`, which
resolved to UTF-8 on this platform):

```
25 failed, 2493 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in
```

Gate command (as declared in the sub-plan, `-q` makes the summary line
unwrapped so the `^` anchor works):

```
python3 -m pytest --timeout=60 --timeout-method=signal -q 2>&1 | tail -3 | grep -qE '^25 failed, 2493 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in '
```

## Conclusion

Two attributed regressions: the stale-line-range sentinel test, and 14 new
`encoding=` lint violations invisible to the attribution rule. Both fixed in
step 1. No other drift: the batch's 8 changed files are exactly the declared
`scope_paths`, and every other failure is baseline-red or environmental
(`test_vl_describe`, red at base).

## Post-fix verification (step 1 gate)

Full suite re-run with the gate's own flags (`-q`), 2026-09-03:

```
25 failed, 2493 passed, 21 skipped, 3 xfailed, 1 warning, 7 errors in 229.88s (0:03:49)
```

Matches the authored regex `^25 failed, 2493 passed, 21 skipped, 3 xfailed,
1 warning, 7 errors in ` — GREEN. Counts moved exactly as predicted: the
sentinel fix moved 1 failed → passed (26 → 25 failed, 2492 → 2493 passed);
the lint fixes changed no test outcome. No failure appeared and none silently
disappeared.
