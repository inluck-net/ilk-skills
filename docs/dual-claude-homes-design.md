# Dual Claude Homes Design

## Status

Implemented. This document turns the diagnostic findings in
`docs/claude-provider-isolation-diagnostic.md` into a cross-platform design for
running separate Planner and Worker Claude Code environments on the same
machine, and the design has now shipped:

- Installers accept a custom Claude home: `install.sh --claude-home <dir>` and
  `install.ps1 -ClaudeHome <dir>` (both with `--only-claude` / `-OnlyClaude`).
- Worker bootstrap + wrapper live under `tools/claude-worker/`
  (`bootstrap.sh` / `bootstrap.ps1`, `claude-worker.sh` / `claude-worker.ps1`).
  See [`tools/claude-worker/README.md`](../tools/claude-worker/README.md) for
  setup and launch commands.

The remainder of this document is the design of record; the sections below
match the shipped behavior.

## Problem

We want two Claude Code roles to coexist:

- **Planner Claude** uses the official Anthropic provider, Opus, and high
  reasoning effort for planning, review, and difficult diagnosis.
- **Worker Claude** uses a lower-cost Anthropic-compatible provider for
  implementation loops such as `/ilk-run`.

The current CCSwitch workflow changes one shared Claude Code environment. On
macOS, CCSwitch rewrites the provider-related `env` block in
`~/.claude/settings.json` and also touches shared Claude Code metadata. That
is fine for one active Claude Code role, but it is unsafe when one Claude Code
instance should remain on official Opus while another runs a cheaper provider.

## Design Goals

- Keep the default `~/.claude` planner environment unchanged.
- Create a separate worker Claude home that can be launched concurrently.
- Avoid live CCSwitch races while a worker run is active.
- Keep skills and slash commands source-controlled in this repo.
- Support macOS and Windows with the same conceptual model.
- Fail closed if the worker provider config is missing.
- Never extract or write provider secrets implicitly.

## Non-Goals

- Do not implement best-of-N worker orchestration in this design. That needs
  isolated git worktrees and separate ilk runtime namespaces.
- Do not create a second OAuth identity for Claude Official. On macOS, Claude
  Code OAuth credentials are stored in Keychain under a fixed service name.
- Do not depend on symlink-swapping `~/.claude`; that model races by design.
- Do not require containers, VMs, or a second OS user for the normal case.

## Architecture

Use two Claude Code homes:

| Role | macOS / Linux home | Windows home | Provider source |
|---|---|---|---|
| Planner | `~/.claude` | `%USERPROFILE%\\.claude` | Official Claude Code OAuth / Keychain or platform credential store |
| Worker | `~/.claude-worker` | `%USERPROFILE%\\.claude-worker` | Explicit `ANTHROPIC_*` env block pinned in worker settings |

The worker is selected with Claude Code's `CLAUDE_CONFIG_DIR` environment
variable. The ilk skill root is selected with `ILK_SKILL_HOME`.

macOS / Linux wrapper shape:

```bash
export CLAUDE_CONFIG_DIR="$HOME/.claude-worker"
export ILK_SKILL_HOME="$HOME/.claude-worker/skills"
exec claude "$@"
```

Windows wrapper shape:

```powershell
$env:CLAUDE_CONFIG_DIR = Join-Path $HOME ".claude-worker"
$env:ILK_SKILL_HOME = Join-Path $HOME ".claude-worker\skills"
claude @args
```

## Provider Isolation

The planner home should keep its normal official-provider state. On macOS,
that means `~/.claude/settings.json` has no provider override `env` block, so
Claude Code uses its normal OAuth credential from Keychain.

The worker home must not rely on the planner's OAuth credential. Instead,
`~/.claude-worker/settings.json` should contain an explicit provider env block:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://provider.example.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "REDACTED_USER_SUPPLIED_TOKEN",
    "ANTHROPIC_MODEL": "worker-model-id"
  }
}
```

The token should be supplied explicitly by the user or copied manually from a
trusted provider source. The implementation must not automatically extract
secrets from `~/.cc-switch/cc-switch.db`.

The worker wrapper should fail before launching if any of these values are
missing:

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL`

