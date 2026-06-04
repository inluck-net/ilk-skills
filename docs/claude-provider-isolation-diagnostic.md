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

_Subsequent steps (1–5) append below: config/CCSwitch probes, alternate-home
feasibility, MCP/command isolation, the recommended isolation model, and QC._

No destructive changes were made.
