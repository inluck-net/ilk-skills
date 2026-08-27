# SP6 — pidfile-flake evidence

## The flake: two gates at adjacent commits

| gate | HEAD | result |
|---|---|---|
| 15:06:15 | `301257a` | verdict **pass**, 0 undeclared |
| 15:20:28 | `786b782` | verdict **fail**, 1 undeclared — `test_pidfile_in_sandbox` |

Commits differ only by plan-bookkeeping (`docs/principles/` doc). The test is
load-sensitive, not broken.

## `rm -f` inventory in scheduler.sh — what gets removed and what survives

```
line 80:  rm -f "$pidfile"        (inside a scan-loop guard)
line 90:  rm -f "$SCHEDULER_PIDFILE"  (EXIT trap, normal exit)
line 112: rm -f "$SCHEDULER_PIDFILE"  (EXIT trap, error exit)
line 113: rm -f "$_SCAN_STDERR_FILE" (cleanup)
line 279: rm -f "$pid_file"       (unrelated local variable)
```

**`SCHEDULER_STATE_FILE` (`scheduler.state.json`) is never removed.** Written at
startup (line 24-27, `write_scheduler_state`), persists after exit.

## Foreground dry-run measurement

Host, `HEAD 59ca53c`, 2026-08-27, `HOME`/`ILK_DATA_HOME`/`ILK_SKILL_HOME`
pinned to an empty sandbox:

```
$ bash scheduler.sh --once --dry-run
exit 0, elapsed 0s
```

Sandbox contents after run:
- `.ilk-data/scheduler.state.json` — `{"pid":…,"started_at":…,"toolkit_head":…}`
- `.ilk-data/logs/scheduler.log` — present

Real `~/.ilk-data/scheduler.pid` mtime unchanged (`02:34:46`) — the sandbox
run did not touch the host.
