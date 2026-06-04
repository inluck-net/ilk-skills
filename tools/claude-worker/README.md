# claude-worker

Run a **Worker Claude** (cheap Anthropic-compatible provider) alongside the
default **Planner Claude** (official provider on `~/.claude`), without either
one disturbing the other's provider state. Design:
[`docs/dual-claude-homes-design.md`](../../docs/dual-claude-homes-design.md).

These scripts only ever touch the worker home you name. They never read,
write, or mutate `~/.claude`, CCSwitch state, or any `cc-switch.db`, and they
never extract or print a provider token.

## Files

| File | Purpose |
|---|---|
| `bootstrap.sh` / `bootstrap.ps1` | Create the worker home (`settings.json` with a pinned provider env block, minimal `.claude.json`), optionally link ilk skills/commands into it. |
| `claude-worker.sh` / `claude-worker.ps1` | Launch `claude` under the worker home: set `CLAUDE_CONFIG_DIR` + `ILK_SKILL_HOME`, run a fail-closed preflight, then exec `claude`. |

## One-time setup

### Option A: Import from CCSwitch (recommended)

If you already have providers configured in CCSwitch, you can import them
directly instead of copying values manually:

macOS / Linux:

```bash
# List available CCSwitch Claude providers (redacted, no secrets printed):
bash tools/claude-worker/bootstrap.sh --list-ccswitch-providers

# Import interactively (pick from a menu):
bash tools/claude-worker/bootstrap.sh --apply --from-ccswitch --interactive --link-skills

# Import a specific provider by id or name:
bash tools/claude-worker/bootstrap.sh --apply --from-ccswitch --provider <provider-id> --link-skills
```

Windows (PowerShell):

```powershell
# List providers
.\tools\claude-worker\bootstrap.ps1 -ListCcsitchProviders

# Import interactively
.\tools\claude-worker\bootstrap.ps1 -Apply -FromCcswitch -Interactive -LinkSkills

# Import specific provider
.\tools\claude-worker\bootstrap.ps1 -Apply -FromCcswitch -Provider <provider-id> -LinkSkills
```

The import reads CCSwitch config **read-only** — it never mutates CCSwitch
state, databases, or settings files.

Official/Claude OAuth providers are **refused by default** to prevent the
worker from accidentally using the planner's official identity. Pass
`--allow-official` / `-AllowOfficial` to override (not recommended).

### Option B: Manual provider values

macOS / Linux:

```bash
# 1. Create the worker home with your provider values (token supplied by you).
bash tools/claude-worker/bootstrap.sh --apply \
  --base-url https://provider.example.com/anthropic \
  --auth-token "$YOUR_WORKER_TOKEN" \
  --model worker-model-id \
  --link-skills            # also installs ilk skills/commands into the home
```

Windows (PowerShell):

```powershell
.\tools\claude-worker\bootstrap.ps1 -Apply `
  -BaseUrl https://provider.example.com/anthropic `
  -AuthToken $env:YOUR_WORKER_TOKEN `
  -Model worker-model-id `
  -LinkSkills
```

Omit `--auth-token` / `-AuthToken` (and the other provider flags) and the
bootstrap reads `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_MODEL` from the environment instead. If any of the three is missing
it writes nothing and exits 3.

## Switching providers later

After initial setup, you can switch the worker to a different CCSwitch provider
without re-running the full bootstrap:

```bash
# Preview which provider would be imported (dry-run):
bash tools/claude-worker/bootstrap.sh --from-ccswitch --provider <new-provider-id>

# Apply the switch:
bash tools/claude-worker/bootstrap.sh --apply --from-ccswitch --provider <new-provider-id>

# Or pick interactively:
bash tools/claude-worker/bootstrap.sh --apply --from-ccswitch --interactive
```

**Important:** Provider switching applies to **new Worker Claude sessions
only**. Any currently running worker processes keep their old provider until
restarted. If an active worker is detected, the bootstrap refuses to overwrite
settings unless you pass `--force` / `-Force`.

A backup of the previous `settings.json` is created automatically
(`settings.json.pre-ilk-<timestamp>`).

## Safety notes

- **Never prints tokens.** All output shows redacted placeholders like
  `***set (42 chars)***`.
- **Read-only CCSwitch access.** The import helper reads the CCSwitch database
  but never writes to it.
- **Fail-closed.** If any required provider field is missing, the bootstrap
  writes nothing and exits 3 — the worker can never silently fall back to the
  planner's OAuth identity.
- **Active worker guard.** Overwriting provider settings while a worker is
  running could break that session. The bootstrap detects this and refuses
  unless `--force` is passed.

## Launching the worker

```bash
# Validate the worker home without launching:
bash tools/claude-worker/claude-worker.sh --preflight-only

# Launch interactive Claude Code under the worker home:
bash tools/claude-worker/claude-worker.sh

# Any args after the wrapper flags are forwarded to claude verbatim.
```

```powershell
.\tools\claude-worker\claude-worker.ps1 --preflight-only
.\tools\claude-worker\claude-worker.ps1
```

If Claude Code is installed but not on `PATH`, the wrapper checks
`~/.local/bin/claude`, the active npm global prefix, and common package-manager
shims. Prefer `~/.local/bin/claude` when you maintain a private stable Claude
Code binary outside npm/fnm's auto-updated package directory:

```bash
CLAUDE_BIN="$HOME/.local/bin/claude" bash tools/claude-worker/claude-worker.sh
bash tools/claude-worker/claude-worker.sh --claude-bin "$HOME/.local/bin/claude"
```

```powershell
$env:CLAUDE_BIN = "$HOME\.local\node\bin\claude.cmd"
.\tools\claude-worker\claude-worker.ps1
```

The wrapper refuses to launch (exit 3) unless the worker home, its
`settings.json`, all three `ANTHROPIC_*` values, and the `ilk-runner` skill
are present — so a misconfigured worker can never silently fall back to the
planner's official OAuth identity.

## Running an ilk loop as the worker

The wrapper deliberately does **not** change `/ilk-run` or any runner default.
Instead, launch the runner *through* the wrapper so the loop inherits the
worker home and skill root. Two equivalent shapes:

```bash
# A. Forward a slash command to an interactive Claude session:
bash tools/claude-worker/claude-worker.sh /ilk-run

# B. Run the runner script directly inside the wrapper's environment, by
#    starting an interactive shell under the worker env first:
CLAUDE_CONFIG_DIR="$HOME/.claude-worker" \
ILK_SKILL_HOME="$HOME/.claude-worker/skills" \
  bash "$ILK_SKILL_HOME/ilk-runner/scripts/ilk-run.sh" .
```

Shape A is the recommended path: the wrapper runs its fail-closed preflight
first, so a broken worker home stops the run before any provider call. Shape B
is a manual escape hatch for when you want to invoke the runner script without
an interactive Claude session — it skips the wrapper's preflight, so only use
it once `--preflight-only` has passed.

Best-of-N (multiple concurrent workers) is explicitly **out of scope** here —
it needs isolated git worktrees and separate ilk runtime keys. See the design
doc's "Best-Of-N Is Separate" section.

## Rollback

The worker home is the only thing these scripts create:

```bash
rm -rf "$HOME/.claude-worker"          # macOS / Linux
```

```powershell
Remove-Item -Recurse -Force "$HOME\.claude-worker"   # Windows
```

The planner home, CCSwitch config, and ilk runtime state are never modified.
