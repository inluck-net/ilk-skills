---
name: ilk-launcher
description: >-
  Launch / stop / status the ilk-loop runner in a detached window per
  project. Triggers: "start ilk", "launch ilk", "跑 ilk", "启动 ilk",
  "ilk 状态", "/ilk-launch", "/ilk-status-all", "/ilk-stop". Works
  across Cursor, Claude Code, and Codex. Companion to ilk-loop —
  spawns/observes `run_ilk_loop_claude.ps1`, never drives the loop itself.
metadata:
  preferred_model: haiku
---

# ilk-launcher — independent-window launcher for ilk-loop

A thin Windows wrapper around `run_ilk_loop_claude.ps1` that:

1. **Spawns the loop in a detached desktop PowerShell window** (via
   `Start-Process powershell -NoExit`) so the run survives Cursor closing,
   session expiry, or agent context resets.
2. **Resolves per-project parameters** (`MaxIterations`, `IterationTimeoutMin`)
   from a project-local config, falling back to global defaults.
3. **Tracks running PIDs** so a status command can answer "which projects
   currently have a ilk window alive?"

This skill **does not** run the loop inside Cursor's shell session. Doing so
would tie a 15–40h autonomous run to the agent's session lifecycle (see
the design rationale in `<vault>/ai-coding-workflow/tool-evaluations/ilk-launcher.md`).

## When to use

- The user says: `/ilk-launch`, `/ilk-status-all`, `/ilk-stop`,
  `start ilk`, `launch ilk`, `跑 ilk`, `启动 ilk`, `ilk 状态`,
  `停 ilk`, `关掉 ilk`.
- Codex users may say: "start the ilk loop", "launch ilk",
  "check ilk status", "stop ilk" without a slash prefix.
- The user wants to start ilk-loop on the current project (cwd inside
  a project with `docs/plans/MASTER-*.md`).
- The user wants to start ilk on a specific named project from the
  global registry.
- The user wants a cross-project summary of which ilk windows are
  running vs idle.

## Architecture

`<skill-root>` below means the installed skills base directory —
`~/.cursor/skills/` (Cursor), `~/.claude/skills/` (Claude Code), or
`~/.codex/skills/` (Codex) — depending on the host agent.

```
<skill-root>/ilk-launcher/
  SKILL.md                  ← this file
  projects.json             ← global registry: name + path (params live with project)
  scripts/
    launch.ps1              ← Start-Process wrapper; writes PID file
    status_all.py           ← iterates projects.json + loop_status.py + PID check
    stop.ps1                ← reads PID file, tree-kills the window

<project>/docs/plans/
  .ilk-launch.json        ← per-project params (optional; falls back to defaults)

~/.ilk-data/projects/<key>/runtime/launcher/
  running.pid               ← PID of the spawned PowerShell window (deleted on clean exit)
  last-launch.json          ← metadata of most recent launch (for status display)
  mcp-worker.json           ← filtered MCP config passed to the runner
```

## Worker engine boundary

The launcher and all ilk skills are **host-agnostic**: they install and
run identically under Cursor, Claude Code, and Codex. Any host can
invoke planning (`/ilk-plan`), single-step execution (`/ilk`), status
(`/ilk-status`), and postmortem (`/ilk-feedback`).

The **detached loop runner** is a different story. Today the only
runner is `run_ilk_loop_claude.sh` (and its PowerShell twin), which
spawns `claude -p` per iteration. This means:

| Capability | Cursor | Claude Code | Codex |
|---|---|---|---|
| Install skills | yes | yes | yes |
| Plan / step / status / postmortem | yes | yes | yes |
| Detached autonomous loop (`/ilk-run`) | yes (via Claude Code CLI) | yes | **not yet** |

Codex users can drive the loop interactively (one step at a time via
`/ilk`) but cannot yet launch a detached autonomous run. A dedicated
`run_ilk_loop_codex.sh` runner will close that gap once the Codex CLI
invocation contract is stable and tested. Until then, the launcher
will reject `--engine codex` with a clear message rather than silently
routing to Claude.

### Adding a Codex runner

When the Codex CLI contract is stable, add these files:

```
skills/ilk-loop/scripts/run_ilk_loop_codex.sh    # bash runner
skills/ilk-loop/scripts/run_ilk_loop_codex.ps1   # PowerShell runner
```

