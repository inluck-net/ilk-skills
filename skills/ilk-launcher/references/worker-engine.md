# Worker Engine Boundary

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

## Engines

The launcher's `worker_engine` — set per-project in `.ilk-launch.json`,
or overridden per-launch with `--engine` / `-Engine` — selects which
Claude home (and therefore which provider) the detached loop uses:

| Engine | Runner | Claude home | Provider | Use when |
|---|---|---|---|---|
| `claude` *(default)* | `run_ilk_loop_claude.*` | `~/.claude` (planner) | Official Anthropic / OAuth | Default — you want the planner's provider. Behavior unchanged from before. |
| `claude-worker` | `run_ilk_loop_claude.*` | `~/.claude-worker` (worker) | Cheap Anthropic-compatible, pinned in the worker home's `settings.json` | Unattended / cost-sensitive runs. |
| `codex` | `run_ilk_loop_codex.*` | n/a | Codex CLI | **Not yet** — rejected with a clear message until the Codex runner lands. |

`claude` and `claude-worker` share the **same** runner script; they
differ only in which home the detached run points at. For
`claude-worker`, the launcher injects
`CLAUDE_CONFIG_DIR=~/.claude-worker` and
`ILK_SKILL_HOME=~/.claude-worker/skills` into the spawned run, so every
`claude -p` iteration resolves the worker home and its cheaper provider
instead of the planner's official one. The runner's settings preflight
reads `${CLAUDE_CONFIG_DIR}/settings.json` and fails closed if the
provider env is missing — a misconfigured worker can never silently
fall back to the planner's OAuth identity.

**Prerequisite:** the worker home must be bootstrapped first
(`tools/claude-worker/bootstrap.sh` / `.ps1`). See
`tools/claude-worker/README.md` and `docs/dual-claude-homes-design.md`
for the planner/worker model.

Examples:

```powershell
& launch.ps1 -ProjectPath … -Engine claude-worker   # route this run under the worker home
```

```bash
bash launch.sh --project-path … --engine claude-worker
```

Or persist it per-project so every launch (and the watchdog) routes to
the worker home:

```json
{ "worker_engine": "claude-worker" }
```

### Default-engine precedence

The engine is resolved in this order (first hit wins):

1. **CLI flag** — `-Engine` / `--engine`
2. **Project config** — `worker_engine` in `.ilk-launch.json`
3. **Machine-wide opt-in** — the `ILK_DEFAULT_ENGINE` environment variable
4. **Hardcoded default** — `claude` (planner)

`ILK_DEFAULT_ENGINE` lets you flip a whole machine to the worker without
editing every project: `export ILK_DEFAULT_ENGINE=claude-worker` (bash) /
`$env:ILK_DEFAULT_ENGINE = 'claude-worker'` (PowerShell). The shipped
default stays `claude`, so a fresh install still "just works" on the
planner's existing credentials.

Two safety behaviors back this up:

- **Fail-closed** — a real launch on `claude-worker` aborts with a clear
  message if `~/.claude-worker` isn't bootstrapped (it never silently
  falls through to OAuth). Dry-run shows a `WorkerHome: ready|MISSING`
  line instead of aborting.
- **Nudge** — a real launch on the planner while a bootstrapped worker
  home exists prints a one-line tip pointing at `ILK_DEFAULT_ENGINE`.

### `-WorkerHome` / `CLAUDE_WORKER_HOME` override

By default, `claude-worker` routes to `~/.claude-worker`. You can
override this to point at a different home — a **slot home** for
concurrent dispatch, or any alternative worker home:

```powershell
# PowerShell: CLI flag (highest precedence)
& launch.ps1 -Engine claude-worker -WorkerHome "~/.claude-worker-2"

# PowerShell: environment variable (middle precedence)
$env:CLAUDE_WORKER_HOME = "~/.claude-worker-2"
& launch.ps1 -Engine claude-worker

# bash: CLI flag
bash launch.sh --engine claude-worker --worker-home ~/.claude-worker-2

# bash: environment variable
CLAUDE_WORKER_HOME=~/.claude-worker-2 bash launch.sh --engine claude-worker
```

**Precedence** (first hit wins):

1. **CLI flag** — `-WorkerHome` / `--worker-home`
2. **Environment variable** — `CLAUDE_WORKER_HOME`
3. **Hardcoded default** — `~/.claude-worker`

The override replaces the hardcoded home everywhere: `CLAUDE_CONFIG_DIR`,
`ILK_SKILL_HOME`, `Test-WorkerHomeReady`, and the dry-run readiness line
(`WorkerHome: ready|MISSING`) all reflect the resolved home. With no
override, behavior is byte-for-byte as before.

### Slot homes (`~/.claude-worker-<i>`)

Slot homes are the substrate for safe cross-project concurrency and the
future V2 best-of-N worktree model. A slot home is a **clone** of the
base `~/.claude-worker`:

- Same `settings.json` provider `env` block (same provider as base by
  default; overridable per-slot for V2 model-diverse best-of-N).
- Symlinked (or junctioned on Windows) `skills/` to the same source.
- Minimal `.claude.json`.

Create a slot home with the bootstrap's `--clone-slot` flag:

```bash
# Clone the base worker home into ~/.claude-worker-2
bash tools/claude-worker/bootstrap.sh --clone-slot 2

# PowerShell
.\tools\claude-worker\bootstrap.ps1 -CloneSlot 2
```

Slot creation is **idempotent** (re-run is a no-op / refresh) and
**lazy** (created on first use). The base `~/.claude-worker` is slot 1;
additional slots are `~/.claude-worker-2`, `~/.claude-worker-3`, …  The
scheduler's `-MaxConcurrent` flag (sub-plan #3) routes each dispatch to
a distinct slot home automatically.

**V2 forward hook** (documented intent, not built in this batch): the
slot-home bootstrap accepts an optional `--model` / `-Model` override so
V2 can pin a different `ANTHROPIC_MODEL` / `ANTHROPIC_BASE_URL` per slot
home, enabling model-diverse best-of-N. Currently accepted and
pass-through (inherits the base provider).

See `tools/claude-worker/README.md` for the full slot-clone reference.

## Adding a Codex runner

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
engine=claude        → run_ilk_loop_claude.sh   (planner home: ~/.claude)
engine=claude-worker → run_ilk_loop_claude.sh   (worker home:  ~/.claude-worker)
engine=codex         → run_ilk_loop_codex.sh
```

## Worker MCP Filtering

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
