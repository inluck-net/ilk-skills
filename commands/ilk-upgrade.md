Pull the latest ilk-skills into the active toolkit clone and make it
effective on the current machine.

This command operates on the **toolkit clone** (the `ilk-skills` repo
symlinked/junctioned into `~/.{cursor,claude,codex}/skills`), NOT on
the project in your current working directory.

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending
on the host agent.

## 1. Check first (default)

Default to `--check` — show the user what's behind before applying.

```bash
# macOS / Linux
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --check
```

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Check
```

Reports current branch, commit, fetch result, and ahead/behind counts.
Read-only — never mutates the working tree or runs the installer.

## 2. Apply the update

If the user explicitly says "upgrade", "apply", "更新", or "拉取最新":

```bash
# macOS / Linux
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --apply
```

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Apply
```

Effect:
1. Fetches latest from origin.
2. Runs `git pull --ff-only` (fast-forward only — refuses merges).
3. Detects if `install.sh` / `install.ps1` changed and re-runs the
   installer automatically when needed.
4. Reports: commit log since last pull, files changed, installer
   outcome.

## 3. Force apply (skip live-loop guard)

By default, `--apply` refuses when a live loop or watchdog PID is
detected. Override with `--force` only when you know the loop is idle:

```bash
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --apply --force
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Apply -Force
```

## When the agent invokes this command

1. Detect OS: macOS/Linux → `upgrade.sh`; Windows → `upgrade.ps1`.
2. Default to `--check` unless the user explicitly says "upgrade" or
   "apply".
3. After `--apply`, report: what changed (commit log), whether the
   installer re-ran, and any warnings (Windows copy-fallback staleness).
4. If `--check` shows the toolkit is already up-to-date, say so and
   stop — don't offer to apply.
5. **Do not** mix with `/ilk-status` — they operate on different repos
   (toolkit vs project).

## Boundary rules

This command operates on the **toolkit clone only**. It must NOT:

- Modify or inspect the cwd project's plans, git state, or files.
- Run `loop_status.py` or any per-project script.
- Auto-apply without the user's explicit request (default is check).
