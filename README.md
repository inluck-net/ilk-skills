# ilk-skills

A staged execution loop toolkit for [Cursor](https://cursor.com) and
[Claude Code](https://claude.com/code), unifying Windows and macOS
agent workflows into a single set of skills.

> *ilk* — "of that kind". A kind of plan-loop: you decompose work into
> a sequenced **plan**, the loop drives a fresh AI session per step,
> and a watchdog keeps it going across timeouts, API hiccups, and
> overnight runs without human babysitting.

## Status

**v0.1** — first public-clean release. Repository is private during
incubation; the API surface is stable enough to drive real overnight
work and is in production use by the maintainer.

## Components

- **`/ilk-plan`** *(slash command)* — turn a free-text task into a
  master plan + sub-plans with machine-checkable acceptance criteria.
- **`/ilk`** *(slash command)* — pick the active master and run the
  next pending step.
- **`ilk-loop`** *(skill)* — the iterative engine: detached
  PowerShell driver, per-step `local_checks`, `[plan:<slug>#step-N]`
  commit tagging, `last-exit.json` sentinel for IPC.
- **`ilk-launcher`** *(skill)* — per-project launch / status / stop,
  plus a single-project dashboard and a cross-project overview.
- **`ilk-feedback`** *(skill)* — postmortem classifier (9 classes
  including `local-checks-stuck`); reads recent iterations and emits
  a recommendation block.
- **`ilk-watchdog`** *(skill)* — auto-resume layer. Reads
  `last-exit.json` fast-path, falls back to PID checks, advances the
  MASTER queue via `promote_next_master.py` on clean ship.

## Features

- **Zero project pollution** — plans, runtime state, and per-project
  logs live under `~/.ilk-data/projects/<project-key>/`, derived from
  the project's `.git` root. Your project repository never grows a
  single skill artifact.
- **Hybrid success/failure signals** — machine-executable
  `local_checks` per sub-plan step (staged-plan style) **plus**
  postmortem classification and watchdog auto-resume at the loop level.
- **Emergent FIFO master queue** — `MASTER-*.md` files sort by
  frontmatter `created` ISO timestamp, with optional `priority`
  override. The watchdog auto-promotes the next queued master when
  the active one ships.
- **Concurrent multi-worktree execution** — every `git worktree`
  resolves to its own `project_key` and therefore its own
  `~/.ilk-data/projects/<key>/`. You can run a feature loop and a
  hotfix loop in parallel from the same repo without either side
  seeing the other's plans, runtime, or PID files. This is also the
  primitive needed for future multi-agent and best-of-N workflows
  (see `skills/ilk-loop/SKILL.md` → *Concurrent multi-worktree
  execution* for the full mechanism).
- **Cross-machine sync via Git** — `install.ps1` (Windows junctions)
  and `install.sh` (macOS / Linux symlinks) populate
  `~/.cursor/skills/`, `~/.claude/skills/`, and the matching
  `commands/` directories straight from a clone of this repo. Push
  on one machine, pull on the other, re-run the installer, done.

## Platforms

| Platform | Cursor | Claude Code | Installer |
|---|---|---|---|
| Windows 10 / 11    | yes | yes | `install.ps1` (junctions for skills, copy-fallback for commands unless Developer Mode is on) |
| macOS              | yes | yes | `install.sh` (symlinks throughout) |
| Linux              | yes | yes | `install.sh` |

## Quick start

```bash
git clone https://github.com/inluck-net/ilk-skills.git
cd ilk-skills

# Windows
./install.ps1 -Apply

# macOS / Linux
./install.sh --apply
```

The first install seeds `skills/ilk-launcher/projects.json` from
`projects.example.json` (this real file is gitignored, per-operator).
Edit it to point at your real project paths, then:

```powershell
# In any project under git control
/ilk-plan "<describe the task>"     # writes plan to ~/.ilk-data/...
& launch.ps1 -ProjectPath .         # spawns a detached loop window
```

```bash
# macOS / Linux equivalent
/ilk-plan "<describe the task>"
launch.sh --project-path .
```

## Layout

```
skills/         per-skill SKILL.md + scripts
commands/       Cursor / Claude slash command bodies (ilk*.md)
install.ps1     Windows installer
install.sh      macOS / Linux installer
tools/          manual, dry-run-by-default utilities (e.g. plan migration)
```

## License

[Apache License 2.0](LICENSE).
