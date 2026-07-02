# Handoff → Mac agent: verify the ilk detached loop runner on macOS (spike S3)

> **For an agent running on macOS.** Decides whether the ilk launcher can start
> and steer a *detached* loop on a Mac node — the prerequisite for the
> hub(Windows)↔agent(Mac) design in ilk-pocket's `docs/specs/2026-07-01-ilk-pocket-design.md`
> (§2.1), spike **S3**. Diagnosed statically from a Windows box; the runtime
> proof needs real macOS hardware. Date: 2026-07-01. Author: Windows planner session.
>
> **Why this lives in ilk-skills, not ilk-pocket:** the scripts under test ARE
> the installed ilk-launcher / ilk-loop skills (this repo), and **ilk-pocket is
> not cloned on the Mac**. So the runbook travels with the skills. You do NOT
> need ilk-pocket to run this — any throwaway/existing project with a
> `docs/plans/MASTER-*.md` works as the test target (see step A).

## Why this spike

The launcher SKILL's prose historically said detached runs were "Claude-only,
PowerShell." The multi-machine design needs the **Mac** to run a *detached* loop
that survives the terminal/SSH session closing — otherwise the phone can't
control a Mac loop through the hub.

## What static analysis (from Windows) already found — expect a PASS

Reading `skills/ilk-launcher/scripts/launch.sh` +
`skills/ilk-loop/scripts/run_ilk_loop_claude.sh` in this repo:

- `launch.sh` **is** a real macOS bash port of `launch.ps1`; both the `claude`
  and `claude-worker` engines map to `run_ilk_loop_claude.sh`
  (`runner_script_for_engine`).
- It detaches via `start_detached_session()`:
  `nohup setsid bash -c "$cmd" >log 2>&1 </dev/null &`, and where `setsid` is
  absent (**typical on macOS** — it ships no `setsid`) falls back to
  `nohup python3 -c 'import os,sys; os.setsid(); os.execvp("/bin/bash", …)' … &`.
  → new session, new process group, no controlling terminal, stdin `/dev/null`.
  This is genuine detachment; it should survive closing Terminal/SSH.
- Group-kill for teardown: `stop.sh` reaps the tree via the leader PID (new
  process group), so a detached run is still stoppable.
- **No `caffeinate`** anywhere → a **sleeping Mac suspends the loop** (same
  limitation as the Windows launcher; a caveat to document, not a blocker). If
  you want it fixed rather than documented, that's a follow-up change to
  `launch.sh` in this repo — out of scope for the verification itself.

So the expected result is **PASS with a sleep caveat**. This runbook confirms it
on real hardware and records the caveat precisely.

## Prerequisites to confirm first

```bash
which claude            # claude CLI installed
claude --version        # and authenticated (run `claude` once if unsure)
which python3           # hard dependency for the setsid shim
tailscale status        # Mac is on the tailnet (needed for the §2.1 mesh)
ls ~/.claude/skills/ilk-launcher/scripts/launch.sh   # skill installed on Mac
```

If `claude` isn't installed/authed on the Mac, that alone blocks Mac-side
control — note it and stop.

## Verification steps

### A. Detached launch survives terminal close (the core test)

Use **any** project that has a `docs/plans/MASTER-*.md` (a throwaway is fine —
ilk-pocket is NOT required). Prefer a `--dry-run` first to see resolution, then
a real launch:

```bash
S=~/.claude/skills/ilk-launcher/scripts/launch.sh
bash "$S" --project-path /path/to/some/project --dry-run     # sanity: prints resolved plan
bash "$S" --project-path /path/to/some/project --max-iterations 2 --iteration-timeout-min 5
```

Record the printed PID / log path. Then **prove detachment**:

```bash
# 1. Find the leader PID (also in the launcher PID file)
python3 ~/.claude/skills/ilk-loop/scripts/ilk_paths.py --start /path/to/some/project --where
cat <that>/runtime/launcher/running.pid

# 2. CLOSE this terminal window entirely (or exit an SSH session), reopen a new one.

# 3. Confirm the process is STILL alive in the new terminal:
ps -p "$(cat <that>/runtime/launcher/running.pid)" -o pid,ppid,stat,command
#    stat should NOT show a controlling terminal; ppid should be 1 (reparented to init)
```

### B. Status tools see it as running

```bash
python3 ~/.claude/skills/ilk-launcher/scripts/status_all.py
python3 ~/.claude/skills/ilk-launcher/scripts/status_progress.py --project-path /path/to/some/project --json
# expect: state "running", launcher_alive true, a live pid
```

### C. Stop works

```bash
bash ~/.claude/skills/ilk-launcher/scripts/stop.sh --project-path /path/to/some/project
# expect: process tree killed, running.pid removed, sentinel -> interrupted
```

### D. Sleep caveat (quick check, don't fully verify)

Confirm there is no `caffeinate` wrapping the run (there isn't, per static read).
Conclusion to record: **for unattended Mac runs, the display/system must be kept
awake** (`caffeinate -i`, or Energy Saver "Prevent sleeping when display is off"
while on power). Same class of caveat as the Windows machine-asleep note.

### E. Leftover from S1 — Mac↔Win tailnet mesh (needed for hub↔agent)

```bash
tailscale ping chad-z66            # the Windows node
# expect a pong (direct or via DERP). Confirms §2.1 hub(Win)↔agent(Mac) reach.
```

## Pass criteria

- **PASS** if: A shows the process alive after closing the terminal (ppid 1,
  no controlling tty); B shows `running`; C stops cleanly; E pongs.
- **PASS-with-caveat** (expected): all of the above, plus the documented
  "keep Mac awake for unattended runs" note.
- **FAIL** if: the runner dies when the terminal closes, or `claude`
  isn't available on the Mac, or `launch.sh` errors on macOS.

## Files under test (this repo)

- `skills/ilk-launcher/scripts/launch.sh` — detached spawn (`start_detached_session`).
- `skills/ilk-loop/scripts/run_ilk_loop_claude.sh` — the `.sh` runner.
- `skills/ilk-launcher/scripts/stop.sh` — process-tree teardown.
- `skills/ilk-launcher/scripts/status_all.py`, `status_progress.py` — status tools.

## Report back

Since **ilk-pocket is not on the Mac**, don't try to edit its spec. Instead,
capture the result in one of these (whichever the planner reaches):

1. Append an **`## Outcome (Mac agent, <date>)`** section to the bottom of THIS
   file (matches the sibling `2026-07-01-sh-steerhook-mac-verify.md` pattern),
   then commit. The Windows planner folds it into the ilk-pocket spec (mark S3
   done, finalize the §2.1 hub↔agent decision).
2. Or drop a line in the cross-project handoffs inbox
   (`~/Documents/handoffs/_inbox.md`) referencing this file.

Please include:

1. `claude --version` + authed? python3 present?
2. Did the process survive terminal close? (the ppid/stat line from step A3)
3. `status_progress.py --json` state value.
4. `stop.sh` clean?
5. `tailscale ping chad-z66` result (direct vs DERP).
6. Any macOS-specific error from `launch.sh`.
