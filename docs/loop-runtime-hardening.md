# Loop runtime hardening (2026-06-13, v0.9.5–v0.9.10)

Fresh-session orientation to a cluster of runner / watchdog / scheduler / planner
fixes shipped 2026-06-13. All were driven by two real uccargo incidents (a Figma
MCP stall, then a 2h48m pre-iteration hang) and the postmortems they produced.
Each fix extracted a **pure, unit-tested decision core** with the shells (`.ps1`
/ `.sh`) as thin consumers — see the components named below.

## Invariants a fresh session must respect

1. **The loop worker has its own config + identity.** The `claude-worker` engine
   runs with `CLAUDE_CONFIG_DIR=~/.claude-worker` and reads ITS OWN
   `.claude.json` `mcpServers` — NOT `~/.claude.json`. To give the loop an MCP,
   use `tools/claude-worker/ilk-worker-mcp add <name>` (writes the server +
   copies only that server's `mcpOAuth`, never `claudeAiOauth`). `/ilk-plan`
   step 4b probes the worker surface via `skills/ilk-loop/scripts/worker_mcp.py`,
   not the interactive `claude mcp list`. See `dual-claude-homes-design.md` →
   MCP Isolation.

2. **git's normal stderr is not a failure.** git writes status like "Switched to
   a new branch" to **stderr**; under PowerShell `$ErrorActionPreference='Stop'`
   that becomes a terminating `NativeCommandError` that can wedge the runner.
   Decide on `$LASTEXITCODE` / exit code, never on stderr presence; use `2>$null`
   (never `2>&1`) and localize `$ErrorActionPreference='Continue'` around git
   calls. (This wedged the runner before iteration 1 → a multi-hour hang.)

3. **Branch policy targets EVERY hostable repo.** In a multi-repo project the
   feat branch must be created in each repo a sub-plan targets (`repo:`), not
   just the first discovered repo. `Setup-Branch` loops all repos (skips ones
   missing the base ref / dirty; fails only on a hard git error or none branched).

4. **A pre-iteration-1 hang is recoverable.** The per-iteration timeout only arms
   once an iteration starts. `skills/ilk-loop/scripts/loop_health.py` provides
   `startup_hang_exceeded` + `hung_alive` (+ `hung_by_mtimes`); the watchdog
   detects a `state=running` loop whose progress (max of JSONL summary mtime and
   sentinel mtime) is stale beyond the threshold (default 45 min) and BLOCKs.
   `collect.py` classifies a `startup-hang` sentinel distinctly from `no-evidence`.

5. **Scheduler blacklist honors a human resolve-ack.** The blacklist decision
   lives once in `skills/ilk-watchdog/scripts/blacklist_status.py` (consumed by
   both schedulers): a project is un-blacklisted when a resolve-ack
   (`runtime/launcher/blacklist-cleared.json`, `cleared_at >= the failing
   postmortem's generated_at`) exists, else the 60-min auto-expiry applies.
   `/ilk-resume` writes the ack; `/ilk-feedback` writes it when a session fixes a
   blocker. NOTE: a long-running scheduler can wedge its in-memory `$blacklistSkip`
   — restart clears it; run it hidden (`Start-Process -WindowStyle Hidden`), not
   `-Detach`.

6. **Degrade-to-default over `status: blocked`.** On a headless loop, `blocked`
   = stall + human. When a safe default exists (e.g. build a page to an existing
   pattern when Figma is absent), the guard must take it. A capability with a
   fallback must NOT be a hard `env_prereq` (the gate pre-empts the fallback).
   Enforced by `skills/ilk-loop/scripts/plan_lint.py` (`/ilk-plan` step 7g) +
   decomposition-principles §17.

7. **PowerShell-written files carry a UTF-8 BOM; read with `utf-8-sig`.** The
   runner writes `last-exit.json` (and other sentinels) with a BOM. Any Python
   reading them must `encoding="utf-8-sig"` — plain `utf-8` makes `json.loads`
   choke on the BOM. This silently made `status_all.py` report every running
   loop as `idle` (the tray bug). Keep critical stdout ASCII-only (zh-CN cp936).

8. **Parse-check ≠ runtime-verify for harness wiring.** A `[ScriptBlock]::Create`
   parse-check proves syntax, not that the wiring runs. The watchdog hung-alive
   guard shipped a `DateTimeOffset.ToUnixTimeSeconds` runtime crash that
   parse-checked clean. Extract decisions into pytest'd helpers AND actually run
   the wiring (e.g. dot-source the runner via `ILK_DOTSOURCE_ONLY=1`; launch the
   watchdog against a live loop and read its activity.log) before shipping.

## Release map

| Tag | Change | Key files |
|---|---|---|
| v0.9.5 | planner probes worker MCP surface; degrade-discipline lints | `worker_mcp.py`, `plan_lint.py`, `commands/ilk-plan.md`, decomposition-principles §17 |
| v0.9.6 | `dependency-unreachable` classification; `ilk-worker-mcp` helper | `collect.py`, `tools/claude-worker/{ilk-worker-mcp.*,worker_mcp_edit.py}`, watchdog blacklist |
| v0.9.7 | scheduler resolve-ack; watchdog.sh classify parity | `blacklist_status.py`, `scheduler.{ps1,sh}`, `commands/ilk-resume.md`, `watchdog.{ps1,sh}` |
| v0.9.8 | runner branch-targets every repo + git-stderr hardening; pre-iter-1 hang + watchdog hung-alive | `run_ilk_loop_claude.{ps1,sh}`, `loop_health.py`, `watchdog.{ps1,sh}`, `collect.py` |
| v0.9.9 | fix watchdog hung-alive runtime crash + fresh-run false-positive | `watchdog.{ps1,sh}`, `loop_health.hung_by_mtimes` |
| v0.9.10 | fix tray-always-idle (sentinel BOM read) | `skills/ilk-loop/scripts/status_all.py` |

## How to continue in a fresh session

- Read this file + the component SKILL.md (`ilk-loop`, `ilk-watchdog`,
  `ilk-launcher`) and `decomposition-principles.md`.
- The improvement backlog (`~/.ilk-data/ilk-skills-improvements/candidates.json`,
  via `/ilk-feedback` → `/ilk-self-improve`) is the queue for further fixes.
- Per-component decisions are tested under each skill's `tests/`; run them
  per-directory (a `tests` package-name collision breaks a combined pytest run).