This prevents silent fallback to the planner's official OAuth identity.

## CCSwitch Relationship

CCSwitch remains useful, but it should not be the runtime switch for both
roles.

Recommended behavior:

- Use CCSwitch interactively for the planner home (`~/.claude`).
- Use CCSwitch or its UI as a reference for worker provider values.
- Pin the worker provider in `~/.claude-worker/settings.json`.
- Do not let a running worker depend on CCSwitch mutating shared
  `~/.claude/settings.json`.

This removes the race where switching the planner provider changes the active
provider of a worker loop.

## MCP Isolation

MCP state is file-based and follows `CLAUDE_CONFIG_DIR`.

Planner:

- Uses normal `~/.claude/.claude.json` MCP entries (`mcpServers`), with OAuth
  tokens in `~/.claude/.credentials.json` (`mcpOAuth`, `claudeAiOauth`).
- Can keep richer planning/review MCPs.

Worker:

- Reads its OWN `~/.claude-worker/.claude.json` (`mcpServers`) — **NOT**
  `~/.claude.json`. `bootstrap` seeds `mcpServers: {}` and never clobbers, so a
  worker starts with **zero** MCPs.
- Should keep a deliberately small set (loops need repo/file/shell more than
  broad integrations).

> **Critical gotcha (cost two stalls, fixed v0.9.5–v0.9.6).** An MCP added to the
> *interactive* config (`~/.claude.json` or `claude mcp add`) does NOT reach the
> worker — the worker only reads `~/.claude-worker/.claude.json`. So a sub-plan
> that hard-gates on an MCP (`env_prereqs: claude mcp list | grep -q figma`) can
> pass interactive review yet fast-fail to `blocked` in the loop. Two
> consequences were wired up:
>
> 1. **Planner probes the worker surface.** `/ilk-plan` step 4b now runs
>    `skills/ilk-loop/scripts/worker_mcp.py list` (reads the worker home's
>    `mcpServers`), NOT the interactive `claude mcp list`, and stops pre-approval
>    if the worker lacks a needed MCP.
> 2. **`ilk-worker-mcp` helper** (`tools/claude-worker/ilk-worker-mcp.{ps1,sh}`,
>    core `worker_mcp_edit.py`) adds an MCP to the worker correctly:
>    - writes the server entry into `~/.claude-worker/.claude.json` `mcpServers`;
>    - for an OAuth MCP (figma), copies ONLY that server's `mcpOAuth` entry into
>      `~/.claude-worker/.credentials.json` — **never** `claudeAiOauth` (that
>      would inject the planner's Claude identity and break the isolation this
>      doc exists to enforce);
>    - `ilk-worker-mcp add figma --from-user`, `... list`, `... verify`.

The worker can also be launched with `--mcp-config <file> --strict-mcp-config`
for per-run MCP isolation (an alternative to editing the worker home).

## Skills And Commands

Claude Code loads skills and slash commands from the selected home:

```text
<claude-home>/skills/<skill-name>
<claude-home>/commands/<command>.md
<claude-home>/tools/migration
```

Today the installers target only the default Claude home:

- macOS/Linux: `~/.claude`
- Windows: `%USERPROFILE%\\.claude`

The implementation should add arbitrary Claude-home support:

macOS/Linux:

```bash
bash install.sh --apply --claude-home "$HOME/.claude-worker" --only-claude
```

Windows:

```powershell
.\install.ps1 -Apply -ClaudeHome "$HOME\.claude-worker" -OnlyClaude
```

The existing default behavior must remain unchanged. Running `install.sh
--apply` without a custom home should continue installing to Cursor, Claude
Code, and Codex exactly as it does today.

## Bootstrap Flow

The worker bootstrap should be explicit and reversible.

1. Create the worker home directory if missing.
2. Create `settings.json` with user-supplied provider env.
3. Create a minimal `.claude.json`, preferably with no MCP servers by default.
4. Link skills, commands, and tools into the worker home by invoking the
   installer with `--claude-home` / `-ClaudeHome`.
