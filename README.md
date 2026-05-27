# ilk-skills

A staged execution loop toolkit for [Cursor](https://cursor.com),
[Claude Code](https://claude.com/code), and
[Codex](https://openai.com/codex), unifying Windows and macOS agent
workflows into a single set of skills.

> *ilk* — "of that kind". A kind of plan-loop: you decompose work into
> a sequenced **plan**, the loop drives a fresh AI session per step,
> and a watchdog keeps it going across timeouts, API hiccups, and
> overnight runs without human babysitting.

## Status

**v0.5** — Codex parity release. All skills and slash commands install
under `~/.codex/skills/` and `~/.codex/commands/` alongside Cursor and
Claude Code; hardcoded `~/.cursor` paths are replaced with a skill-root
resolver, command prompts are host-neutral, and the launcher gained a
`worker_engine` config + `--engine` override to select between the
Claude and (forthcoming) Codex runners. New `ilk-runner` skill plus
`/ilk-run` and `/ilk-status` slash commands sequence launcher + watchdog
for supervised unattended runs.

## Components

- **`/ilk-plan`** *(slash command)* — turn a free-text task into a
  master plan + sub-plans with machine-checkable acceptance criteria.
- **`/ilk`** *(slash command)* — pick the active master and run the
  next pending step.
- **`/ilk-run`** *(slash command)* — start the loop with its watchdog
  for unattended runs. Launches ilk in a detached window, then starts
  the watchdog to auto-restart on clean exits.
- **`/ilk-status`** *(slash command)* — read-only progress check.
  Shows queue state and rich dashboard without launching or stopping
  anything.
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
- **`ilk-runner`** *(skill)* — orchestration layer that sequences
  launcher + watchdog for supervised unattended runs. Owns the
  workflow guardrails; delegates to launcher and watchdog.

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
- **Meta-projects (polyrepo umbrellas)** — drop a `.ilk-meta.json` at
  a non-git parent directory that contains several sibling git repos
  (e.g. `myproj/` with `api/`, `portal/`, `ops/`, `docs/`) and ilk
  treats the whole umbrella as **one** project. A single MASTER drives
  cross-repo batches; each sub-plan declares `repo: <member>` and the
  loop cd's into that member for commits, local_checks, CI waits, and
  ship-report generation. See `skills/ilk-loop/docs/meta-projects.md`
  for the convention and a worked example.
- **Cross-machine sync via Git** — `install.ps1` (Windows junctions)
  and `install.sh` (macOS / Linux symlinks) populate
  `~/.cursor/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, and the matching
  `commands/` directories straight from a clone of this repo. Push
  on one machine, pull on the other, re-run the installer, done.

## Codex support boundary

All skills install and function under Codex via `~/.codex/skills/`.
Planning (`/ilk-plan`), single-step execution (`/ilk`), status
(`/ilk-status`), and postmortem (`/ilk-feedback`) work identically
across all three hosts. Detached autonomous loop runs (`/ilk-run` with
watchdog) currently rely on the Claude Code CLI runner
(`run_ilk_loop_claude.sh`); a dedicated Codex runner will close that
gap.

## Platforms

| Platform | Cursor | Claude Code | Codex | Installer |
|---|---|---|---|---|
| Windows 10 / 11    | yes | yes | yes | `install.ps1` (junctions for skills, copy-fallback for commands unless Developer Mode is on) |
| macOS              | yes | yes | yes | `install.sh` (full symlink set including bash entry points) |
| Linux              | yes | yes | yes | `install.sh` |

## Dependencies

- **bash** — macOS ships 3.2 (ancient but sufficient); any modern Linux
  distribution is fine.
- **Python 3** — used by `loop_status.py`, `status_all.py`, and other
  helpers.
- **`gtimeout`** *(macOS only)* — the bash runner uses GNU timeout for
  iteration time-boxing. Install via `brew install coreutils`.
- **`jq`** *(recommended)* — the bash runner prefers `jq` for JSON
  parsing when available; falls back to a Python one-liner if missing.

## Quick start

```bash
git clone https://github.com/inluck-net/ilk-skills.git
cd ilk-skills

# Windows
./install.ps1 -Apply

# macOS / Linux
./install.sh --apply
```

The installer creates symlinks (or junctions on Windows) into
`~/.cursor/skills/`, `~/.claude/skills/`, and `~/.codex/skills/`.
Use `--only-codex` / `-OnlyCodex` to install for a single host only.

The first install seeds `skills/ilk-launcher/projects.json` from
`projects.example.json` (this real file is gitignored, per-operator).
Edit it to point at your real project paths, then:

```powershell
# In any project under git control (Cursor / Claude Code)
/ilk-plan "<describe the task>"     # writes plan to ~/.ilk-data/...
& launch.ps1 -ProjectPath .         # spawns a detached loop window
```

```bash
# macOS / Linux equivalent (Cursor / Claude Code)
/ilk-plan "<describe the task>"
bash "$HOME/.claude/skills/ilk-launcher/scripts/launch.sh" --project-path .
```

Codex users invoke the same skills through natural language — the
installer places identical files under `~/.codex/skills/`.

## Layout

```
skills/         per-skill SKILL.md + scripts
commands/       slash command bodies for Cursor, Claude Code, and Codex (ilk*.md)
docs/           repo-level documentation
docs/standards/ external standards this repo follows + compliance table
install.ps1     Windows installer
install.sh      macOS / Linux installer
tools/          manual, dry-run-by-default utilities (e.g. plan migration)
```

See [`docs/standards/`](docs/standards/agentskills-io.md) for the
agentskills.io references this repo tracks and the current per-skill
compliance status.

## License

[Apache License 2.0](LICENSE).
