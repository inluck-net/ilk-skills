# Retro 2026-09-22 — the switch wrote the old model back

`ilk-worker-model use mimo-v2.6-pro@coder` claimed success (exit 0, all homes
reported "env rewritten") and every file still said `mimo-v2.5-pro`.  The
switch never landed.

## What happened

| Time | Event |
|---|---|
| 2026-09-22 ~11:15 | first `use mimo-v2.6-pro@coder` — probe on manager fails, rollback, exit PROBE |
| 2026-09-22 ~11:15 | second run (not clear why) — probe on planner succeeds, exit 0 |
| 2026-09-22 ~11:18 | `show` reports all homes still at mimo-v2.5-pro |

## Root cause: three bugs stacked

### Bug 1 — `resolve_provider_env` always fails (the fatal one)

`ccswitch_import` is not on PATH in production (it ships as a `.py` in
`tools/claude-worker/`, not as an installed entrypoint).  `resolve_provider_env`
called it as a bare command, always got `FileNotFoundError`, raised
`ValueError`, and `resolve_provider_env_or_home` fell back to reading the home's
`settings.json` — which still has the **old** model.

So the switch reads the old env from the home, writes it back, and reports
success.  Every `use` call is a no-op.

**Fix:** `_ccswitch_cmd()` helper tries the bare command first (respects PATH
and test fakes), falls back to invoking `ccswitch_import.py` next to
`worker_model.py` with `sys.executable`.

### Bug 2 — the probe sweeps non-API roles

`cmd_use` switches ALL role homes (planner, manager, workers) via
`_homes_for_roles`.  The manager uses `"auth": "official"` (OAuth).
`probe_config` runs a `claude -p` session under the manager's home, which
reports the official account's model (not the env override).  The mismatch
check fires, a rollback restores every home, and the switch never lands.

**Fix:** the config-mismatch check now applies only to worker-tier homes.
Non-worker homes get the liveness check only.

### Bug 3 — same PATH issue in `list` calls

Three call sites for `ccswitch_import list` had the same bare-command problem.
Fixed with the same `_ccswitch_cmd()` helper.

## What to carry forward

- **A command that "succeeds" but does nothing is worse than one that fails
  loudly.**  Exit 0 with "env rewritten" while every file is unchanged is the
  hardest failure mode to diagnose — it looks exactly like success.
- **A fallback that reads stale state is not a fallback.**  The "fallback to
  home's env block" was intended for when CCSwitch is genuinely not installed.
  In practice it fired every time because the bare command was not on PATH.
  The fallback gave the old value, the write accepted it, and the probe (if it
  ran) saw the same stale value and passed.
