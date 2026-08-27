# Shell harness wall-clock — 2026-08-27

Measured on macOS (Darwin 25.6.0), live launchd scheduler (pid 82184),
hermetic sandbox (HOME + ILK_DATA_HOME pinned to tmpdir).

| Harness | BEFORE (s) | AFTER (s) | Δ |
|---|---|---|---|
| test_scheduler_lock_contention.sh | 120 | 0.19 | 630× |
| test_scheduler_clone_logging.sh | n/a | 0.03 | — |
| test_project_runner_liveness.sh | n/a | 0.74 | — |
| test_stop_leaves_no_survivors.sh | n/a | 9.71 | — |

**BEFORE** for `test_scheduler_lock_contention.sh` is from run `20260827-011645`,
iteration 2, 01:24:31 (120s). The 630× speedup is direct evidence the harness
had been scanning the real 20-project `~/.ilk-data`.

## Judgment call: case-1 timeout bound

judgment call: case-1 bound = `60`s because case 2 already uses `timeout 60`
and the hermetic case-1 measured at 0.19s (300× headroom); wrong if a slower
host (`ship.hosts` also lists `rezmac`) where a legitimate run exceeds 60s and
the harness fails for being slow rather than wrong.
