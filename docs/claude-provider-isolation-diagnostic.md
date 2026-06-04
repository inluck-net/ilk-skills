# Dual Claude Provider Isolation — Diagnostic Report

**Status:** in progress (diagnostic sub-plan
`2026-06-04-dual-claude-provider-isolation-diagnostic`)
**Machine:** macOS (Darwin 25.5.0), single user `chad`
**Scope:** Non-destructive investigation only. This report records the
read-only probes used and the evidence they produced. It does not change any
provider, auth, MCP, or ilk runtime state.

## Goal

Determine whether one machine can run **two isolated Claude Code instances**
with independent provider/model behaviour:

- **Planner Claude** — official Anthropic Opus / high-effort, for planning,
  reviews, and hard diagnosis.
- **Worker Claude** — a CCSwitch-backed cheaper provider, for `/ilk-run`
  execution loops.

The deliverable is an evidence-based recommendation for the safest isolation
model, plus a follow-up implementation outline.

---

## Step 0 — Inventory: current install layout

### How the ilk-skills suite is installed

The repo at `/Users/chad/Projects/inluck-net/ilk-skills` is the **single
source of truth**. `install.sh` (macOS/Linux) and `install.ps1` (Windows)
create per-host links from each agent's config home into the repo:

| Host | Skills dir | Commands dir |
|------|-----------|--------------|
| Cursor | `~/.cursor/skills/<name>` | `~/.cursor/commands/<file>` |
| Claude Code | `~/.claude/skills/<name>` | `~/.claude/commands/<file>` |
| Codex | `~/.codex/skills/<name>` | `~/.codex/commands/<file>` |

- **macOS/Linux** (`install.sh`): every link is a POSIX symlink
  (`ln -sfn`). Skill links point at a directory; command links point at a
  single `.md` file. `tools/migration` is also symlinked under
  `<home>/../tools/migration` (i.e. `~/.claude/tools/migration`).
- **Windows** (`install.ps1`): skill dirs become **junctions** (no admin
  needed); command files become **symlinks** via `cmd /c mklink` (needs
  admin or Developer Mode), with a copy-fallback when neither is available.

Discovery is name-scoped: skills matching `ilk-*`, commands matching `ilk*`.
Both installers are idempotent dry-run-by-default; `--apply`/`-Apply`
executes, `--force`/`-Force` backs up real (non-link) targets to
`<link>.pre-ilk-<timestamp>` before linking.

### Observed installed links on this machine (read-only `ls -la`)

All three homes are present and fully linked to the same repo. Every entry
below is a symlink into `/Users/chad/Projects/inluck-net/ilk-skills`.

**Skills** (identical set in `~/.claude`, `~/.cursor`, `~/.codex`):

```
ilk-feedback  -> .../skills/ilk-feedback
ilk-launcher  -> .../skills/ilk-launcher
ilk-loop      -> .../skills/ilk-loop
ilk-runner    -> .../skills/ilk-runner
ilk-watchdog  -> .../skills/ilk-watchdog
```

**Commands** (identical set in all three homes):

```
ilk.md  ilk-plan.md  ilk-lark-tickets.md  ilk-feedback.md
ilk-run.md  ilk-status.md  ilk-stop.md
```

**Tools** (all three homes):

```
~/.claude/tools/migration  -> .../tools/migration
~/.cursor/tools/migration  -> .../tools/migration
~/.codex/tools/migration   -> .../tools/migration
```

### Implication for a future "named Claude home"

Claude Code resolves skills/commands relative to its config home. Today that
home is hard-coded to `~/.claude` in both installers. To stand up a second
Claude home (e.g. `~/.claude-worker`), a future installer would need to link
**the same repo sources** into:

```
~/.claude-worker/skills/<name>     -> <repo>/skills/<name>
~/.claude-worker/commands/<file>   -> <repo>/commands/<file>
~/.claude-worker/tools/migration   -> <repo>/tools/migration
```

That is purely additive — installing into a new home does not touch the
existing `~/.claude` links. The harder question (answered in later steps) is
whether Claude Code can be *told* to use `~/.claude-worker` as its home, and
whether auth/MCP/provider state follows that home or stays global.

### Runtime/plans state location (already external)

`ilk_paths.py` confirms all per-project runtime state already lives **outside**
both the repo and any Claude home, under `~/.ilk-data/projects/<key>/`
(plans, runtime, logs). The skill root is resolved via `ILK_SKILL_HOME` env →
auto-detect from the running script → first of
`~/.codex/skills`, `~/.cursor/skills`, `~/.claude/skills`. This means the ilk
plan/runtime layer is already home-agnostic; only Claude Code's own
config/auth/MCP is home-bound — that is the real subject of this diagnostic.

---

_Subsequent steps append below._

---

## Step 1 — Read-only Claude Code & CCSwitch path discovery

