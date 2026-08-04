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

**v0.8.13** — empty-repo resilience, `/ilk-schedule`, and macOS-friendly
monitoring surfaces:

- **Empty-repo / stale-state resilience.** A run pointed at a freshly
  `git init`'d repo (branch but zero commits) no longer cascades into a hard
  stop: the runner degrades a missing `HEAD` to `(unknown)` instead of leaking a
  fatal, `collect.py` classifies a run that died before iter 1 as `interrupted`
  (instead of failing to produce a postmortem), the watchdog ignores a
  stale/contradicted sentinel and cross-checks `loop_status` before declaring
  "queue drained", `promote_next_master` treats `pending` as a `queued` alias,
  and the launcher's active-guard ignores a finished-but-lingering loop window.
- **`/ilk-schedule`** *(new slash command)* — launches the single cross-project
  scheduler detached, the way `/ilk-run` launches the per-project loop+watchdog.
  Use it to drain **all** projects' queues from one supervisor instead of one
  watchdog per project. `scheduler.{ps1,sh}` gained `-Detach` / `--detach`.
- **Monitoring surfaces (esp. for macOS, where detached runs are headless
  `screen` sessions with no visible window):**
  - `loop_status.py --json` + `status_all.py` — machine-readable per-project and
    all-projects status.
  - `/ilk-status --watch` (bash `--watch` / PowerShell `-Watch`) — a
    self-refreshing all-projects + slots cockpit. Replaces re-running
    `/ilk-status`.
  - **Desktop notifications** on watchdog/scheduler events (ship, blocked,
    restart, postmortem-failed, queue-drained). macOS `osascript`, Windows
    toast, Linux `notify-send`. Gated by `ILK_NOTIFY` (`0` disables).
  - **tmux slot sessions** — set `ILK_MULTIPLEXER=tmux` so the scheduler runs
    each slot in a named `ilk` session (`tmux attach -t ilk` to see one pane per
    slot); `screen` stays the fallback (`auto` = tmux if present).
  - **xbar / SwiftBar menu-bar plugin** (`tools/xbar/`) — glanceable macOS
    menu-bar status; see [`tools/xbar/README.md`](tools/xbar/README.md).

> **Upgrading on macOS:** `git pull` this repo, then re-run `bash install.sh`
> to symlink the new `/ilk-schedule` command + scripts into `~/.claude/...`
> (and your other agent homes). The monitoring surfaces, notifications, and
> tmux mode are **unverified on macOS** until you exercise them there — see each
> command doc's "Manual user verification" notes.

**v0.8.10** — planner/worker cost split + cross-project scheduling:

- **`claude-worker` launcher engine.** Route the detached loop under the
  cheap worker home (`~/.claude-worker`) instead of the planner's
  official provider, for unattended / cost-sensitive runs
  (`--engine claude-worker`, or `{ "worker_engine": "claude-worker" }`
  in `.ilk-launch.json`). See
  [`skills/ilk-launcher/references/worker-engine.md`](skills/ilk-launcher/references/worker-engine.md).
- **Machine-wide default opt-in** (v0.8.10). Set
  `ILK_DEFAULT_ENGINE=claude-worker` to default a whole machine to the
  worker without editing every project. Precedence: CLI `--engine` >
  `.ilk-launch.json` > `ILK_DEFAULT_ENGINE` > `claude`. The shipped
  default stays `claude`; a real `claude-worker` launch fails closed if
  the worker home isn't bootstrapped, and a planner launch nudges when a
  worker home is available.
- **Cross-project scheduler (V1).** A single daemon
  (`skills/ilk-watchdog/scripts/scheduler.{ps1,sh}`) drains every
  project's queue FIFO, one at a time, routed through the worker engine —
  per-project sentinel mutex, poll/idle/auto-wake, pool cap 1, global
  budget ceiling, non-starving blacklist skip. It dispatches each
  project's real source repo path (v0.8.9).
- Earlier milestones: a one-command `claude-worker` PATH installer
  (v0.8.7) and the dual planner/worker Claude homes design.

**v0.5** — Codex parity release. All skills and slash commands install
under `~/.codex/skills/` and `~/.codex/commands/` alongside Cursor and
Claude Code; hardcoded `~/.cursor` paths are replaced with a skill-root
resolver, command prompts are host-neutral, and the launcher gained the
`worker_engine` config + `--engine` override. New `ilk-runner` skill plus
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
  anything. `--watch` (PowerShell `-Watch`) gives a self-refreshing
  all-projects + slots cockpit.
- **`/ilk-schedule`** *(slash command)* — launch the single cross-project
  scheduler detached (drains every project's queue into slot homes, one
  supervisor for all). The cross-project sibling of `/ilk-run`; both coexist.
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
  ship-report generation. See `skills/ilk-loop/references/meta-projects.md`
  for the convention and a worked example.
