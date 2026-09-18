# Dual Claude Homes — QC Evidence

Cross-platform QC run for the dual Claude homes design
([`dual-claude-homes-design.md`](dual-claude-homes-design.md)), captured on
macOS (Darwin) on 2026-06-04. All commands are non-destructive (no `--apply`).
Re-run any of them from the repo root to reproduce.

## QC1 — Bash syntax (`bash -n`)

```
$ bash -n install.sh tools/claude-worker/bootstrap.sh tools/claude-worker/claude-worker.sh
OK: all three parse cleanly
```

`bootstrap.ps1` and `claude-worker.ps1` are present alongside the bash scripts.

## QC2 — Installer dry-run with a custom Claude home

```
$ bash install.sh --dry-run --claude-home /tmp/ilk-claude-worker-test --only-claude
claude home:    /tmp/ilk-claude-worker-test (custom)
targets:        Claude Code [/tmp/ilk-claude-worker-test]
actions:        create=12
```

Only the named custom home is targeted (5 `ilk-*` skills + 7 commands +
`tools/migration`); Cursor and Codex are untouched because `--only-claude` is
set.

## QC2b — Default install regression (no custom home)

```
$ bash install.sh --dry-run
targets:        Cursor Claude Code Codex
actions:        skip-correct=36
```

Default behavior is unchanged: all three default targets, every link already
correct. Custom-home support did not alter the default install path.

## QC3 — Bootstrap dry-run with provider values

```
$ bash tools/claude-worker/bootstrap.sh \
    --base-url https://provider.example.com/anthropic \
    --auth-token <33-char dummy> --model worker-model-id \
    --home /tmp/ilk-claude-worker-test
=== claude-worker bootstrap (DRY-RUN) ===
auth token:   ***set (33 chars)***
Would create worker home and write settings.json + .claude.json.
Dry-run complete. Re-run with --apply to bootstrap.
exit=0
```

The token is masked (length-bucketed) and never printed in full. Dry-run wrote
nothing.

## QC3b — Bootstrap fail-closed (missing provider value)

```
$ bash tools/claude-worker/bootstrap.sh --base-url https://x/anthropic --auth-token DUMMY ...
ERROR: incomplete provider env — refusing to write a worker home that
would silently fall back to the planner's official OAuth identity.
Missing:
  - model (--model / ANTHROPIC_MODEL)
exit=3
```

A missing `ANTHROPIC_MODEL` (or base URL / auth token) is fail-closed with
exit 3 — nothing is written.

## QC4 — Wrapper preflight failure against an empty home

```
$ bash tools/claude-worker/claude-worker.sh --home <empty temp dir> --preflight-only
ERROR: worker preflight failed — refusing to launch a worker that would
silently fall back to the planner's official OAuth identity.
Problems:
  - worker settings.json missing: .../settings.json
  - ANTHROPIC_BASE_URL missing ...
  - ANTHROPIC_AUTH_TOKEN missing ...
  - ANTHROPIC_MODEL missing ...
  - ilk-runner skill not found at .../skills/ilk-runner ...
exit=3
```

The wrapper enumerates every missing prerequisite at once and refuses to launch
(exit 3), so a misconfigured worker can never silently fall back to the
planner's official OAuth identity.

## QC5 — Local checks (sub-plan `local_checks`)

```
$ grep -q 'claude-worker' docs/dual-claude-homes-design.md   # OK
$ grep -q -- '--claude-home' install.sh                       # OK
$ grep -q -- '-ClaudeHome' install.ps1                        # OK
```

## QC6 — CCSwitch import (synthetic fixture)

Using a synthetic CCSwitch database fixture with test providers:

```
$ python3 tools/claude-worker/ccswitch_import.py list --ccswitch-dir /tmp/ccswitch-fixture
  test-provider-1
    name:       Test Provider 1
    base_url:   https://test1.example.com/anthropic
    auth_token: ***set (33 chars)***
    model:      claude-sonnet-4-20250514

  test-provider-2 [official]
    name:       Official Claude
    base_url:   https://api.anthropic.com
    auth_token: ***set (40 chars)***
    model:      claude-sonnet-4-20250514
```