The runner must accept the same interface as the Claude runner:

| Flag | Purpose |
|---|---|
| `--project-path PATH` | Absolute path to the project root |
| `--max-iterations N` | Hard cap on loop iterations |
| `--iteration-timeout-min N` | Per-iteration wall-clock timeout |
| `--mcp-config-path PATH` | Filtered MCP config to pass to the worker |

The runner should invoke the Codex CLI (`codex` or equivalent) in a
loop, one invocation per sub-plan step, writing structured JSONL logs
compatible with the `ilk-feedback` postmortem skill. It must exit with
code 0 on clean ship, non-zero on failure, and write a
`last-exit.json` sentinel for the watchdog.

The launcher selects the runner based on the resolved `worker_engine`:

```
engine=claude → run_ilk_loop_claude.sh
engine=codex  → run_ilk_loop_codex.sh
```

## State directory

All per-project runtime state (PID files, launch metadata, MCP worker
configs, and postmortems) lives outside the project tree under:

```
~/.ilk-data/projects/<key>/runtime/launcher/
```

To discover the exact paths for the project in the current directory:

```bash
python3 <skill-root>/ilk-loop/scripts/ilk_paths.py --start . --where
```

This keeps the project repo clean and avoids accidental commits of
launcher artifacts.

## Repo-tree invariant

Skill artifacts live **only** under `~/.ilk-data/projects/<key>/...`.
Never write anything into the project tree, and **never modify any
repo-tracked file** (including `.gitignore`, `.gitattributes`,
`README.md`, or any other versioned file) to accommodate skill
artifacts.

If you find legacy in-project `.ilk-launcher/` or `.ilk-watchdog/`
directories from an older skill version, the **only** valid actions are:

```bash
# Option A — direct removal:
rm -rf <project>/.ilk-launcher <project>/.ilk-watchdog

# Option B — run the migrator (moves any salvageable state to ~/.ilk-data/):
python3 <skill-root>/../tools/migration/migrate_project_runtime_dirs.py \
    --project . --apply
```

Adding `.ilk-launcher/` or `.ilk-watchdog/` to the project's
`.gitignore` is **wrong** — it bakes the existence of skill state into
the project repo. The correct invariant is "skill state does not exist
in the project at all." If `.gitignore` already mentions these paths
from a previous mistake, that's a separate cleanup; do not add new
entries.

## Config files

### Per-project: `<project>/docs/plans/.ilk-launch.json`

```json
{
  "max_iterations": 40,
  "iteration_timeout_min": 60,
  "worker_enable_mcp": ["lark-tickets"]
}
```

Lives in `docs/plans/` (next to MASTER plan) so it travels with the
project's plan convention. Optional — without it, global defaults apply.

#### Worker MCP filtering

Loop workers usually need a very small subset of the MCPs you have
registered in Claude Code. The launcher lets you restrict the worker's
MCP set per project, which cuts iteration cost (chrome-devtools
snapshots in particular stay resident in the agent's context for the
rest of each session — at ~10% of total tokens per `/usage` self-reports
when not actively muted).

Pick **one** of these modes — never both, the launcher will refuse:

**Whitelist** (`worker_enable_mcp`, **recommended default**):

```json
{ "worker_enable_mcp": ["lark-tickets"] }
```

Only the named MCPs are exposed to the worker. Best when you want
deterministic cost discipline: most loop work needs files + git +
shell, occasionally `lark-tickets` for state transitions on ship.
chrome-devtools and figma stay off unless a specific batch needs them.

**Blacklist** (`worker_disable_mcp`):

```json
{ "worker_disable_mcp": ["chrome-devtools", "figma"] }
```

Everything from `~/.claude.json` is exposed EXCEPT the listed ones.
Looser, useful when you want most of your registry available but a
known-expensive server muted.

**Per-launch override** — either mode can be flipped on the launcher
CLI for a single run:

```powershell
& launch.ps1 -ProjectPath … -EnableMcp "lark-tickets,chrome-devtools"   # whitelist
& launch.ps1 -ProjectPath … -DisableMcp "chrome-devtools"               # blacklist
```