All probes below were read-only (`ls`, `find`, `stat`, `command -v`,
`--version`, `cat` of structure with secret values masked, `grep` of logs,
`security find-generic-password` with no secret printed). No provider switch,
login, or config write was performed.

### Claude Code

| Item | Finding |
|------|---------|
| `claude` binary | on PATH via fnm shell shim; **v2.1.156** |
| Install method | `installMethod: global` (per `~/.claude.json`) |
| Config home | `~/.claude/` (dir) **+** `~/.claude.json` (sibling file) |
| Settings | `~/.claude/settings.json` (model, env, permissions, theme, plugins) |
| Auth/credentials | **macOS Keychain** item `svce="Claude Code-credentials" acct="chad"` — *not* a file. (`~/.claude/.credentials.json` absent.) |
| OAuth account | metadata (email, org, seat tier) in `~/.claude.json` → `oauthAccount` |
| MCP servers | global `chrome-devtools` under `~/.claude.json` → `mcpServers`; **1 of 15** per-project entries also defines its own `mcpServers` |
| Sessions/history | `~/.claude/sessions/`, `history.jsonl`, `projects/`, `session-env/` |
| Self-backups | `~/.claude/backups/.claude.json.backup.<epoch-ms>` (rolling) |

Key fact: **auth lives in the Keychain**, keyed by service name, not by the
config home path. So pointing Claude Code at a different home does **not** by
itself give it a different identity — both homes would read the same Keychain
credential unless auth is overridden by env (`ANTHROPIC_*`) or the home uses a
file credential. This is central to the isolation analysis (Steps 2–4).

Current `~/.claude/settings.json` → `env` is **empty (`{}`)** — i.e. Claude
Code is presently in "Claude Official" (OAuth) mode, not routed to any
alternate provider.

### CCSwitch ("CC Switch.app")

| Item | Finding |
|------|---------|
| App | `/Applications/CC Switch.app`, bundle `com.ccswitch.desktop`, **v3.13.0** (Tauri) |
| Store | `~/.cc-switch/cc-switch.db` (~9 MB SQLite), `~/.cc-switch/settings.json`, `~/.cc-switch/logs/cc-switch.log`, `~/.cc-switch/backups/db_backup_*.db` |
| App-support | `~/Library/Application Support/com.ccswitch.desktop/app_paths.json` (currently `{}`) |
| Manages | Claude, Codex, Gemini, OpenCode, OpenClaw, OMO providers (multi-tool switcher, not Claude-only) |
| Current Claude provider | `currentProviderClaude` in `~/.cc-switch/settings.json` = `claude-official` |
| Skill sync | `skillSyncMethod=auto`, `skillStorageLocation=cc_switch`, `enableClaudePluginIntegration=false` |

### What CCSwitch mutates (evidence-based)

Log lines prove the switch mechanism:

```
14:14:00  托盘菜单事件: claude_claude-official
14:14:00  切换到Claude供应商: claude-official        # switch to official
13:38:35  切换到Claude供应商: 66f41c76-…-11abf60fd28f # earlier: switch to a custom provider (UUID)
```

Correlating those switch events with file mtimes:

- `~/.claude/settings.json` (mtime 14:14, the official-switch moment) now has
  `env: {}`. → **On switch, CCSwitch rewrites `~/.claude/settings.json`'s
  `env` block**: it injects `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_MODEL` for a custom provider, and **clears it to `{}` for
  "Claude Official"** (falling back to the Keychain OAuth token).
- `~/.claude/backups/.claude.json.backup.<ts>` files cluster at the same
  switch times → **CCSwitch backs up `~/.claude.json` before writing it.**
- `~/.cc-switch/cc-switch.db` + `settings.json` (`currentProviderClaude`) are
  CCSwitch's own state.
- A periodic `[SESSION-SYNC]` job *reads* (scans) `~/.claude/` and `~/.codex/`
  session files for usage stats — read-only ingestion into its DB.

This is **independently corroborated by the ilk runner code**
(`run_ilk_loop_claude.{ps1,sh}`, `run_reviewer.py`): the runner reads
`~/.claude/settings.json`'s `env` block and, when it is non-empty (CCSwitch
routed to a non-Anthropic endpoint), clears conflicting *process-env*
`ANTHROPIC_*` so settings.json is the sole auth source — and treats an empty
`env: {}` ("Claude Official") as non-authoritative so OAuth still works.

### Mutation summary

CCSwitch operates on a **single, global Claude home (`~/.claude`)**: it
toggles provider auth by rewriting one shared `settings.json` env block (plus
`~/.claude.json`) in place. It has **no notion of two simultaneous homes** —
its model is "one active provider at a time for one `~/.claude`." This is the
core tension for running a planner and a worker concurrently (analysed in
Steps 2–4).

No destructive changes were made.