5. Run a non-mutating preflight:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-worker" claude --version
```

6. Optionally run a user-approved live smoke prompt against the worker
   provider.

The bootstrap must never delete `~/.claude`, change the planner provider, or
modify CCSwitch state.

## Wrapper Behavior

The wrapper should provide a narrow, predictable entry point:

- `claude-worker` starts Claude Code using the worker home.
- `claude-worker-ilk-run` may later launch `/ilk-run` or the runner scripts
  under the worker home.

Preflight checks:

- worker home exists;
- worker `settings.json` exists;
- required `ANTHROPIC_*` values are present;
- `ILK_SKILL_HOME` points to an existing `ilk-runner` skill;
- `claude --version` works under `CLAUDE_CONFIG_DIR`.

The wrapper should print the active worker home before launching so it is
obvious which environment is running.

## macOS Notes

- Planner OAuth credentials are in macOS Keychain under Claude Code's fixed
  credential service. Alternate Claude homes do not create separate OAuth
  identities by themselves.
- Worker provider separation must come from explicit `ANTHROPIC_*` env in the
  worker home.
- Symlinks are sufficient for skills and commands.
- CCSwitch currently targets the default Claude home; do not assume it can
  manage `~/.claude-worker` independently.

## Windows Notes

- Use `%USERPROFILE%\\.claude-worker` as the worker home.
- `CLAUDE_CONFIG_DIR` and `ILK_SKILL_HOME` should be set in the PowerShell
  wrapper process before invoking `claude`.
- Skill directories should use junctions, matching current `install.ps1`
  behavior.
- Command files should use symlinks when available, with the existing copy
  fallback.
- The exact credential-store behavior for Claude Official on Windows still
  needs implementation-time verification. The worker should not depend on it;
  it should use explicit provider env.

## Failure Modes And Mitigations

| Failure mode | Mitigation |
|---|---|
| Worker provider token expires | Existing `/ilk-feedback` should classify API/auth stalls; re-pin token. |
| Worker env missing | Wrapper fails closed before launching. |
| Worker skills missing | Run installer with `--claude-home` / `-ClaudeHome`; wrapper checks `ilk-runner`. |
| Planner switch affects worker | Worker never reads shared `~/.claude/settings.json`; it uses `CLAUDE_CONFIG_DIR`. |
| User launches worker in same repo concurrently | Existing ilk per-project PID guard should stop duplicate same-project runs; best-of-N needs separate worktrees. |

## Best-Of-N Is Separate

Multiple Worker Claude instances are possible only when their workspaces and
runtime namespaces are isolated.

Safe future best-of-N shape:

```text
one task
  -> create N git worktrees / branches
  -> create N isolated ilk runtime keys or run IDs
  -> launch N worker homes/processes
  -> collect candidate patches/reports
  -> evaluate and merge/cherry-pick one winner
```

Do not run N workers against the same git worktree and same ilk project key.
They would race on files, plan frontmatter, PID files, logs, commits, and
watchdog state.

## Implementation Plan

All five steps have shipped (see the file pointers below):

1. ✅ Added `--claude-home <dir>` to `install.sh` and `-ClaudeHome <dir>` to
   `install.ps1`.
2. ✅ Added worker bootstrap scripts for macOS/Linux and Windows
   (`tools/claude-worker/bootstrap.sh`, `bootstrap.ps1`).
3. ✅ Added worker wrappers that set `CLAUDE_CONFIG_DIR` and `ILK_SKILL_HOME`
   and fail closed when provider env is missing
   (`tools/claude-worker/claude-worker.sh`, `claude-worker.ps1`).
4. ✅ Documented the planner/worker workflow in `tools/claude-worker/README.md`
   and cross-referenced it from `commands/ilk-run.md` and
   `skills/ilk-runner/SKILL.md`.
5. ✅ Added QC checks for install links and wrapper preflight on macOS, with
   Windows parity documented and script syntax checked where possible
   (see `docs/dual-claude-homes-verification.md`).

## Rollback

Rollback is intentionally simple:

```bash
rm -rf "$HOME/.claude-worker"
```

On Windows:

```powershell
Remove-Item -Recurse -Force "$HOME\.claude-worker"
```

The planner home, CCSwitch config, and existing ilk runtime state are not
modified by the worker-home design.