- **Ambient observability (cross-platform, macOS-first)** — `status_all.py`
  exposes all-projects state as JSON, feeding a `/ilk-status --watch` cockpit, an
  xbar/SwiftBar menu-bar plugin, and event-driven desktop notifications
  (`ILK_NOTIFY`). On macOS, where detached runs are headless `screen` sessions,
  `ILK_MULTIPLEXER=tmux` puts each scheduler slot in an attachable `ilk` tmux
  session. None of these change loop behaviour — they're additive and default-off.
- **Cross-machine sync via Git** — `install.ps1` (Windows junctions)
  and `install.sh` (macOS / Linux symlinks) populate
  `~/.cursor/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, and the matching
  `commands/` directories straight from a clone of this repo. Push
  on one machine, pull on the other, re-run the installer, done.

## Building tools on top of ilk

If you're building an *external* tool that observes or controls the loop —
a dashboard, a bot, or a mobile remote-controller — start with
[`docs/integration-surface.md`](docs/integration-surface.md). It documents the
consumer-facing surface: the status CLIs and their `--json` schemas, the
`~/.ilk-data/` file layout, the plan-file contract, the verification gates, and
— critically — the fact that the loop is **plan-file-driven with no
mid-iteration steering**. ilk exposes no HTTP API or auth layer by design, so a
controller is a thin shim over these CLIs/files plus whatever transport and auth
you add.

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

> ### ⚠️ Read before installing: this changes your agents *globally*
>
> `conventions/config.yml` ships with `auto_use_ilk_plan: true`, and the
> normal `--apply` path always reconciles the auto-plan managed block. So
> installing does **not** only add slash commands — it edits your
> **user-global agent instructions**, which apply to *every* project on the
> machine, not just this repo:
>
> | File | Effect |
> |---|---|
> | `~/.claude/CLAUDE.md` | Claude Code routes implementation work to `/ilk-plan` by default |
> | `~/.codex/AGENTS.md` | same routing for Codex |
> | `~/.cursor/rules/ilk-auto-plan.mdc` | same routing for Cursor |
>
> Note that **`--dry-run` does not show this step** — it exits before the
> auto-plan reconcile — so the dry-run output understates what `--apply`
> touches.
>
> **To install the skills without changing global agent behavior**, set
> `auto_use_ilk_plan: false` in `conventions/config.yml` before applying.
> The block is delimited by `<!-- ilk:auto-plan:start -->` /
> `<!-- ilk:auto-plan:end -->` markers, so it is removable: set the
> preference to `false` and re-run the installer to reconcile it away.
>
> The routing heuristic itself is documented in
> [`conventions/auto-plan-routing.md`](conventions/auto-plan-routing.md).

```bash
git clone https://github.com/inluck-net/ilk-skills.git
cd ilk-skills

# Recommended first: see what would be linked.
# Dry-run is the default for both installers — omit -Apply / --apply.
./install.sh --dry-run          # Windows: ./install.ps1

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

## Upgrading ilk-skills

The easiest way to update is the `/ilk-upgrade` command:

```bash
# macOS / Linux — check what's behind, then apply
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --check
bash "<skill-root>/ilk-upgrade/scripts/upgrade.sh" --apply
```

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Check
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "<skill-root>\ilk-upgrade\scripts\upgrade.ps1" -Apply
```

`<skill-root>` is `~/.claude/skills/`, `~/.cursor/skills/`, or
`~/.codex/skills/` depending on your host agent.

`--check` is read-only — it fetches and reports ahead/behind counts.
`--apply` pulls with `--ff-only` and re-runs the installer
automatically when needed. If a live loop is running, `--apply`
refuses unless you pass `--force`.

**Manual equivalent:**

```bash
cd /path/to/ilk-skills   # your local clone
git pull --ff-only
# macOS / Linux
./install.sh --apply
# Windows
./install.ps1 -Apply
```

## Auto-use-ilk-plan routing

The `--auto-use-ilk-plan` / `-AutoUseIlkPlan` flag installs a routing rule
into each host agent's user-global instructions, so every new session
defaults to routing implementation work to `/ilk-plan` instead of
direct-implementing.

**What it manages** (three files, all user-global):

| Host | File | Type |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` | delimited managed block |
| Codex | `~/.codex/AGENTS.md` | delimited managed block |
| Cursor | `~/.cursor/rules/ilk-auto-plan.mdc` | dedicated owned file |

**Enable it:**

```bash
# macOS / Linux
./install.sh --auto-use-ilk-plan --apply

# Windows
./install.ps1 -AutoUseIlkPlan -Apply
```

**Disable it** (removes the block/file from all hosts):

```bash
# macOS / Linux — edit conventions/config.yml, set auto_use_ilk_plan: false, then:
./install.sh --only-auto-plan --apply

# Windows
./install.ps1 -OnlyAutoPlan -Apply
```

**Cross-machine propagation:** commit `conventions/config.yml` with
`auto_use_ilk_plan: true`, push, then on each machine run
`/ilk-upgrade --apply`. The upgrade reconciles the block automatically
after every successful pull — zero per-machine steps needed.

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
