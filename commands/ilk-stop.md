Stop a running ilk-loop and its watchdog for the project above cwd.

This is a **destructive-capable but safe-by-default** stop command. It
resolves the project, stops the watchdog (so it won't auto-restart), then
tree-kills the loop process and any orphaned workers. Use when the user
says "stop ilk", "停 ilk", `/ilk-stop`, "关掉 ilk", "kill ilk",
"shutdown ilk", or wants to end a running loop.

The safe default **never** touches tracked or untracked project files.
An explicit `--reset-worker-changes` flag is required to clean up
worker artifacts.

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending
on the host agent.

## 1. Resolve project context

Resolve the project once with `ilk_paths.py` (owned by `ilk-loop`).

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
```

```powershell
# Windows
python3 "<skill-root>\ilk-loop\scripts\ilk_paths.py" --start .
```

Extract `project_root` from the JSON output. If `project_root` is `null`,
tell the user to `cd` into a project root and STOP.

## 2. Stop the loop

Invoke the host-appropriate stop script with the resolved project path:

```bash
# macOS / Linux
bash "<skill-root>/ilk-launcher/scripts/stop.sh" --project-path "$PROJECT_ROOT"
```

```powershell
# Windows
& "<skill-root>\ilk-launcher\scripts\stop.ps1" -ProjectPath $ProjectRoot
```

The script will:

1. Stop the watchdog first (prevents auto-restart).
2. Read the PID file from the external launcher dir.
3. Tree-kill the process group (`kill -- -PID` on macOS/Linux,
   `taskkill /T /F /PID` on Windows).
4. Scan for orphaned worker processes (claude, gtimeout, tee, renderer)
   and terminate them.
5. Mark the sentinel as interrupted (so `/ilk-feedback` classifies
   correctly).
6. Report the repo's dirty-tree state as a read-only summary.

Report what was stopped and any warnings from the script output.

## 3. Reset mode (explicit opt-in only)

If the user explicitly asks to reset worker artifacts (wrong model,
dirty tree), pass the reset flag:

```bash
# macOS / Linux — preview then reset
bash "<skill-root>/ilk-launcher/scripts/stop.sh" --project-path "$PROJECT_ROOT" --reset-worker-changes
```

```powershell
# Windows — preview then reset
& "<skill-root>\ilk-launcher\scripts\stop.ps1" -ProjectPath $ProjectRoot -ResetWorkerChanges
```

The reset mode shows a dry-run preview, then runs `git restore .` for
tracked changes and `git clean -fd` for untracked files. Logs and
postmortems are preserved (they live under `~/.ilk-data/`).

**Never** pass `--reset-worker-changes` unless the user explicitly
requests it. The safe default is to stop only.

## 4. Summary

After stopping, print:

```
ilk stopped: <project_key>
  PID:          <pid> (killed)
  Watchdog:     stopped
  Orphans:      <count> terminated (or "none found")
  Repo state:   clean / <dirty summary>
```

Tell the user they can check postmortem results with `/ilk-feedback`
and relaunch with `/ilk-run`.
