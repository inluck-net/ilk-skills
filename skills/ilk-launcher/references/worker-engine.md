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
engine=claude → run_ilk_loop_claude.sh
engine=codex  → run_ilk_loop_codex.sh
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