Token values are redacted (length-bucketed) in human output.

```
$ python3 tools/claude-worker/ccswitch_import.py export --provider test-provider-1 --ccswitch-dir /tmp/ccswitch-fixture
{
  "id": "test-provider-1",
  "name": "Test Provider 1",
  "category": "third-party",
  "is_official": false,
  "ANTHROPIC_BASE_URL": "https://test1.example.com/anthropic",
  "ANTHROPIC_AUTH_TOKEN": "***set (33 chars)***",
  "ANTHROPIC_MODEL": "claude-sonnet-4-20250514"
}
```

Export with `--machine` emits the raw token (for piping to bootstrap):

```
$ python3 tools/claude-worker/ccswitch_import.py export --provider test-provider-1 --machine --ccswitch-dir /tmp/ccswitch-fixture | python3 -c "import json,sys; d=json.load(sys.stdin); print('token present:', bool(d.get('ANTHROPIC_AUTH_TOKEN')))"
token present: True
```

Official provider import is refused by default:

```
$ bash tools/claude-worker/bootstrap.sh --from-ccswitch --provider test-provider-2 --ccswitch-dir /tmp/ccswitch-fixture
error: provider 'test-provider-2' is an official/Claude OAuth provider.
Importing it into the worker home would use the planner's official identity.
Pass --allow-official to override (not recommended).
exit=2
```

## QC7 — CCSwitch import via bootstrap (synthetic fixture)

```
$ bash tools/claude-worker/bootstrap.sh --from-ccswitch --provider test-provider-1 \
    --home /tmp/ilk-worker-test --ccswitch-dir /tmp/ccswitch-fixture
Imported provider 'test-provider-1' from CCSwitch.

=== claude-worker bootstrap (DRY-RUN) ===
worker home:  /tmp/ilk-worker-test
base url:     https://test1.example.com/anthropic
auth token:   ***set (33 chars)***
model:        claude-sonnet-4-20250514
link skills:  no

Would create worker home and write settings.json + .claude.json.
Dry-run complete. Re-run with --apply to bootstrap.
exit=0
```

The import reads CCSwitch state read-only and populates the bootstrap env vars.
The token is masked in all output.

## Windows parity

`pwsh` / Windows PowerShell is **not installed on this macOS QC host**, so the
`.ps1` scripts could not be executed or `[scriptblock]::Create`-parsed here.
Documented parity instead:

- `install.ps1` exposes `-ClaudeHome` and `-OnlyClaude` (verified by grep) with
  the same `~`-expansion and absolute-path normalization as `install.sh`.
- `tools/claude-worker/bootstrap.ps1` carries the matching provider flags
  (`-Apply`, `-LinkSkills`, `-BaseUrl`, `-AuthToken`, `-Model`) and the
  `ANTHROPIC_*` env block (27 key-token matches).
- `tools/claude-worker/claude-worker.ps1` sets `CLAUDE_CONFIG_DIR` /
  `ILK_SKILL_HOME`, runs the fail-closed preflight, and reads the
  `ANTHROPIC_*` values (21 key-token matches).

**To complete Windows verification on a Windows host**, run the dry-run /
preflight commands from
[`dual-claude-homes-verification.md`](dual-claude-homes-verification.md) §1–§2
under PowerShell and confirm the same exit codes (0 for dry-run/preflight-OK,
3 for missing provider env / empty home).

## Summary

| Check | Result |
|---|---|
| Bash syntax (install + bootstrap + wrapper) | PASS |
| Custom-home installer dry-run | PASS |
| Default install unchanged | PASS (skip-correct=36) |
| Bootstrap dry-run (values masked) | PASS |
| Bootstrap fail-closed (missing value) | PASS (exit 3) |
| Wrapper preflight failure (empty home) | PASS (exit 3) |
| Sub-plan local_checks | PASS |
| CCSwitch list (redacted output) | PASS |
| CCSwitch export (redacted JSON) | PASS |
| CCSwitch export --machine (raw token) | PASS |
| CCSwitch official provider refusal | PASS (exit 2) |
| CCSwitch import via bootstrap (dry-run) | PASS |
| Windows `.ps1` execution | Not run (pwsh absent) — parity documented |
