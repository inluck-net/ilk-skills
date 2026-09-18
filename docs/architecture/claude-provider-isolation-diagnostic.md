# Dual Claude Provider Isolation — Diagnostic Report

**Status:** complete (diagnostic sub-plan
`2026-06-04-dual-claude-provider-isolation-diagnostic`, shipped 2026-06-04)
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

---

## Step 2 — Alternate-home feasibility (safe probes)

### Supported mechanism: `CLAUDE_CONFIG_DIR`

Claude Code **does** support an alternate config home via the
`CLAUDE_CONFIG_DIR` environment variable. Evidence (read-only):

- The string `CLAUDE_CONFIG_DIR` is present in the installed binary
  (`@anthropic-ai/claude-code` v2.1.156, both the launcher and the
  `darwin-arm64` native binary).
- A harmless probe — `CLAUDE_CONFIG_DIR="$(mktemp -d)" claude --version` —
  ran cleanly, printed the version, and created **zero** files in the temp
  home (version path needs no config). It did not prompt for login or mutate
  anything. The temp dir was removed afterward.

> Not run (per safety rules): launching an *interactive/`-p`* session under a
> fresh `CLAUDE_CONFIG_DIR`, because the first real session can trigger
> onboarding/auth writes. Standing up the worker home for real is an
> implementation-step action requiring manual confirmation — see Step 4.

### Other per-invocation isolation flags (from `claude --help`)

These let a single binary be pointed at different state without a second
install — useful building blocks for a worker wrapper:

| Flag | Use for isolation |
|------|-------------------|
| `--settings <file-or-json>` | inject a provider `env` block (BASE_URL/AUTH_TOKEN/MODEL) for *this run only*, without editing `~/.claude/settings.json` |
| `--setting-sources <list>` | restrict which setting layers load |
| `--mcp-config <files...>` + `--strict-mcp-config` | give the worker its own MCP set, ignoring the global one |
| `--plugin-dir`, `--agents`, `--add-dir` | scope plugins/agents/dirs per run |

### The auth caveat (the crux)

Credentials are stored in the **macOS Keychain** under the fixed service name
`Claude Code-credentials` — the key does **not** vary with
`CLAUDE_CONFIG_DIR`. Consequences:

- Two homes that both rely on OAuth would resolve to the **same Anthropic
  identity** from the Keychain. An alternate home alone does *not* give you a
  second provider.
- **Provider divergence comes from env, not from the home.** When
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` (and `ANTHROPIC_MODEL`) are
  set — via process env or the home's `settings.json` `env` block — Claude
  Code uses that endpoint and ignores the Keychain OAuth. This is exactly the
  toggle CCSwitch drives, and exactly what the ilk runner already manipulates.

So the feasible shape is:

- **Planner home** — default `~/.claude`, **empty** `env` block → Keychain
  OAuth → official Opus / high effort.
- **Worker home** — `CLAUDE_CONFIG_DIR=~/.claude-worker` with a
  `settings.json` `env` block carrying the cheap provider's
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`. It never
  needs the Keychain OAuth, so the two roles do not collide on identity.

This means **alternate homes are feasible**, and isolation does *not* require
CCSwitch to keep toggling the shared `~/.claude` — the worker's provider can
be pinned in its own home's settings once. The cheap-provider token itself
must be sourced (one time, by the human) from the CCSwitch provider config /
`cc-switch.db`; the diagnostic did not extract any token.

No destructive changes were made.

---

## Step 3 — MCP and skill/command isolation

### Where each thing is resolved from

| Layer | Source today | Follows `CLAUDE_CONFIG_DIR`? |
|-------|--------------|------------------------------|
| Global MCP servers | `~/.claude.json` → `mcpServers` (observed: `chrome-devtools`) | **Yes** — `.claude.json` is resolved under the config home, so an alternate home starts with an empty `mcpServers`. |
| Per-project MCP servers | per-project entry inside `~/.claude.json` (observed: 1 of 15 projects) | Yes (same file). |
| Settings (`env`, model, permissions) | `<home>/settings.json` | Yes — each home has its own. |
| Skills | `<home>/skills/<name>` (symlinks → repo) | Yes — loaded relative to the home. |
| Slash commands | `<home>/commands/<file>` (symlinks → repo) | Yes. |
| Plugins | `<home>/plugins/` | Yes. |
| Auth (OAuth) | macOS **Keychain** (`Claude Code-credentials`) | **No** — Keychain key is fixed; shared across homes (see Step 2). |

