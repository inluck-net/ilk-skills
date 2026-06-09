---
name: ilk-upgrade
description: >-
  Pull the latest ilk-skills into the active toolkit clone and make it
  effective on the current machine. Triggers: "/ilk-upgrade", "upgrade
  ilk", "update ilk-skills", "升级 ilk", "ilk 更新", "拉取最新",
  "ilk-upgrade --check", "ilk-upgrade --apply". Works across Cursor,
  Claude Code, and Codex. Operates on the *toolkit clone* (resolved
  from the installed skill symlink), never on the cwd project.
---

# ilk-upgrade — pull latest ilk-skills and make it effective

A cross-platform command that:

1. **Resolves the active toolkit clone** from the script's own real
   (symlink-resolved) path — never from cwd, never hard-coded.
2. **Fetches and computes ahead/behind** against `origin/main`
   (`--check`, read-only).
3. **Applies the update** via `git pull --ff-only` and re-runs the
   installer when needed (`--apply`).
4. **Reports what changed** — commit log, file diff, and whether the
   installer was re-run.

This command operates on the **toolkit clone** (the `ilk-skills` repo
symlinked/junctioned into `~/.{cursor,claude,codex}/skills`), NOT on
the project in your current working directory.

## When to use

- The user says: `/ilk-upgrade`, `upgrade ilk`, `update ilk-skills`,
  `升级 ilk`, `ilk 更新`, `拉取最新`, `ilk-upgrade --check`,
  `ilk-upgrade --apply`.
- Codex users may say: "upgrade ilk", "update the skills toolkit",
  "pull latest ilk-skills" without a slash prefix.
- The user wants to check if the toolkit is behind origin (`--check`).
- The user wants to pull the latest and make it effective (`--apply`).
- The user wants to see what changed in the last update.

## Two staleness meanings

There are two independent git repos in play:

- **The toolkit clone** — this `ilk-skills` repo, symlinked/junctioned
  into `~/.{cursor,claude,codex}/skills`. "Stale" = behind
  `origin/main`. **This is what ilk-upgrade operates on.**
- **The project being looped** — whatever cwd resolves to; its own git
  + `~/.ilk-data/projects/<key>/` plans.

`/ilk-status` and `loop_status.py` are **per-project** — they report
the cwd project's loop progress and know nothing about the toolkit's
git state. `ilk-upgrade` is the inverse: it knows nothing about your
project's loop; it only cares about whether the toolkit is current.

## Architecture

`<skill-root>` below means the installed skills base directory —
`~/.cursor/skills/` (Cursor), `~/.claude/skills/` (Claude Code), or
`~/.codex/skills/` (Codex) — depending on the host agent.

```
<skill-root>/ilk-upgrade/
  SKILL.md                  ← this file
  scripts/
    upgrade.sh              ← macOS / Linux engine
    upgrade.ps1             ← Windows engine (parity + copy-fallback detect)
```

## Resolution order

The toolkit clone is **always** resolved from the script's own real
(symlink-resolved) path:

1. The script resolves its own path (`$0` in bash, `$MyInvocation` in
   pwsh) through any symlinks.
2. Walks up: `scripts/ → ilk-upgrade/ → skills/ → repo root`.
3. This is the clone path — no cwd dependency, no hard-coding.

## Standard workflows

### W1. Check if the toolkit is up to date (read-only)

```bash
# macOS / Linux
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --check
```

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Check
```

Reports:
- Current branch and commit.
- Fetch result (new data vs already up-to-date).
- Ahead/behind `origin/main` counts.
- **Never** mutates the working tree or runs the installer.

### W2. Apply the update

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
4. Reconciles the auto-plan managed block (`install.sh --only-auto-plan --apply` /
   `install.ps1 -OnlyAutoPlan -Apply`) unconditionally after every successful
   pull. This ensures `conventions/config.yml` preferences propagate to the
   host agent's user-global instructions on every upgrade.
5. Reports: commit log since last pull, files changed, installer
   outcome.

### W3. Force apply (skip live-loop guard)

By default, `--apply` refuses to run when a live loop or watchdog PID
is detected (swapping skill code under a running loop is dangerous).
Override with `--force`:

```bash
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --apply --force
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Apply -Force
```

## When the agent invokes this skill

1. **Default to `--check` first** — show the user what's behind before
   applying.
2. If the user explicitly says "upgrade" or "apply", run `--apply`
   directly.
3. On Windows, use `upgrade.ps1`; on macOS/Linux, use `upgrade.sh`.
4. After `--apply`, report: what changed (commit log), whether the
   installer re-ran, and any copy-fallback staleness warnings (Windows).
5. **Do not** mix this with `/ilk-status` — they operate on different
   repos (toolkit vs project).

## Guards

- **Live-loop detection**: `--apply` checks for running loop/watchdog
  PIDs and refuses unless `--force` is passed. This prevents swapping
  skill code under an active loop.
- **Fast-forward only**: `git pull --ff-only` refuses non-linear
  histories. If the local clone has diverged, the command reports the
  conflict and exits cleanly — it never force-resets.
- **Copy-fallback staleness (Windows)**: on Windows, the installer may
  use a copy-fallback when symlinks aren't available. `upgrade.ps1`
  detects when copied command files are older than the source and warns.

## See also

- `<skill-root>/ilk-loop/SKILL.md` — the loop itself.
- `<skill-root>/ilk-launcher/SKILL.md` — the launcher that spawns
  detached loop windows.
- `commands/ilk-upgrade.md` — the slash command body that dispatches
  to the platform-appropriate engine script.
