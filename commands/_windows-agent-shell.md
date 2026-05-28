# Windows agent shell rules (ilk commands)

On Windows, Cursor's Shell tool often runs **Git Bash**, not PowerShell.
The ilk command docs include separate bash and PowerShell blocks — **do not
paste PowerShell syntax into Git Bash** (or vice versa).

## Common failures

| Mistake | Symptom |
|---------|---------|
| `python3` on Windows | "Python was not found" (Microsoft Store alias) |
| `$env:USERPROFILE\...` in Git Bash | Literal `:USERPROFILE` in the path |
| PowerShell blocks run in Bash | `Write-Output: command not found` |
| Bash blocks run in PowerShell | `$HOME` may work, but `&&` needs PS 7+ |

## Preferred: orchestration scripts via PowerShell

These scripts resolve skill root, pick `python` vs `python3`, check the
queue, promote when needed, launch, and start the watchdog. **Run them with
`powershell -File` even when the agent shell is Git Bash.**

```powershell
# /ilk-run — supervised launch (from project cwd or pass -Start)
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cursor\skills\ilk-runner\scripts\ilk-run.ps1"

# /ilk-status — read-only progress
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cursor\skills\ilk-runner\scripts\ilk-status.ps1"

# /ilk-stop — stop loop + watchdog
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cursor\skills\ilk-runner\scripts\ilk-stop.ps1"
```

Skill root resolution order: `ILK_SKILL_HOME`, then auto-detect from the
installed script path, then first existing of
`~/.cursor/skills`, `~/.claude/skills`, `~/.codex/skills`.

## If running step-by-step in Git Bash

- Use **`python`**, not `python3` (unless `python3` is verified on PATH).
- Use **`$HOME/.cursor/skills/...`**, not `$env:USERPROFILE\...`.
- **`launch.ps1` / `watchdog.ps1` / `stop.ps1`** must be invoked via
  `powershell -NoProfile -ExecutionPolicy Bypass -File "..."`.

Example (Git Bash):

```bash
SKILL_ROOT="$HOME/.cursor/skills"
python "$SKILL_ROOT/ilk-loop/scripts/ilk_paths.py" --start .
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_ROOT/ilk-launcher/scripts/launch.ps1" -ProjectPath "/path/to/project"
```

## If running step-by-step in PowerShell

- Use **`python`**, not `python3`.
- Use **`$env:USERPROFILE\.cursor\skills\...`** or dot-source helpers from
  `ilk-loop/scripts/_ilk_skill_root.ps1` and `_resolve_python.ps1`.