Mechanism: the launcher reads `~/.claude.json`'s `mcpServers`, filters
according to the chosen mode, writes the resulting JSON (UTF-8 no BOM)
to `~/.ilk-data/projects/<key>/runtime/launcher/mcp-worker.json`, and passes it through
`run_ilk_loop_claude.ps1 -McpConfigPath` so every `claude -p` call gets
`--mcp-config <path> --strict-mcp-config`. `--strict-mcp-config` also
drops claude.ai-synced servers (Gmail / Drive) for the worker — those
are almost never useful in loop work anyway.

### Global registry: `<skill-root>/ilk-launcher/projects.json`

> **First-run bootstrap**: this file is gitignored (per-operator data).
> After running `install.ps1` / `install.sh`, copy
> `projects.example.json` next to it as `projects.json` and edit the
> entries to point at your real project paths. The bootstrap snippets
> below use the Cursor install path (`$HOME/.cursor/skills`); substitute
> `.claude` or `.codex` for the other hosts.
>
> ```powershell
> # Win (Cursor; use .claude or .codex for the other hosts)
> $dir = "$HOME\.cursor\skills\ilk-launcher"
> if (-not (Test-Path "$dir\projects.json")) {
>   Copy-Item "$dir\projects.example.json" "$dir\projects.json"
> }
> ```
> ```bash
> # macOS / Linux (Cursor; use .claude or .codex for the other hosts)
> dir="$HOME/.cursor/skills/ilk-launcher"
> [[ -f "$dir/projects.json" ]] || cp "$dir/projects.example.json" "$dir/projects.json"
> ```

```json
{
  "projects": [
    { "name": "example-a", "path": "C:\\path\\to\\your\\project-a" },
    { "name": "example-b", "path": "C:\\path\\to\\your\\project-b" }
  ]
}
```

Only `name` + `path`. Per-project params live with the project (above).
This file is only needed for `status_all` and for `launch.ps1 -ProjectName <name>`
without `-ProjectPath`.

## Resolution order

### Project resolution (which project to launch)
1. `-ProjectPath <abs>` flag wins.
2. `-ProjectName <name>` → look up in `projects.json`.
3. Walk up from current working directory looking for `docs/plans/MASTER-*.md`.
4. None found → error with `projects.json` content as hint.

### Parameter resolution (`MaxIterations`, `IterationTimeoutMin`)
1. CLI override flag.
2. `<project>/docs/plans/.ilk-launch.json`.
3. Built-in default: `MaxIterations=30`, `IterationTimeoutMin=30`.

## Standard workflows

### W1. Launch ilk for current / specified project

> **Orientation budget when the user asks to launch:** exactly one
> `loop_status.py` call, plus a master-plan status flip from `queued` to
> `active` if needed. That's the whole budget. **Do not**:
> - inspect git history (`git log`, `git branch`, `git show <branch>:...`)
> - read proposal docs, MASTER plans, or sub-plans for context
> - run `--dry-run` before the real launch
> - re-run `loop_status.py` to "confirm" the first result
> - `ps`-check unrelated PIDs found in stale state files
>
> `loop_status.py` exiting **1** is the **normal** "next plan is pending"
> signal — it is not an error and does not warrant retry or investigation.
> If the master is `queued`, flip it to `active` in one edit and continue.
> If a stale PID file references a dead process, the launcher cleans it
> up itself — do not pre-clean. A correct launch path is 3–4 tool calls,
> not 20+. Anything beyond this budget is over-investigation; stop and
> launch.

```powershell
# In Cursor terminal, inside a project (cwd walk-up resolves it):
& "$HOME\.cursor\skills\ilk-launcher\scripts\launch.ps1"

# By registered name:
& "$HOME\.cursor\skills\ilk-launcher\scripts\launch.ps1" -ProjectName es_api

# Explicit path + ad-hoc param override:
& "$HOME\.cursor\skills\ilk-launcher\scripts\launch.ps1" `
    -ProjectPath C:\path\to\your\project `
    -MaxIterations 60 -IterationTimeoutMin 30
```

```bash
# macOS / Linux equivalent:
bash "$HOME/.cursor/skills/ilk-launcher/scripts/launch.sh"

# By registered name:
bash "$HOME/.cursor/skills/ilk-launcher/scripts/launch.sh" --project-name es_api

# Explicit path + ad-hoc param override:
bash "$HOME/.cursor/skills/ilk-launcher/scripts/launch.sh" \
    --project-path /path/to/your/project \
    --max-iterations 60 --iteration-timeout-min 30
```