The important split: **everything file-based follows the config home, but the
Keychain credential does not.** That is why the worker home must assert its
provider through an `env` block rather than expecting a separate login.

### Consequence for MCP

A second home (`~/.claude-worker`) starts with **no MCP servers**. The worker
either:

1. gets its own `mcpServers` written into `~/.claude-worker/.claude.json`
   (independent set — recommended; the worker rarely needs `chrome-devtools`),
   or
2. is launched with `--mcp-config <file> --strict-mcp-config` for fully
   per-invocation MCP, ignoring all home/global config.

Either way MCP is **cleanly isolatable** — the planner's MCP set does not leak
into the worker.

### Consequence for skills / commands (installer work required)

Skills and commands load from `<home>/skills` and `<home>/commands`. The ilk
suite must therefore be linked into the worker home as well. **Today both
installers hard-code the three homes** (`~/.cursor`, `~/.claude`, `~/.codex`):

- `install.sh` builds the Claude target as a literal
  `TARGET_SKILLS+=("$HOME/.claude/skills")` /
  `TARGET_COMMANDS+=("$HOME/.claude/commands")`.
- `install.ps1` builds it as a literal `Join-Path $HOME ".claude\skills"`.

Neither accepts an arbitrary Claude home. To support `~/.claude-worker` the
follow-up implementation must add a parameter, e.g.:

- **macOS/Linux:** `--claude-home <dir>` (default `~/.claude`), or a new
  `--only-claude-worker` selector that targets `$HOME/.claude-worker`.
- **Windows:** `-ClaudeHome <dir>` / `-OnlyClaudeWorker`.

Good news on the side links: the `tools/migration` link is already built as
`<skills_dir>/../tools/migration`, so it resolves correctly under any home
(`~/.claude-worker/tools/migration`) with no extra change. Discovery
(`ilk-*` skills, `ilk*` commands) and the symlink/junction logic are
home-agnostic — only the **target home list** needs to become a parameter.

`ilk_paths.skill_root()` already prefers `ILK_SKILL_HOME`, then auto-detects
from the running script, then falls back to `~/.codex` → `~/.cursor` →
`~/.claude`. A worker launched from `~/.claude-worker/skills/...` would
auto-detect correctly; setting `ILK_SKILL_HOME` in the worker wrapper makes it
deterministic.

### Net

- **MCP isolation:** native and clean (per-home `.claude.json` + `--mcp-config`).
- **Skill/command isolation:** mechanically trivial but **requires a small
  installer change** to target a second Claude home.
- **No conflict** between the two homes' skills/commands — they are independent
  link sets pointing at the same single-source repo.

No destructive changes were made.

---

## Step 4 — Recommendation

### Candidate models compared

| # | Model | Isolation strength | Effort | Failure modes |
|---|-------|--------------------|--------|---------------|
| A | **`CLAUDE_CONFIG_DIR` alternate home + thin wrapper** | High (separate settings/MCP/skills/commands per home; provider pinned via worker `env`) | Low–Med (installer param + wrapper) | worker `env` token expiry; forgetting `ILK_SKILL_HOME`; both homes share Keychain but worker overrides it via env |
| B | Wrapper scripts with named homes (no env var) | Same as A — a wrapper is *how* you set `CLAUDE_CONFIG_DIR` | Low | identical to A; really a sub-case of A |
| C | Symlink swapping (`~/.claude` repointed per role) | **Low / fragile** | Med | **race condition** when planner + worker run concurrently — the whole point fails; non-atomic; easy to corrupt live state |
| D | Separate macOS user account | **Highest** (OS-level Keychain, config, processes all separate) | High | heavy context-switch (fast-user-switching or `su`), duplicate tool installs, clumsy cross-user repo access, overkill for one operator |
| E | Container / VM for the worker | Very high | Highest | Claude Code + CCSwitch + MCP + repo mounts inside a container; GUI CCSwitch doesn't belong in a headless container; large maintenance surface |

### Recommended isolation model

