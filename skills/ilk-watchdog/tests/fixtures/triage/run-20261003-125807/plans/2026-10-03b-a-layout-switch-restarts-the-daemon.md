---
plan: a-layout-switch-restarts-the-daemon
status: shipped
current_step: 2
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-10-03
verification_tier: loop-verified
regression_for: "chad-mbp 2026-10-03 12:5x: after install.sh --layout release --apply rewired the scheduler plist, bounce_daemons.sh said 'fresh (toolkit_head matches HEAD)' and left pid 4805 running scheduler.sh from the dev clone; fixed only by a manual bootout/bootstrap"
depends_on: ["a-release-host-knows-its-head"]
data_prereqs: []
env_prereqs: []
recommended_iteration_timeout_min: 60
local_checks: []
scope_paths:
  - "skills/ilk-watchdog/scripts/bounce_daemons.sh"
  - "install.sh"
  - "skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py"
unit_test_targets:
  - "skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py"
e2e_test_targets: []
must_add_tests: true
ci_required: false
ci_status_endpoint: github
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
auto_block_fails: 1

---

# Sub-plan: a layout switch restarts the daemon

Part of [MASTER-2026-10-03b-a-release-host-knows-its-version-execution-plan](./MASTER-2026-10-03b-a-release-host-knows-its-version-execution-plan.md). **Order #1.**

> **Work only in the selfmod worktree.** Relative paths only; never the LIVE
> clone, never `~/.ilk/releases`, never through `~/.claude*/skills/…`.
> Tests must NEVER call the real `launchctl` or touch
> `~/Library/LaunchAgents`: use a fake `launchctl` on PATH and a tmp plist
> dir, as the existing bouncer tests do. Never run the full suite yourself.
> **Never write a `### Step` heading outside `## Steps`.** After you ship,
> END YOUR TURN.

## Before you start

- `bounce_daemons.sh` decides `stale` by comparing the state file's
  `toolkit_head` with the tree's head (`:97-145`; after sub-plan 0 via
  `_ilk_toolkit_head.sh`). When stale it bounces with launchctl bootout +
  bootstrap (`:329`, `:345`), which re-reads the plist.
- The state file is `$ILK_DATA/scheduler.state.json`, holding `{pid,
  started_at, toolkit_head}`.
- `install.sh` `--layout release|clone` (`:744-…`) rewrites the plist's
  `scheduler.sh` path (the "would rewire plist" line in a dry run) but never
  bounces.
- Read the existing tests first: `skills/ilk-watchdog/tests/test_bounce_daemons.py`
  and `test_bouncer_cwd_independence.py` already fake `launchctl` and the
  plist dir; reuse their fixtures.
- Contract docs: `skills/ilk-loop/references/detached-component-contracts.md`.

## What changes

1. **Freshness includes the script path.** The daemon is `stale` when the
   recorded pid's command line (`ps -o command= -p <pid>`) does not contain
   the plist's `scheduler.sh` path (from the plist's ProgramArguments:
   `plutil -extract ProgramArguments json` on macOS, or a plain grep of the
   `<string>…scheduler.sh</string>` line as the portable fallback). The
   reason names both paths. A recorded pid that is not alive is `stale`
   (today's behaviour if already so; pin it).
2. **`install.sh --layout … --apply` bounces after rewiring a plist.** It
   calls the bouncer from the NEW layout's path, and prints the bouncer's
   verdict. Not on a dry run.

## Acceptance criteria

- **AC-1:** state `toolkit_head` = HEAD, but the recorded pid's command line
  runs `/clone/…/scheduler.sh` while the plist names
  `/home/.ilk/current/…/scheduler.sh`: `--check` prints `stale` and names
  both paths. Red-first.
- **AC-2:** `install.sh --layout release --apply` with a fake plist and a fake
  `launchctl` invokes bootout + bootstrap exactly once. Red-first.
- **AC-3 (control):** same head, same path, live pid: `fresh`.
- **AC-4 (control):** `install.sh --layout release` (dry run) never calls
  launchctl.

## Steps

### Step 0 — red-first pins

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py -q -p no:cacheprovider --timeout=120 --timeout-method=signal"
    timeout: 300
```

- Write `skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py`.
  AC-1 and AC-2 are `xfail(strict=True)`; AC-3 and AC-4 are unmarked.
- Commit: `test(deploy): pin that a daemon on the wrong script path is stale and a layout switch restarts it [plan:a-layout-switch-restarts-the-daemon#step-0]`

### Step 1 — path-aware freshness, and a layout switch bounces

```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py skills/ilk-watchdog/tests/test_a_release_host_knows_its_head.py skills/ilk-watchdog/tests/test_bounce_daemons.py skills/ilk-watchdog/tests/test_bouncer_cwd_independence.py skills/ilk-watchdog/tests/test_scheduler_state_file.py skills/ilk-watchdog/tests/test_install_scheduler_autostart.py skills/ilk-ship/tests/test_host_deploy_status.py skills/ilk-ship/tests/test_false_ok_family.py tests/test_install_layout.py -q -p no:cacheprovider --timeout=180 --timeout-method=signal"
    timeout: 900
  - command: "bash tests/test_install_targets.sh"
    timeout: 300
```

- Implement "What changes", and remove the xfail marks.
- Commit: `fix(deploy): a daemon on the wrong script path is stale, and install.sh --layout --apply restarts it [plan:a-layout-switch-restarts-the-daemon#step-1]`
- Ship via `ship_transition.py --ship a-layout-switch-restarts-the-daemon`, then END YOUR TURN.

## Findings

### Re-entry 2026-10-03 13:23:13 — step 0 killed at its bound
- Step in flight: ### Step 0 — red-first pins

## Reference reading

- `skills/ilk-watchdog/scripts/bounce_daemons.sh`
- `install.sh` (the `--layout` section)
- `skills/ilk-loop/references/detached-component-contracts.md`
