# Dual Claude Homes — Verification Guide

A short, copy-pasteable checklist for setting up, verifying, and rolling back a
**Worker Claude** home alongside the default **Planner Claude** home. Design of
record: [`dual-claude-homes-design.md`](dual-claude-homes-design.md). Scripts:
[`../tools/claude-worker/`](../tools/claude-worker/).

Every command below is non-destructive unless it carries `--apply` / `-Apply`.
Dry-runs write nothing. None of these scripts read, write, or mutate
`~/.claude`, CCSwitch state, or `cc-switch.db`, and none extract a provider
token — you always supply the token explicitly.

## 0. Prerequisites

- `claude` (Claude Code CLI) on `PATH`.
- A worker provider's `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and
  `ANTHROPIC_MODEL`. Copy the token from a trusted source — the scripts never
  fetch it for you.
- The default Planner home stays on `~/.claude`; you do not touch it here.

## 1. Setup

### macOS / Linux

```bash
# Dry-run first: preview the worker home + link command, write nothing.
bash tools/claude-worker/bootstrap.sh \
  --base-url https://provider.example.com/anthropic \
  --auth-token "$YOUR_WORKER_TOKEN" \
  --model worker-model-id

# Apply: create ~/.claude-worker and link ilk skills/commands into it.
bash tools/claude-worker/bootstrap.sh --apply --link-skills \
  --base-url https://provider.example.com/anthropic \
  --auth-token "$YOUR_WORKER_TOKEN" \
  --model worker-model-id
```

Provider values may also come from the environment (`ANTHROPIC_BASE_URL` /
`ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`); flags win when both are present.

### Windows (PowerShell)

```powershell
# Dry-run
.\tools\claude-worker\bootstrap.ps1 `
  -BaseUrl https://provider.example.com/anthropic `
  -AuthToken $env:YOUR_WORKER_TOKEN `
  -Model worker-model-id

# Apply
.\tools\claude-worker\bootstrap.ps1 -Apply -LinkSkills `
  -BaseUrl https://provider.example.com/anthropic `
  -AuthToken $env:YOUR_WORKER_TOKEN `
  -Model worker-model-id
```

If you skip `--link-skills` / `-LinkSkills`, link skills separately:

```bash
bash install.sh --apply --claude-home "$HOME/.claude-worker" --only-claude
```

```powershell
.\install.ps1 -Apply -ClaudeHome "$HOME\.claude-worker" -OnlyClaude
```

## 2. Verify (preflight, no launch)

The wrapper runs a fail-closed preflight — worker home, `settings.json`, all
three `ANTHROPIC_*` values, and the `ilk-runner` skill must be present, or it
refuses to launch (exit 3). Run it without launching:

```bash
bash tools/claude-worker/claude-worker.sh --preflight-only
```

```powershell
.\tools\claude-worker\claude-worker.ps1 --preflight-only
```

Expected: `Preflight OK: worker home, provider env, and ilk-runner all
present.` The auth token is shown masked (`***set (N chars)***`) and never
printed in full.

## 3. Launch

```bash
# Interactive Worker Claude:
bash tools/claude-worker/claude-worker.sh

# Run an ilk loop as the worker (inherits the fail-closed preflight):
bash tools/claude-worker/claude-worker.sh /ilk-run
```

```powershell
.\tools\claude-worker\claude-worker.ps1
.\tools\claude-worker\claude-worker.ps1 /ilk-run
```

Do **not** live-switch the provider with CCSwitch during a worker run, and do
**not** run multiple workers against the same git worktree / ilk project key —
best-of-N needs isolated worktrees and runtime keys (out of scope here).

## 4. Rollback

The worker home is the only artifact these scripts create:

```bash
rm -rf "$HOME/.claude-worker"
```

```powershell
Remove-Item -Recurse -Force "$HOME\.claude-worker"
```

The Planner home (`~/.claude`), CCSwitch config, and ilk runtime state are
never modified, so rollback needs nothing else.

## 5. QC evidence

The cross-platform QC run for this design (bash syntax, installer dry-runs,
bootstrap dry-run, wrapper preflight-failure against an empty home, and Windows
parity notes) is recorded in
[`dual-claude-homes-qc-evidence.md`](dual-claude-homes-qc-evidence.md).