**Model A — a `CLAUDE_CONFIG_DIR`-based named worker home, driven by a thin
wrapper, with the provider pinned in the worker home's `settings.json` `env`
block.** Concretely:

- **Planner** = default `~/.claude`, empty `env` → Keychain OAuth → official
  Opus. Left exactly as-is; CCSwitch keeps managing it interactively.
- **Worker** = `~/.claude-worker`, selected by `CLAUDE_CONFIG_DIR`, with its
  own `settings.json` `env` (cheap provider `ANTHROPIC_BASE_URL` /
  `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`), its own (minimal) MCP set, and
  ilk skills/commands linked in. A wrapper exports `CLAUDE_CONFIG_DIR`,
  `ILK_SKILL_HOME`, and (optionally) the provider env before invoking
  `claude` / the ilk runner.

**Why A over the others:** it is the only option that is simultaneously
(1) concurrency-safe — the two homes never write the same file, so a planner
and a worker can run at the same time (C fails here); (2) low-effort — it
reuses the `env`-block mechanism the ilk runner *already* understands and the
provider tokens CCSwitch *already* stores; and (3) reversible — deleting
`~/.claude-worker` fully removes it with zero impact on the planner. D and E
deliver marginally stronger isolation that this single-operator,
single-machine use case does not need, at a large recurring cost.

**Relationship to CCSwitch:** CCSwitch stays the *interactive* switcher for the
planner's `~/.claude`. For the worker, CCSwitch is used **once** as the source
of the provider's base-URL/token, which is then pinned into
`~/.claude-worker/settings.json`. The worker does **not** rely on CCSwitch
toggling shared state at runtime — eliminating the race where a switch in the
GUI would yank the provider out from under a running worker loop.

### Tradeoffs, failure modes, rollback

- **Tradeoff:** the worker's provider token is pinned in a plaintext
  `settings.json` `env` block (same exposure the ilk runner already documents).
  Acceptable for a local dev token; rotate via the wrapper or a re-pin step.
- **Failure mode — token expiry:** worker loop 401s. Detected by the existing
  `api-blocked` postmortem classifier (`collect.py`). Fix = re-pin token.
- **Failure mode — skill not found in worker home:** caused by the installer
  not having linked into `~/.claude-worker`. Fix = run the new installer
  target; set `ILK_SKILL_HOME` in the wrapper as a belt-and-braces default.
- **Failure mode — accidental shared Keychain use:** if the worker `env` block
  is empty, the worker silently falls back to the planner's OAuth identity.
  Mitigation = wrapper asserts a non-empty `ANTHROPIC_BASE_URL` before launch.
- **Rollback:** `rm -rf ~/.claude-worker` and delete the wrapper. The planner's
  `~/.claude`, CCSwitch, MCP, and ilk runtime are untouched throughout.

### Follow-up implementation plan (outline — NOT shipped here)

A future, separate batch (per MASTER "Out of scope") would:

1. **Installer parameter** — add `--claude-home <dir>` / `-ClaudeHome` (and a
   convenience `--only-claude-worker` / `-OnlyClaudeWorker`) to `install.sh`
   and `install.ps1`, replacing the literal `~/.claude` Claude target with a
   variable. Link skills, commands, and `tools/migration` into the worker home.
2. **Worker bootstrap script** — create `~/.claude-worker/`, write a
   `settings.json` with the provider `env` block (token sourced once from
   CCSwitch / `cc-switch.db`), and a minimal `.claude.json` (own/empty MCP).
3. **Wrapper / launcher integration** — a `claude-worker` wrapper (and an ilk
   runner flag) that exports `CLAUDE_CONFIG_DIR=~/.claude-worker`,
   `ILK_SKILL_HOME`, asserts a non-empty `ANTHROPIC_BASE_URL`, then execs
   `claude` / `run_ilk_loop_claude.{sh,ps1}`. The runner's existing env-clear
   logic already DTRT once the home's `settings.json` env is authoritative.
4. **Docs + verification** — a PRIMER note and a preflight check that the
   worker home authenticates (cheap `claude -p "ok"` against the worker home)
   without disturbing the planner.

This model is feasible; the only code change required is the installer
parameter (item 1) — everything else is configuration the existing runner
already supports.

No destructive changes were made.
