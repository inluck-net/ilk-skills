Diagnose why nothing is running for the current project.

This is a **read-only** diagnostic command. It walks a sequence of gates
in order, prints the FIRST blocker with its evidence, and stops. A gate
that cannot be evaluated reports `unknown`, never `pass`.

Use when the user says "ilk doctor", "why is nothing running",
`/ilk-doctor`, "diagnose ilk", or wants to know why a loop is idle.

**This tool never mutates anything.** No file writes, no killing, no
cleaning stale files. A doctor that fixes things is a doctor nobody can
run safely on a live loop.

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending
on the host agent.

## Usage

```bash
python3 "<skill-root>/ilk-runner/scripts/doctor.py" --project-path .
python3 "<skill-root>/ilk-runner/scripts/doctor.py" --project-path . --json
python3 "<skill-root>/ilk-runner/scripts/doctor.py" --project-path . --sample-interval 5
```

## Gate order

The doctor evaluates gates in this order and stops at the first blocker:

| # | Gate | What it checks |
|---|---|---|
| 0 | progress-over-time | Samples the newest iter log twice. **Growing → `progressing` (stop; healthy). Static → `quiet` (continue).** |
| 1 | master-status | `draft` / `paused` / `shipped` / `queued` / `active` / none found |
| 2 | subplan-statuses | All shipped / all blocked / nothing runnable, naming blocked slugs |
| 3 | blacklist | Via `blacklist_status.py`, with the expiry time |
| 4 | lock-holders | `lsof` on `run.lock` — reports LIVE holders, not the pid in the file |
| 5 | process-set | Runners matching the project path via `ilk_project_runners` |
| 6 | sentinel-vs-reality | `last-exit.json` state compared against live processes |
| 7 | config-resolution | Resolved `iteration_timeout_min` / `max_iterations` vs `.ilk-launch.json` |

## Key behavior

- **`quiet` is not `stalled`.** A 15-minute foreground gate is byte-identical
  to a stall in any single sample. The doctor uses two samples and reports
  `quiet` (not `stalled`) when the file is static.
- **A gate that cannot be evaluated reports `unknown`, never `pass`.** Silent
  degradation reproduces the exact bug this tool exists to catch.
- **Every check prints what it looked at** (path, command, count) so the
  verdict is auditable from the output alone.
- **No `|| echo` fallbacks and no `2>/dev/null` on diagnostics.** A broken
  probe must be distinguishable from a true zero.

## Options

- `--project-path <path>` — required. Path to the project root.
- `--json` — emit findings as structured JSON instead of human-readable text.
- `--sample-interval <seconds>` — seconds between the two progress-over-time
  samples (default: 20). Use a short interval for testing.

## Examples

```bash
# Human-readable diagnosis
python3 ~/.claude/skills/ilk-runner/scripts/doctor.py --project-path ~/Projects/my-project

# Structured output for scripting
python3 ~/.claude/skills/ilk-runner/scripts/doctor.py --project-path . --json

# Quick check (shorter sample interval)
python3 ~/.claude/skills/ilk-runner/scripts/doctor.py --project-path . --sample-interval 5
```