Effect: a new desktop PowerShell window appears with title
`ilk: <project-name>` and starts the loop. The window stays open
after the loop exits (`-NoExit`) so the final 50 lines remain readable.

### W2. Status across all registered projects (terse one-liner each)

```powershell
python "$HOME\.cursor\skills\ilk-launcher\scripts\status_all.py"
```

Shows a table:

```
project       state    plan-status                              window-pid
es_api        running  next: 2026-05-22-cleanup step 3 of 7     54321
myproj       idle     all sub-plans shipped                    -
crawler       idle     next: 2026-05-22-zara-source step 0 of 9 -
```

**Two states only** (this version): `running` (PID file exists AND PID
alive) vs `idle` (no PID file, OR PID file references dead process →
stale PID file is auto-cleaned).

> 🚧 **Future**: a third `needs-review` state will be added once
> `gap-analysis.md` P0-3 lands (ilk stops at staging push, expects
> human to review ship-report before promoting). The status table is
> designed so adding a third column case is non-breaking.

### W2b. Single-project rich progress dashboard

When the user wants a deep view of ONE project — what's shipped, what's
left, pace, ETA — use `status_progress.py` instead of `status_all.py`.

```powershell
python "$HOME\.cursor\skills\ilk-launcher\scripts\status_progress.py" -ProjectName myproj
# or by path / cwd
python "$HOME\.cursor\skills\ilk-launcher\scripts\status_progress.py" -ProjectPath C:\path\to\your\project
python "$HOME\.cursor\skills\ilk-launcher\scripts\status_progress.py"  # cwd walk-up
```

Sample output:

```
项目: myproj
当前: audit-wallet-rmb-account step 0/10
批次日期: 2026-05-23

进度 (4/7 shipped, 3 pending)
[▓▓▓▓▓▓▓▓▓▓] foundation-cleanup-and-radius-audit  4/4    shipped
[▓▓▓▓▓▓▓▓▓▓] rework-payment-pages                 9/9    shipped
[▓▓▓▓▓▓▓▓▓▓] audit-home-and-cart                  9/9    shipped
[▓▓▓▓▓▓▓▓▓▓] audit-orders-and-imports             10/10  shipped
[░░░░░░░░░░] audit-wallet-rmb-account             0/10   pending  ← here
[░░░░░░░░░░] audit-user-center-and-bank-card-page 0/6    pending
[░░░░░░░░░░] new-mall-orders-member-points        0/10   pending

剩余 (机械累加，不含已 ship): 26 步
节奏 (最近 ≤5 个 step commit 平均): 12.3 min/step  [基于 560 个 step commit / 5 个 repo]
ETA (按当前节奏): ~5h 21min  (今天 21:23)
```

**This is the固化 / hardcoded skeleton for "what's the state?" questions.**
The agent should:

1. Run `status_progress.py` for the project the user is asking about.
2. Print its output verbatim or in a markdown box.
3. **Then add agent-only judgment on top** (do NOT bake these into the
   script — they're context-dependent):
   - "距上次查询 X min, 完成 Y 步" — agent knows from chat timestamps
   - "Loop 健康 / 异常" — agent's read of pace stability + watchdog
     activity log + recent BLOCKED events
   - Project-specific commit distribution (e.g. "portal:5 docs:9") —
     agent runs `git log -20 --oneline` per repo if interesting
   - Recommended action — agent's call

Two-way separation of concerns:

| Mechanical (in script) | Judgment (agent adds) |
|---|---|
| Bar chart per sub-plan | "Loop 健康" verdict |
| min/step from git log | Stability assessment |
| ETA = pace × remaining | "should you intervene?" |
| Status counts | "anything unusual?" |

When the user says any of these, use `status_progress.py` (NOT `status_all.py`):
- "/ilk-launcher 现在状态如何"
- "myproj 进度"
- "ilk 跑到哪了"
- "show progress on <project>"
- "where are we on <project>"

### W3. Stop a running ilk for a project

```powershell
& "$HOME\.cursor\skills\ilk-launcher\scripts\stop.ps1" -ProjectName es_api
# or
& "$HOME\.cursor\skills\ilk-launcher\scripts\stop.ps1" -ProjectPath C:\path\to\your\project
```

```bash
# macOS / Linux equivalent:
bash "$HOME/.cursor/skills/ilk-launcher/scripts/stop.sh" --project-name es_api
# or
bash "$HOME/.cursor/skills/ilk-launcher/scripts/stop.sh" --project-path /path/to/your/project
```

Reads the PID file from the external launcher dir (resolved via
ilk_paths.py), runs `taskkill /T /F /PID <n>` (tree-kill so `claude`
and its children die with the wrapper), deletes the PID file.

### W4. Launch all registered projects

```powershell
& "$HOME\.cursor\skills\ilk-launcher\scripts\launch.ps1" -All
```

```bash
# macOS / Linux equivalent:
bash "$HOME/.cursor/skills/ilk-launcher/scripts/launch.sh" --all
```

Iterates `projects.json` and launches each. Skips any project that
already has a running PID. Use sparingly — three concurrent `claude`
processes share API rate limits and CPU.

## When the agent invokes this skill

1. Identify which workflow the user wants (W1–W4).
2. For W1: if cwd is in home, ask which project (don't blindly walk up
   in home — there's nothing to find). Otherwise just call `launch.ps1`
   with no args and let it walk up.
3. For W2: just run `status_all.py` and pretty-print its output.
4. For W3: confirm which project to stop (especially if multiple are
   running) before invoking `stop.ps1`.
5. **Do not** try to monitor the launched window's progress via
   `AwaitShell` or terminal polling — the whole point is the window is
   independent. After `Start-Process` returns, your job is done.
6. After launching, report: window title, PID, resolved
   `MaxIterations` / `IterationTimeoutMin`, **the rationale** for those
   values (see decision guide below), and where the JSONL log will be
   written (`$HOME\.cursor\skills\ilk-loop\logs\<...>.jsonl`).

## Agent decision guide: choosing `MaxIterations` and `IterationTimeoutMin`

> When launched via the agent, the agent should pick these values **based
> on the next pending sub-plan's content** AND **the recent run history
> for this project**, not on a static per-project config. The
> `.ilk-launch.json` mechanism still works as a fallback for humans
> running `launch.ps1` directly without agent help.

### Step 1.5 — Read recent postmortems (history-aware)

Before reasoning about the next sub-plan, the agent should glance at the
last few postmortems for this project (produced by the `ilk-feedback`
skill):

```
~/.ilk-data/projects/<key>/runtime/launcher/postmortems/*.md
```

Each postmortem has a YAML front-matter block with:

```yaml
classification: timeout-bound | max-iter-bound | api-flaky | api-blocked
                | stuck-no-progress | budget-exhausted | interrupted | clean-success
recommended_max_iterations: <int>
recommended_iteration_timeout_min: <int>
```

Read **only the front-matter** of the **3 newest** files (they're tiny;
this is cheap). Apply these soft rules:

| Pattern in last 3 postmortems | Adjustment to your sub-plan-based estimate |
|---|---|
| ≥2 of 3 are `timeout-bound` | Use `max(your_estimate, max(recommended_iteration_timeout_min))` |
| ≥2 of 3 are `max-iter-bound` | Use `max(your_estimate, max(recommended_max_iterations))` |
| ≥2 of 3 are `api-flaky` or `api-blocked` | Tell the user before launching: "endpoint has been unstable; want to switch model or proceed?" |
| ≥2 of 3 are `stuck-no-progress` | Tell the user the sub-plan colortile may need restructuring; ask before launching |
| Mostly `clean-success` | No adjustment; trust your sub-plan-based estimate |
| No postmortems exist | First run for this project — proceed with sub-plan-based estimate only |

This is **history as a prior**, not a hard override. The sub-plan reading
in Steps 1–4 still drives the baseline; postmortems shift defaults when
real evidence suggests we underestimated.

### Step 1 — Read what's about to run

Before calling `launch.ps1`, the agent should:

1. Run `loop_status.py` to find the next pending sub-plan (or read
   `status_all.py` output if already at hand).
2. Read that sub-plan's front-matter: `estimated_steps`, `current_step`,
   `priority`, `tickets`.
3. Skim its **Steps** section + **Manual user verification** section
   (if any). Notice signals like:
   - mentions of `pytest` / `vitest` / `npm run build` / `tsc` (compile-bound)
   - mentions of `playwright` / `e2e` / `chrome-devtools` smoke test
     (browser-bound; slow)
   - mentions of `wait_ci` / "wait for CI green" / "push and wait"
     (CI-bound; can hit 15–60 min per step)
   - data-heavy work: scraping, indexing, large file processing
   - pure refactors / small fixes / docs (fast)

### Step 2 — Pick `MaxIterations`

Baseline: `remaining_steps = estimated_steps - current_step`.

| Situation | Suggested `MaxIterations` |
|---|---|
| Single sub-plan, ≤8 remaining steps, no risky steps | `max(remaining_steps × 2, 10)` |
| Multi sub-plan queue (many pending in master) | `max(total_remaining_steps × 1.5, 20)` |
| Steps include CI-wait or e2e — agent may need retries on red | bump above by +10 |
| Project unfamiliar / first run after big plan change | round up generously, cap at 60 |

Hard floor: **10**. Hard ceiling: **60** unless user explicitly asked for more.

### Step 3 — Pick `IterationTimeoutMin`

| Step character | Suggested `IterationTimeoutMin` |
|---|---|
| Pure code edits + unit tests only | **20** |
| Includes `npm run build` / `tsc` / `pytest` over a real suite | **30** |
| Includes browser smoke (chrome-devtools / playwright e2e) | **45** |
| Includes "push and wait for CI" with project CI ≥ 15 min | **60** |
| Includes "push and wait for CI" with project CI ≥ 30 min | **90** |

Hard floor: **15**. Hard ceiling: **120**.

### Step 4 — Apply project-specific common sense

The agent should also weight known project facts (from `current-stack.md`
and chad's prior signals — agent can ask if unsure):

- `myproj`: has playwright e2e + Gitee CI 15–30min → default toward
  60 timeout.
- `es_api`: backend API, mostly pytest → 30 timeout is plenty.
- `crawler`: data scraping iterations can each be slow → 60 timeout,
  fewer iterations needed.
- New / unknown project: **ask the user**, don't guess wildly.

### Step 5 — Always tell the user what you picked and why

When reporting back after launch, include a one-liner like:

> "Picked `-MaxIterations 30 -IterationTimeoutMin 60` because next
> sub-plan has 12 remaining steps and 3 of them include playwright e2e
> + push-and-wait-CI."

This lets chad correct you cheaply (`stop.ps1 -ProjectName foo` then
relaunch with adjusted flags).

### When NOT to apply heuristics

- User explicitly passes `-MaxIterations N` / `-IterationTimeoutMin M` →
  obey verbatim, don't second-guess.
- `.ilk-launch.json` exists in `<project>/docs/plans/` AND user is
  invoking the launcher directly (not via agent prompt) → the launcher
  already handles fallback resolution, agent doesn't need to override.
- User says "用默认" / "use defaults" → don't compute; let the launcher's
  built-in fallback (30 / 30) apply.

## Known limitations

- **Windows session-bound**: `Start-Process` detaches from Cursor but not
  from the Windows interactive user session. Logging out of Windows /
  switching users kills the windows. Same caveat as running the script
  manually. For true overnight independence, run via Task Scheduler
  with "Run whether user is logged on or not" — out of scope here.
- **No concurrent run protection**: if you launch twice for the same
  project, you get two ilk processes fighting over commits. The
  launcher refuses to start if `running.pid` exists AND that PID is
  alive; it cleans up stale PID files automatically.

## Relationship to ilk-loop

| Concern | Owner |
|---|---|
| What the loop does each iteration | `ilk-loop` skill + `run_ilk_loop_claude.ps1` |
| Plan state machine, sub-plan progression | `ilk-loop` |
| Where to write logs | `run_ilk_loop_claude.ps1` (its `-LogDir`) |
| **Spawning** the runner, in **independent windows** | **`ilk-launcher`** (this skill) |
| **Tracking** which projects have live runs | **`ilk-launcher`** |
| **Stopping** runs | **`ilk-launcher`** |

Strict separation: the launcher never reaches into loop internals. It
only spawns, observes-via-PID, and kills.

## See also

- `<skill-root>/ilk-loop/SKILL.md` — the loop itself.
- `<vault>/ai-coding-workflow/tool-evaluations/ilk-launcher.md` —
  design rationale (why detached windows, not Cursor-hosted background).
- `<vault>/ai-coding-workflow/gap-analysis.md` P0-3 — the future
  `needs-review` state this launcher leaves room for.
